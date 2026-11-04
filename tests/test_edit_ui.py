"""Mode « Modifier » du lecteur, piloté à la souris (pytest-qt, sans fenêtre visible)."""

import pymupdf
import pytest
from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QLineEdit, QTextEdit

from docflex.core.annotations import page_specs
from docflex.ui.field_editor import CheckFocus
from tests.test_annotate_ui import at, drag, reader  # noqa: F401  (fixture partagée)


def click(qtbot, reader, x, y, page=0):
    qtbot.mouseClick(reader.canvas.viewport(), Qt.LeftButton, pos=at(reader, x, y, page))


def page_text(reader, page=0):
    return reader.doc[page].get_text()


def spans(reader, needle, page=0):
    return [s for b in reader.doc[page].get_text("dict")["blocks"] for l in b.get("lines", [])
            for s in l["spans"] if needle in s["text"]]


def test_edit_line_in_place_then_undo_redo(reader, qtbot):
    reader.set_mode("edit")
    reader.set_tool("edit_text")
    line = reader.canvas.line_at(0, (80, 135))
    assert line.text == "Le lecteur affiche ce texte de démonstration."
    click(qtbot, reader, 80, 135)
    editor = reader.text_editor.widget
    assert isinstance(editor, QLineEdit) and editor.text() == line.text and editor.hasFocus()
    assert editor.geometry().contains(at(reader, 80, 135))  # posé sur la ligne
    # La barre montre la mise en forme d'origine de la ligne.
    assert reader.edit_bar.size_menu.currentData() == pytest.approx(round(line.size, 1))
    assert reader.edit_bar.color_btn.color == pytest.approx(line.color)
    editor.setText(line.text.replace("démonstration", "test"))
    qtbot.keyClick(editor, Qt.Key_Return)
    assert reader.text_editor.widget is None
    assert "Le lecteur affiche ce texte de test." in page_text(reader)
    assert "démonstration" not in page_text(reader) and reader.dirty
    assert "police" in reader.status.text()
    new = spans(reader, "de test.")[0]
    assert new["size"] == pytest.approx(line.size)  # style d'origine gardé
    assert reader.edit_bar.size_menu.currentData() == reader.edit_style["size"]  # barre revenue au texte ajouté
    reader.undo()
    assert "démonstration" in page_text(reader)
    reader.redo()
    assert "de test." in page_text(reader)

    click(qtbot, reader, 80, 135)
    qtbot.keyClicks(reader.text_editor.widget, " abandon")
    qtbot.keyClick(reader.text_editor.widget, Qt.Key_Escape)  # annulé : rien ne change
    assert reader.text_editor.widget is None and "abandon" not in page_text(reader)
    click(qtbot, reader, 400, 700)  # pas de texte ici
    assert "Pas de texte" in reader.status.text()


def test_edit_line_style_changed_from_bar_applies_to_that_line_only(reader, qtbot):
    reader.set_mode("edit")
    reader.set_tool("edit_text")
    click(qtbot, reader, 80, 135)
    editor = reader.text_editor.widget
    editor.setText("Ligne agrandie")
    reader.edit_bar.size_chosen.emit(20.0)
    reader.edit_bar.bold_chosen.emit(True)
    assert editor.hasFocus() and editor.font().bold()
    click(qtbot, reader, 400, 700)  # clic ailleurs : la saisie est gardée
    span = spans(reader, "Ligne agrandie")[0]
    assert span["size"] == pytest.approx(20) and span["flags"] & 16
    assert "nouvelle mise en forme" in reader.status.text()
    assert reader.edit_style["size"] == 12.0 and not reader.edit_style["bold"]  # texte ajouté : inchangé


