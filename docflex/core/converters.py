"""Moteur de conversion de fichiers.

Chaque `Conversion` relie un ensemble d'extensions source à un format cible.
L'interface choisit un format cible ; `convert_files` trouve, pour chaque
fichier, la conversion adaptée à son extension.
"""

from __future__ import annotations

import csv
import io
import logging
import re
import warnings
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path
from typing import Any, Callable

import pymupdf
from PIL import Image, ImageOps

from docflex.core import office
from docflex.core.tasks import Cancelled, Progress, Report
from docflex.utils.files import ext_of, unique_path

# pdf2docx importe l'ancien module `fitz`.
warnings.filterwarnings("ignore", message=".*fitz.*", category=FutureWarning)

@dataclass(frozen=True)
class Option:
    key: str
    label: str
    choices: tuple[tuple[str, Any], ...]  # (libellé affiché, valeur)
    default: int = 0

    @property
    def default_value(self) -> Any:
        return self.choices[self.default][1]


@dataclass(frozen=True)
class Conversion:
    sources: frozenset[str]
    target: str
    run: Callable[..., list[Path]]
    options: tuple[Option, ...] = ()
    # batch=True : `run` reçoit la liste de tous les fichiers du groupe.
    batch: bool = False
    needs_libreoffice: bool = False

    def available(self) -> bool:
        return not self.needs_libreoffice or office.is_available()


TARGET_LABELS = {
    "pdf": "PDF",
    "docx": "Word (.docx)",
    "odt": "OpenDocument texte (.odt)",
    "xlsx": "Excel (.xlsx)",
    "ods": "OpenDocument tableur (.ods)",
    "csv": "CSV",
    "pptx": "PowerPoint (.pptx)",
    "odp": "OpenDocument présentation (.odp)",
    "png": "Image PNG",
    "jpg": "Image JPG",
    "webp": "Image WebP",
    "tiff": "Image TIFF",
    "txt": "Texte brut (.txt)",
}

IMAGE_EXTS = frozenset({"png", "jpg", "jpeg", "bmp", "gif", "tif", "tiff", "webp"})
WORD_EXTS = frozenset({"doc", "docx", "odt", "rtf"})
SHEET_EXTS = frozenset({"xls", "xlsx", "ods"})
SLIDE_EXTS = frozenset({"ppt", "pptx", "odp"})
# Formats que PyMuPDF ouvre nativement (hors PDF et images).
MUPDF_DOC_EXTS = frozenset({"epub", "xps", "oxps", "fb2", "mobi", "cbz", "svg", "txt"})


# ---------------------------------------------------------------- options

DPI_OPTION = Option(
    "dpi", "Résolution", (("72 ppp (écran)", 72), ("150 ppp", 150), ("300 ppp (impression)", 300), ("600 ppp", 600)), 1
)
QUALITY_OPTION = Option("quality", "Qualité", (("Haute (95)", 95), ("Moyenne (85)", 85), ("Compacte (70)", 70)), 1)
CSV_SEP_OPTION = Option(
    "sep", "Séparateur", (("Point-virgule ;  (Excel FR)", ";"), ("Virgule ,", ","), ("Tabulation", "\t"))
)
CSV_DECIMAL_OPTION = Option("decimal", "Décimales", (("Virgule  1,5", ","), ("Point  1.5", ".")))
CSV_ENCODING_OPTION = Option(
    "encoding", "Encodage", (("UTF-8 (compatible Excel)", "utf-8-sig"), ("UTF-8 sans BOM", "utf-8"), ("Windows-1252", "cp1252"))
)
SHEETS_OPTION = Option("sheets", "Feuilles", (("Toutes (un CSV par feuille)", "all"), ("Première feuille", "first")))
NUMBERS_OPTION = Option("numbers", "Nombres", (("Convertir en nombres", True), ("Garder en texte", False)))
MERGE_OPTION = Option("merge", "Regroupement", (("Un seul PDF", True), ("Un PDF par image", False)))
PAGE_SIZE_OPTION = Option("page_size", "Format de page", (("Taille de l'image", "image"), ("A4", "a4")))


# ------------------------------------------------------------ PDF → images

def _pdf_to_images(src: Path, dest: Path, opts: dict, progress: Progress, fmt: str) -> list[Path]:
    outputs: list[Path] = []
    with pymupdf.open(src) as doc:
        if doc.needs_pass:
            raise ValueError("Le PDF est protégé par un mot de passe.")
        count = doc.page_count
        if count > 1:
            dest = unique_path(dest / f"{src.stem}_pages")
            dest.mkdir(parents=True)
        width = len(str(count))
        for i, page in enumerate(doc):
            progress(i / count, f"Page {i + 1}/{count}")
            pix = page.get_pixmap(dpi=opts["dpi"], alpha=False)
            name = f"{src.stem}.{fmt}" if count == 1 else f"{src.stem}_p{i + 1:0{width}d}.{fmt}"
            out = unique_path(dest / name)
            if fmt == "jpg":
                pix.save(out, jpg_quality=opts["quality"])
            else:
                pix.save(out)
            outputs.append(out)
    return outputs


