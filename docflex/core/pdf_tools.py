"""Outils PDF : fusion, filigrane (texte ou image), numérotation, mot de passe, OCR,
extraction d'images, métadonnées.

Filigrane, numérotation et OCR ramènent d'abord les pages tournées à une
rotation nulle (`remove_rotation` : rendu identique). Tout ce qui est ajouté
s'écrit alors dans le repère de la page telle qu'on la voit.
"""

from __future__ import annotations

import io
import math
import secrets
from dataclasses import dataclass
from pathlib import Path

import pymupdf
from PIL import Image, ImageOps

from docflex.core.split import METADATA_KEYS, open_pdf, parse_page_spec
from docflex.core.tasks import Progress, no_progress
from docflex.utils.files import unique_path

TESSDATA = Path(__file__).resolve().parent.parent / "resources" / "tessdata"
OCR_LANGUAGES = {"fra": "Français", "eng": "Anglais", "fra+eng": "Français et anglais"}

FONT = pymupdf.Font("helv")  # Helvetica : présente dans tout lecteur PDF, accents compris


def _upright(page: pymupdf.Page) -> None:
    if page.rotation:
        page.remove_rotation()
        # remove_rotation place sa transformation en tête du contenu sans l'isoler (q … Q) :
        # sans cela, tout ce qu'on ajoute ensuite serait tourné aussi.
        page.wrap_contents()


def _selected_pages(doc: pymupdf.Document, spec: str) -> list[int]:
    """Pages à traiter : toutes si `spec` est vide, sinon « 1-3, 5 »…"""
    if not spec.strip():
        return list(range(doc.page_count))
    return sorted({i for a, b in parse_page_spec(spec, doc.page_count) for i in range(a, b + 1)})


def _save(doc: pymupdf.Document, out: Path, **kwargs) -> Path:
    doc.save(out, garbage=3, deflate=True, **kwargs)
    return out


# ------------------------------------------------------------------ fusion

def merge_pdfs(paths: list[Path], out: Path, bookmarks: bool = True, progress: Progress = no_progress) -> Path:
    """Fusionne dans l'ordre donné. Avec `bookmarks`, un signet par fichier regroupe ses propres signets."""
    if len(paths) < 2:
        raise ValueError("Ajoutez au moins deux PDF à fusionner.")
    toc: list = []
    with pymupdf.open() as merged:
        for i, path in enumerate(paths):
            progress(i / len(paths), path.name)
            with open_pdf(path) as doc:
                start = merged.page_count
                merged.insert_pdf(doc)
                if bookmarks:
                    toc.append([1, path.stem, start + 1])
                    toc += [[level + 1, title, start + page] for level, title, page, *_ in doc.get_toc()]
                if i == 0:
                    merged.set_metadata({k: v for k, v in doc.metadata.items() if k in METADATA_KEYS and v})
        merged.set_toc(toc)
        progress(0.95, "Écriture…")
        return _save(merged, out)


# --------------------------------------------------------------- filigrane

@dataclass(frozen=True)
class Watermark:
    text: str
    diagonal: bool = True
    opacity: float = 0.2
    color: tuple[float, float, float] = (0.5, 0.5, 0.5)
    pages: str = ""  # vide : toutes


def watermark_pdf(src: Path, dest: Path, mark: Watermark, progress: Progress = no_progress) -> Path:
    text = mark.text.strip()
    if not text:
        raise ValueError("Saisissez le texte du filigrane.")
    with open_pdf(src) as doc:
        pages = _selected_pages(doc, mark.pages)
        for n, i in enumerate(pages):
            progress(n / len(pages), f"Page {i + 1}")
            page = doc[i]
            _upright(page)
            rect = page.rect
            # Taille ajustée pour que le texte occupe ~75 % de la diagonale (ou de la largeur).
            span = (rect.width ** 2 + rect.height ** 2) ** 0.5 if mark.diagonal else rect.width
            fontsize = min(0.75 * span / FONT.text_length(text, 1), rect.height / 4)
            width = FONT.text_length(text, fontsize)
            center = (rect.tl + rect.br) / 2
            writer = pymupdf.TextWriter(rect, opacity=mark.opacity, color=mark.color)
            writer.append(center + (-width / 2, fontsize * 0.35), text, font=FONT, fontsize=fontsize)
            # Diagonale montante (bas-gauche → haut-droit). Le « morph » s'applique dans le repère PDF
            # natif (axe y vers le haut) : un angle positif fait donc monter le texte.
            degrees = math.degrees(math.atan2(rect.height, rect.width)) if mark.diagonal else 0
            writer.write_text(page, morph=(center, pymupdf.Matrix(degrees)))
        return _save(doc, unique_path(dest / f"{src.stem}_filigrane.pdf"))


