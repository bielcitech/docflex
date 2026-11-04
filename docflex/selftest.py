"""Autotest de l'exécutable empaqueté : `DocFlex --selftest <dossier>`.

Vérifie que les bibliothèques embarquées se chargent et que les conversions
principales fonctionnent, sans ouvrir de fenêtre. Le résultat est écrit dans
`<dossier>/selftest.txt` (les applications fenêtrées n'ont pas de console
sous Windows). Code de sortie 0 si tout est bon.
"""

from __future__ import annotations

import traceback
from pathlib import Path


def run(out_dir: Path) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    failures = 0

    def check(name: str, fn) -> None:
        nonlocal failures
        try:
            detail = fn()
            lines.append(f"OK    {name}{f' — {detail}' if detail else ''}")
        except Exception:
            failures += 1
            lines.append(f"ECHEC {name}\n{traceback.format_exc()}")

    import pymupdf
    from openpyxl import Workbook
    from PIL import Image

    from docflex.core import office
    from docflex.core.converters import convert_files

    pdf = out_dir / "essai.pdf"
    doc = pymupdf.open()
    for i in range(2):
        doc.new_page().insert_text((72, 72), f"Page {i + 1} — éàç", fontsize=14)
    doc.save(pdf)
    xlsx = out_dir / "essai.xlsx"
    wb = Workbook()
    wb.active.append(["Produit", "Prix"])
    wb.active.append(["Café", 2.5])
    wb.save(xlsx)
    png = out_dir / "essai.png"
    Image.new("RGBA", (60, 40), (200, 30, 30, 128)).save(png)

    def conversion(paths, target, expected):
        def fn():
            report = convert_files(paths, target)
            if report.errors:
                raise RuntimeError(report.errors)
            if len(report.outputs) != expected:
                raise RuntimeError(f"{len(report.outputs)} fichiers au lieu de {expected}")
            return ", ".join(p.name for p in report.outputs)
        return fn

    def interface():
        from PySide6.QtGui import QImageReader

        from docflex.app import MainWindow, create_app

        create_app()
        formats = {bytes(f).decode() for f in QImageReader.supportedImageFormats()}
        if "svg" not in formats:
            raise RuntimeError("module SVG de Qt absent : icônes de l'interface manquantes")
        window = MainWindow()
        image = window.grab().toImage()
        view = window.views["reader"]
        if not view.open(pdf):
            raise RuntimeError("le lecteur n'a pas ouvert le PDF")
        reader = view.current
        reader.canvas.wait_rendered()
        rendered = reader.canvas.pages[0].pixmap is not None
        from docflex.core.annotations import AnnotSpec, Change, page_specs

        reader._record(Change(0, None, AnnotSpec("rect", rect=(100, 300, 200, 400), color=(1, 0, 0))))
        reader.undo()
        reader.redo()
        from docflex.core import editing

        line = editing.text_lines(reader.doc[0])[0]
        reader._content_edit(0, lambda p: editing.replace_line(p, line, "Ligne corrigée 12 €"))
        reader.undo()
        reader.redo()
        annotated = out_dir / "annoté.pdf"
        if not reader.save_to(annotated) or [s.kind for s in page_specs(reader.doc[0])] != ["rect"]:
            raise RuntimeError("annotation, annulation ou enregistrement en échec")
        with pymupdf.open(annotated) as d:
            if "Ligne corrigée 12 €" not in d[0].get_text():
                raise RuntimeError("modification du texte non enregistrée")
        from PySide6.QtPrintSupport import QPrinter

        from docflex.ui.printing import print_pages

        printer = QPrinter(QPrinter.HighResolution)  # impression « dans un PDF » : même chemin qu'une imprimante
        printer.setOutputFormat(QPrinter.PdfFormat)
        printer.setOutputFileName(str(out_dir / "imprimé.pdf"))
        if not print_pages(reader.doc, printer, [0]) or pymupdf.open(out_dir / "imprimé.pdf").page_count != 1:
            raise RuntimeError("impression en échec")
        view.close_document()
        window.close()
        if image.isNull() or not rendered:
            raise RuntimeError("la fenêtre ou la page n'a pas pu être dessinée")
        return f"fenêtre {image.width()}×{image.height()}, lecteur, annotations, édition, impression, SVG ok"

    check("pymupdf", lambda: pymupdf.VersionBind)
    check("PDF → PNG", conversion([pdf], "png", 2))
    check("PDF → JPG", conversion([pdf], "jpg", 2))
    check("PDF → DOCX", conversion([pdf], "docx", 1))
    check("PDF → TXT", conversion([pdf], "txt", 1))
    check("PNG → PDF", conversion([png], "pdf", 1))
    check("XLSX → CSV", conversion([xlsx], "csv", 1))

    def split_pdf():
        from docflex.core.split import plan_split, split_pdf

        return ", ".join(p.name for p in split_pdf(pdf, plan_split(2, "each")))

    def compress_pdf():
        from docflex.core.compress import compress_file

        photo = out_dir / "photo.png"
        Image.effect_noise((1600, 1200), 60).convert("RGB").save(photo)
        heavy = out_dir / "lourd.pdf"
        with pymupdf.open() as d:
            d.new_page().insert_image(pymupdf.Rect(36, 36, 559, 428), filename=photo)
            d.save(heavy)
        outputs, note = compress_file(heavy, None, "strong", grayscale=True)
        if not outputs:
            raise RuntimeError(note)
        return note

    def organize_pdf():
        from docflex.core.organize import PageRef, build_pdf

        out = build_pdf([pdf], [PageRef(0, 1).rotated(90), PageRef(0, 0).cropped((0, 0, 1, 0.5))],
                        out_dir / "organisé.pdf")
        with pymupdf.open(out) as d:
            if [page.rotation for page in d] != [90, 0] or d[1].rect.height > 430:
                raise RuntimeError("rotation ou recadrage non appliqués")
        return out.name

    def pdf_tools_check():
        from docflex.core import pdf_tools

        merged = pdf_tools.merge_pdfs([pdf, pdf], out_dir / "fusion.pdf")
        marked = pdf_tools.watermark_pdf(merged, out_dir, pdf_tools.Watermark("CONFIDENTIEL"))
        numbered = pdf_tools.number_pages(marked, out_dir, pdf_tools.Numbering("Page {n} sur {total}"))
        locked = pdf_tools.protect_pdf(numbered, out_dir, "selftest")
        opened = pdf_tools.unprotect_pdf(locked, out_dir, "selftest")
        with pymupdf.open(opened) as d:
            text = d[3].get_text()
            if d.page_count != 4 or "CONFIDENTIEL" not in text or "Page 4 sur 4" not in text:
                raise RuntimeError(f"résultat inattendu : {text!r}")
        stamped = pdf_tools.image_watermark_pdf(opened, out_dir, pdf_tools.ImageMark(png, position="bottom-right"))
        images = pdf_tools.extract_images(stamped, out_dir, skip_small=False)
        cleaned = pdf_tools.edit_metadata(stamped, out_dir, {"title": "Autotest"}, wipe=True)
        if len(images) != 1 or pdf_tools.read_metadata(cleaned) != {**dict.fromkeys(pdf_tools.METADATA_FIELDS, ""),
                                                                   "title": "Autotest"}:
            raise RuntimeError("logo, extraction d'images ou métadonnées incorrects")
        return "fusion, filigrane texte et logo, numérotation, mot de passe, images, métadonnées"

    def ocr_check():
        from docflex.core import pdf_tools

        with pymupdf.open() as source:
            page = source.new_page()
            page.insert_text((72, 120), "Bonjour le monde", fontsize=28)
            pix = page.get_pixmap(dpi=200)
        scan = out_dir / "scan.pdf"
        with pymupdf.open() as d:
            d.new_page().insert_image(pymupdf.Rect(0, 0, 595, 842), pixmap=pix)
            d.save(scan)
        out, _ = pdf_tools.ocr_pdf(scan, out_dir, "fra", 200)
        with pymupdf.open(out) as d:
            text = d[0].get_text().strip()
        if "Bonjour" not in text:
            raise RuntimeError(f"texte reconnu : {text!r}")
        return f"« {text} » (données : {pdf_tools.TESSDATA.name})"

    def extras_check():
        from docflex.core import pdf_extras

        doc_path = out_dir / "extras.pdf"
        with pymupdf.open() as d:
            d.new_page().insert_text((72, 72), "Contact : test@exemple.fr")
            d.new_page()
            d.new_page().add_text_annot((100, 100), "Note")
            d.save(doc_path)
        cleaned, removed = pdf_extras.remove_blank_pages(doc_path, out_dir)
        redacted, _ = pdf_extras.redact_pdf(doc_path, out_dir, [], ["email"])
        _, changes = pdf_extras.compare_pdfs(doc_path, redacted, out_dir)
        pdf_extras.flatten_pdf(doc_path, out_dir)
        pdf_extras.sign_pdf(doc_path, out_dir, png, 0, (0.6, 0.8, 0.9, 0.9))
        if removed != 2 or "exemple.fr" in pymupdf.open(redacted)[0].get_text() or not changes:
            raise RuntimeError("pages blanches, caviardage ou comparaison incorrects")
        return "pages blanches, caviardage, comparaison, aplatissement, signature"

    check("Découpage PDF", split_pdf)
    check("Outils complémentaires", extras_check)
    check("Outils PDF", pdf_tools_check)
    check("OCR", ocr_check)
    check("Organisation PDF", organize_pdf)
    check("Compression PDF", compress_pdf)
    check("Interface Qt", interface)
    lines.append(f"INFO  LibreOffice : {office.find_soffice() or 'non installé'}")

    (out_dir / "selftest.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 1 if failures else 0
