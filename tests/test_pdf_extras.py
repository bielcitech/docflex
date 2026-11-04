from pathlib import Path

import pymupdf
import pytest
from PIL import Image, ImageDraw

from docflex.core.pdf_extras import (
    compare_pdfs, flatten_pdf, redact_pdf, remove_blank_pages, sign_pdf, signature_png,
)
from tests.test_pdf_tools import ink
from tests.test_split_compress import page_texts

# ------------------------------------------------------------ pages blanches


def test_remove_blank_pages(tmp_path):
    src = tmp_path / "scan.pdf"
    with pymupdf.open() as doc:
        doc.new_page().insert_text((72, 72), "Contenu", fontsize=20)
        doc.new_page()                                          # vide
        doc.new_page().insert_text((280, 420), "Annexe", fontsize=10)  # presque vide mais avec du texte
        dusty = doc.new_page()                                  # scan avec poussières
        for k in range(40):
            dusty.draw_circle((50 + k * 12, 300 + (k % 7) * 40), 1.5, color=None, fill=(0, 0, 0))
        doc.save(src)

    out, removed = remove_blank_pages(src, tmp_path)
    assert removed == 1 and page_texts(out) == ["Contenu", "Annexe", ""]
    out, removed = remove_blank_pages(src, tmp_path, max_ink=0.004)
    assert removed == 2 and page_texts(out) == ["Contenu", "Annexe"]

    with pymupdf.open() as doc:
        doc.new_page().insert_text((72, 72), "Rien à retirer")
        doc.save(tmp_path / "plein.pdf")
    with pytest.raises(ValueError, match="Aucune page blanche"):
        remove_blank_pages(tmp_path / "plein.pdf", tmp_path)


# ---------------------------------------------------------------- caviardage

def test_redaction_removes_text_for_good(tmp_path):
    src = tmp_path / "rh.pdf"
    with pymupdf.open() as doc:
        page = doc.new_page()
        lines = ["Salarié : Jean DUPONT", "Contact : jean.dupont@exemple.fr", "Tél. 06 12 34 56 78",
                 "IBAN FR76 3000 6000 0112 3456 7890 189", "Service : comptabilité"]
        for i, line in enumerate(lines):
            page.insert_text((72, 100 + i * 30), line, fontsize=12)
        doc.save(src)

    out, count = redact_pdf(src, tmp_path, ["dupont"], ["email", "phone", "iban"])
    text = page_texts(out)[0]
    assert count >= 4
    for secret in ("DUPONT", "exemple.fr", "34 56", "FR76", "0112"):
        assert secret not in text
    assert "comptabilité" in text and "Jean" in text  # le reste est intact
    with pymupdf.open(out) as doc:
        pix = doc[0].get_pixmap(dpi=40)
        assert not doc[0].first_annot  # rectangles appliqués, plus aucune annotation à retirer
    assert ink(pix, 0, 0, 1, 1) > 100  # rectangles noirs visibles

    with pytest.raises(ValueError, match="Aucune occurrence"):
        redact_pdf(src, tmp_path, ["introuvable"])
    with pytest.raises(ValueError, match="Indiquez"):
        redact_pdf(src, tmp_path, ["  "])


# ---------------------------------------------------------------- comparaison

def text_pdf(path: Path, pages: list[str]) -> Path:
    with pymupdf.open() as doc:
        for text in pages:
            doc.new_page().insert_text((72, 100), text, fontsize=12)
        doc.save(path)
    return path


def annots(path: Path) -> list[int]:
    with pymupdf.open(path) as doc:
        return [len(list(page.annots())) for page in doc]


def test_compare_highlights_changes(tmp_path):
    old = text_pdf(tmp_path / "v1.pdf", ["Le prix est de 100 euros par mois"])
    new = text_pdf(tmp_path / "v2.pdf", ["Le prix est de 120 euros par mois TTC"])
    (out_old, out_new), changes = compare_pdfs(old, new, tmp_path)
    assert changes == 2
    assert annots(out_old) == [1] and annots(out_new) == [2]  # « 100 » retiré ; « 120 » et « TTC » ajoutés