IMAGE_POSITIONS = {
    "center": "Au centre",
    "top-left": "En haut, à gauche",
    "top-right": "En haut, à droite",
    "bottom-left": "En bas, à gauche",
    "bottom-right": "En bas, à droite",
}


@dataclass(frozen=True)
class ImageMark:
    image: Path
    scale: float = 0.3          # largeur de l'image / largeur de la page
    position: str = "center"
    opacity: float = 0.25
    pages: str = ""


def _translucent_png(path: Path, opacity: float) -> tuple[bytes, float]:
    """Image en PNG avec la transparence demandée ; retourne aussi son rapport hauteur/largeur."""
    with Image.open(path) as raw:
        img = ImageOps.exif_transpose(raw).convert("RGBA")
    alpha = img.getchannel("A").point(lambda a: round(a * opacity))
    img.putalpha(alpha)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue(), img.height / img.width


def image_watermark_pdf(src: Path, dest: Path, mark: ImageMark, progress: Progress = no_progress) -> Path:
    if not mark.image.is_file():
        raise ValueError("Choisissez l'image à apposer.")
    data, ratio = _translucent_png(mark.image, mark.opacity)
    with open_pdf(src) as doc:
        pages = _selected_pages(doc, mark.pages)
        xref = 0  # image insérée une seule fois, puis réutilisée sur chaque page
        for n, i in enumerate(pages):
            progress(n / len(pages), f"Page {i + 1}")
            page = doc[i]
            _upright(page)
            rect = page.rect
            width = rect.width * mark.scale
            height = width * ratio
            if height > rect.height * 0.9:  # image très haute : on la limite à la page
                height = rect.height * 0.9
                width = height / ratio
            vertical, _, horizontal = mark.position.partition("-")
            x = {"left": rect.x0 + MARGIN, "right": rect.x1 - MARGIN - width}.get(
                horizontal, (rect.x0 + rect.x1 - width) / 2)
            y = {"top": rect.y0 + MARGIN, "bottom": rect.y1 - MARGIN - height}.get(
                vertical, (rect.y0 + rect.y1 - height) / 2)
            target = pymupdf.Rect(x, y, x + width, y + height)
            if xref:
                page.insert_image(target, xref=xref)
            else:
                xref = page.insert_image(target, stream=data)
        return _save(doc, unique_path(dest / f"{src.stem}_filigrane.pdf"))


# ------------------------------------------------------------ numérotation

NUMBER_FORMATS = {
    "{n}": "1, 2, 3…",
    "Page {n}": "Page 1",
    "Page {n} sur {total}": "Page 1 sur 12",
    "{n} / {total}": "1 / 12",
}
POSITIONS = {
    "bottom-center": "En bas, au centre",
    "bottom-right": "En bas, à droite",
    "bottom-left": "En bas, à gauche",
    "top-center": "En haut, au centre",
    "top-right": "En haut, à droite",
}
MARGIN = 28  # points (~1 cm) depuis le bord


@dataclass(frozen=True)
class Numbering:
    template: str = "{n}"
    position: str = "bottom-center"
    start: int = 1
    skip_first: bool = False
    fontsize: float = 10


