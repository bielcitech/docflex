"""Annotations dans le lecteur, pilotées à la souris (pytest-qt, sans fenêtre visible)."""

import pymupdf
import pytest
from PySide6.QtCore import Qt

from docflex.app import MainWindow, create_app
from docflex.core.annotations import page_specs
from tests.test_reader_ui import book


@pytest.fixture
def reader(qtbot, tmp_path):
    create_app()
    win = MainWindow()
    win.resize(1280, 840)
    win.show()
    view = win.views["reader"]
    view.open(book(tmp_path / "livre.pdf", 3))
    tab = view.current  # l'onglet du document : barres, toile, historique
    tab.canvas.set_zoom(1.0)
    tab.canvas.go_to(0)
    yield tab
    view.dirty = False  # pas de question à la fermeture
    win.close()
    win.deleteLater()


def at(reader, x, y, page=0):
    canvas = reader.canvas
    return canvas.mapFromScene(canvas.pages[page].mapToScene(x, y))


def drag(qtbot, reader, start, end, page=0, steps=4):
    viewport = reader.canvas.viewport()
    qtbot.mousePress(viewport, Qt.LeftButton, pos=at(reader, *start, page))
    for i in range(1, steps + 1):
        x = start[0] + (end[0] - start[0]) * i / steps
        y = start[1] + (end[1] - start[1]) * i / steps
        qtbot.mouseMove(viewport, at(reader, x, y, page))
    qtbot.mouseRelease(viewport, Qt.LeftButton, pos=at(reader, *end, page))


def kinds(reader, page=0):
    return [s.kind for s in page_specs(reader.doc[page])]


def test_highlight_selected_text_then_undo_redo(reader):
    reader.canvas.select_words(0, 2, 4)  # « Le lecteur affiche »
    reader.set_tool("highlight")
    assert kinds(reader) == ["highlight"] and reader.tool == "select"  # appliqué, retour à la sélection
    assert reader.dirty and reader.save_btn.isEnabled() and reader.window().isWindowModified()
    assert "Surlignage — « Le lecteur affiche »" in reader.annot_list.item(0).text()
    reader.undo()
    assert kinds(reader) == [] and reader.redo_btn.isEnabled()
    reader.redo()
    assert kinds(reader) == ["highlight"]


def test_markup_tool_by_dragging_over_text(reader, qtbot):
    reader.set_tool("strikeout")
    words = reader.canvas.words(0)
    first, last = words[2].rect, words[3].rect
    drag(qtbot, reader, (first.x0 + 2, (first.y0 + first.y1) / 2), (last.x1 - 2, (last.y0 + last.y1) / 2))
    spec = page_specs(reader.doc[0])[0]
    assert spec.kind == "strikeout" and spec.color == pytest.approx((0.9, 0.15, 0.15), abs=0.02)


def test_draw_shapes_ink_note_and_textbox(reader, qtbot):
    reader.set_tool("rect")
    drag(qtbot, reader, (100, 300), (250, 400))
    reader.set_tool("arrow")
    drag(qtbot, reader, (300, 300), (450, 380))
    reader.set_tool("ink")
    drag(qtbot, reader, (100, 450), (300, 520), steps=10)
    reader.ask_text = lambda title, initial: "Penser à relire"
    reader.set_tool("note")
    qtbot.mouseClick(reader.canvas.viewport(), Qt.LeftButton, pos=at(reader, 500, 200))
    reader.set_tool("text")
    reader._set_style(fontsize=16.0)
    reader.ask_text = lambda title, initial: "Une zone de texte assez longue pour passer à la ligne"
    drag(qtbot, reader, (100, 600), (260, 615))
    assert kinds(reader) == ["rect", "arrow", "ink", "note", "text"]
    specs = page_specs(reader.doc[0])
    assert specs[0].rect == pytest.approx((100, 300, 250, 400), abs=2)
    assert specs[3].text == "Penser à relire"
    assert specs[4].fontsize == 16 and specs[4].rect[3] - specs[4].rect[1] > 40  # agrandie pour le texte
    assert reader.annot_list.count() == 5

    reader.ask_text = lambda title, initial: None  # annulé : rien n'est créé
    reader.set_tool("note")
    qtbot.mouseClick(reader.canvas.viewport(), Qt.LeftButton, pos=at(reader, 520, 260))
    assert len(kinds(reader)) == 5


def test_select_move_recolor_edit_delete(reader, qtbot):
    reader.set_tool("rect")
    drag(qtbot, reader, (100, 300), (200, 380))
    reader.set_tool("select")
    drag(qtbot, reader, (150, 302), (190, 342))  # saisir le bord du rectangle et le déplacer
    spec = page_specs(reader.doc[0])[0]
    assert spec.rect == pytest.approx((140, 340, 240, 420), abs=2)
    assert reader.canvas.selected_annot == (0, 0) and reader.annot_bar.delete_btn.isEnabled()

    reader.annot_bar.color_chosen.emit((0.15, 0.45, 0.95))  # recolore la sélection
    assert page_specs(reader.doc[0])[0].color == pytest.approx((0.15, 0.45, 0.95), abs=0.02)
    assert reader.styles["rect"]["color"] == (0.9, 0.15, 0.15)  # le style de l'outil n'a pas changé

    reader.ask_text = lambda title, initial: "Zone à revoir"
    qtbot.mouseDClick(reader.canvas.viewport(), Qt.LeftButton, pos=at(reader, 190, 380))
    assert page_specs(reader.doc[0])[0].text == "Zone à revoir"

    reader.canvas.select_annotation((0, 0))
    reader.delete_selected()
    assert kinds(reader) == []
    for _ in range(4):
        reader.undo()
    assert page_specs(reader.doc[0])[0].rect == pytest.approx((100, 300, 200, 380), abs=2)


def test_save_reopen_and_close_prompt(reader, qtbot, tmp_path):
    path = reader.path
    reader.set_tool("ellipse")
    drag(qtbot, reader, (100, 300), (250, 400))
    assert reader.save() and not reader.dirty and not reader.save_btn.isEnabled()
    reader.set_tool("rect")
    drag(qtbot, reader, (300, 300), (400, 400))

    reader.ask_save = lambda: "cancel"
    assert not reader.confirm_close() and reader.doc is not None
    reader.ask_save = lambda: "discard"
    assert reader.confirm_close()
    view = reader.window().views["reader"]
    view.close_document()
    with pymupdf.open(path) as doc:
        assert [s.kind for s in page_specs(doc[0])] == ["ellipse"]  # le rectangle non enregistré a disparu

    view.open(path)
    reader = view.current
    reader.set_tool("line")
    drag(qtbot, reader, (100, 500), (300, 500))
    target = tmp_path / "copie annotée.pdf"
    assert reader.save_to(target) and reader.path == target and not reader.dirty
    with pymupdf.open(target) as doc:
        assert [s.kind for s in page_specs(doc[0])] == ["ellipse", "line"]
    reader.undo()  # historique repart à zéro après « Enregistrer sous » : rien à annuler
    assert kinds(reader) == ["ellipse", "line"]
