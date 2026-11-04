from datetime import datetime
from pathlib import Path

import pymupdf
import pytest
from openpyxl import Workbook, load_workbook
from PIL import Image

from docflex.core import office
from docflex.core.converters import convert_files, options_for, targets_for
from docflex.core.tasks import Cancelled
from docflex.utils.files import unique_path


@pytest.fixture
def pdf3(tmp_path: Path) -> Path:
    doc = pymupdf.open()
    for i in range(3):
        page = doc.new_page()
        page.insert_text((72, 72), f"Bonjour page {i + 1}", fontsize=14)
    path = tmp_path / "doc.pdf"
    doc.save(path)
    return path


@pytest.fixture
def xlsx2(tmp_path: Path) -> Path:
    wb = Workbook()
    ws = wb.active
    ws.title = "Ventes"
    ws.append(["Produit", "Prix", "Date"])
    ws.append(["Café", 2.5, datetime(2026, 10, 30)])
    ws.append(["Thé", 3, None])
    wb.create_sheet("Stock 2026").append(["A", 1])
    path = tmp_path / "data.xlsx"
    wb.save(path)
    return path


def make_png(path: Path, size=(40, 20), mode="RGBA") -> Path:
    Image.new(mode, size, (255, 0, 0, 128) if mode == "RGBA" else (0, 0, 255)).save(path)
    return path


def test_targets_intersection(tmp_path):
    assert targets_for([Path("a.pdf")])[:4] == ["png", "jpg", "docx", "txt"]
    assert "pdf" in targets_for([Path("a.png"), Path("b.jpg")])
    assert targets_for([Path("a.pdf"), Path("b.csv")]) == []
    assert [o.key for o in options_for([Path("a.pdf")], "jpg")] == ["dpi", "quality"]


def test_pdf_to_png_one_folder_per_document(pdf3, tmp_path):
    report = convert_files([pdf3], "png", {"dpi": 72})
    assert not report.errors
    assert [p.name for p in report.outputs] == ["doc_p1.png", "doc_p2.png", "doc_p3.png"]
    assert report.outputs[0].parent == tmp_path / "doc_pages"
    with Image.open(report.outputs[0]) as img:
        assert img.size == (595, 842)  # A4 à 72 ppp


def test_pdf_to_jpg_and_txt(pdf3, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    assert len(convert_files([pdf3], "jpg", out_dir=out).outputs) == 3
    txt = convert_files([pdf3], "txt", out_dir=out).outputs[0]
    assert "Bonjour page 3" in txt.read_text(encoding="utf-8")


def test_pdf_to_docx(pdf3):
    from docx import Document

    report = convert_files([pdf3], "docx")
    assert not report.errors, report.errors
    text = "\n".join(p.text for p in Document(report.outputs[0]).paragraphs)
    assert "Bonjour page 2" in text


def test_images_to_single_pdf_a4(tmp_path):
    imgs = [make_png(tmp_path / "a.png"), make_png(tmp_path / "b.png", (20, 40), "RGB")]
    report = convert_files(imgs, "pdf", {"merge": True, "page_size": "a4"})
    assert [p.name for p in report.outputs] == ["a (fusion).pdf"]
    with pymupdf.open(report.outputs[0]) as doc:
        assert doc.page_count == 2
        assert doc[0].rect.width > doc[0].rect.height  # image paysage → page paysage
        assert doc[1].rect.width < doc[1].rect.height


def test_images_to_separate_pdfs(tmp_path):
    imgs = [make_png(tmp_path / "a.png"), make_png(tmp_path / "b.png")]
    report = convert_files(imgs, "pdf", {"merge": False, "page_size": "image"})
    assert sorted(p.name for p in report.outputs) == ["a.pdf", "b.pdf"]


def test_png_with_alpha_to_jpg(tmp_path):
    out = convert_files([make_png(tmp_path / "a.png")], "jpg").outputs[0]
    with Image.open(out) as img:
        assert img.mode == "RGB"


def test_xlsx_to_csv_all_sheets(xlsx2, tmp_path):
    report = convert_files([xlsx2], "csv")
    assert [p.name for p in report.outputs] == ["data_Ventes.csv", "data_Stock 2026.csv"]
    content = report.outputs[0].read_text(encoding="utf-8-sig").splitlines()
    assert content == ["Produit;Prix;Date", "Café;2,5;2026-10-30", "Thé;3;"]


def test_xlsx_to_csv_rejects_ambiguous_separator(xlsx2):
    report = convert_files([xlsx2], "csv", {"sep": ",", "decimal": ","})
    assert report.errors and not report.outputs


def test_csv_to_xlsx_roundtrip(tmp_path):
    src = tmp_path / "t.csv"
    src.write_bytes("nom;valeur;code\nCafé;2,5;007\nThé;3;12\n".encode("cp1252"))
    out = convert_files([src], "xlsx").outputs[0]
    rows = list(load_workbook(out).active.iter_rows(values_only=True))
    assert rows == [("nom", "valeur", "code"), ("Café", 2.5, "007"), ("Thé", 3, 12)]


def test_txt_to_pdf(tmp_path):
    src = tmp_path / "notes.txt"
    src.write_text("Première ligne\nDeuxième", encoding="utf-8")
    out = convert_files([src], "pdf").outputs[0]
    with pymupdf.open(out) as doc:
        assert "Première ligne" in doc[0].get_text()


def test_errors_do_not_stop_batch(pdf3, tmp_path):
    broken = tmp_path / "casse.pdf"
    broken.write_bytes(b"pas un pdf")
    report = convert_files([broken, pdf3], "txt")
    assert len(report.outputs) == 1 and report.errors[0][0] == broken


def test_cancel_propagates(pdf3):
    def progress(fraction, message):
        if fraction > 0:
            raise Cancelled

    with pytest.raises(Cancelled):
        convert_files([pdf3], "png", progress=progress)


@pytest.mark.skipif(not office.is_available(), reason="LibreOffice absent")
def test_xlsx_to_pdf_with_libreoffice(xlsx2):
    report = convert_files([xlsx2], "pdf")
    assert not report.errors, report.errors
    with pymupdf.open(report.outputs[0]) as doc:
        assert "Café" in doc[0].get_text()


def test_unique_path(tmp_path):
    (tmp_path / "a.pdf").touch()
    assert unique_path(tmp_path / "a.pdf").name == "a (2).pdf"
