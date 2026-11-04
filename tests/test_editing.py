from pathlib import Path

import pymupdf
import pytest
from PIL import Image

from docflex.core.editing import (
    EditError, add_image, add_text, field_at, fields, line_at, move_block, replace_line, set_field, text_blocks,
    text_lines, whiteout,
)

SYSTEM_TTF = next((p for p in (
    Path("/System/Library/Fonts/Supplemental/Arial.ttf"), Path("C:/Windows/Fonts/arial.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
) if p.exists()), None)


def page_with_lines(rotation=0, fontname="helv", fontfile=None):
    doc = pymupdf.open()
    page = doc.new_page()
    kwargs = {"fontname": fontname} if fontfile is None else {"fontname": "F0", "fontfile": str(fontfile)}
    page.insert_text((72, 100), "Le prix est de 100 euros par mois", fontsize=14, color=(0.8, 0, 0), **kwargs)
    page.insert_text((72, 124), "Ligne voisine à préserver", fontsize=14, **kwargs)
    page.set_rotation(rotation)
    return doc, page


def lines_text(page):
    return [line.text for line in text_lines(page)]


@pytest.mark.parametrize("rotation", [0, 90, 270])
def test_replace_line_keeps_place_size_color_and_direction(rotation):
    doc, page = page_with_lines(rotation)
    before = line_at(page, pymupdf.Point(text_lines(page)[0].rect[:2]) + (3, 3))
    assert before.text == "Le prix est de 100 euros par mois"
    reused = replace_line(page, before, "Le prix est de 120 euros par mois")
    assert not reused  # Helvetica standard, non embarquée : police proche (Helvetica)
    lines = text_lines(page)
    assert lines_text(page) == ["Ligne voisine à préserver", "Le prix est de 120 euros par mois"] or \
        sorted(lines_text(page)) == sorted(["Ligne voisine à préserver", "Le prix est de 120 euros par mois"])
    new = next(line for line in lines if "120" in line.text)
    assert new.size == pytest.approx(14) and new.color == pytest.approx((0.8, 0, 0), abs=0.01)
    assert new.direction == pytest.approx(before.direction)
    assert abs(new.rect[0] - before.rect[0]) < 2 and abs(new.rect[1] - before.rect[1]) < 3


def test_special_characters_survive_with_standard_fonts():
    doc, page = page_with_lines()
    replace_line(page, text_lines(page)[0], "Total : 1 730,00 € — « œuvre »")
    add_text(page, (72, 400), "Prix ’net’ : 12 €")
    text = page.get_text()
    assert "1 730,00 € — « œuvre »" in text and "Prix ’net’ : 12 €" in text


def test_fallback_font_follows_family_and_weight():
    doc, page = page_with_lines(fontname="tibo")  # Times gras
    replace_line(page, text_lines(page)[0], "Nouveau texte")
    new = text_lines(page)[-1]
    assert new.family == "serif" and new.flags & 16  # Times (ou son équivalent Nimbus Roman), gras


@pytest.mark.skipif(SYSTEM_TTF is None, reason="aucune police TrueType système disponible")
def test_original_embedded_font_is_reused_when_it_has_the_glyphs():
    doc, page = page_with_lines(fontfile=SYSTEM_TTF)
    original_font = text_lines(page)[0].font
    assert replace_line(page, text_lines(page)[0], "Prix révisé : 120 €")
    assert text_lines(page)[-1].font == original_font


def test_font_name_matching():
    from docflex.core.editing import _font_key

    assert _font_key("ArialMT") == _font_key("Arial Regular") == "arial"
    assert _font_key("ABCDEF+Arial-BoldMT") == _font_key("Arial Bold") == "arialbold"
    assert _font_key("TimesNewRomanPSMT") == _font_key("Times New Roman") == "timesnew"


def test_empty_text_just_erases_the_line():
    doc, page = page_with_lines()
    replace_line(page, text_lines(page)[0], "  ")
    assert lines_text(page) == ["Ligne voisine à préserver"]


@pytest.mark.parametrize("rotation", [0, 90, 270])
def test_add_text_is_horizontal_on_screen(rotation):
    doc, page = page_with_lines(rotation)
    add_text(page, (60, 300), "Ajouté\nsur deux lignes", family="serif", size=16, color=(0, 0, 0.8))
    added = [line for line in text_lines(page) if line.text in ("Ajouté", "sur deux lignes")]
    assert len(added) == 2 and added[0].family == "serif"
    shown_dir = pymupdf.Point(added[0].direction) * pymupdf.Matrix(rotation)
    assert shown_dir.x == pytest.approx(1)  # horizontal à l'écran
    assert added[0].rect[0] == pytest.approx(60, abs=2) and added[0].rect[1] >= 298


@pytest.mark.parametrize("rotation", [0, 90])
def test_add_image_upright_on_screen(tmp_path, rotation):
    image = tmp_path / "logo.png"
    img = Image.new("RGB", (100, 60), "white")
    img.paste((255, 0, 0), (0, 0, 100, 15))  # bande rouge en haut
    img.save(image)
    doc, page = page_with_lines(rotation)
    add_image(page, (100, 300, 300, 420), image)
    pix = page.get_pixmap(dpi=72)
    top, bottom = pix.pixel(200, 308), pix.pixel(200, 410)
    assert top[0] > 200 and top[1] < 80 and bottom == (255, 255, 255)


def test_whiteout_really_removes_what_is_below():
    doc, page = page_with_lines()
    first = text_lines(page)[0]
    whiteout(page, first.rect)
    assert lines_text(page) == ["Ligne voisine à préserver"]
    assert "100 euros" not in page.get_text()


def test_pending_redactions_from_other_software_block_editing():
    doc, page = page_with_lines()
    page.add_redact_annot(pymupdf.Rect(300, 600, 400, 650))  # caviardage en attente
    with pytest.raises(EditError, match="caviardages en attente"):
        whiteout(page, (72, 80, 200, 110))
    with pytest.raises(EditError):
        replace_line(page, text_lines(page)[0], "x")
    assert "100 euros" in page.get_text()


def test_form_fields_fill():
    doc = pymupdf.open()
    page = doc.new_page()
    specs = [
        (pymupdf.PDF_WIDGET_TYPE_TEXT, "nom", (72, 72, 300, 92), None),
        (pymupdf.PDF_WIDGET_TYPE_CHECKBOX, "accord", (72, 110, 86, 124), None),
        (pymupdf.PDF_WIDGET_TYPE_COMBOBOX, "pays", (72, 140, 300, 160), ["France", "Belgique", "Suisse"]),
    ]
    for kind, name, rect, choices in specs:
        widget = pymupdf.Widget()
        widget.field_type, widget.field_name, widget.rect = kind, name, pymupdf.Rect(rect)
        if choices:
            widget.choice_values = choices
            widget.field_value = choices[0]
        page.add_widget(widget)
    found = fields(page)
    assert [f.name for f in found] == ["nom", "accord", "pays"] and found[2].choices == ("France", "Belgique", "Suisse")
    set_field(page, field_at(page, (100, 80)).xref, "Marie Curie")
    set_field(page, field_at(page, (78, 116)).xref, True)
    set_field(page, field_at(page, (100, 150)).xref, "Suisse")
    data = doc.tobytes()
    with pymupdf.open("pdf", data) as reopened:
        values = {w.field_name: w.field_value for w in reopened[0].widgets()}
    assert values["nom"] == "Marie Curie" and values["pays"] == "Suisse" and values["accord"] not in ("Off", "", False)
    assert field_at(page, (500, 500)) is None


def test_replace_line_style_override_changes_only_what_is_given():
    doc, page = page_with_lines()
    line = text_lines(page)[0]
    assert line.style == {"family": "sans", "size": 14, "color": pytest.approx((0.8, 0, 0), abs=0.01),
                          "bold": False, "italic": False}
    assert not replace_line(page, line, "Plus grand et gras", {"size": 18.0, "bold": True})
    new = next(l for l in text_lines(page) if "gras" in l.text)
    assert new.size == pytest.approx(18) and new.flags & 16 and new.family == "sans"
    assert new.color == pytest.approx((0.8, 0, 0), abs=0.01)  # couleur d'origine gardée


@pytest.mark.skipif(SYSTEM_TTF is None, reason="aucune police TrueType système disponible")
def test_original_font_kept_when_only_size_or_color_change():
    doc, page = page_with_lines(fontfile=SYSTEM_TTF)
    line = text_lines(page)[0]
    assert replace_line(page, line, "Prix en bleu", {"color": (0, 0, 1)})
    new = next(l for l in text_lines(page) if "bleu" in l.text)
    assert new.font == line.font and new.color == pytest.approx((0, 0, 1))


def test_add_text_bold_italic():
    doc, page = page_with_lines()
    add_text(page, (72, 300), "Gras italique", family="serif", size=12, bold=True, italic=True)
    new = next(l for l in text_lines(page) if l.text == "Gras italique")
    assert new.family == "serif" and new.flags & 16 and new.flags & 2


def test_line_rewritten_twice_keeps_its_family():
    # MuPDF marque NimbusSans (Helvetica embarquée) « avec empattement » : le nom doit primer.
    doc, page = page_with_lines()
    replace_line(page, text_lines(page)[0], "Première correction")
    line = next(l for l in text_lines(page) if "Première" in l.text)
    assert line.family == "sans"
    replace_line(page, line, "Seconde correction")
    assert next(l for l in text_lines(page) if "Seconde" in l.text).family == "sans"


@pytest.mark.parametrize("rotation", [0, 90, 270])
def test_move_block_shifts_on_screen_and_keeps_style(rotation):
    doc, page = page_with_lines(rotation)
    add_text(page, (300, 420), "Bloc ajouté\nsur deux lignes", family="serif", size=16, color=(0, 0, 0.8), bold=True)
    block = next(b for b in text_blocks(page) if "Bloc" in b.text)
    assert "sur deux lignes" in block.text  # les deux lignes forment un seul paragraphe
    assert move_block(page, block, 100, 50)  # polices standard : identiques
    moved = next(b for b in text_blocks(page) if "Bloc" in b.text)
    assert moved.rect[0] == pytest.approx(block.rect[0] + 100, abs=1)  # décalage à l'écran, page tournée ou non
    assert moved.rect[1] == pytest.approx(block.rect[1] + 50, abs=1)
    for span in moved.spans:
        assert span.size == pytest.approx(16) and span.color == pytest.approx((0, 0, 0.8), abs=0.01)
        assert span.flags & 16
    assert sorted(lines_text(page)) == ["Bloc ajouté", "Le prix est de 100 euros par mois",
                                        "Ligne voisine à préserver", "sur deux lignes"]  # rien d'autre effacé


@pytest.mark.skipif(SYSTEM_TTF is None, reason="aucune police TrueType système disponible")
def test_move_block_reuses_embedded_font():
    doc, page = page_with_lines(fontfile=SYSTEM_TTF)
    block = text_blocks(page)[0]
    font = block.spans[0].font
    assert move_block(page, block, 0, 300)
    assert {s.font for b in text_blocks(page) for s in b.spans} == {font}


def test_two_original_fonts_on_one_page_stay_distinct():
    # PyMuPDF ne remplace pas une police déjà présente sous le même nom : chacune a le sien.
    fonts = [p for p in (Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
                         Path("/System/Library/Fonts/Supplemental/Times New Roman.ttf"),
                         Path("C:/Windows/Fonts/arial.ttf"), Path("C:/Windows/Fonts/times.ttf")) if p.exists()][:2]
    if len(fonts) < 2:
        pytest.skip("deux polices TrueType système nécessaires")
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), "Première police", fontname="FA", fontfile=str(fonts[0]), fontsize=14)
    page.insert_text((72, 200), "Seconde police", fontname="FB", fontfile=str(fonts[1]), fontsize=14)
    before = {l.text: l.font for l in text_lines(page)}
    replace_line(page, line_at(page, (80, 95)), "Première modifiée")
    replace_line(page, line_at(page, (80, 195)), "Seconde modifiée")
    after = {l.text: l.font for l in text_lines(page)}
    assert after == {"Première modifiée": before["Première police"], "Seconde modifiée": before["Seconde police"]}
