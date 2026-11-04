"""Réorganisation de pages : ordre, rotation, suppression, duplication, fusion.

Le document en cours d'édition est une simple liste de `PageRef` ; rien n'est
écrit tant que `build_pdf` n'est pas appelé.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path

import pymupdf
from PIL import Image

from docflex.core.split import METADATA_KEYS, open_pdf
from docflex.core.tasks import Progress, no_progress

THUMB_WIDTH = 220  # px ; assez pour le zoom maximal de la grille
BLANK = -1         # `PageRef.source` d'une page blanche insérée
A4_SIZE = (595.0, 842.0)  # points ; format d'une page blanche sans page voisine

# Cadre (x0, y0, x1, y1) en fractions de la page, origine en haut à gauche.
Box = tuple[float, float, float, float]
FULL: Box = (0.0, 0.0, 1.0, 1.0)


@dataclass(frozen=True)
class PageRef:
    source: int    # indice dans la liste des PDF sources, ou BLANK
    index: int     # page d'origine, à partir de 0
    rotation: int = 0  # rotation ajoutée par l'utilisateur : 0, 90, 180 ou 270
    # Recadrage, mesuré sur la page telle qu'elle s'affiche AVANT la rotation utilisateur
    # (même repère que la miniature). None = pleine page.
    crop: Box | None = None
    size: tuple[float, float] | None = None  # pages blanches : largeur, hauteur en points

    @property
    def is_blank(self) -> bool:
        return self.source == BLANK

    def rotated(self, degrees: int) -> PageRef:
        return replace(self, rotation=(self.rotation + degrees) % 360)

    def cropped(self, box: Box | None) -> PageRef:
        """`box` est mesuré sur la page telle qu'elle s'affiche, rotation utilisateur comprise."""
        if self.is_blank:
            return self
        if box is None or is_full(box):
            return replace(self, crop=None)
        return replace(self, crop=rotate_box(box, -self.rotation))

    def display_crop(self) -> Box:
        """Cadre actuel vu à l'écran (rotation utilisateur comprise)."""
        return rotate_box(self.crop or FULL, self.rotation)


def blank_page(width: float, height: float) -> PageRef:
    return PageRef(BLANK, 0, size=(width, height))


def is_full(box: Box, tolerance: float = 0.002) -> bool:
    return all(abs(a - b) <= tolerance for a, b in zip(box, FULL))


def rotate_box(box: Box, degrees: int) -> Box:
    """Cadre d'une page tournée de `degrees` (sens horaire) : même zone, nouveau repère."""
    def turn(x: float, y: float) -> tuple[float, float]:
        for _ in range((degrees // 90) % 4):
            x, y = 1 - y, x  # un quart de tour horaire
        return x, y

    (ax, ay), (bx, by) = turn(box[0], box[1]), turn(box[2], box[3])
    return (min(ax, bx), min(ay, by), max(ax, bx), max(ay, by))


def pages_of(source: int, page_count: int) -> list[PageRef]:
    return [PageRef(source, i) for i in range(page_count)]


# ------------------------------------------------- opérations d'édition
# Fonctions pures : (pages, positions sélectionnées) → (nouvelles pages, positions à sélectionner).

Edit = tuple[list[PageRef], list[int]]


def rotate_pages(pages: list[PageRef], rows: list[int], degrees: int) -> Edit:
    chosen = set(rows)
    return [p.rotated(degrees) if i in chosen else p for i, p in enumerate(pages)], sorted(chosen)


def delete_pages(pages: list[PageRef], rows: list[int]) -> Edit:
    chosen = set(rows)
    return [p for i, p in enumerate(pages) if i not in chosen], []


def duplicate_pages(pages: list[PageRef], rows: list[int]) -> Edit:
    """Chaque page sélectionnée est copiée juste après elle ; les copies sont sélectionnées."""
    chosen, result, copies = set(rows), [], []
    for i, page in enumerate(pages):
        result.append(page)
        if i in chosen:
            result.append(page)
            copies.append(len(result) - 1)
    return result, copies


def reverse_pages(pages: list[PageRef], rows: list[int]) -> Edit:
    """Inverse les pages sélectionnées entre elles (ou tout le document si moins de deux)."""
    targets = sorted(set(rows)) if len(set(rows)) > 1 else list(range(len(pages)))
    result = list(pages)
    for position, page in zip(targets, reversed([pages[i] for i in targets])):
        result[position] = page
    return result, sorted(set(rows)) if len(set(rows)) > 1 else []


def crop_pages(pages: list[PageRef], rows: list[int], box: Box | None) -> Edit:
    """Applique le même cadre visible (rotation comprise) à chaque page choisie."""
    chosen = set(rows)
    return [p.cropped(box) if i in chosen else p for i, p in enumerate(pages)], sorted(chosen)


def crop_pages_each(pages: list[PageRef], boxes: dict[int, Box | None]) -> Edit:
    """Un cadre propre à chaque page, mesuré sur la page avant rotation utilisateur."""
    result = [
        replace(p, crop=None if boxes[i] is None or is_full(boxes[i]) else boxes[i]) if i in boxes and not p.is_blank
        else p
        for i, p in enumerate(pages)
    ]
    return result, sorted(boxes)


def insert_blank(pages: list[PageRef], rows: list[int], size: tuple[float, float]) -> Edit:
    """Page blanche après la dernière page sélectionnée (à la fin si rien n'est sélectionné)."""
    at = max(rows) + 1 if rows else len(pages)
    return pages[:at] + [blank_page(*size)] + pages[at:], [at]


def move_pages(pages: list[PageRef], rows: list[int], target: int) -> Edit:
    """Déplace les pages sélectionnées, en bloc et dans leur ordre, avant la position `target`.

    `target` est exprimé dans l'ordre actuel (0 = tout au début, len(pages) = à la fin).
    """
    chosen = set(rows)
    block = [pages[i] for i in sorted(chosen)]
    rest = [p for i, p in enumerate(pages) if i not in chosen]
    insert_at = target - sum(1 for i in chosen if i < target)
    result = rest[:insert_at] + block + rest[insert_at:]
    return result, list(range(insert_at, insert_at + len(block)))


def content_box(doc: pymupdf.Document, index: int, margin: float = 0.015) -> Box | None:
    """Zone non blanche de la page (repère de la miniature), avec une petite marge. None si page vide."""
    pix = render_thumbnail(doc, index, 360, gray=True)
    image = Image.frombytes("L", (pix.width, pix.height), pix.samples)
    bbox = image.point(lambda v: 255 if v < 245 else 0).getbbox()
    if bbox is None:
        return None
    x0, y0, x1, y1 = bbox
    w, h = pix.width, pix.height
    return (max(0.0, x0 / w - margin), max(0.0, y0 / h - margin), min(1.0, x1 / w + margin), min(1.0, y1 / h + margin))


def render_thumbnail(doc: pymupdf.Document, index: int, width: int = THUMB_WIDTH, gray: bool = False) -> pymupdf.Pixmap:
    """Miniature RVB de la page, à sa rotation d'origine (sans la rotation utilisateur)."""
    page = doc[index]
    zoom = width / page.rect.width
    colorspace = pymupdf.csGRAY if gray else pymupdf.csRGB
    return page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), colorspace=colorspace, alpha=False)


