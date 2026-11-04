"""Onglets du lecteur et barres d'outils flottantes (pytest-qt, sans fenêtre visible)."""

import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtWidgets import QPushButton, QTabBar

from docflex.app import MainWindow, create_app
from docflex.core.annotations import AnnotSpec, Change
from docflex.ui.settings import settings
from tests.test_reader_ui import book


@pytest.fixture
def window(qtbot):
    create_app()
    win = MainWindow()
    win.resize(1280, 840)
    win.show()
    yield win
    view = win.views["reader"]
    view.dirty = False
    view.set_bars_visible(True)  # réglage partagé entre les tests
    win.close()
    win.deleteLater()


def test_several_documents_in_tabs(window, qtbot, tmp_path):
    view = window.views["reader"]
    first, second = book(tmp_path / "Premier.pdf", 2), book(tmp_path / "Second.pdf", 3)
    assert view.open(first) and view.open(second)
    assert view.tabs.count() == 3 and view.current.path == second and view.pages.count() == 3
    assert "Second.pdf" in window.windowTitle()

    assert view.open(first)  # déjà ouvert : on revient à son onglet
    assert view.tabs.count() == 3 and view.tabs.currentIndex() == 1 and "Premier.pdf" in window.windowTitle()

    tab = view.current
    tab._record(Change(0, None, AnnotSpec("rect", rect=(100, 100, 200, 200), color=(1, 0, 0))))
    assert view.tabs.tabText(1) == "• Premier.pdf" and window.isWindowModified()
    view._step_tab(1)
    assert view.current.path == second and not window.isWindowModified()

    tab.ask_save = lambda: "cancel"
    close = view.tabs.tabButton(1, QTabBar.RightSide).findChild(QPushButton)
    qtbot.mouseClick(close, Qt.LeftButton)
    assert view.tabs.count() == 3 and view.current is tab  # question posée sur le bon onglet, annulée
    tab.ask_save = lambda: "discard"
    qtbot.mouseClick(close, Qt.LeftButton)
    assert [t.path for t in view.documents] == [second]

    view.close_document()
    assert view.current is None and view.pages.currentWidget() is view.home and view.strip.isHidden()
    assert window.windowTitle() == "DocFlex"


def test_quit_asks_for_each_modified_tab(window, tmp_path):
    view = window.views["reader"]
    for name in ("a", "b"):
        view.open(book(tmp_path / f"{name}.pdf", 1))
        view.current._set_dirty(True)
    asked = []
    for tab in view.documents:
        tab.ask_save = lambda t=tab: asked.append(t.title) or "discard"
    assert view.confirm_close() and asked == ["a.pdf", "b.pdf"]


def test_floating_bars_slide_away_and_back(window, qtbot, tmp_path):
    view = window.views["reader"]
    view.open(book(tmp_path / "livre.pdf", 3))
    tab = view.current
    top, bottom = tab.top_bar, tab.bottom_bar
    area = top.parentWidget()
    qtbot.waitUntil(lambda: top.isVisible() and top.y() > 0)
    assert bottom.geometry().bottom() < area.height() and abs(top.geometry().center().x() - area.width() / 2) <= 1

    # Le document défile sous les barres mais commence sous celle du haut.
    canvas = tab.canvas
    tab._choose_zoom(1.0)
    canvas.go_to(0)
    page_top = canvas.mapFromScene(canvas.pages[0].mapToScene(QPointF(0, 0))).y()
    assert page_top >= top.geometry().bottom()

    view.toggle_bars()
    assert not view.bars_visible and not settings().value("reader/toolbars", True, bool)
    qtbot.waitUntil(lambda: top.isHidden() and bottom.isHidden(), timeout=2000)
    view.toggle_bars()
    qtbot.waitUntil(lambda: tab.overlay.shown == 1.0 and top.y() > 0, timeout=2000)
    assert view.bars_btn.isChecked()

    view.open(book(tmp_path / "autre.pdf", 1))  # un nouvel onglet suit le réglage courant
    view.set_bars_visible(False)
    assert view.current.overlay.visible is False and tab.overlay.shown == 0.0  # onglet caché : sans animation


