"""Formulaires : boutons radio en groupe et calculs automatiques (sans moteur JavaScript)."""

import pymupdf
import pytest

from docflex.core import editing, forms


def text_field(page, name, y, calc="", fmt=""):
    widget = pymupdf.Widget()
    widget.field_type, widget.field_name = pymupdf.PDF_WIDGET_TYPE_TEXT, name
    widget.rect = pymupdf.Rect(72, y, 220, y + 20)
    if calc:
        widget.script_calc = calc
    if fmt:
        widget.script_format = fmt
    page.add_widget(widget)


def order_form() -> pymupdf.Document:
    """Bon de commande comme le fait Acrobat : notation simplifiée puis somme, format en euros."""
    doc = pymupdf.open()
    doc.new_page()
    doc.new_page()
    text_field(doc[0], "Prix unitaire", 100)
    text_field(doc[0], "Qte", 130)
    text_field(doc[0], "Port", 160)
    text_field(doc[0], "Sous-total", 190, '/** BVCALC Prix\\ unitaire * Qte EVCALC **/ event.value = 0;')
    text_field(doc[1], "Total", 100, 'AFSimple_Calculate("SUM", new Array ("Sous-total", "Port"));',
               'AFNumber_Format(2, 2, 0, 0, " €", false);')
    return pymupdf.open("pdf", doc.tobytes())


def values(doc):
    return {w.field_name: w.field_value for page in doc for w in page.widgets()}


def test_calculations_follow_the_form():
    doc = order_form()
    calcs = forms.calculations(doc)
    assert [(c.target, c.operation, c.sources, c.expression) for c in calcs] == [
        ("Sous-total", "", (), "Prix\\ unitaire * Qte"), ("Total", "SUM", ("Sous-total", "Port"), "")]
    for field in editing.fields(doc[0])[:3]:
        editing.set_field(doc[0], field.xref, {"Prix unitaire": "1 250,50", "Qte": "3", "Port": "12"}[field.name])
    assert forms.recalculate(doc) == {0, 1}
    assert values(doc)["Sous-total"] == "3751.5" and values(doc)["Total"] == "3.763,50 €"
    assert forms.recalculate(doc) == set()  # déjà à jour : rien ne bouge


@pytest.mark.parametrize("text, number", [("1,234.50", 1234.5), ("1.234,50 €", 1234.5), ("1 234,5", 1234.5),
                                          ("12 %", 12), ("−3", -3), ("", None), ("abc", None)])
def test_numbers_as_typed(text, number):
    assert forms.to_number(text) == number


def test_formats_and_expressions():
    assert forms.format_number(-1234.5, 'AFNumber_Format(2, 0, 0, 0, "$", true);') == "-$1,234.50"
    assert forms.format_number(0.125, "AFPercent_Format(1, 2);") == "12,5 %"
    assert forms.format_number(3.0, "") == "3" and forms.format_number(2.5, "") == "2.5"
    assert forms.evaluate("(A + 2) * B / 4 - -1", {"A": 2, "B": 3}.get) == 4
    assert forms.evaluate("A / B", {"A": 1, "B": 0}.get) == 0  # pas de division par zéro
    with pytest.raises(ValueError):
        forms.evaluate("A + (B", {"A": 1, "B": 2}.get)
    assert forms.parse_calculation("X", "event.value = 42;") is None  # script écrit à la main : ignoré


def test_set_calculation_makes_a_readonly_total():
    doc = pymupdf.open()
    doc.new_page()
    for name, y in (("A", 100), ("B", 130), ("Total", 160)):
        text_field(doc[0], name, y)
    for field in editing.fields(doc[0])[:2]:
        editing.set_field(doc[0], field.xref, "2,5" if field.name == "A" else "4")
    total = editing.fields(doc[0])[2]
    forms.set_calculation(doc, doc[0], total.xref, "PRD", ["A", "B"], decimals=1)
    doc = pymupdf.open("pdf", doc.tobytes())
    assert values(doc)["Total"] == "10,0" and editing.fields(doc[0])[2].readonly


def test_radio_groups_keep_a_single_choice():
    doc = pymupdf.open()
    doc.new_page()
    doc.new_page()
    for option, y in (("Petit", 100), ("Très grand", 130)):
        editing.add_field(doc[0], (72, y, 86, y + 14), "field_radio", "Taille", (option,))
    editing.add_field(doc[1], (72, 100, 86, 114), "field_radio", "Taille")  # même groupe, autre page
    with pytest.raises(editing.EditError):
        editing.add_field(doc[0], (72, 200, 86, 214), "field_radio", "Taille", ("Petit",))
    assert editing.radio_options(doc, "Taille") == ["Petit", "Très grand", "Choix 1"]

    def checked():
        return [f.option for page in doc for f in editing.fields(page) if f.checked]

    radios = editing.fields(doc[0])
    editing.set_field(doc[0], radios[1].xref, True)
    assert checked() == ["Très grand"]
    editing.set_field(doc[1], editing.fields(doc[1])[0].xref, True)
    assert checked() == ["Choix 1"]
    doc = pymupdf.open("pdf", doc.tobytes())  # relu comme par un autre lecteur
    assert [w.field_value for page in doc for w in page.widgets()] == ["Off", "Off", "Choix 1"]
