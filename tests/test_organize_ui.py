"""Organiseur de pages piloté comme par un utilisateur (pytest-qt, sans fenêtre visible)."""

import pymupdf
import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QImage

from docflex.app import MainWindow, create_app
from docflex.ui import organize_view
from docflex.ui.crop_dialog import CropCanvas
from tests.test_split_compress import make_pdf, page_texts


@pytest.fixture
def view(qtbot):
    create_app()
    win = MainWindow()  # pas de qtbot.addWidget : il fermerait la fenêtre avant notre nettoyage
    win.resize(1100, 760)
    win.show()
    win.show_view("organize")
    organizer = win.views["organize"]
    yield organizer
    organizer._saved = list(organizer.pages)  # sinon « Fermer sans enregistrer ? » attendrait un clic
    win.close()
    win.deleteLater()


def wait_thumbs(qtbot, view):
    qtbot.waitUntil(lambda: all(not loader.isRunning() for loader in view._loaders), timeout=10000)
    qtbot.wait(20)  # livraison des derniers signaux


def select(view, *rows):
    view.grid.clearSelection()
    for row in rows:
        view.grid.item(row).setSelected(True)


def labels(view):
    return [view.grid.item(i).text() for i in range(view.grid.count())]


def test_edit_undo_redo_and_save(view, qtbot, tmp_path):
    src = make_pdf(tmp_path / "doc.pdf", 5)
    view.files.add([src])
    wait_thumbs(qtbot, view)
    assert view._stack.currentIndex() == 1 and len(view.grid.thumbs) == 5
    assert labels(view) == ["1", "2", "3", "4", "5"] and not view.dirty
    assert not view.actions["delete"].isEnabled()  # rien de sélectionné

    select(view, 1)
    view.delete()
    assert labels(view) == ["1", "3", "4", "5"] and view.dirty and "modifié" in view.info.text()
    select(view, 0)
    view.duplicate()
    assert labels(view) == ["1", "1", "3", "4", "5"] and view.grid.selected_rows() == [1]
    view.rotate(90)
    view.grid.moved.emit([1], 5)  # comme un glisser-déposer de la copie tout à la fin
    assert labels(view) == ["1", "3", "4", "5", "1"] and view.pages[-1].rotation == 90

    view.undo()
    view.undo()
    assert labels(view) == ["1", "1", "3", "4", "5"] and view.pages[1].rotation == 0
    view.redo()
    view.redo()
    assert labels(view) == ["1", "3", "4", "5", "1"]
    assert not view.actions["redo"].isEnabled()

    out = tmp_path / "résultat.pdf"
    view.save_to(out)
    qtbot.waitUntil(lambda: not view.panel.busy, timeout=10000)
    assert page_texts(out) == ["Page 1", "Page 3", "Page 4", "Page 5", "Page 1"]
    with pymupdf.open(out) as doc:
        assert doc[-1].rotation == 90
    assert not view.dirty and page_texts(src)[1] == "Page 2"  # original intact


def test_save_over_source_reloads(view, qtbot, tmp_path):
    src = make_pdf(tmp_path / "doc.pdf", 3)
    view.open(src)
    view.reverse()
    view.save_to(src)
    qtbot.waitUntil(lambda: not view.panel.busy, timeout=10000)
    wait_thumbs(qtbot, view)
    assert page_texts(src) == ["Page 3", "Page 2", "Page 1"]
    # Les numéros affichés repartent du fichier réécrit, sans historique trompeur.
    assert labels(view) == ["1", "2", "3"] and not view.dirty and not view.actions["undo"].isEnabled()


def test_add_second_pdf_uses_letters(view, qtbot, tmp_path):
    view.open(make_pdf(tmp_path / "a.pdf", 2))
    view.add_documents([make_pdf(tmp_path / "b.pdf", 2)])
    wait_thumbs(qtbot, view)
    assert labels(view) == ["A1", "A2", "B1", "B2"] and view.grid.selected_rows() == [2, 3]
    assert "A = a.pdf" in view.info.text() and "B = b.pdf" in view.info.text()
    view.undo()  # l'ajout s'annule comme le reste
    assert labels(view) == ["A1", "A2"]


def test_insertion_row_geometry(view, qtbot, tmp_path):
    view.open(make_pdf(tmp_path / "a.pdf", 6))
    qtbot.wait(20)
    first = view.grid.visualItemRect(view.grid.item(0))
    second = view.grid.visualItemRect(view.grid.item(1))
    last = view.grid.visualItemRect(view.grid.item(5))
    assert view.grid.insertion_row(first.topLeft()) == 0
    assert view.grid.insertion_row(second.center() - QPoint(5, 0)) == 1   # moitié gauche → avant
    assert view.grid.insertion_row(second.center() + QPoint(5, 0)) == 2   # moitié droite → après
    assert view.grid.insertion_row(last.bottomRight() + QPoint(40, 40)) == 6