def test_search_unfolds_and_toast_fades(window, qtbot, tmp_path):
    view = window.views["reader"]
    view.open(book(tmp_path / "livre.pdf", 3))
    tab = view.current
    view.set_bars_visible(False)
    assert tab.search_bar.isHidden()
    tab.focus_search()  # ⌘F : les barres reviennent et le tiroir sort de sous celle du haut
    qtbot.waitUntil(lambda: tab.overlay.drawer_shown == 1.0 and tab.overlay.shown == 1.0, timeout=1000)
    assert view.bars_visible and tab.search_btn.isChecked() and tab.search_bar.isVisible()
    assert tab.search_bar.y() > tab.top_bar.geometry().bottom()
    assert tab.search_bar.geometry().right() == tab.top_bar.geometry().right()
    tab.search_field.setText("chapitre")
    tab._on_search()
    assert tab.search_count.text() == "1 / 3"
    tab.toggle_search()
    qtbot.waitUntil(lambda: tab.search_bar.isHidden(), timeout=1000)
    assert tab.search_field.text() == "" and tab.canvas.hits == []

    tab.status.setText("Texte copié.")
    assert tab.status.isVisible() and tab.status.geometry().bottom() < tab.bottom_bar.y()
    tab.status._hold.start(1)  # sans attendre les quelques secondes de lecture
    qtbot.waitUntil(lambda: tab.status.isHidden(), timeout=2000)
    assert tab.status.text() == "Texte copié."


def test_side_panel_makes_room_for_the_bottom_bar(window, qtbot, tmp_path):
    view = window.views["reader"]
    view.open(book(tmp_path / "livre.pdf", 2))
    tab = view.current
    assert tab.side.isVisible()
    window.resize(1024, 700)  # largeur minimale, menu de gauche déplié
    qtbot.waitUntil(lambda: tab.side.isHidden())  # trop étroit : la barre du bas passe d'abord
    qtbot.waitUntil(lambda: tab.bottom_bar.width() == tab.bottom_bar.sizeHint().width())  # entière
    window.resize(1400, 840)
    qtbot.waitUntil(lambda: tab.side.isVisible())  # la place revient : le panneau aussi
    tab._toggle_panel()
    assert tab.side.isHidden() and not tab.side_wanted


def test_links_and_bookmarks_navigate(window, qtbot, tmp_path):
    from tests.test_reader import linked_doc

    path = tmp_path / "liens.pdf"
    linked_doc().save(path)
    view = window.views["reader"]
    view.open(path)
    tab = view.current
    canvas = tab.canvas
    tab._choose_zoom(1.0)
    canvas.go_to(0)

    def click(x, y):
        pos = canvas.mapFromScene(canvas.pages[0].mapToScene(x, y))
        qtbot.mouseMove(canvas.viewport(), pos)
        qtbot.mouseClick(canvas.viewport(), Qt.LeftButton, pos=pos)

    opened = []
    canvas.uri_clicked.connect(opened.append)
    click(60, 210)
    assert opened == ["https://example.com"] and canvas.viewport().toolTip() == "https://example.com"
    click(60, 110)  # lien interne : page 3, titre visé en haut, sous la barre flottante
    assert canvas.current_page == 2
    target = canvas.mapFromScene(canvas.pages[2].mapToScene(0, 300)).y()
    assert tab.top_bar.geometry().bottom() < target < tab.top_bar.geometry().bottom() + 60

    canvas.go_to(0)
    tab.side_mode.changed.emit("outline")
    item = tab.outline.topLevelItem(0)
    assert item.text(0) == "Annexe" and item.text(1) == "3" and tab.outline_note.isHidden()
    tab.outline.itemClicked.emit(item, 0)
    assert canvas.current_page == 2


def test_headings_detected_when_the_pdf_has_no_bookmarks(window, tmp_path):
    from tests.test_reader import word_like_pdf

    path = tmp_path / "word.pdf"
    word_like_pdf().save(path)
    view = window.views["reader"]
    view.open(path)
    tab = view.current
    assert tab._headings_pending and tab.outline.topLevelItemCount() == 0  # rien de lu avant le clic
    tab.side_mode.changed.emit("outline")
    titles = [tab.outline.topLevelItem(i).text(0) for i in range(tab.outline.topLevelItemCount())]
    assert titles == ["Chapitre 1", "Chapitre 2"] and tab.outline.topLevelItem(0).childCount() == 2
    assert tab.outline_note.isVisible()
    tab.outline.itemClicked.emit(tab.outline.topLevelItem(1), 0)
    assert tab.canvas.current_page == 1
