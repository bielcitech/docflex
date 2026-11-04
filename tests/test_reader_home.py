"""Accueil du lecteur : documents récents, favoris, reprise de lecture."""

import pytest
from PySide6.QtCore import Qt

from docflex.app import MainWindow, create_app
from tests.test_reader_ui import book


@pytest.fixture
def window(qtbot):
    create_app()
    win = MainWindow()
    win.resize(1280, 840)
    win.show()
    reader = win.views["reader"]
    reader.recent.clear(keep_favorites=False)  # historique propre à chaque test
    reader.home.refresh()
    yield win
    reader.dirty = False
    win.close()
    win.deleteLater()


def cards(home):
    return [home.list.item(i).data(Qt.UserRole) for i in range(home.list.count())]


def test_closed_documents_appear_and_reopen_where_left(window, qtbot, tmp_path):
    reader = window.views["reader"]
    home = reader.home
    assert home.body.currentIndex() == 1 and "Aucun document" in home.empty_title.text()

    first, second = book(tmp_path / "Premier.pdf", 6), book(tmp_path / "Second.pdf", 2)
    reader.open(first)
    reader.current.canvas.go_to(4)
    reader.close_document()
    reader.open(second)
    reader.close_document()
    assert [e.name for e in cards(home)] == ["Second", "Premier"] and home.count.text() == "2 documents"
    assert cards(home)[1].last_page == 4 and cards(home)[1].page_count == 6

    qtbot.waitUntil(lambda: home.thumbnail(cards(home)[0].path) is not None, timeout=5000)
    assert any((home.cache).glob("*.png"))  # miniature gardée en cache

    item = home.list.item(1)  # clic sur la carte « Premier »
    qtbot.mouseClick(home.list.viewport(), Qt.LeftButton, pos=home.list.visualItemRect(item).center())
    assert reader.path == first and reader.current.canvas.current_page == 4
    assert "Reprise à la page 5" in reader.current.status.text()


def test_favorites_search_list_mode_and_clear(window, tmp_path):
    reader = window.views["reader"]
    home = reader.home
    for name in ("Rapport annuel", "Contrat", "Devis"):
        reader.open(book(tmp_path / f"{name}.pdf", 1))
        reader.close_document()
    contrat = next(e for e in cards(home) if e.name == "Contrat")
    action = next(a for a in home.menu_for(contrat).actions() if a.text() == "Ajouter aux favoris")
    action.trigger()
    home.tabs.set_value("favorites")
    home.tabs.changed.emit("favorites")
    assert [e.name for e in cards(home)] == ["Contrat"] and cards(home)[0].favorite

    home.tabs.set_value("recent")
    home.tabs.changed.emit("recent")
    home.search.setText("rap")
    assert [e.name for e in cards(home)] == ["Rapport annuel"]
    home.search.setText("zzz")
    assert home.body.currentIndex() == 1 and home.empty_title.text() == "Aucun résultat"
    home.search.clear()

    home.set_grid_mode(False)
    assert home.list.viewMode() == home.list.ViewMode.ListMode and home.list_btn.isChecked()
    home.set_grid_mode(True)

    home._clear()
    assert [e.name for e in cards(home)] == ["Contrat"]  # le favori survit au nettoyage


def test_missing_file_and_hover(window, qtbot, tmp_path):
    reader = window.views["reader"]
    home = reader.home
    path = book(tmp_path / "Disparu.pdf", 1)
    reader.open(path)
    reader.close_document()
    path.unlink()
    home.refresh()
    assert not cards(home)[0].exists
    qtbot.mouseClick(home.list.viewport(), Qt.LeftButton, pos=home.list.visualItemRect(home.list.item(0)).center())
    assert reader.doc is None and "introuvable" in home.status.text()

    home._set_hovered(0)  # survol : la carte se soulève et la miniature s'incline, en douceur
    qtbot.waitUntil(lambda: 0 < home.hover_amount(0) < 1, timeout=1000)  # progressif, pas d'un coup
    qtbot.waitUntil(lambda: home.hover_amount(0) == pytest.approx(1), timeout=1000)
    home._set_hovered(-1)
    qtbot.waitUntil(lambda: home.hover_amount(0) == pytest.approx(0), timeout=1000)


def test_long_file_names_wrap_at_punctuation():
    from PySide6.QtGui import QFont, QFontMetrics

    from docflex.ui.reader_home import _elide_lines

    metrics = QFontMetrics(QFont())
    width = metrics.horizontalAdvance("Chart.11.Aerodrome.")
    lines = _elide_lines("Chart.11.Aerodrome.Chart.en", metrics, width, 2)
    assert len(lines) == 2 and lines[0].startswith("Chart.11") and "Chart" in lines[1]
    long = _elide_lines("Un titre vraiment très long " * 5, metrics, width, 2)
    assert len(long) == 2 and long[1].endswith("…")