def pdf_to_png(src, dest, opts, progress):
    return _pdf_to_images(src, dest, opts, progress, "png")


def pdf_to_jpg(src, dest, opts, progress):
    return _pdf_to_images(src, dest, opts, progress, "jpg")


# ----------------------------------------------------------- PDF → texte

def pdf_to_txt(src: Path, dest: Path, opts: dict, progress: Progress) -> list[Path]:
    with pymupdf.open(src) as doc:
        if doc.needs_pass:
            raise ValueError("Le PDF est protégé par un mot de passe.")
        parts = []
        for i, page in enumerate(doc):
            progress(i / doc.page_count, f"Page {i + 1}/{doc.page_count}")
            parts.append(page.get_text())
    out = unique_path(dest / f"{src.stem}.txt")
    out.write_text("\n\n".join(parts), encoding="utf-8")
    return [out]


# ------------------------------------------------------------ PDF → DOCX

def pdf_to_docx(src: Path, dest: Path, opts: dict, progress: Progress) -> list[Path]:
    from pdf2docx import Converter  # import lent : seulement à l'usage

    # pdf2docx configure le logger racine en INFO et journalise chaque page.
    logging.getLogger().setLevel(logging.WARNING)

    with pymupdf.open(src) as doc:
        if doc.needs_pass:
            raise ValueError("Le PDF est protégé par un mot de passe.")
        count = doc.page_count
    out = unique_path(dest / f"{src.stem}.docx")
    progress(0.0, f"Analyse de la mise en page ({count} page{'s' if count > 1 else ''})…")
    converter = Converter(str(src))
    try:
        converter.convert(str(out))
    finally:
        converter.close()
    return [out]


# ------------------------------------------------------- Images ↔ images

def _flatten_for_jpg(img: Image.Image) -> Image.Image:
    """JPG n'a pas de transparence : on la pose sur un fond blanc."""
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        background = Image.new("RGB", rgba.size, (255, 255, 255))
        background.paste(rgba, mask=rgba.getchannel("A"))
        return background
    return img.convert("RGB")


def _image_to(src: Path, dest: Path, opts: dict, progress: Progress, fmt: str) -> list[Path]:
    out = unique_path(dest / f"{src.stem}.{fmt}")
    with Image.open(src) as raw:
        img = ImageOps.exif_transpose(raw)
        if fmt == "jpg":
            _flatten_for_jpg(img).save(out, "JPEG", quality=opts["quality"], optimize=True)
        elif fmt == "webp":
            img.save(out, "WEBP", quality=opts["quality"])
        elif fmt == "tiff":
            img.save(out, "TIFF", compression="tiff_lzw")
        else:
            img.save(out, fmt.upper())
    return [out]


def _image_converter(fmt: str):
    return lambda src, dest, opts, progress: _image_to(src, dest, opts, progress, fmt)


# ------------------------------------------------------------ Images → PDF

A4 = pymupdf.paper_rect("a4")
A4_MARGIN = 28  # ~1 cm


def images_to_pdf(srcs: list[Path], dest: Path, opts: dict, progress: Progress) -> list[Path]:
    groups = [srcs] if opts["merge"] else [[s] for s in srcs]
    outputs: list[Path] = []
    done, total = 0, len(srcs)
    for group in groups:
        pdf = pymupdf.open()
        for src in group:
            progress(done / total, src.name)
            # Pillow normalise les formats que MuPDF lit mal (GIF, BMP 16 bits…).
            with Image.open(src) as raw:
                img = ImageOps.exif_transpose(raw)
                buf = io.BytesIO()
                (img if img.mode in ("RGB", "L", "RGBA") else img.convert("RGBA")).save(buf, "PNG")
                width, height = img.size
            if opts["page_size"] == "a4":
                portrait = height >= width
                rect = A4 if portrait else pymupdf.Rect(0, 0, A4.height, A4.width)
                page = pdf.new_page(width=rect.width, height=rect.height)
                frame = rect + (A4_MARGIN, A4_MARGIN, -A4_MARGIN, -A4_MARGIN)
            else:
                page = pdf.new_page(width=width, height=height)
                frame = page.rect
            page.insert_image(frame, stream=buf.getvalue(), keep_proportion=True)
            done += 1
        name = f"{group[0].stem} (fusion).pdf" if len(group) > 1 else f"{group[0].stem}.pdf"
        out = unique_path(dest / name)
        pdf.save(out, garbage=3, deflate=True)
        pdf.close()
        outputs.append(out)
    return outputs


