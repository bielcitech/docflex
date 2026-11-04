from pathlib import Path

import pymupdf
import pytest
from PIL import Image

from docflex.core.pdf_tools import (
    ImageMark, Numbering, Permissions, Watermark, edit_metadata, extract_images, image_watermark_pdf, merge_pdfs,
    number_pages, ocr_pdf, protect_pdf, read_metadata, unprotect_pdf, watermark_pdf,
)
from tests.test_split_compress import make_pdf, page_texts


def ink(pix: pymupdf.Pixmap, x0: float, y0: float, x1: float, y1: float) -> int:
    """Nombre de pixels non blancs dans une zone (fractions de la page)."""
    count = 0
    for y in range(int(y0 * pix.height), int(y1 * pix.height)):
        for x in range(int(x0 * pix.width), int(x1 * pix.width)):
            if pix.pixel(x, y)[0] < 240:
                count += 1
    return count


def blank_pdf(path: Path, pages: int = 1, rotation: int = 0, size=(595, 842)) -> Path:
    with pymupdf.open() as doc:
        for _ in range(pages):
            doc.new_page(width=size[0], height=size[1]).set_rotation(rotation)
        doc.save(path)
    return path


# ------------------------------------------------------------------ fusion

def test_merge_keeps_order_and_nests_bookmarks(tmp_path):
    a = make_pdf(tmp_path / "Rapport.pdf", 2, toc=[[1, "Intro", 1]])
    b = make_pdf(tmp_path / "Annexe.pdf", 1)
    out = merge_pdfs([b, a], tmp_path / "fusion.pdf")
    assert page_texts(out) == ["Page 1", "Page 1", "Page 2"]
    with pymupdf.open(out) as doc:
        assert doc.get_toc() == [[1, "Annexe", 1], [1, "Rapport", 2], [2, "Intro", 2]]
        assert doc.metadata["author"] == "XenoviaLabs"
    with pymupdf.open(merge_pdfs([a, b], tmp_path / "sans.pdf", bookmarks=False)) as doc:
        assert doc.get_toc() == []
    with pytest.raises(ValueError, match="deux PDF"):
        merge_pdfs([a], tmp_path / "x.pdf")


# --------------------------------------------------------------- filigrane

@pytest.mark.parametrize("rotation", [0, 90])
def test_watermark_rises_diagonally_and_is_translucent(tmp_path, rotation):
    src = blank_pdf(tmp_path / "b.pdf", rotation=rotation)
    out = watermark_pdf(src, tmp_path, Watermark("CONFIDENTIEL", opacity=0.3, color=(0, 0, 0)))
    with pymupdf.open(out) as doc:
        assert "CONFIDENTIEL" in doc[0].get_text()
        pix = doc[0].get_pixmap(dpi=30)
    # Diagonale montante : de l'encre en bas à gauche et en haut à droite, pas dans les deux autres coins.
    rising = ink(pix, 0.1, 0.6, 0.4, 0.9) + ink(pix, 0.6, 0.1, 0.9, 0.4)
    falling = ink(pix, 0.1, 0.1, 0.4, 0.4) + ink(pix, 0.6, 0.6, 0.9, 0.9)
    assert rising > 20 and falling == 0
    darkest = min(pix.pixel(x, y)[0] for x in range(pix.width) for y in range(pix.height))
    assert 120 < darkest < 240  # noir à 30 % d'opacité : gris clair


def test_watermark_horizontal_and_page_selection(tmp_path):
    src = blank_pdf(tmp_path / "b.pdf", pages=3)
    out = watermark_pdf(src, tmp_path, Watermark("BROUILLON", diagonal=False, pages="2-"))
    assert [("BROUILLON" in t) for t in page_texts(out)] == [False, True, True]
    with pymupdf.open(out) as doc:
        pix = doc[1].get_pixmap(dpi=30)
    assert ink(pix, 0, 0.4, 1, 0.6) > 0 and ink(pix, 0, 0, 1, 0.3) == 0
    with pytest.raises(ValueError, match="texte"):
        watermark_pdf(src, tmp_path, Watermark("  "))


# ------------------------------------------------------------ numérotation

def number_box(path: Path, page: int, text: str) -> tuple[pymupdf.Rect, pymupdf.Rect]:
    with pymupdf.open(path) as doc:
        p = doc[page]
        hits = p.search_for(text)
        assert hits, f"« {text} » absent de la page {page + 1}"
        return hits[0], p.rect


