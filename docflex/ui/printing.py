"""Impression d'un PDF : chaque page est rendue par PyMuPDF puis envoyée à l'imprimante.

Ce qui est imprimé est ce qui est affiché : annotations, champs remplis et modifications non
encore enregistrées compris. Chaque page garde son orientation (une page en paysage s'imprime
en paysage) ; elle est réduite si elle dépasse la zone imprimable, jamais agrandie.
"""

from __future__ import annotations

from typing import Callable

import pymupdf
from PySide6.QtCore import QRectF
from PySide6.QtGui import QImage, QPageLayout, QPainter
from PySide6.QtPrintSupport import QPrinter

MAX_DPI = 300  # au-delà, des fichiers énormes sans gain visible à l'impression


def pages_to_print(printer: QPrinter, page_count: int, current: int) -> list[int]:
    """Pages choisies dans le dialogue d'impression (indices à partir de 0)."""
    mode = printer.printRange()
    if mode == QPrinter.CurrentPage:
        return [current]
    if mode == QPrinter.PageRange and printer.fromPage() > 0:
        first, last = printer.fromPage() - 1, min(printer.toPage() or page_count, page_count) - 1
        return list(range(first, last + 1))
    return list(range(page_count))


def print_pages(doc: pymupdf.Document, printer: QPrinter, pages: list[int],
                progress: Callable[[int], bool] | None = None) -> bool:
    """Imprime `pages` ; `progress(n)` est appelé avant chaque page et peut tout annuler (False)."""
    painter = QPainter()
    dpi = min(printer.resolution(), MAX_DPI)
    for n, index in enumerate(pages):
        if progress is not None and not progress(n):
            if painter.isActive():
                printer.abort()
                painter.end()
            return False
        page = doc[index]
        landscape = page.rect.width > page.rect.height
        layout = printer.pageLayout()
        layout.setOrientation(QPageLayout.Landscape if landscape else QPageLayout.Portrait)
        printer.setPageLayout(layout)  # pris en compte à la page suivante
        if n == 0:
            if not painter.begin(printer):
                return False
        else:
            printer.newPage()

        area = printer.pageLayout().paintRectPixels(printer.resolution())
        width_px = page.rect.width * printer.resolution() / 72  # taille réelle de la page
        height_px = page.rect.height * printer.resolution() / 72
        scale = min(area.width() / width_px, area.height() / height_px, 1.0)
        target = QRectF(0, 0, width_px * scale, height_px * scale)
        target.moveCenter(QRectF(0, 0, area.width(), area.height()).center())

        zoom = dpi / 72 * scale
        pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
        image = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format_RGB888)
        painter.drawImage(target, image)
    if painter.isActive():
        painter.end()
    return True