def test_add_text_typed_in_page(reader, qtbot):
    reader.set_mode("edit")
    reader.edit_bar.family_chosen.emit("serif")
    reader.edit_bar.size_chosen.emit(18.0)
    reader.edit_bar.bold_chosen.emit(True)
    reader.set_tool("add_text")
    click(qtbot, reader, 100, 400)
    editor = reader.text_editor.widget
    assert isinstance(editor, QTextEdit) and editor.hasFocus()
    assert editor.font().pixelSize() == 24 and editor.font().bold()  # 18 pt à 100 % (96 dpi)
    qtbot.keyClicks(editor, "Ajout par DocFlex")
    qtbot.keyClick(editor, Qt.Key_Return)  # nouvelle ligne, pas validation
    qtbot.keyClicks(editor, "Seconde ligne")
    reader.edit_bar.color_chosen.emit((0.0, 0.0, 0.8))  # change aussi le texte en cours
    reader.canvas.set_zoom(2.0)
    assert editor.font().pixelSize() == 48  # suit le zoom
    reader.canvas.set_zoom(1.0)
    click(qtbot, reader, 400, 700)  # termine la saisie, sans poser un nouveau texte
    assert reader.text_editor.widget is None
    first, second = spans(reader, "Ajout par DocFlex")[0], spans(reader, "Seconde ligne")[0]
    assert first["flags"] & 4 and first["flags"] & 16 and first["size"] == pytest.approx(18)  # empattement, gras
    assert first["color"] == 0x0000CC
    assert first["origin"][1] == pytest.approx(418, abs=0.5)  # 1re ligne de base 18 pt sous le clic
    assert second["origin"][1] - first["origin"][1] == pytest.approx(18 * 1.25, abs=0.5)
    assert reader.edit_style["color"] == (0.0, 0.0, 0.8)  # gardé pour le texte suivant

    click(qtbot, reader, 100, 500)
    qtbot.keyClicks(reader.text_editor.widget, "Valide au clavier")
    qtbot.keyClick(reader.text_editor.widget, Qt.Key_Return, Qt.ControlModifier)
    assert "Valide au clavier" in page_text(reader)
    click(qtbot, reader, 100, 600)
    qtbot.keyClicks(reader.text_editor.widget, "Abandon")
    qtbot.keyClick(reader.text_editor.widget, Qt.Key_Escape)
    assert "Abandon" not in page_text(reader)
    reader.undo()
    reader.undo()
    assert "Ajout par" not in page_text(reader)


def test_add_text_image_and_whiteout(reader, qtbot, tmp_path):
    reader.set_mode("edit")
    reader.set_tool("add_text")
    click(qtbot, reader, 100, 400)
    qtbot.keyClicks(reader.text_editor.widget, "Ajout par DocFlex")
    reader.set_tool("select")  # changer d'outil garde la saisie
    assert "Ajout par DocFlex" in page_text(reader)

    image = tmp_path / "tampon.png"
    Image.new("RGB", (120, 60), (20, 120, 220)).save(image)
    reader.ask_image = lambda: image
    reader.set_tool("add_image")
    drag(qtbot, reader, (300, 450), (420, 510))
    assert len(reader.doc[0].get_images()) == 1

    reader.set_tool("whiteout")
    drag(qtbot, reader, (60, 80), (300, 108))  # efface « Chapitre 1 »
    assert "Chapitre 1" not in page_text(reader) and "Le lecteur" in page_text(reader)
    for _ in range(3):
        reader.undo()
    assert "Chapitre 1" in page_text(reader) and not reader.doc[0].get_images()
    assert "Ajout par" not in page_text(reader)


def test_image_tool_cancelled_returns_to_select(reader):
    reader.set_mode("edit")
    reader.ask_image = lambda: None
    reader.set_tool("add_image")
    assert reader.tool == "select" and reader.edit_bar.tools["select"].isChecked()