def test_numbering_formats_and_positions(tmp_path):
    src = blank_pdf(tmp_path / "b.pdf", pages=3)
    out = number_pages(src, tmp_path, Numbering("Page {n} sur {total}", "bottom-center"))
    box, page = number_box(out, 2, "Page 3 sur 3")
    assert box.y1 > page.height * 0.93 and abs((box.x0 + box.x1) / 2 - page.width / 2) < 2

    out = number_pages(src, tmp_path, Numbering("{n}", "top-right", start=5, skip_first=True))
    assert page_texts(out)[0] == "" and page_texts(out)[1:] == ["6", "7"]
    box, page = number_box(out, 1, "6")
    assert box.y0 < page.height * 0.07 and box.x1 > page.width * 0.9


def test_numbering_on_rotated_and_cropped_page(tmp_path):
    src = tmp_path / "r.pdf"
    with pymupdf.open() as doc:
        page = doc.new_page(width=595, height=842)
        page.set_cropbox(pymupdf.Rect(100, 100, 495, 742))
        page.set_rotation(90)  # affichée en paysage 642 × 395
        doc.save(src)
    out = number_pages(src, tmp_path, Numbering("{n} / {total}", "bottom-right"))
    with pymupdf.open(out) as doc:
        pix = doc[0].get_pixmap(dpi=40)
    assert pix.width > pix.height
    assert ink(pix, 0.75, 0.85, 1, 1) > 0 and ink(pix, 0, 0, 1, 0.8) == 0  # en bas à droite, comme affichée


# ------------------------------------------------------------- mot de passe

def test_protect_then_unprotect(tmp_path):
    src = make_pdf(tmp_path / "s.pdf", 2)
    locked = protect_pdf(src, tmp_path, "s3cret", Permissions(print=True, copy=False))
    with pymupdf.open(locked) as doc:
        assert doc.needs_pass and not doc.authenticate("faux") and doc.authenticate("s3cret")
        assert not doc.permissions & pymupdf.PDF_PERM_COPY and doc.permissions & pymupdf.PDF_PERM_PRINT
        assert doc[0].get_text().strip() == "Page 1"

    with pytest.raises(ValueError, match="incorrect"):
        unprotect_pdf(locked, tmp_path, "faux")
    opened = unprotect_pdf(locked, tmp_path, "s3cret")
    with pymupdf.open(opened) as doc:
        assert not doc.needs_pass and not doc.is_encrypted
    assert page_texts(opened) == ["Page 1", "Page 2"]
    with pytest.raises(ValueError, match="pas protégé"):
        unprotect_pdf(src, tmp_path, "x")
    with pytest.raises(ValueError, match="mot de passe"):
        protect_pdf(src, tmp_path, "")


# --------------------------------------------------------------------- OCR

def scanned_pdf(path: Path, text: str, rotation: int = 0) -> Path:
    """Page « numérisée » : une image du texte, sans aucun texte extractible."""
    with pymupdf.open() as source:
        page = source.new_page()
        page.insert_textbox(pymupdf.Rect(60, 80, 540, 400), text, fontsize=22)
        pix = page.get_pixmap(dpi=200)
    with pymupdf.open() as doc:
        # Scan enregistré de travers puis redressé par la rotation de la page, comme le font les scanners.
        page = doc.new_page(width=842, height=595) if rotation % 180 else doc.new_page()
        page.insert_image(page.rect, pixmap=pix, rotate=rotation)
        page.set_rotation(rotation)
        doc.save(path)
    return path


@pytest.mark.parametrize("rotation", [0, 90])
def test_ocr_makes_scan_searchable_without_changing_its_look(tmp_path, rotation):
    src = scanned_pdf(tmp_path / "scan.pdf", "Facture numéro 2026\nBonjour le monde", rotation)
    assert page_texts(src) == [""]
    out, pages = ocr_pdf(src, tmp_path, language="fra", dpi=200)
    assert pages == 1
    with pymupdf.open(out) as doc:
        text = doc[0].get_text()
        assert "Facture" in text and "2026" in text and "monde" in text
        assert doc[0].search_for("Bonjour")  # recherche possible
        after = doc[0].get_pixmap(dpi=40)
    with pymupdf.open(src) as doc:
        before = doc[0].get_pixmap(dpi=40)
    assert (after.width, after.height) == (before.width, before.height)
    assert after.samples == before.samples  # texte invisible : rendu identique


def test_ocr_skips_pages_with_text(tmp_path):
    src = tmp_path / "t.pdf"
    with pymupdf.open() as doc:
        doc.new_page().insert_text((72, 72), "Un document numérique, pas un scan.")
        doc.save(src)
    with pytest.raises(ValueError, match="déjà du texte"):
        ocr_pdf(src, tmp_path, dpi=100)


# ---------------------------------------------------------- filigrane image


def logo(path: Path, size=(200, 100), color=(200, 0, 0, 255)) -> Path:
    Image.new("RGBA", size, color).save(path)
    return path


