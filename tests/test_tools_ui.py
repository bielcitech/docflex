"""Outils PDF pilotés comme par un utilisateur (pytest-qt, sans fenêtre visible)."""

import pymupdf
import pytest
from PySide6.QtCore import Qt

from docflex.app import MainWindow, create_app
from tests.test_pdf_tools import scanned_pdf
from tests.test_split_compress import make_pdf, page_texts


@pytest.fixture
def tools(qtbot):
    create_app()
    win = MainWindow()
    qtbot.addWidget(win)
    win.show_view("tools")
    return win.views["tools"]


def run(qtbot, page):
    assert page.panel.run_btn.isEnabled(), page.panel.status.text()
    qtbot.mouseClick(page.panel.run_btn, Qt.LeftButton)
    qtbot.waitUntil(lambda: not page.panel.busy, timeout=60000)


def test_merge_order_and_save(tools, qtbot, tmp_path):
    page = tools.pages["merge"]
    a, b = make_pdf(tmp_path / "b_second.pdf", 1), make_pdf(tmp_path / "a_premier.pdf", 2)
    page.files.add([a])
    assert not page.panel.run_btn.isEnabled() and "deux PDF" in page.panel.status.text()
    page.files.add([b])
    page.files.sort_by_name()
    assert page.files.paths == [b, a]
    page.files.move(a, -1)
    assert page.files.paths == [a, b]
    assert page.default_output().name == "b_second (fusion).pdf"
    page.merge_to(tmp_path / "out.pdf")
    qtbot.waitUntil(lambda: not page.panel.busy, timeout=10000)
    assert page_texts(tmp_path / "out.pdf") == ["Page 1", "Page 1", "Page 2"]


def test_watermark_and_numbering(tools, qtbot, tmp_path):
    src = make_pdf(tmp_path / "doc.pdf", 2)
    mark = tools.pages["watermark"]
    mark.files.add([src])
    mark.text.setText("")
    assert not mark.panel.run_btn.isEnabled() and "texte" in mark.panel.status.text()
    mark.text.setText("BROUILLON")
    run(qtbot, mark)
    assert all("BROUILLON" in t for t in page_texts(tmp_path / "doc_filigrane.pdf"))

    numbers = tools.pages["numbering"]
    tools.show_tool("numbering")
    numbers.files.add([src])
    numbers.template.setCurrentIndex(numbers.template.findData("Page {n} sur {total}"))
    numbers.skip_first.setChecked(True)
    run(qtbot, numbers)
    texts = page_texts(tmp_path / "doc_numéroté.pdf")
    assert "sur" not in texts[0] and "Page 2 sur 2" in texts[1]


def test_password_validation_and_round_trip(tools, qtbot, tmp_path):
    page = tools.pages["password"]
    page.files.add([make_pdf(tmp_path / "doc.pdf", 1)])
    assert "Saisissez" in page.panel.status.text()
    page.password.setText("motdepasse1")
    page.confirm.setText("autre")
    assert not page.panel.run_btn.isEnabled() and "correspondent" in page.panel.status.text()
    page.confirm.setText("motdepasse1")
    run(qtbot, page)
    locked = tmp_path / "doc_protégé.pdf"
    with pymupdf.open(locked) as doc:
        assert doc.needs_pass

    page.mode.set_value("remove")
    page.mode.changed.emit("remove")
    assert page.confirm.isHidden() and page.panel.run_btn.text() == "Retirer la protection"
    page.files.clear()
    page.files.add([locked])
    page.password.setText("faux")
    run(qtbot, page)
    assert "incorrect" in page.panel.log.toPlainText()
    page.password.setText("motdepasse1")
    run(qtbot, page)
    with pymupdf.open(tmp_path / "doc_protégé_déverrouillé.pdf") as doc:
        assert not doc.needs_pass


def test_ocr_page(tools, qtbot, tmp_path):
    page = tools.pages["ocr"]
    page.files.add([scanned_pdf(tmp_path / "scan.pdf", "Bonjour le monde")])
    page.quality.setCurrentIndex(page.quality.findData(200))
    run(qtbot, page)
    assert "1 page reconnue" in page.panel.log.toPlainText()
    assert "Bonjour" in page_texts(tmp_path / "scan_ocr.pdf")[0]


def test_image_watermark_mode(tools, qtbot, tmp_path):
    from PIL import Image

    page = tools.pages["watermark"]
    page.files.add([make_pdf(tmp_path / "doc.pdf", 1)])
    page.kind.set_value("image")
    page.kind.changed.emit("image")
    assert page.text.isHidden() and not page.picker.isHidden()
    assert not page.panel.run_btn.isEnabled() and "image" in page.panel.status.text()
    logo = tmp_path / "logo.png"
    Image.new("RGBA", (100, 50), (0, 0, 200, 255)).save(logo)
    page.set_image(logo)
    run(qtbot, page)
    with pymupdf.open(tmp_path / "doc_filigrane.pdf") as doc:
        assert len(doc[0].get_images()) == 1


def test_extract_images_page(tools, qtbot, tmp_path):
    from PIL import Image

    jpg = tmp_path / "photo.jpg"
    Image.new("RGB", (200, 100), (200, 50, 50)).save(jpg)
    src = tmp_path / "album.pdf"
    with pymupdf.open() as doc:
        doc.new_page().insert_image(pymupdf.Rect(50, 50, 250, 150), filename=jpg)
        doc.save(src)
    page = tools.pages["images"]
    page.files.add([src])
    run(qtbot, page)
    assert "1 image extraite" in page.panel.log.toPlainText()
    assert len(list((tmp_path / "album_images").iterdir())) == 1