# ------------------------------------------------- EPUB, XPS, TXT… → PDF

def mupdf_doc_to_pdf(src: Path, dest: Path, opts: dict, progress: Progress) -> list[Path]:
    with pymupdf.open(src) as doc:
        data = doc.convert_to_pdf()
    out = unique_path(dest / f"{src.stem}.pdf")
    with pymupdf.open("pdf", data) as pdf:
        pdf.save(out, garbage=3, deflate=True)
    return [out]


# ---------------------------------------------------------- XLSX → CSV

def _cell_to_text(value: Any, decimal: str) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "VRAI" if value else "FAUX"
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S") if value.time() != time(0) else value.strftime("%Y-%m-%d")
    if isinstance(value, (date, time)):
        return value.isoformat()
    if isinstance(value, float):
        text = repr(int(value)) if value.is_integer() else repr(value)
        return text.replace(".", decimal) if decimal != "." else text
    return str(value)


def _safe_filename(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]+', "_", name).strip() or "feuille"


def xlsx_to_csv(src: Path, dest: Path, opts: dict, progress: Progress) -> list[Path]:
    from openpyxl import load_workbook

    if opts["sep"] == "," and opts["decimal"] == ",":
        raise ValueError("Séparateur et décimales ne peuvent pas être tous deux la virgule.")
    wb = load_workbook(src, read_only=True, data_only=True)
    try:
        sheets = wb.worksheets if opts["sheets"] == "all" else wb.worksheets[:1]
        outputs = []
        for i, ws in enumerate(sheets):
            progress(i / len(sheets), f"Feuille « {ws.title} »")
            name = f"{src.stem}.csv" if len(sheets) == 1 else f"{src.stem}_{_safe_filename(ws.title)}.csv"
            out = unique_path(dest / name)
            with out.open("w", newline="", encoding=opts["encoding"], errors="replace") as fh:
                writer = csv.writer(fh, delimiter=opts["sep"])
                for row in ws.iter_rows(values_only=True):
                    writer.writerow(_cell_to_text(v, opts["decimal"]) for v in row)
            outputs.append(out)
        return outputs
    finally:
        wb.close()


# ---------------------------------------------------------- CSV → XLSX

_NUMBER_RE = re.compile(r"^-?(0|[1-9]\d*)([.,]\d+)?$")


def _read_text_any_encoding(src: Path) -> str:
    raw = src.read_bytes()
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1")


def _coerce(value: str) -> Any:
    """« 42 » → 42, « 3,5 » → 3.5 ; « 007 » ou « 12/03 » restent du texte."""
    if _NUMBER_RE.match(value):
        if "," in value or "." in value:
            return float(value.replace(",", "."))
        return int(value)
    return value