def test_image_watermark_position_opacity_and_single_copy(tmp_path):
    src = blank_pdf(tmp_path / "b.pdf", pages=3)
    out = image_watermark_pdf(src, tmp_path, ImageMark(logo(tmp_path / "logo.png"), scale=0.3,
                                                      position="bottom-right", opacity=0.5))
    with pymupdf.open(out) as doc:
        pix = doc[2].get_pixmap(dpi=30)
        assert len({img[0] for page in doc for img in page.get_images()}) == 1  # image partagée
    assert ink(pix, 0.6, 0.85, 0.95, 0.96) > 0 and ink(pix, 0, 0, 1, 0.8) == 0
    r, g, b = pix.pixel(int(pix.width * 0.8), int(pix.height * 0.92))
    assert r > 200 and 100 < g < 160  # rouge à 50 % sur blanc : rose


def test_image_watermark_on_rotated_page_and_errors(tmp_path):
    src = blank_pdf(tmp_path / "r.pdf", rotation=90)
    out = image_watermark_pdf(src, tmp_path, ImageMark(logo(tmp_path / "l.png"), position="top-left", opacity=1))
    with pymupdf.open(out) as doc:
        pix = doc[0].get_pixmap(dpi=30)
    assert pix.width > pix.height and ink(pix, 0, 0, 0.4, 0.35) > 0 and ink(pix, 0.5, 0.5, 1, 1) == 0
    with pytest.raises(ValueError, match="image"):
        image_watermark_pdf(src, tmp_path, ImageMark(tmp_path / "absent.png"))


# ------------------------------------------------------- extraction d'images

def test_extract_images(tmp_path):
    jpg = tmp_path / "photo.jpg"
    Image.new("RGB", (120, 80), (10, 120, 200)).save(jpg, quality=90)
    cmyk = tmp_path / "cmyk.jpg"
    Image.new("CMYK", (100, 100), (0, 200, 200, 0)).save(cmyk)
    src = tmp_path / "images.pdf"
    with pymupdf.open() as doc:
        for _ in range(2):  # la même photo sur deux pages : extraite une fois
            doc.new_page().insert_image(pymupdf.Rect(50, 50, 290, 210), filename=jpg)
        page = doc[1]
        page.insert_image(pymupdf.Rect(300, 50, 400, 150), filename=logo(tmp_path / "a.png", (100, 100), (0, 0, 255, 128)))
        page.insert_image(pymupdf.Rect(50, 300, 150, 400), filename=cmyk)
        page.insert_image(pymupdf.Rect(10, 10, 20, 20), filename=logo(tmp_path / "puce.png", (16, 16)))
        doc.save(src)

    outputs = extract_images(src, tmp_path)
    assert outputs[0].parent == tmp_path / "images_images"
    names = sorted(p.suffix for p in outputs)
    assert names == [".jpg", ".png", ".png"]  # photo, logo transparent, CMJN converti ; puce ignorée
    assert jpg.read_bytes() == next(p for p in outputs if p.suffix == ".jpg").read_bytes()  # sans perte
    transparent = [p for p in outputs if p.suffix == ".png" and Image.open(p).mode == "RGBA"]
    assert len(transparent) == 1 and Image.open(transparent[0]).getpixel((5, 5))[3] == 128

    assert len(extract_images(src, tmp_path, skip_small=False)) == 4
    with pytest.raises(ValueError, match="Aucune image"):
        extract_images(make_pdf(tmp_path / "t.pdf", 1), tmp_path)


# --------------------------------------------------------------- métadonnées

def test_edit_and_wipe_metadata(tmp_path):
    src = make_pdf(tmp_path / "m.pdf", 1)  # auteur « XenoviaLabs », titre « Rapport »
    with pymupdf.open(src) as doc:
        doc.set_xml_metadata("<x:xmpmeta xmlns:x='adobe:ns:meta/'/>")
        doc.saveIncr()
    assert read_metadata(src)["author"] == "XenoviaLabs"

    out = edit_metadata(src, tmp_path, {"title": "Nouveau titre", "author": None, "subject": "", "keywords": "a, b"})
    meta = read_metadata(out)
    assert meta["title"] == "Nouveau titre" and meta["author"] == "XenoviaLabs" and meta["keywords"] == "a, b"
    with pymupdf.open(out) as doc:
        assert doc.get_xml_metadata() == ""

    wiped = edit_metadata(src, tmp_path, {"title": "Seul champ"}, wipe=True)
    with pymupdf.open(wiped) as doc:
        assert doc.metadata["title"] == "Seul champ"
        assert not doc.metadata["author"] and not doc.metadata["creationDate"]