def test_annotations_and_content_edits_share_history(reader, qtbot):
    reader.set_tool("rect")
    drag(qtbot, reader, (100, 300), (200, 380))
    reader.set_mode("edit")
    reader.set_tool("whiteout")
    drag(qtbot, reader, (60, 80), (300, 108))
    reader.set_mode("annotate")
    reader.set_tool("ellipse")
    drag(qtbot, reader, (300, 300), (400, 380))
    assert [s.kind for s in page_specs(reader.doc[0])] == ["rect", "ellipse"]
    reader.undo()  # ellipse
    reader.undo()  # blanc correcteur
    assert "Chapitre 1" in page_text(reader) and [s.kind for s in page_specs(reader.doc[0])] == ["rect"]
    reader.undo()  # rectangle
    assert page_specs(reader.doc[0]) == [] and not reader.undo_btn.isEnabled()
    for _ in range(3):
        reader.redo()
    assert "Chapitre 1" not in page_text(reader)
    assert [s.kind for s in page_specs(reader.doc[0])] == ["rect", "ellipse"]


def test_fill_form_and_save(reader, qtbot, tmp_path):
    src = tmp_path / "formulaire.pdf"
    with pymupdf.open() as doc:
        page = doc.new_page()
        for kind, name, rect, choices in (
            (pymupdf.PDF_WIDGET_TYPE_TEXT, "Nom", (72, 72, 300, 92), None),
            (pymupdf.PDF_WIDGET_TYPE_CHECKBOX, "Accord", (72, 110, 88, 126), None),
            (pymupdf.PDF_WIDGET_TYPE_COMBOBOX, "Pays", (72, 140, 300, 160), ["France", "Belgique", "Suisse"]),
        ):
            widget = pymupdf.Widget()
            widget.field_type, widget.field_name, widget.rect = kind, name, pymupdf.Rect(rect)
            if choices:
                widget.choice_values, widget.field_value = choices, choices[0]
            page.add_widget(widget)
        doc.save(src)
    view = reader.window().views["reader"]
    view.open(src)  # second onglet
    reader = view.current
    reader.canvas.set_zoom(1.0)
    reader.canvas.go_to(0)
    assert "formulaire" in reader.status.text()
    editor = reader.field_editor

    click(qtbot, reader, 100, 82)  # « Nom » : on écrit directement dans le champ
    assert isinstance(editor.widget, QLineEdit) and editor.widget.hasFocus()
    qtbot.keyClicks(editor.widget, "Marie Curie")
    qtbot.keyClick(editor.widget, Qt.Key_Tab)  # → « Accord »
    assert isinstance(editor.widget, CheckFocus) and editor.target[1].name == "Accord"
    qtbot.keyClick(editor.widget, Qt.Key_Space)  # coché
    qtbot.keyClick(editor.widget, Qt.Key_Tab)  # → « Pays »
    assert isinstance(editor.widget, QComboBox) and editor.widget.count() == 3
    editor.widget.setCurrentIndex(2)
    qtbot.keyClick(editor.widget, Qt.Key_Tab)  # la valeur est gardée ; on reboucle sur « Nom »
    assert editor.target[1].name == "Nom" and editor.widget.text() == "Marie Curie"
    qtbot.keyClick(editor.widget, Qt.Key_Backtab)  # ⇧Tab : retour à « Pays »
    assert editor.target[1].name == "Pays"

    qtbot.keyClick(editor.widget, Qt.Key_Tab)
    qtbot.keyClicks(editor.widget, "Pierre")
    qtbot.keyClick(editor.widget, Qt.Key_Escape)  # Échap : saisie abandonnée
    assert editor.widget is None
    assert reader.save()
    with pymupdf.open(src) as doc:
        values = {w.field_name: w.field_value for w in doc[0].widgets()}
    assert values["Nom"] == "Marie Curie" and values["Pays"] == "Suisse" and values["Accord"] not in ("Off", "")

    click(qtbot, reader, 100, 82)
    editor.widget.insert("Irène")  # QTest ne sait pas taper les accents
    assert reader.save() and not reader.dirty  # ⌘S pendant la saisie : elle est enregistrée aussi
    with pymupdf.open(src) as doc:
        assert next(w for w in doc[0].widgets() if w.field_name == "Nom").field_value == "Irène"
    reader.undo()
    assert editor.widget is None


