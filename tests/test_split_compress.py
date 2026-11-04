import random
from pathlib import Path

import pymupdf
import pytest
from PIL import Image

from docflex.core.compress import compress_file
from docflex.core.split import parse_page_spec, plan_split, split_pdf
from docflex.core.tasks import run_batch


def make_pdf(path: Path, pages: int, toc=None) -> Path:
    doc = pymupdf.open()
    for i in range(pages):
        doc.new_page().insert_text((72, 72), f"Page {i + 1}", fontsize=20)
    if toc:
        doc.set_toc(toc)
    doc.set_metadata({"author": "XenoviaLabs", "title": "Rapport"})
    doc.save(path)
    return path


def page_texts(path: Path) -> list[str]:
    with pymupdf.open(path) as doc:
        return [p.get_text().strip() for p in doc]


def photo(size=(2400, 1800)) -> Image.Image:
    """Image « photographique » : dégradé + bruit, qui se compresse mal en PNG."""
    rnd = random.Random(1)
    small = Image.new("RGB", (size[0] // 8, size[1] // 8))
    small.putdata([
        (x * 255 // small.width, y * 255 // small.height, rnd.randrange(256))
        for y in range(small.height) for x in range(small.width)
    ])
    return small.resize(size, Image.Resampling.BICUBIC)


# ------------------------------------------------------------ plages

@pytest.mark.parametrize("spec, expected", [
    ("1-3, 5, 8-", [(0, 2), (4, 4), (7, 9)]),
    ("-2; 10", [(0, 1), (9, 9)]),
    (" 4 - 6 ", [(3, 5)]),
])
def test_parse_page_spec(spec, expected):
    assert parse_page_spec(spec, 10) == expected


@pytest.mark.parametrize("spec, message", [
    ("", "Indiquez"),
    ("abc", "n'est pas compris"),
    ("0-2", "10 pages"),
    ("4-11", "10 pages"),
    ("6-2", "précéder"),
])
def test_parse_page_spec_errors(spec, message):
    with pytest.raises(ValueError, match=message):
        parse_page_spec(spec, 10)


def test_plan_modes():
    assert [p.suffix for p in plan_split(5, "each")] == ["p1", "p2", "p3", "p4", "p5"]
    assert [p.describe() for p in plan_split(5, "chunk", "2")] == ["1-2", "3-4", "5"]
    extract = plan_split(10, "extract", "1, 3-4")
    assert len(extract) == 1 and extract[0].page_count == 3
    with pytest.raises(ValueError):
        plan_split(5, "chunk", "0")


def test_plan_bookmarks():
    toc = [[1, "Intro", 2], [2, "Détail", 3], [1, "Chap: 2/fin", 4], [1, "Hors doc", 99]]
    parts = plan_split(6, "bookmarks", toc=toc)
    assert [(p.suffix, p.describe()) for p in parts] == [
        ("00 Début", "1"), ("01 Intro", "2-3"), ("02 Chap 2 fin", "4-6"),
    ]
    with pytest.raises(ValueError, match="signets"):
        plan_split(6, "bookmarks", toc=[])


# --------------------------------------------------------- découpage

def test_split_ranges_into_folder(tmp_path):
    src = make_pdf(tmp_path / "rapport.pdf", 6)
    outputs = split_pdf(src, plan_split(6, "ranges", "1-2, 5-"))
    assert outputs[0].parent == tmp_path / "rapport (découpé)"
    assert [p.name for p in outputs] == ["rapport_p1-2.pdf", "rapport_p5-6.pdf"]
    assert page_texts(outputs[1]) == ["Page 5", "Page 6"]
    with pymupdf.open(outputs[0]) as doc:
        assert doc.metadata["author"] == "XenoviaLabs"
        assert doc.metadata["title"] == "Rapport — p1-2"


def test_split_extract_single_file_next_to_source(tmp_path):
    src = make_pdf(tmp_path / "r.pdf", 6)
    [out] = split_pdf(src, plan_split(6, "extract", "6, 1-2"))
    assert out == tmp_path / "r_extrait.pdf"
    assert page_texts(out) == ["Page 6", "Page 1", "Page 2"]  # l'ordre saisi est respecté


def test_split_rejects_encrypted(tmp_path):
    src = make_pdf(tmp_path / "s.pdf", 2)
    locked = tmp_path / "locked.pdf"
    with pymupdf.open(src) as doc:
        doc.save(locked, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="x", owner_pw="y")
    with pytest.raises(ValueError, match="mot de passe"):
        split_pdf(locked, plan_split(2, "each"))


# --------------------------------------------------------- compression

@pytest.fixture
def heavy_pdf(tmp_path) -> Path:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 60), "Rapport annuel", fontsize=18)
    img = tmp_path / "photo.png"
    photo().save(img)
    page.insert_image(pymupdf.Rect(72, 80, 523, 418), filename=img)  # ~380 ppp
    path = tmp_path / "lourd.pdf"
    doc.save(path)
    return path


def test_compress_pdf_levels(heavy_pdf, tmp_path):
    before = heavy_pdf.stat().st_size
    [rec], note = compress_file(heavy_pdf, None, "recommended")
    [strong], _ = compress_file(heavy_pdf, None, "strong")
    assert rec.name == "lourd_compressé.pdf" and strong.name == "lourd_compressé (2).pdf"
    assert strong.stat().st_size < rec.stat().st_size < before / 3
    assert "→" in note and "%" in note
    assert "Rapport annuel" in page_texts(rec)[0]  # le texte reste intact


def test_compress_pdf_grayscale(heavy_pdf):
    [out], _ = compress_file(heavy_pdf, None, "recommended", grayscale=True)
    with pymupdf.open(out) as doc:
        xref = doc[0].get_images()[0][0]
        assert pymupdf.Pixmap(doc, xref).n == 1  # une seule composante : gris


def test_compress_already_optimal_creates_nothing(tmp_path):
    src = tmp_path / "petit.pdf"
    make_pdf(src, 1)
    first, _ = compress_file(src, None, "light")
    target = first[0] if first else src
    outputs, note = compress_file(target, tmp_path, "light")
    assert outputs == [] and "déjà optimisé" in note


def test_compress_images(tmp_path):
    jpg = tmp_path / "photo.jpg"
    photo().save(jpg, quality=98)
    png = tmp_path / "capture.png"
    photo((1800, 1200)).save(png)

    [light], _ = compress_file(jpg, None, "light")
    with Image.open(light) as img:
        assert img.size == (2400, 1800)  # légère : dimensions conservées
    [strong], _ = compress_file(jpg, None, "strong")
    with Image.open(strong) as img:
        assert max(img.size) == 1600
    [png_out], _ = compress_file(png, None, "strong")
    assert png_out.stat().st_size < png.stat().st_size / 2


def test_compress_jpeg_keeps_exif_rotation(tmp_path):
    src = tmp_path / "tel.jpg"
    exif = Image.Exif()
    exif[0x0112] = 6  # rotation de 90° : l'image stockée 300×200 s'affiche 200×300
    photo((300, 200)).save(src, exif=exif, quality=95)
    [out], _ = compress_file(src, None, "recommended")
    with Image.open(out) as img:
        assert img.size == (200, 300)


def test_run_batch_collects_errors(heavy_pdf, tmp_path):
    bad = tmp_path / "faux.pdf"
    bad.write_bytes(b"rien")
    report = run_batch([bad, heavy_pdf], lambda p, prog: compress_file(p, None, "strong", progress=prog))
    assert len(report.outputs) == 1 and report.errors[0][0] == bad and len(report.notes) == 1
