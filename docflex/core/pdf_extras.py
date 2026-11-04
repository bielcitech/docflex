"""Outils PDF complémentaires : pages blanches, caviardage, comparaison, aplatissement, signature visuelle."""

from __future__ import annotations

import difflib
import io
import re
from dataclasses import dataclass
from pathlib import Path

import pymupdf
from PIL import Image, ImageOps

from docflex.core.organize import Box
from docflex.core.pdf_tools import _save, _upright
from docflex.core.split import open_pdf
from docflex.core.tasks import Progress, no_progress
from docflex.utils.files import unique_path

# ------------------------------------------------------------ pages blanches

# Part maximale de pixels sombres pour qu'une page soit jugée blanche.
BLANK_SENSITIVITY = {
    "strict": ("Stricte : pages totalement vides", 0.0005),
    "tolerant": ("Tolérante : scans avec poussières ou traces", 0.004),
}


def ink_ratio(page: pymupdf.Page) -> float:
    """Part des pixels nettement plus sombres que le blanc, sur un rendu en niveaux de gris réduit."""
    pix = page.get_pixmap(dpi=40, colorspace=pymupdf.csGRAY, alpha=False)
    dark = sum(1 for value in pix.samples if value < 200)
    return dark / max(1, pix.width * pix.height)


def is_blank(page: pymupdf.Page, max_ink: float) -> bool:
    """Une page portant du texte n'est jamais blanche, même un simple titre ; le seuil d'encre ne
    juge que les pages sans texte (scans)."""
    return not page.get_text().strip() and ink_ratio(page) <= max_ink


def remove_blank_pages(src: Path, dest: Path, max_ink: float = 0.0005,
                       progress: Progress = no_progress) -> tuple[Path, int]:
    with open_pdf(src) as doc:
        blank = []
        for i, page in enumerate(doc):
            progress(i / doc.page_count * 0.9, f"Analyse de la page {i + 1}/{doc.page_count}")
            if is_blank(page, max_ink):
                blank.append(i)
        if not blank:
            raise ValueError("Aucune page blanche détectée.")
        if len(blank) == doc.page_count:
            raise ValueError("Toutes les pages semblent blanches : rien n'a été retiré.")
        doc.delete_pages(blank)
        return _save(doc, unique_path(dest / f"{src.stem}_sans_pages_blanches.pdf")), len(blank)


# ---------------------------------------------------------------- caviardage

REDACT_PATTERNS = {
    "email": ("Adresses e-mail", r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"),
    "phone": ("Numéros de téléphone (France)", r"(?:\+33\s?|0)[1-9](?:[\s.-]?\d{2}){4}"),
    "iban": ("IBAN", r"\b[A-Z]{2}\d{2}(?:\s?[A-Z0-9]{4}){2,7}(?:\s?[A-Z0-9]{1,3})?\b"),
}


def redact_pdf(src: Path, dest: Path, terms: list[str], patterns: list[str] = (),
               progress: Progress = no_progress) -> tuple[Path, int]:
    """Supprime définitivement le texte visé (et ce qu'il recouvre) sous des rectangles noirs.

    Les termes sont cherchés sans tenir compte des majuscules. Le texte retiré n'existe plus
    dans le fichier : il ne peut pas être récupéré par copier-coller ni par extraction.
    """
    terms = [t.strip() for t in terms if t.strip()]
    regexes = [re.compile(REDACT_PATTERNS[key][1]) for key in patterns]
    if not terms and not regexes:
        raise ValueError("Indiquez des mots à masquer ou un type d'information.")
    count = 0
    with open_pdf(src) as doc:
        for i, page in enumerate(doc):
            progress(i / doc.page_count, f"Page {i + 1}/{doc.page_count}")
            text = page.get_text()
            targets = set(terms)
            for regex in regexes:
                targets.update(m.group(0) for m in regex.finditer(text))
            for target in targets:
                for quad in page.search_for(target, quads=True):
                    page.add_redact_annot(quad, fill=(0, 0, 0))
                    count += 1
            page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_PIXELS)
        if count == 0:
            raise ValueError("Aucune occurrence trouvée. Pour un document numérisé, faites d'abord un OCR.")
        return _save(doc, unique_path(dest / f"{src.stem}_caviardé.pdf"), clean=True), count


# ---------------------------------------------------------------- comparaison

REMOVED = (0.95, 0.45, 0.45)  # rouge : texte retiré (version d'origine)
ADDED = (0.45, 0.85, 0.5)     # vert : texte ajouté (nouvelle version)


@dataclass(frozen=True)
class _Word:
    page: int
    rect: pymupdf.Rect
    text: str