def test_create_rename_and_delete_fields(reader, qtbot):
    reader.set_mode("edit")
    reader.ask_line = lambda initial, title="": initial  # nom proposé : « Champ 1 », « Champ 2 »…
    reader.set_tool("field_text")
    drag(qtbot, reader, (72, 400), (300, 422))
    reader.set_tool("field_check")
    click(qtbot, reader, 80, 450)  # un clic suffit pour une case
    reader.ask_text = lambda title, initial: "Rouge\nVert\n\nBleu"
    reader.set_tool("field_choice")
    click(qtbot, reader, 80, 490)
    fields = reader.canvas.fields(0)
    assert [(f.name, f.choices) for f in fields] == [("Champ 1", ()), ("Champ 2", ()),
                                                     ("Champ 3", ("Rouge", "Vert", "Bleu"))]
    assert fields[0].rect == pytest.approx((72, 400, 300, 422), abs=1) and "Champ 3" in reader.status.text()

    reader.ask_line = lambda initial, title="": "Champ 1"  # nom déjà pris : refusé
    reader.set_tool("field_text")
    click(qtbot, reader, 80, 600)
    assert len(reader.canvas.fields(0)) == 3 and "déjà" in reader.status.text()

    reader.set_tool("select")
    menu = reader.field_menu_for(0, fields[1])
    reader.ask_line = lambda initial, title="": "Accord"
    next(a for a in menu.actions() if a.text() == "Renommer le champ…").trigger()
    assert [f.name for f in reader.canvas.fields(0)][1] == "Accord"
    menu = reader.field_menu_for(0, reader.canvas.fields(0)[0])
    next(a for a in menu.actions() if a.text() == "Supprimer le champ").trigger()
    assert [f.name for f in reader.canvas.fields(0)] == ["Accord", "Champ 3"]
    reader.undo()
    assert len(reader.canvas.fields(0)) == 3

    click(qtbot, reader, 100, 411)  # le nouveau champ se remplit comme les autres
    qtbot.keyClicks(reader.field_editor.widget, "Bonjour")
    click(qtbot, reader, 400, 700)  # clic ailleurs : la saisie est gardée
    qtbot.waitUntil(lambda: reader.field_editor.widget is None)
    assert reader.canvas.fields(0)[0].value == "Bonjour"


def test_everything_survives_save_after_content_edits(reader, qtbot):
    """PyMuPDF : après tobytes(), saveIncr perd les modifications antérieures — on réécrit tout."""
    path = reader.path
    reader.set_tool("rect")
    drag(qtbot, reader, (100, 300), (200, 380))       # annotation
    reader.set_mode("edit")
    reader.set_tool("whiteout")
    drag(qtbot, reader, (60, 80), (300, 108))         # contenu (instantané)
    reader.set_mode("annotate")
    reader.set_tool("ellipse")
    drag(qtbot, reader, (300, 300), (400, 380))       # annotation après l'instantané
    assert reader.save()
    with pymupdf.open(path) as doc:
        assert [s.kind for s in page_specs(doc[0])] == ["rect", "ellipse"]
        assert "Chapitre 1" not in doc[0].get_text()
    reader.set_tool("line")                           # après enregistrement : retour à l'incrémental
    drag(qtbot, reader, (100, 500), (300, 500))
    assert reader.save()
    with pymupdf.open(path) as doc:
        assert [s.kind for s in page_specs(doc[0])] == ["rect", "ellipse", "line"]