def number_pages(src: Path, dest: Path, numbering: Numbering, progress: Progress = no_progress) -> Path:
    with open_pdf(src) as doc:
        total = numbering.start + doc.page_count - 1
        for i, page in enumerate(doc):
            if i == 0 and numbering.skip_first:
                continue
            progress(i / doc.page_count, f"Page {i + 1}")
            _upright(page)
            text = numbering.template.format(n=numbering.start + i, total=total)
            width = FONT.text_length(text, numbering.fontsize)
            rect = page.rect
            vertical, horizontal = numbering.position.split("-")
            x = {"left": rect.x0 + MARGIN, "center": (rect.x0 + rect.x1 - width) / 2,
                 "right": rect.x1 - MARGIN - width}[horizontal]
            y = rect.y1 - MARGIN if vertical == "bottom" else rect.y0 + MARGIN + numbering.fontsize
            page.insert_text((x, y), text, fontsize=numbering.fontsize, fontname="helv", color=(0.2, 0.2, 0.2))
        return _save(doc, unique_path(dest / f"{src.stem}_numéroté.pdf"))


# ------------------------------------------------------------- mot de passe

@dataclass(frozen=True)
class Permissions:
    print: bool = True
    copy: bool = False
    modify: bool = False

    @property
    def flags(self) -> int:
        flags = pymupdf.PDF_PERM_ACCESSIBILITY  # lecteurs d'écran toujours autorisés
        if self.print:
            flags |= pymupdf.PDF_PERM_PRINT | pymupdf.PDF_PERM_PRINT_HQ
        if self.copy:
            flags |= pymupdf.PDF_PERM_COPY
        if self.modify:
            flags |= pymupdf.PDF_PERM_MODIFY | pymupdf.PDF_PERM_ANNOTATE | pymupdf.PDF_PERM_FORM | pymupdf.PDF_PERM_ASSEMBLE
        return flags


def protect_pdf(src: Path, dest: Path, password: str, permissions: Permissions = Permissions(),
                progress: Progress = no_progress) -> Path:
    """Chiffre en AES-256. Le mot de passe propriétaire est aléatoire : les restrictions ne peuvent
    pas être levées avec le seul mot de passe d'ouverture."""
    if not password:
        raise ValueError("Saisissez un mot de passe.")
    with open_pdf(src) as doc:
        progress(0.5, "Chiffrement…")
        return _save(
            doc, unique_path(dest / f"{src.stem}_protégé.pdf"),
            encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw=password,
            owner_pw=secrets.token_urlsafe(24), permissions=permissions.flags,
        )


def unprotect_pdf(src: Path, dest: Path, password: str, progress: Progress = no_progress) -> Path:
    with pymupdf.open(src) as doc:
        if not doc.is_pdf:
            raise ValueError("Ce fichier n'est pas un PDF.")
        if not doc.is_encrypted and not doc.needs_pass:
            raise ValueError("Ce PDF n'est pas protégé.")
        if doc.needs_pass and not doc.authenticate(password):
            raise ValueError("Mot de passe incorrect.")
        progress(0.5, "Déchiffrement…")
        return _save(doc, unique_path(dest / f"{src.stem}_déverrouillé.pdf"), encryption=pymupdf.PDF_ENCRYPT_NONE)


# --------------------------------------------------------------------- OCR

def ocr_pdf(src: Path, dest: Path, language: str = "fra", dpi: int = 300, skip_text_pages: bool = True,
            progress: Progress = no_progress) -> tuple[Path, int]:
    """Ajoute une couche de texte invisible (sélectionnable, cherchable) sous les pages numérisées.

    L'image d'origine est conservée telle quelle. Retourne (fichier, nombre de pages reconnues).
    """
    done = 0
    with open_pdf(src) as doc:
        for i, page in enumerate(doc):
            progress(i / doc.page_count, f"Page {i + 1}/{doc.page_count} — reconnaissance du texte…")
            if skip_text_pages and len(page.get_text().strip()) > 20:
                continue
            _upright(page)
            textpage = page.get_textpage_ocr(language=language, dpi=dpi, full=True, tessdata=str(TESSDATA))
            for x0, y0, x1, y1, word, *_ in page.get_text("words", textpage=textpage):
                _invisible_word(page, pymupdf.Rect(x0, y0, x1, y1), word)
            done += 1
        if done == 0:
            raise ValueError("Toutes les pages contiennent déjà du texte : rien à reconnaître.")
        progress(0.97, "Écriture…")
        return _save(doc, unique_path(dest / f"{src.stem}_ocr.pdf")), done