def test_compare_ignores_reflow_and_identical(tmp_path):
    old = text_pdf(tmp_path / "a.pdf", ["alpha beta gamma", "delta"])
    new = text_pdf(tmp_path / "b.pdf", ["alpha beta", "gamma delta"])  # même texte, coupé ailleurs
    assert compare_pdfs(old, new, tmp_path) == ([], 0)
    with pytest.raises(ValueError, match="pas de texte"):
        blank = tmp_path / "vide.pdf"
        with pymupdf.open() as doc:
            doc.new_page()
            doc.save(blank)
        compare_pdfs(blank, blank, tmp_path)


# --------------------------------------------------------------- aplatissement

def test_flatten_forms_and_annotations(tmp_path):
    src = tmp_path / "form.pdf"
    with pymupdf.open() as doc:
        page = doc.new_page()
        widget = pymupdf.Widget()
        widget.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
        widget.field_name = "nom"
        widget.field_value = "Marie Curie"
        widget.rect = pymupdf.Rect(72, 72, 300, 100)
        page.add_widget(widget)
        page.add_text_annot((400, 100), "Note de relecture")
        doc.save(src)
    out = flatten_pdf(src, tmp_path)
    with pymupdf.open(out) as doc:
        page = doc[0]
        assert not page.first_widget and not page.first_annot and not doc.is_form_pdf
        assert "Marie Curie" in page.get_text()  # la valeur saisie fait désormais partie de la page
    with pytest.raises(ValueError, match="Rien à aplatir"):
        flatten_pdf(text_pdf(tmp_path / "t.pdf", ["x"]), tmp_path)


# ------------------------------------------------------------ signature visuelle

def signature_photo(path: Path) -> Path:
    """Signature « photographiée » : trait noir sur papier légèrement gris."""
    img = Image.new("RGB", (400, 200), (238, 238, 235))
    ImageDraw.Draw(img).line([(60, 140), (140, 60), (220, 140), (330, 70)], fill=(15, 15, 40), width=8)
    img.save(path)
    return path


def test_signature_background_becomes_transparent(tmp_path):
    with Image.open(__import__("io").BytesIO(signature_png(signature_photo(tmp_path / "s.jpg")))) as img:
        assert img.mode == "RGBA"
        assert img.width < 400 and img.height < 200  # marges vides retirées
        assert img.getpixel((img.width // 2, 0))[3] == 0 or img.getpixel((0, 0))[3] == 0


def test_sign_page_keeps_content_visible(tmp_path):
    src = tmp_path / "contrat.pdf"
    with pymupdf.open() as doc:
        for _ in range(2):
            page = doc.new_page()
            page.draw_rect(pymupdf.Rect(300, 650, 560, 800), color=None, fill=(0.85, 0.85, 0.85))  # zone grisée
        doc.save(src)
    image = signature_photo(tmp_path / "sig.png")
    out = sign_pdf(src, tmp_path, image, 1, (0.55, 0.78, 0.92, 0.93))
    with pymupdf.open(out) as doc:
        assert not doc[0].get_images() and len(doc[1].get_images()) == 1
        pix = doc[1].get_pixmap(dpi=40)
    dark = ink(pix, 0.55, 0.78, 0.92, 0.93)
    assert dark > 10
    # Fond transparent : le gris de la page reste visible autour du trait.
    grays = [pix.pixel(x, y)[0] for x in range(int(pix.width * 0.56), int(pix.width * 0.9))
             for y in range(int(pix.height * 0.79), int(pix.height * 0.92))]
    assert grays.count(217) + grays.count(216) + grays.count(218) > len(grays) / 3

    all_pages = sign_pdf(src, tmp_path, image, 0, (0.7, 0.85, 0.9, 0.95), all_pages=True)
    with pymupdf.open(all_pages) as doc:
        assert len({img[0] for page in doc for img in page.get_images()}) == 1  # paraphe partagé
        assert all(page.get_images() for page in doc)