def test_radio_buttons_and_calculated_total(reader, qtbot):
    reader.set_mode("edit")
    reader.ask_line = lambda initial, title="": "Taille" if "radio" in title else initial
    reader.set_tool("field_radio")
    for y in (450, 480):
        click(qtbot, reader, 80, y)
    assert "Option 2" in reader.status.text()
    reader.ask_line = lambda initial, title="": initial
    reader.set_tool("field_text")
    for y in (520, 560, 600):
        click(qtbot, reader, 80, y)  # Champ 1, Champ 2, Champ 3

    reader.set_tool("select")
    total = next(f for f in reader.canvas.fields(0) if f.name == "Champ 3")
    reader.ask_calculation = lambda target, names: ("SUM", ["Champ 1", "Champ 2"], 2)
    next(a for a in reader.field_menu_for(0, total).actions() if a.text() == "Calcul automatique…").trigger()
    assert "recalculé" in reader.status.text()

    editor = reader.field_editor
    click(qtbot, reader, 100, 520)
    qtbot.keyClicks(editor.widget, "12,5")
    qtbot.keyClick(editor.widget, Qt.Key_Tab)  # → Champ 2 ; le total (lecture seule) est sauté
    assert editor.target[1].name == "Champ 2"
    qtbot.keyClicks(editor.widget, "7")
    qtbot.keyClick(editor.widget, Qt.Key_Return)
    assert next(f for f in reader.canvas.fields(0) if f.name == "Champ 3").value == "19,50"
    click(qtbot, reader, 100, 600)
    assert editor.widget is None and "lecture seule" in reader.status.text()

    click(qtbot, reader, 80, 457)  # première option
    click(qtbot, reader, 80, 487)  # seconde : la première se décoche
    radios = [f for f in reader.canvas.fields(0) if f.kind == pymupdf.PDF_WIDGET_TYPE_RADIOBUTTON]
    assert [f.checked for f in radios] == [False, True]
    qtbot.keyClick(editor.widget, Qt.Key_Space)  # Espace sur une option cochée : elle le reste
    assert [f.checked for f in reader.canvas.fields(0) if f.kind == pymupdf.PDF_WIDGET_TYPE_RADIOBUTTON] == [False, True]
    reader.undo()
    assert [f.checked for f in reader.canvas.fields(0) if f.kind == pymupdf.PDF_WIDGET_TYPE_RADIOBUTTON] == [True, False]


def test_click_on_another_line_while_editing_moves_to_it(reader, qtbot):
    reader.set_mode("edit")
    reader.set_tool("edit_text")
    click(qtbot, reader, 80, 135)
    reader.text_editor.widget.setText("Ligne corrigée")
    other = next(l for l in reader.canvas._lines[0] if "Chapitre" in l.text)
    click(qtbot, reader, other.rect[0] + 5, (other.rect[1] + other.rect[3]) / 2)
    assert "Ligne corrigée" in page_text(reader)  # la première est enregistrée…
    assert reader.text_editor.widget.text() == other.text  # …et la seconde s'ouvre aussitôt
    assert reader.edit_bar.size_menu.currentData() == pytest.approx(round(other.size, 1))


def test_drag_moves_added_text_click_still_edits(reader, qtbot):
    reader.set_mode("edit")
    reader.set_tool("add_text")
    click(qtbot, reader, 100, 400)
    qtbot.keyClicks(reader.text_editor.widget, "A deplacer")
    reader.set_tool("edit_text")
    before = spans(reader, "A deplacer")[0]["origin"]
    drag(qtbot, reader, (110, 410), (260, 520))
    after = spans(reader, "A deplacer")[0]["origin"]
    assert (after[0] - before[0], after[1] - before[1]) == (pytest.approx(150, abs=0.5), pytest.approx(110, abs=0.5))
    assert reader.text_editor.widget is None and "déplacé" in reader.status.text()
    reader.undo()
    assert spans(reader, "A deplacer")[0]["origin"] == pytest.approx(before)
    reader.redo()

    click(qtbot, reader, 265, 515)  # simple clic sur le texte déplacé : on le modifie
    assert reader.text_editor.widget.text() == "A deplacer"


def test_drag_a_modified_line(reader, qtbot):
    reader.set_mode("edit")
    reader.set_tool("edit_text")
    click(qtbot, reader, 80, 135)
    reader.text_editor.widget.setText("Ligne corrigée puis déplacée")
    qtbot.keyClick(reader.text_editor.widget, Qt.Key_Return)
    drag(qtbot, reader, (80, 135), (80, 335))
    moved = spans(reader, "Ligne corrigée")[0]
    assert moved["origin"][1] == pytest.approx(140 + 200, abs=0.5)
    assert "Chapitre 1" in page_text(reader)  # le reste de la page ne bouge pas