def _words(doc: pymupdf.Document) -> list[_Word]:
    return [
        _Word(i, pymupdf.Rect(w[:4]), w[4])
        for i, page in enumerate(doc)
        for w in sorted(page.get_text("words"), key=lambda w: (w[5], w[6], w[7]))  # ordre de lecture
    ]


def compare_pdfs(old: Path, new: Path, dest: Path, progress: Progress = no_progress) -> tuple[list[Path], int]:
    """Surligne les différences de texte : retraits en rouge dans l'ancienne version, ajouts en vert dans
    la nouvelle. La comparaison porte sur tout le document : un paragraphe qui change de page n'est pas
    compté comme modifié. Retourne ([ancienne annotée, nouvelle annotée], nombre de différences)."""
    with open_pdf(old) as a, open_pdf(new) as b:
        progress(0.1, "Lecture du texte…")
        words_a, words_b = _words(a), _words(b)
        if not words_a and not words_b:
            raise ValueError("Ces PDF ne contiennent pas de texte. Pour des documents numérisés, faites d'abord un OCR.")
        progress(0.3, "Comparaison…")
        matcher = difflib.SequenceMatcher(None, [w.text for w in words_a], [w.text for w in words_b], autojunk=False)
        changes = 0
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "equal":
                continue
            changes += 1
            _highlight(a, words_a[i1:i2], REMOVED)
            _highlight(b, words_b[j1:j2], ADDED)
        if changes == 0:
            return [], 0
        progress(0.9, "Écriture…")
        out_a = _save(a, unique_path(dest / f"{old.stem}_différences.pdf"))
        out_b = _save(b, unique_path(dest / f"{new.stem}_différences.pdf"))
    return [out_a, out_b], changes


def _highlight(doc: pymupdf.Document, words: list[_Word], color) -> None:
    pages: dict[int, pymupdf.Page] = {}  # garder la page en vie : sinon l'annotation n'y est plus rattachée
    for word in words:
        page = pages.setdefault(word.page, doc[word.page])
        annot = page.add_highlight_annot(word.rect)
        annot.set_colors(stroke=color)
        annot.update()


# --------------------------------------------------------------- aplatissement

def flatten_pdf(src: Path, dest: Path, progress: Progress = no_progress) -> Path:
    """Intègre formulaires et annotations au contenu des pages : ils ne sont plus modifiables."""
    with open_pdf(src) as doc:
        if not any(page.first_annot or page.first_widget for page in doc):
            raise ValueError("Rien à aplatir : ce PDF n'a ni formulaire ni annotation.")
        progress(0.5, "Aplatissement…")
        doc.bake(annots=True, widgets=True)
        return _save(doc, unique_path(dest / f"{src.stem}_aplati.pdf"))


# ------------------------------------------------------------ signature visuelle

def signature_png(path: Path, remove_white: bool = True) -> bytes:
    """Image de signature en PNG ; avec `remove_white`, le fond clair (papier photographié) devient transparent."""
    with Image.open(path) as raw:
        img = ImageOps.exif_transpose(raw).convert("RGBA")
    if remove_white:
        luminance = img.convert("L")
        # Opaque sous 170, transparent au-dessus de 225, fondu entre les deux : bords d'encre adoucis.
        fade = luminance.point(lambda v: 255 if v < 170 else 0 if v > 225 else round(255 * (225 - v) / 55))
        alpha = Image.composite(fade, Image.new("L", img.size, 0), img.getchannel("A"))
        img.putalpha(alpha)
        bbox = alpha.getbbox()
        if bbox:  # on retire les marges vides autour du tracé
            img = img.crop(bbox)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def sign_pdf(src: Path, dest: Path, image: Path, page_index: int, box: Box, all_pages: bool = False,
             remove_white: bool = True, progress: Progress = no_progress) -> Path:
    """Appose l'image de signature dans `box` (fractions de la page affichée). Ce n'est pas une
    signature électronique certifiée : c'est l'équivalent d'une signature scannée."""
    if not image.is_file():
        raise ValueError("Choisissez l'image de la signature.")
    data = signature_png(image, remove_white)
    with open_pdf(src) as doc:
        targets = range(doc.page_count) if all_pages else [page_index]
        xref = 0
        for n, i in enumerate(targets):
            progress(n / len(targets), f"Page {i + 1}")
            page = doc[i]
            _upright(page)
            r = page.rect
            rect = pymupdf.Rect(r.x0 + box[0] * r.width, r.y0 + box[1] * r.height,
                                r.x0 + box[2] * r.width, r.y0 + box[3] * r.height)
            if xref:
                page.insert_image(rect, xref=xref, keep_proportion=True)
            else:
                xref = page.insert_image(rect, stream=data, keep_proportion=True)
        return _save(doc, unique_path(dest / f"{src.stem}_signé.pdf"))