def test_metadata_page_single_multi_and_wipe(tools, qtbot, tmp_path):
    from docflex.core.pdf_tools import read_metadata

    page = tools.pages["metadata"]
    a = make_pdf(tmp_path / "a.pdf", 1)  # auteur « XenoviaLabs », titre « Rapport »
    page.files.add([a])
    assert page.fields["author"].text() == "XenoviaLabs"  # un seul fichier : valeurs pré-remplies
    page.fields["title"].setText("Nouveau titre")
    page.fields["author"].setText("")  # vidé : retiré du fichier
    run(qtbot, page)
    meta = read_metadata(tmp_path / "a_métadonnées.pdf")
    assert meta["title"] == "Nouveau titre" and meta["author"] == ""

    page.wipe.setChecked(True)
    assert page.fields["title"].text() == ""  # ce qui est affiché = ce qui sera enregistré
    page.wipe.setChecked(False)
    assert page.fields["title"].text() == "Rapport"

    b = make_pdf(tmp_path / "b.pdf", 1)
    page.files.add([b])
    assert page.fields["author"].text() == "" and page.fields["author"].placeholderText() == "inchangé"
    page.fields["keywords"].setText("lot")
    run(qtbot, page)
    meta_b = read_metadata(tmp_path / "b_métadonnées.pdf")
    assert meta_b["keywords"] == "lot" and meta_b["author"] == "XenoviaLabs"  # vide = inchangé en lot


# --------------------------------------------------- outils complémentaires

def test_index_cards_open_tools(tools, qtbot):
    from docflex.ui.tools_view import TOOLS, ToolCard

    cards = tools.index.findChildren(ToolCard)
    assert len(cards) == len(TOOLS) == 12
    qtbot.mouseClick(cards[3], Qt.LeftButton)  # « Signer »
    assert tools.stack.currentWidget() is tools.pages["sign"] and tools.tool_title.text() == "Signer"
    assert tools.header.isHidden()
    tools.show_index()
    assert tools.stack.currentWidget() is tools.index and not tools.header.isHidden()


def test_redact_page(tools, qtbot, tmp_path):
    src = tmp_path / "rh.pdf"
    with pymupdf.open() as doc:
        doc.new_page().insert_text((72, 72), "Contact : marie@exemple.fr — Marie Curie")
        doc.save(src)
    page = tools.pages["redact"]
    page.files.add([src])
    assert not page.panel.run_btn.isEnabled() and "Indiquez" in page.panel.status.text()
    page.terms.setPlainText("curie\n")
    page.patterns["email"].setChecked(True)
    run(qtbot, page)
    text = page_texts(tmp_path / "rh_caviardé.pdf")[0]
    assert "Curie" not in text and "exemple.fr" not in text and "Marie" in text
    assert "2 zones caviardées" in page.panel.log.toPlainText()


def test_compare_page(tools, qtbot, tmp_path):
    from tests.test_pdf_extras import text_pdf

    page = tools.pages["compare"]
    page.old.add([text_pdf(tmp_path / "v1.pdf", ["Livraison sous 10 jours"])])
    assert not page.panel.run_btn.isEnabled() and "deux versions" in page.panel.status.text()
    page.new.add([text_pdf(tmp_path / "v2.pdf", ["Livraison sous 15 jours ouvrés"])])
    run(qtbot, page)
    assert "2 différences trouvées" in page.panel.log.toPlainText()
    assert (tmp_path / "v1_différences.pdf").exists() and (tmp_path / "v2_différences.pdf").exists()


def test_blank_pages_and_flatten_pages(tools, qtbot, tmp_path):
    src = tmp_path / "scan.pdf"
    with pymupdf.open() as doc:
        doc.new_page().insert_text((72, 72), "Recto")
        doc.new_page()
        verso = doc.new_page()
        verso.insert_text((72, 72), "Verso")
        verso.add_text_annot((100, 100), "Note")
        doc.save(src)
    blank = tools.pages["blank"]
    blank.files.add([src])
    run(qtbot, blank)
    assert "1 page blanche retirée" in blank.panel.log.toPlainText()

    flatten = tools.pages["flatten"]
    flatten.files.add([src])
    run(qtbot, flatten)
    with pymupdf.open(tmp_path / "scan_aplati.pdf") as doc:
        assert not any(page.first_annot for page in doc)


def test_sign_page(tools, qtbot, tmp_path):
    from tests.test_pdf_extras import signature_photo

    page = tools.pages["sign"]
    page.files.add([make_pdf(tmp_path / "contrat.pdf", 3)])
    assert page.page_spin.value() == 3 and page.page_total.text() == "sur 3"  # dernière page par défaut
    assert not page.panel.run_btn.isEnabled() and "signature" in page.panel.status.text()
    page.set_image(signature_photo(tmp_path / "sig.jpg"))
    assert page.canvas.signature is not None and page.canvas.signature.hasAlphaChannel()
    page.canvas.set_box((0.1, 0.1, 0.4, 0.2))
    run(qtbot, page)
    with pymupdf.open(tmp_path / "contrat_signé.pdf") as doc:
        assert [bool(p.get_images()) for p in doc] == [False, False, True]
        placed = doc[2].get_image_rects(doc[2].get_images()[0][0])[0]
        assert placed.y1 < doc[2].rect.height * 0.25  # là où le cadre a été placé