def csv_to_xlsx(src: Path, dest: Path, opts: dict, progress: Progress) -> list[Path]:
    from openpyxl import Workbook

    text = _read_text_any_encoding(src)
    try:
        dialect = csv.Sniffer().sniff(text[:20000], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    wb = Workbook(write_only=True)
    ws = wb.create_sheet(_safe_filename(src.stem)[:31])
    for row in csv.reader(io.StringIO(text), dialect):
        ws.append([_coerce(v) for v in row] if opts["numbers"] else row)
    out = unique_path(dest / f"{src.stem}.xlsx")
    wb.save(out)
    return [out]


# --------------------------------------------------- Bureautique (LibreOffice)

def _libreoffice_converter(fmt: str):
    def run(src: Path, dest: Path, opts: dict, progress: Progress) -> list[Path]:
        progress(0.0, "Conversion par LibreOffice…")
        return [office.convert(src, fmt, unique_path(dest / f"{src.stem}.{fmt}"))]

    return run


def _libreoffice(sources: frozenset[str], targets: list[str]) -> list[Conversion]:
    return [
        Conversion(sources - {t}, t, _libreoffice_converter(t), needs_libreoffice=True) for t in targets
    ]


# ------------------------------------------------------------- Registre

CONVERSIONS: list[Conversion] = [
    Conversion(frozenset({"pdf"}), "png", pdf_to_png, (DPI_OPTION,)),
    Conversion(frozenset({"pdf"}), "jpg", pdf_to_jpg, (DPI_OPTION, QUALITY_OPTION)),
    Conversion(frozenset({"pdf"}), "docx", pdf_to_docx),
    Conversion(frozenset({"pdf"}), "txt", pdf_to_txt),
    Conversion(IMAGE_EXTS, "pdf", images_to_pdf, (MERGE_OPTION, PAGE_SIZE_OPTION), batch=True),
    Conversion(IMAGE_EXTS - {"png"}, "png", _image_converter("png")),
    Conversion(IMAGE_EXTS - {"jpg", "jpeg"}, "jpg", _image_converter("jpg"), (QUALITY_OPTION,)),
    Conversion(IMAGE_EXTS - {"webp"}, "webp", _image_converter("webp"), (QUALITY_OPTION,)),
    Conversion(IMAGE_EXTS - {"tif", "tiff"}, "tiff", _image_converter("tiff")),
    Conversion(MUPDF_DOC_EXTS, "pdf", mupdf_doc_to_pdf),
    Conversion(
        frozenset({"xlsx", "xlsm"}),
        "csv",
        xlsx_to_csv,
        (SHEETS_OPTION, CSV_SEP_OPTION, CSV_DECIMAL_OPTION, CSV_ENCODING_OPTION),
    ),
    Conversion(frozenset({"csv"}), "xlsx", csv_to_xlsx, (NUMBERS_OPTION,)),
    *_libreoffice(WORD_EXTS | SHEET_EXTS | SLIDE_EXTS, ["pdf"]),
    *_libreoffice(WORD_EXTS, ["docx", "odt"]),
    *_libreoffice(SHEET_EXTS, ["xlsx", "ods"]),
    *_libreoffice(frozenset({"xls", "ods"}), ["csv"]),
    *_libreoffice(SLIDE_EXTS, ["pptx", "odp"]),
]


def find_conversion(src_ext: str, target: str) -> Conversion | None:
    for conv in CONVERSIONS:
        if conv.target == target and src_ext in conv.sources:
            return conv
    return None


def targets_for(paths: list[Path], include_unavailable: bool = True) -> list[str]:
    """Formats cibles possibles pour TOUS les fichiers donnés, dans l'ordre du registre."""
    if not paths:
        return []
    exts = {ext_of(p) for p in paths}
    result: list[str] = []
    for conv in CONVERSIONS:
        t = conv.target
        if t in result:
            continue
        convs = [find_conversion(e, t) for e in exts]
        if all(convs) and (include_unavailable or all(c.available() for c in convs)):
            result.append(t)
    return result


def options_for(paths: list[Path], target: str) -> list[Option]:
    seen: dict[str, Option] = {}
    for ext in {ext_of(p) for p in paths}:
        conv = find_conversion(ext, target)
        for opt in conv.options if conv else ():
            seen.setdefault(opt.key, opt)
    return list(seen.values())


def convert_files(
    paths: list[Path],
    target: str,
    opts: dict | None = None,
    out_dir: Path | None = None,
    progress: Callable[[float, str], None] = lambda f, m: None,
) -> Report:
    """Convertit `paths` vers `target`.

    `out_dir=None` : chaque résultat est écrit à côté de son fichier source.
    `progress(fraction_globale, message)` peut lever `Cancelled`.
    """
    report = Report()

    # Regroupe les fichiers par conversion, en gardant l'ordre d'origine.
    groups: dict[int, tuple[Conversion, list[Path]]] = {}
    for path in paths:
        conv = find_conversion(ext_of(path), target)
        if conv is None:
            report.errors.append((path, f"Conversion vers {TARGET_LABELS.get(target, target)} impossible."))
            continue
        groups.setdefault(id(conv), (conv, []))[1].append(path)

    # Une « unité » de travail : un fichier, ou un groupe entier en mode batch.
    units: list[tuple[Conversion, list[Path]]] = []
    for conv, files in groups.values():
        units.extend([(conv, files)] if conv.batch else [(conv, [f]) for f in files])

    total = sum(len(files) for _, files in units) or 1
    done = 0
    for conv, files in units:
        label = files[0].name if len(files) == 1 else f"{len(files)} fichiers"
        weight = len(files)

        def sub_progress(fraction: float, message: str | None, _label=label, _done=done, _w=weight):
            progress((_done + fraction * _w) / total, f"{_label} — {message}" if message else _label)

        sub_progress(0.0, None)
        values = {o.key: (opts or {}).get(o.key, o.default_value) for o in conv.options}
        dest = out_dir or files[0].parent
        try:
            if not conv.available():
                raise RuntimeError(
                    "LibreOffice est requis pour cette conversion. "
                    "Installez-le gratuitement depuis libreoffice.org puis relancez DocFlex."
                )
            produced = conv.run(files if conv.batch else files[0], dest, values, sub_progress)
            report.outputs.extend(produced)
        except Cancelled:
            raise
        except Exception as exc:  # une erreur sur un fichier n'arrête pas le lot
            for f in files:
                report.errors.append((f, str(exc) or exc.__class__.__name__))
        done += weight
        progress(done / total, label)
    return report
