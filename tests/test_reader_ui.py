"""Lecteur PDF piloté comme par un utilisateur (pytest-qt, sans fenêtre visible)."""

from pathlib import Path

import pymupdf
import pytest
from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtGui import QFileOpenEvent
from PySide6.QtWidgets import QApplication

from docflex.app import MainWindow, create_app
from tests.test_split_compress import make_pdf


@pytest.fixture
def window(qtbot):
    create_app()
    win = MainWindow()
    qtbot.addWidget(win)
    win.resize(1100, 760)
    win.show()
    return win


@pytest.fixture
def view(window):
    return window.views["reader"]


@pytest.fixture
def reader(view, tmp_path):
    """L'onglet d'un livre de cinq pages."""
    view.open(book(tmp_path / "livre.pdf"))
    return view.current


def book(path: Path, pages: int = 5) -> Path:
    with pymupdf.open() as doc:
        for i in range(pages):
            page = doc.new_page()
            page.insert_text((72, 100), f"Chapitre {i + 1}", fontsize=20)
            page.insert_text((72, 140), "Le lecteur affiche ce texte de démonstration.", fontsize=12)
        if pages > 1:
            doc.set_toc([[1, "Début", 1], [2, "Détail", 2], [1, "Fin", pages]])
        doc.save(path)
    return path


def test_open_navigate_and_zoom(view, qtbot, tmp_path):
    assert view.current is None and view.strip.isHidden()  # écran d'accueil, sans onglets
    view.open(book(tmp_path / "livre.pdf"))
    reader = view.current
    assert view.pages.currentWidget() is reader and reader.doc.page_count == 5 and view.strip.isVisible()
    assert view.tabs.tabText(1) == "livre.pdf" and "livre.pdf · 5 pages" in view.tabs.tabToolTip(1)
    assert "livre.pdf" in reader.window().windowTitle()
    assert reader.page_total.text() == "/ 5" and reader.outline.topLevelItemCount() == 2
    assert reader.thumbs.currentRow() == 0  # miniature de la page courante mise en évidence

    reader.canvas.go_to(3)
    qtbot.waitUntil(lambda: reader.page_spin.value() == 4)
    item = reader.outline.topLevelItem(1)  # signet « Fin »
    reader.outline.itemClicked.emit(item, 0)
    assert reader.canvas.current_page == 4

    reader._choose_zoom(2.0)
    assert reader.canvas.zoom == pytest.approx(2.0) and reader.canvas.fit is None
    assert reader.zoom_menu.currentText() == "200 %"
    reader.canvas.zoom_by(1.1)
    assert reader.zoom_menu.currentText() == "220 %"
    reader._choose_zoom("width")
    canvas = reader.canvas
    page_px = canvas.pages[0].size.width() * canvas.pixels_per_point
    assert canvas.viewport().width() * 0.85 < page_px <= canvas.viewport().width()

    canvas.wait_rendered()
    assert canvas.pages[canvas.current_page].pixmap is not None  # page visible rendue


def test_search_and_copy(reader, qtbot, tmp_path):
    reader.search_field.setText("CHAPITRE")
    reader._on_search()
    assert reader.search_count.text() == "1 / 5"
    reader.next_hit(1)
    reader.next_hit(1)
    assert reader.search_count.text() == "3 / 5"
    page, rect = reader.canvas.hits[2]
    shown = reader.canvas.mapToScene(reader.canvas.viewport().rect()).boundingRect()
    hit = reader.canvas.pages[page].mapRectToScene(rect.x0, rect.y0, rect.width, rect.height)
    assert page == 2 and shown.contains(hit)  # le résultat courant est à l'écran
    reader.next_hit(-1)
    assert reader.search_count.text() == "2 / 5"
    reader.search_field.setText("introuvable")
    reader._on_search()
    assert reader.search_count.text() == "Aucun résultat"
    reader.search_field.clear()
    assert reader.canvas.hits == []

    words = reader.canvas.words(0)
    reader.canvas.select_words(0, 2, 5)
    reader.copy_selection()
    assert QApplication.clipboard().text() == "Le lecteur affiche ce"
    assert " ".join(w.text for w in words[2:6]) == "Le lecteur affiche ce"


def test_mouse_selection(reader, qtbot, tmp_path):
    canvas = reader.canvas
    canvas.go_to(0)
    words = canvas.words(0)

    def view_point(word):
        center = canvas.pages[0].mapToScene(word.rect.x0 + 2, (word.rect.y0 + word.rect.y1) / 2)
        return canvas.mapFromScene(center)

    qtbot.mousePress(canvas.viewport(), Qt.LeftButton, pos=view_point(words[2]))
    qtbot.mouseMove(canvas.viewport(), view_point(words[4]))
    qtbot.mouseRelease(canvas.viewport(), Qt.LeftButton, pos=view_point(words[4]))
    assert canvas.selected_text() == "Le lecteur affiche"
    qtbot.mouseClick(canvas.viewport(), Qt.LeftButton, pos=QPoint(2, 2))  # clic dans la marge
    assert canvas.selected_text() == ""


def test_password_protected(view, qtbot, tmp_path):
    src = book(tmp_path / "secret_src.pdf", 1)
    locked = tmp_path / "secret.pdf"
    with pymupdf.open(src) as doc:
        doc.save(locked, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="ouvre", owner_pw="o")
    answers = iter(["faux", "ouvre"])
    view.ask_password = lambda name, retry: next(answers)
    assert view.open(locked) and view.doc.page_count == 1
    reader = view.current
    reader._loader.wait()  # miniatures du panneau : rendues avec le mot de passe saisi
    qtbot.wait(20)
    assert not reader.thumbs.item(0).icon().isNull()

    view.close_document()
    view.ask_password = lambda name, retry: None  # l'utilisateur renonce
    assert not view.open(locked) and view.current is None and view.pages.currentWidget() is view.home


def test_open_from_system_and_routing(window, tmp_path):
    pdf = book(tmp_path / "doc.pdf", 1)
    QApplication.sendEvent(QApplication.instance(), QFileOpenEvent(str(pdf)))
    assert window.stack.currentIndex() == 0 and window.views["reader"].path == pdf

    other = tmp_path / "tableau.csv"
    other.write_text("a;b\n1;2\n")
    window.open_path(other)
    assert window.stack.currentIndex() == 1 and window.views["convert"].files.paths == [other]
    window.open_path(tmp_path)  # un dossier : ignoré
    assert window.views["convert"].files.paths == [other]


def test_not_a_pdf(view, tmp_path):
    bad = tmp_path / "faux.pdf"
    bad.write_bytes(b"pas un pdf")
    assert not view.open(bad)
    assert "impossible" in view.home.status.text() or "n'est pas" in view.home.status.text()