def build_pdf(sources: list[Path], pages: list[PageRef], out: Path, progress: Progress = no_progress) -> Path:
    """Écrit `pages` dans `out`. `out` peut être l'un des fichiers sources : il est remplacé à la fin."""
    if not pages:
        raise ValueError("Le document ne contient plus aucune page.")
    docs = [open_pdf(src) for src in sources]
    tmp = out.with_name(f".{out.stem}.docflex-tmp.pdf")
    try:
        with pymupdf.open() as new:
            first_position: dict[tuple[int, int], int] = {}
            for position, ref in enumerate(pages):
                if position % 20 == 0:
                    progress(position / len(pages), f"Page {position + 1}/{len(pages)}")
                if ref.is_blank:
                    new.new_page(width=ref.size[0], height=ref.size[1])
                    continue
                # insert_pdf réutilise les ressources partagées (polices, images) d'un même document source.
                new.insert_pdf(docs[ref.source], from_page=ref.index, to_page=ref.index, links=False, annots=True)
                page = new[-1]
                if ref.crop:
                    _apply_crop(page, ref.crop)
                if ref.rotation:
                    page.set_rotation((page.rotation + ref.rotation) % 360)
                first_position.setdefault((ref.source, ref.index), position)

            new.set_toc(_remapped_toc(docs, first_position))
            meta = {k: v for k, v in docs[0].metadata.items() if k in METADATA_KEYS and v}
            new.set_metadata(meta)
            progress(0.95, "Écriture…")
            new.save(tmp, garbage=3, deflate=True)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    finally:
        for doc in docs:
            doc.close()
    os.replace(tmp, out)
    return out


def _apply_crop(page: pymupdf.Page, box: Box) -> None:
    """Recadre via la CropBox : le contenu masqué reste dans le fichier, rien n'est détruit."""
    shown = page.rect  # page affichée, rotation d'origine comprise, origine (0, 0)
    visible = pymupdf.Rect(
        box[0] * shown.width, box[1] * shown.height, box[2] * shown.width, box[3] * shown.height
    )
    unrotated = (visible * page.derotation_matrix).normalize()
    origin = page.cropbox.tl
    page.set_cropbox((unrotated + (origin.x, origin.y, origin.x, origin.y)) & page.mediabox)


def _remapped_toc(docs: list[pymupdf.Document], first_position: dict[tuple[int, int], int]) -> list:
    """Signets dont la page cible existe encore, triés selon le nouvel ordre.

    Les niveaux sont recalés pour rester valides (un niveau ne peut pas sauter
    plus d'un cran par rapport au signet précédent).
    """
    entries = []
    for s, doc in enumerate(docs):
        for level, title, page, *_ in doc.get_toc():
            position = first_position.get((s, page - 1))
            if position is not None:
                entries.append((position, len(entries), level, title))
    entries.sort()
    toc, previous = [], 0
    for position, _, level, title in entries:
        level = min(level, previous + 1)
        toc.append([level, title, position + 1])
        previous = level
    return toc
