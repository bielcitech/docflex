"""Impression : on imprime « dans un PDF » (QPrinter) pour vérifier pages, orientation et contenu."""

import pymupdf
import pytest
from PySide6.QtPrintSupport import QPrinter

from docflex.app import MainWindow, create_app
from docflex.core.annotations import AnnotSpec, Change
from tests.test_reader_ui import book


@pytest.fixture
def tab(qtbot, tmp_path):
    create_app()
    win = MainWindow()
    win.resize(1280, 840)
    win.show()
    view = win.views["reader"]
    path = book(tmp_path / "livre.pdf", 3)
    with pymupdf.open(path) as doc:  # une page en paysage au milieu
        doc[1].set_rotation(90)
        doc.saveIncr()
    view.open(path)
    yield view.current
    view.dirty = False
    win.close()
    win.deleteLater()


def print_to(tab, target, configure=lambda printer: None):
    def ask(printer):
        printer.setOutputFormat(QPrinter.PdfFormat)
        printer.setOutputFileName(str(target))
        configure(printer)
        return True

    tab.ask_printer = ask
    return tab.print_document()


def test_print_every_page_with_its_orientation(tab, tmp_path):
    tab._record(Change(0, None, AnnotSpec("rect", rect=(100, 300, 300, 400), color=(1, 0, 0))))  # non enregistré
    target = tmp_path / "imprimé.pdf"
    assert print_to(tab, target) and "3 pages" in tab.status.text()
    with pymupdf.open(target) as out:
        assert out.page_count == 3
        assert [p.rect.width > p.rect.height for p in out] == [False, True, False]
        pix = out[0].get_pixmap(dpi=30)
        reds = sum(1 for i in range(0, len(pix.samples), 3)
                   if pix.samples[i] > 200 and pix.samples[i + 1] < 90 and pix.samples[i + 2] < 90)
        assert reds > 5  # l'annotation, pas encore enregistrée, est imprimée


def test_print_range_and_current_page(tab, tmp_path):
    target = tmp_path / "plage.pdf"

    def pages_2_to_3(printer):
        printer.setPrintRange(QPrinter.PageRange)
        printer.setFromTo(2, 3)

    assert print_to(tab, target, pages_2_to_3)
    with pymupdf.open(target) as out:
        assert out.page_count == 2 and out[0].rect.width > out[0].rect.height

    tab.canvas.go_to(2)
    assert print_to(tab, tmp_path / "courante.pdf", lambda p: p.setPrintRange(QPrinter.CurrentPage))
    with pymupdf.open(tmp_path / "courante.pdf") as out:
        assert out.page_count == 1 and out[0].rect.width < out[0].rect.height  # page 3, en portrait

    tab.ask_printer = lambda printer: False  # dialogue fermé : rien n'est imprimé
    assert not tab.print_document()