def test_rejects_encrypted_and_confirms_discard(view, qtbot, tmp_path, monkeypatch):
    locked = tmp_path / "secret.pdf"
    with pymupdf.open(make_pdf(tmp_path / "s.pdf", 2)) as doc:
        doc.save(locked, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="x", owner_pw="y")
    view.files.add([locked])
    assert view._stack.currentIndex() == 0 and "mot de passe" in view.panel.status.text()

    view.files.add([make_pdf(tmp_path / "ok.pdf", 3)])
    select(view, 0)
    view.delete()
    monkeypatch.setattr(organize_view, "ask", lambda *a, **k: False)
    view.close_document()
    assert view.sources  # annulé : le document reste ouvert
    monkeypatch.setattr(organize_view, "ask", lambda *a, **k: True)
    view.close_document()
    assert not view.sources and view._stack.currentIndex() == 0


# ------------------------------------------------------- recadrage


@pytest.fixture
def canvas(qtbot):
    image = QImage(400, 600, QImage.Format_RGB888)
    image.fill(Qt.white)
    widget = CropCanvas(image)
    qtbot.addWidget(widget)
    widget.resize(424, 624)  # image affichée à l'échelle 1, décalée de 12 px
    widget.show()
    return widget


def at(canvas, fx, fy) -> QPoint:
    r = canvas.image_rect()
    return QPoint(round(r.left() + fx * r.width()), round(r.top() + fy * r.height()))


def test_canvas_draw_move_resize(canvas, qtbot):
    # Au départ le cadre couvre toute la page : on le réduit par son coin.
    qtbot.mousePress(canvas, Qt.LeftButton, pos=at(canvas, 1, 1))
    qtbot.mouseMove(canvas, at(canvas, 0.5, 0.5))
    qtbot.mouseRelease(canvas, Qt.LeftButton, pos=at(canvas, 0.5, 0.5))
    assert canvas.box == pytest.approx((0, 0, 0.5, 0.5), abs=0.01)

    # Nouveau cadre tracé vers le haut et la gauche, hors du cadre actuel.
    qtbot.mousePress(canvas, Qt.LeftButton, pos=at(canvas, 0.9, 0.9))
    qtbot.mouseMove(canvas, at(canvas, 0.6, 0.7))
    qtbot.mouseRelease(canvas, Qt.LeftButton, pos=at(canvas, 0.6, 0.7))
    assert canvas.box == pytest.approx((0.6, 0.7, 0.9, 0.9), abs=0.01)

    # Déplacement, bloqué au bord de la page.
    qtbot.mousePress(canvas, Qt.LeftButton, pos=at(canvas, 0.75, 0.8))
    qtbot.mouseMove(canvas, at(canvas, 1.2, 0.8))
    qtbot.mouseRelease(canvas, Qt.LeftButton, pos=at(canvas, 1.2, 0.8))
    assert canvas.box == pytest.approx((0.7, 0.7, 1.0, 0.9), abs=0.01)

    # Simple clic hors du cadre : le cadre ne disparaît pas.
    qtbot.mouseClick(canvas, Qt.LeftButton, pos=at(canvas, 0.1, 0.1))
    assert canvas.box == pytest.approx((0.7, 0.7, 1.0, 0.9), abs=0.01)


def test_crop_dialog_applies_to_selection_and_saves(view, qtbot, tmp_path):
    view.open(make_pdf(tmp_path / "doc.pdf", 3))
    wait_thumbs(qtbot, view)
    select(view, 0, 2)
    view.rotate(90)  # page tournée : le cadre vu à l'écran doit être converti

    def accept(dialog):
        assert dialog.scope.currentData() == "selected"  # sélection multiple proposée par défaut
        dialog.canvas.set_box((0, 0, 1, 0.5))
        return 1  # QDialog.Accepted

    view._exec = accept
    view.crop_dialog()
    assert view.pages[0].crop is not None and view.pages[1].crop is None and view.pages[2].crop is not None
    assert view.pages[0].display_crop() == pytest.approx((0, 0, 1, 0.5))

    view._exec = lambda dialog: 0  # Annuler : rien ne change
    before = list(view.pages)
    view.crop_dialog(1)
    assert view.pages == before

    view.undo()
    assert all(p.crop is None for p in view.pages)
    view.redo()

    out = tmp_path / "o.pdf"
    view.save_to(out)
    qtbot.waitUntil(lambda: not view.panel.busy, timeout=10000)
    with pymupdf.open(out) as doc:
        # Page tournée (paysage à l'écran) puis moitié haute conservée : plus large que haute.
        assert doc[0].rect.width > doc[0].rect.height * 1.9
        assert doc[1].rect == pymupdf.Rect(0, 0, 595, 842)


def test_trim_margins_and_blank_page(view, qtbot, tmp_path):
    src = tmp_path / "m.pdf"
    with pymupdf.open() as doc:
        page = doc.new_page(width=400, height=600)
        page.draw_rect(pymupdf.Rect(100, 150, 300, 450), color=None, fill=(0, 0, 0))
        doc.new_page(width=400, height=600)  # page vide : laissée telle quelle
        doc.save(src)
    view.open(src)
    select(view, 0, 1)
    view.trim_margins()
    assert view.pages[0].crop == pytest.approx((0.235, 0.235, 0.765, 0.765), abs=0.02)
    assert view.pages[1].crop is None

    select(view, 0)
    view.add_blank()
    assert labels(view) == ["1", "blanche", "2"] and view.pages[1].size == pytest.approx((400, 600))
    view.crop_dialog(1)  # une page blanche ne se recadre pas
    assert "blanche" in view.panel.status.text()
    assert view.pages[1].crop is None
