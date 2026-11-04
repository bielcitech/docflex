"""Pilote les vues comme le ferait un utilisateur (pytest-qt, sans fenêtre visible)."""

from pathlib import Path

import pymupdf
import pytest
from PIL import Image
from PySide6.QtCore import QMimeData, QPointF, Qt, QUrl
from PySide6.QtGui import QDropEvent

from docflex.app import MainWindow, create_app
from docflex.ui import theme
from tests.test_split_compress import make_pdf, photo


@pytest.fixture
def window(qtbot):
    create_app()
    win = MainWindow()
    qtbot.addWidget(win)
    yield win
    theme.current().set_mode("system")


def run_and_wait(qtbot, panel):
    assert panel.run_btn.isEnabled()
    qtbot.mouseClick(panel.run_btn, Qt.LeftButton)
    qtbot.waitUntil(lambda: not panel.busy, timeout=30000)


def drop(widget, paths: list[Path]) -> None:
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(p)) for p in paths])
    event = QDropEvent(QPointF(10, 10), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
    widget.dropEvent(event)


def test_convert_view(window, qtbot, tmp_path):
    view = window.views["convert"]
    assert not view.panel.run_btn.isEnabled()
    src = make_pdf(tmp_path / "doc.pdf", 2)
    drop(view.files, [src, tmp_path / "archive.zip"])
    assert view.files.paths == [src]
    assert "archive.zip" in view.panel.status.text()  # le message de rejet reste visible

    view.target_menu.setCurrentIndex(view.target_menu.findData("jpg"))
    assert set(view._option_menus) == {"dpi", "quality"}
    run_and_wait(qtbot, view.panel)
    assert sorted(p.name for p in (tmp_path / "doc_pages").iterdir()) == ["doc_p1.jpg", "doc_p2.jpg"]
    assert view.panel.open_btn.isEnabled()
    assert "2 fichiers créés" in view.panel.log.toPlainText()

    view.files.clear()
    assert not view.panel.run_btn.isEnabled() and not view.target_menu.isEnabled()


def test_convert_view_keeps_target_when_files_change(window, tmp_path):
    view = window.views["convert"]
    view.files.add([make_pdf(tmp_path / "a.pdf", 1)])
    view.target_menu.setCurrentIndex(view.target_menu.findData("txt"))
    view.files.add([make_pdf(tmp_path / "b.pdf", 1)])
    assert view.target == "txt"


def test_split_view(window, qtbot, tmp_path):
    view = window.views["split"]
    src = make_pdf(tmp_path / "rapport.pdf", 6, toc=[[1, "Intro", 1], [1, "Suite", 4]])
    view.files.add([src])
    assert "6 pages" in view.preview_title.text() and "2 signets" in view.preview_title.text()

    view.mode_menu.setCurrentIndex(view.mode_menu.findData("ranges"))
    assert not view.panel.run_btn.isEnabled()  # aucune plage saisie
    qtbot.keyClicks(view.value_entry, "9")
    assert "6 pages" in view.preview.toPlainText()
    assert view.value_entry.property("invalid") is True and not view.panel.run_btn.isEnabled()
    view.value_entry.setText("1-2, 5-")
    assert "→ 2 fichiers" in view.preview_title.text()
    run_and_wait(qtbot, view.panel)
    folder = tmp_path / "rapport (découpé)"
    assert sorted(p.name for p in folder.iterdir()) == ["rapport_p1-2.pdf", "rapport_p5-6.pdf"]

    view.mode_menu.setCurrentIndex(view.mode_menu.findData("chunk"))
    assert view.value_entry.text() == "2" and "→ 3 fichiers" in view.preview_title.text()
    view.mode_menu.setCurrentIndex(view.mode_menu.findData("ranges"))
    assert view.value_entry.text() == "1-2, 5-"  # la saisie de chaque mode est conservée

    view.mode_menu.setCurrentIndex(view.mode_menu.findData("bookmarks"))
    assert view.value_entry.isHidden()
    assert "01 Intro" in view.preview.toPlainText()


def test_split_view_rejects_encrypted(window, tmp_path):
    view = window.views["split"]
    locked = tmp_path / "secret.pdf"
    with pymupdf.open(make_pdf(tmp_path / "s.pdf", 2)) as doc:
        doc.save(locked, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="x", owner_pw="y")
    view.files.add([locked])
    assert view.files.paths == []
    assert "mot de passe" in view.panel.status.text()


def test_compress_view(window, qtbot, tmp_path):
    view = window.views["compress"]
    img = tmp_path / "photo.png"
    photo().save(img)
    pdf = tmp_path / "lourd.pdf"
    doc = pymupdf.open()
    doc.new_page().insert_image(pymupdf.Rect(72, 72, 523, 410), filename=img)
    doc.save(pdf)

    assert not view.gray.isEnabled()  # aucun PDF
    view.files.add([pdf, img])
    assert view.gray.isEnabled()
    view.level.set_value("light")
    view.level.changed.emit("light")
    assert not view.gray.isEnabled()
    view.level.set_value("strong")
    view.level.changed.emit("strong")
    view.gray.setChecked(True)
    run_and_wait(qtbot, view.panel)
    assert (tmp_path / "lourd_compressé.pdf").exists() and (tmp_path / "photo_compressé.png").exists()
    assert "→" in view.panel.log.toPlainText()
    with Image.open(tmp_path / "photo_compressé.png") as out:
        assert max(out.size) == 1600


def test_navigation_and_theme(window):
    window.show_view("compress")
    assert window.stack.currentIndex() == 4 and window.nav_buttons["compress"].isChecked()
    theme.current().set_mode("dark")
    assert theme.current().scheme == "dark"
    theme.current().set_mode("light")
    assert theme.current().scheme == "light"


def test_sidebar_toggle_animates_and_is_remembered(window, qtbot):
    assert window.sidebar_visible and window.sidebar.width() == 224
    window.toggle_sidebar()
    qtbot.wait(60)
    assert 0 < window.sidebar.width() < 224  # en cours d'animation
    qtbot.waitUntil(lambda: window.sidebar.isHidden(), timeout=2000)
    assert window.sidebar_handle.text() == "›"

    from docflex.ui.settings import settings

    assert settings().value("sidebar/visible", True, bool) is False
    other = type(window)()  # nouveau lancement : barre toujours repliée
    qtbot.addWidget(other)
    assert other.sidebar.isHidden()

    window.set_sidebar_visible(True)
    qtbot.waitUntil(lambda: window.sidebar.width() == 224, timeout=2000)
    assert not window.sidebar.isHidden() and window.sidebar_handle.text() == "‹"