def _invisible_word(page: pymupdf.Page, box: pymupdf.Rect, word: str) -> None:
    """Mot invisible (mode de rendu 3) étiré pour couvrir exactement la zone reconnue."""
    fontsize = box.height * 0.9
    length = FONT.text_length(word, fontsize)
    if length <= 0 or box.width <= 0:
        return
    origin = pymupdf.Point(box.x0, box.y1 - box.height * 0.2)
    page.insert_text(origin, word, fontsize=fontsize, fontname="helv", render_mode=3,
                     morph=(origin, pymupdf.Matrix(box.width / length, 1)))


# ------------------------------------------------------- extraction d'images

MIN_IMAGE_SIDE = 48  # px : en dessous, puces, filets et icônes sont ignorés


def extract_images(src: Path, dest: Path, skip_small: bool = True, progress: Progress = no_progress) -> list[Path]:
    """Enregistre chaque image du PDF une seule fois, dans « <nom>_images/ ».

    Les JPEG sont copiés tels quels (aucune perte) ; les autres formats sont convertis
    en PNG, transparence comprise.
    """
    outputs: list[Path] = []
    seen: set[int] = set()
    folder: Path | None = None
    with open_pdf(src) as doc:
        for i, page in enumerate(doc):
            progress(i / doc.page_count, f"Page {i + 1}/{doc.page_count}")
            for n, info in enumerate(page.get_images(full=True), start=1):
                xref, smask, width, height = info[0], info[1], info[2], info[3]
                if xref in seen or (skip_small and min(width, height) < MIN_IMAGE_SIDE):
                    continue
                seen.add(xref)
                if folder is None:
                    folder = unique_path(dest / f"{src.stem}_images")
                    folder.mkdir(parents=True)
                base = folder / f"{src.stem}_p{i + 1}_{n}"
                outputs.append(_write_image(doc, xref, smask, base))
    if not outputs:
        raise ValueError("Aucune image à extraire dans ce PDF." if not seen else "Seulement de très petites images.")
    return outputs


def _write_image(doc: pymupdf.Document, xref: int, smask: int, base: Path) -> Path:
    extracted = doc.extract_image(xref)
    if extracted["ext"] in ("jpeg", "jpg") and not smask and extracted.get("colorspace", 3) in (1, 3):
        out = base.with_suffix(".jpg")
        out.write_bytes(extracted["image"])
        return out
    pix = pymupdf.Pixmap(doc, xref)
    if pix.colorspace and pix.colorspace.n not in (1, 3):  # CMJN, etc. : PNG n'accepte que gris et RVB
        pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
    if smask:
        pix = pymupdf.Pixmap(pix, pymupdf.Pixmap(doc, smask))  # masque de transparence
    out = base.with_suffix(".png")
    pix.save(out)
    return out


# --------------------------------------------------------------- métadonnées

METADATA_FIELDS = {
    "title": "Titre",
    "author": "Auteur",
    "subject": "Sujet",
    "keywords": "Mots-clés",
    "creator": "Application d'origine",
    "producer": "Producteur PDF",
}


def read_metadata(src: Path) -> dict[str, str]:
    with open_pdf(src) as doc:
        return {key: doc.metadata.get(key) or "" for key in METADATA_FIELDS}


def edit_metadata(src: Path, dest: Path, values: dict[str, str | None], wipe: bool = False,
                  progress: Progress = no_progress) -> Path:
    """`values[clé]` : nouveau texte ("" pour effacer), ou None pour garder la valeur d'origine.

    `wipe` repart de zéro : toutes les informations d'origine, dates comprises, sont retirées.
    Les métadonnées XMP sont toujours supprimées : sinon certains lecteurs afficheraient encore
    les anciennes valeurs.
    """
    with open_pdf(src) as doc:
        progress(0.3, "Mise à jour des informations…")
        # set_metadata ne touche qu'aux clés fournies : on les passe toutes, vides pour les retirer.
        keys = [k for k in doc.metadata if k not in ("format", "encryption")]
        meta = {k: "" if wipe else (doc.metadata.get(k) or "") for k in keys}
        for key, value in values.items():
            if value is not None:
                meta[key] = value.strip()
        doc.set_metadata(meta)
        doc.del_xml_metadata()
        return _save(doc, unique_path(dest / f"{src.stem}_métadonnées.pdf"))
