"""Clics réels sur les boutons (et pas seulement des appels de méthodes).

Régression : avec PySide6, `clicked(bool)` remplit le premier paramètre d'un lambda
`lambda k=key: …` ; le menu latéral et les outils restaient alors figés.
"""

from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest

from docflex.app import MainWindow, create_app
from tests.test_reader_ui import book
from tests.test_split_compress import make_pdf


@pytest.fixture
def window(qtbot):
    create_app()
    win = MainWindow()  # pas de qtbot.addWidget : il fermerait la fenêtre avant notre nettoyage
    win.resize(1280, 840)
    win.show()
    yield win
    # Sinon « Fermer sans enregistrer ? » attendrait un clic à la fermeture.
    win.views["organize"]._saved = list(win.views["organize"].pages)
    win.views["reader"].dirty = False
    win.close()
    win.deleteLater()


def test_sidebar_buttons_switch_views(window, qtbot):
    for index, (key, btn) in enumerate(window.nav_buttons.items()):
        qtbot.mouseClick(btn, Qt.LeftButton)
        assert window.stack.currentIndex() == index, key
        assert btn.isChecked()


def test_annotation_and_edit_tool_buttons(window, qtbot, tmp_path):
    window.views["reader"].open(book(tmp_path / "livre.pdf", 2))
    reader = window.views["reader"].current
    for name, btn in reader.annot_bar.tools.items():
        qtbot.mouseClick(btn, Qt.LeftButton)
        assert reader.tool == name and reader.canvas.tool == name
    qtbot.mouseClick(reader.mode._buttons["edit"], Qt.LeftButton)
    reader.ask_image = lambda: Path("inutile.png")
    for name, btn in reader.edit_bar.tools.items():
        qtbot.mouseClick(btn, Qt.LeftButton)
        assert reader.tool == name, name


def test_color_menu_entries(window, tmp_path):
    window.views["reader"].open(book(tmp_path / "livre.pdf", 1))
    reader = window.views["reader"].current
    reader.set_tool("rect")
    action = reader.annot_bar.color_btn.menu().actions()[2]  # « Bleu »
    action.trigger()
    assert reader.styles["rect"]["color"] == pytest.approx((0.15, 0.45, 0.95))


def test_merge_arrows_reorder(window, qtbot, tmp_path):
    page = window.views["tools"].pages["merge"]
    a, b = make_pdf(tmp_path / "a.pdf", 1), make_pdf(tmp_path / "b.pdf", 1)
    page.files.add([a, b])
    row = page.files._list.itemWidget(page.files._list.item(1))
    up = next(btn for btn in row.findChildren(type(window.nav_buttons["reader"])) if btn.text() == "▲")
    qtbot.mouseClick(up, Qt.LeftButton)
    assert page.files.paths == [b, a]


# ------------------------------------- organiser : déplacement à la souris


@pytest.fixture
def organizer(window, tmp_path):
    view = window.views["organize"]
    window.show_view("organize")
    view.open(make_pdf(tmp_path / "doc.pdf", 6))
    return view


def labels(view):
    return [view.grid.item(i).text() for i in range(view.grid.count())]


def mouse_drag(grid, start: QPoint, end: QPoint, steps: int = 8, modifier=Qt.NoModifier):
    viewport = grid.viewport()
    QTest.mousePress(viewport, Qt.LeftButton, modifier, start)
    for i in range(1, steps + 1):
        QTest.mouseMove(viewport, start + (end - start) * (i / steps))
    QTest.mouseRelease(viewport, Qt.LeftButton, Qt.NoModifier, end)


def center(grid, row):
    return grid.visualItemRect(grid.item(row)).center()


def test_drag_one_page_with_the_mouse(organizer):
    grid = organizer.grid
    # Page 1 lâchée sur la moitié droite de la page 4 : elle se place après la 4.
    mouse_drag(grid, center(grid, 0), center(grid, 3) + QPoint(20, 0))
    assert labels(organizer) == ["2", "3", "4", "1", "5", "6"]
    assert grid.selected_rows() == [3] and organizer.dirty
    organizer.undo()
    assert labels(organizer) == ["1", "2", "3", "4", "5", "6"]


def test_drag_several_selected_pages(organizer):
    grid = organizer.grid
    grid.item(1).setSelected(True)
    grid.item(4).setSelected(True)
    # Saisir l'une des pages sélectionnées emporte toute la sélection, dans son ordre.
    mouse_drag(grid, center(grid, 4), center(grid, 0) - QPoint(20, 0))
    assert labels(organizer) == ["2", "5", "1", "3", "4", "6"]


def test_escape_cancels_and_click_does_not_move(organizer):
    grid = organizer.grid
    viewport = grid.viewport()
    start = center(grid, 0)
    QTest.mousePress(viewport, Qt.LeftButton, Qt.NoModifier, start)
    for i in range(1, 6):
        QTest.mouseMove(viewport, start + QPoint(i * 30, 0))
    QTest.keyClick(grid, Qt.Key_Escape)
    QTest.mouseRelease(viewport, Qt.LeftButton, Qt.NoModifier, start + QPoint(150, 0))
    QTest.mouseClick(viewport, Qt.LeftButton, Qt.NoModifier, center(grid, 2))
    assert labels(organizer) == ["1", "2", "3", "4", "5", "6"] and not organizer.dirty
