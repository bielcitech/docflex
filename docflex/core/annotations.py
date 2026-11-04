"""Annotations PDF : création, description, recréation.

Toutes les géométries sont exprimées dans le repère de la page AFFICHÉE (rotation comprise),
comme dans le lecteur. PyMuPDF travaille dans le repère non tourné : conversion avec
`derotation_matrix` à l'écriture et `rotation_matrix` à la lecture.

Une annotation se décrit par un `AnnotSpec` (type, géométrie, style, texte) et se retrouve par
son xref. Déplacer, recolorer ou annuler revient à supprimer UNE annotation et à la recréer
d'après sa description : les autres annotations de la page (y compris celles d'autres logiciels,
avec leurs réponses) ne sont jamais touchées.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from typing import Callable

import pymupdf

Color = tuple[float, float, float]
Point = tuple[float, float]

MARKUPS = {"highlight": "Highlight", "underline": "Underline", "strikeout": "StrikeOut"}
SUPPORTED = {
    "Highlight": "highlight", "Underline": "underline", "StrikeOut": "strikeout", "Text": "note",
    "FreeText": "text", "Square": "rect", "Circle": "ellipse", "Line": "line", "Ink": "ink",
}
KIND_LABELS = {
    "highlight": "Surlignage", "underline": "Soulignement", "strikeout": "Texte barré", "note": "Note",
    "text": "Zone de texte", "rect": "Rectangle", "ellipse": "Ellipse", "line": "Ligne", "arrow": "Flèche",
    "ink": "Dessin",
}
NOTE_SIZE = 20  # points : icône des notes


@dataclass(frozen=True)
class AnnotSpec:
    kind: str                                   # clé de KIND_LABELS
    rect: tuple[float, float, float, float]     # repère affiché
    color: Color = (1.0, 0.85, 0.1)
    opacity: float = 1.0
    width: float = 2.0                          # épaisseur des traits
    text: str = ""                              # note, zone de texte, commentaire
    fontsize: float = 12.0
    points: tuple[tuple[Point, ...], ...] = field(default=())  # quads, extrémités de ligne, traits
    author: str = ""

    def moved(self, dx: float, dy: float) -> AnnotSpec:
        x0, y0, x1, y1 = self.rect
        return replace(
            self,
            rect=(x0 + dx, y0 + dy, x1 + dx, y1 + dy),
            points=tuple(tuple((x + dx, y + dy) for x, y in stroke) for stroke in self.points),
        )

    @property
    def movable(self) -> bool:
        return self.kind not in MARKUPS  # un surlignage suit son texte


# ----------------------------------------------------------------- repères

def to_page(page: pymupdf.Page, rect) -> pymupdf.Rect:
    return (pymupdf.Rect(rect) * page.derotation_matrix).normalize()


def to_page_point(page: pymupdf.Page, point: Point) -> pymupdf.Point:
    return pymupdf.Point(point) * page.derotation_matrix


def shown_rect(page: pymupdf.Page, annot: pymupdf.Annot) -> pymupdf.Rect:
    return (annot.rect * page.rotation_matrix).normalize()


# ---------------------------------------------------------------- création

def _finish(annot: pymupdf.Annot, spec: AnnotSpec) -> pymupdf.Annot:
    if spec.author:
        annot.set_info(title=spec.author)
    if spec.text and spec.kind not in ("note", "text"):  # commentaire d'un surlignage ou d'une forme
        annot.set_info(content=spec.text)
    annot.update()
    return annot


def create(page: pymupdf.Page, spec: AnnotSpec) -> pymupdf.Annot:
    """Crée l'annotation décrite par `spec` sur `page`."""
    kind = spec.kind
    if kind in MARKUPS:
        quads = [pymupdf.Quad([to_page_point(page, p) for p in quad]) for quad in spec.points]
        annot = getattr(page, f"add_{kind}_annot")(quads=quads)
        annot.set_colors(stroke=spec.color)
        annot.set_opacity(spec.opacity)
    elif kind == "note":
        x0, y0, _, _ = spec.rect
        annot = page.add_text_annot(to_page_point(page, (x0, y0)), spec.text, icon="Comment")
        annot.set_colors(stroke=spec.color)
    elif kind == "text":
        annot = page.add_freetext_annot(
            to_page(page, spec.rect), spec.text, fontsize=spec.fontsize, fontname="helv",
            text_color=spec.color, rotate=page.rotation, opacity=spec.opacity,
        )
    elif kind in ("rect", "ellipse"):
        add = page.add_rect_annot if kind == "rect" else page.add_circle_annot
        annot = add(to_page(page, spec.rect))
        annot.set_colors(stroke=spec.color)
        annot.set_border(width=spec.width)
        annot.set_opacity(spec.opacity)
    elif kind in ("line", "arrow"):
        start, end = spec.points[0]
        annot = page.add_line_annot(to_page_point(page, start), to_page_point(page, end))
        annot.set_colors(stroke=spec.color)
        annot.set_border(width=spec.width)
        annot.set_opacity(spec.opacity)
        if kind == "arrow":
            annot.set_line_ends(pymupdf.PDF_ANNOT_LE_NONE, pymupdf.PDF_ANNOT_LE_OPEN_ARROW)
    elif kind == "ink":
        strokes = [[tuple(to_page_point(page, p)) for p in stroke] for stroke in spec.points]
        annot = page.add_ink_annot(strokes)
        annot.set_colors(stroke=spec.color)
        annot.set_border(width=spec.width)
        annot.set_opacity(spec.opacity)
    else:
        raise ValueError(f"Type d'annotation inconnu : {kind}")
    return _finish(annot, spec)


def markup_quads(word_rects: list[pymupdf.Rect]) -> tuple[tuple[Point, ...], ...]:
    """Quadrilatères d'un surlignage : un par ligne de texte (mots voisins fusionnés)."""
    lines: list[pymupdf.Rect] = []
    for rect in word_rects:
        same_line = lines and abs(lines[-1].y0 - rect.y0) < rect.height / 2 and rect.x0 >= lines[-1].x0
        if same_line:
            lines[-1] |= rect
        else:
            lines.append(pymupdf.Rect(rect))
    return tuple(((r.x0, r.y0), (r.x1, r.y0), (r.x0, r.y1), (r.x1, r.y1)) for r in lines)


def bounds(points) -> tuple[float, float, float, float]:
    xs = [x for stroke in points for x, _ in stroke]
    ys = [y for stroke in points for _, y in stroke]
    return (min(xs), min(ys), max(xs), max(ys))


# ----------------------------------------------------------------- lecture

_DA_SIZE = re.compile(r"([\d.]+)\s+Tf")
_DA_RGB = re.compile(r"([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+rg")
_DA_GRAY = re.compile(r"([\d.]+)\s+g\b")


def capture(page: pymupdf.Page, annot: pymupdf.Annot) -> AnnotSpec | None:
    """Description de l'annotation, ou None si DocFlex ne sait pas la recréer fidèlement."""
    kind = SUPPORTED.get(annot.type[1])
    if kind is None:
        return None
    rotate = page.rotation_matrix
    stroke = tuple(annot.colors.get("stroke") or (0.0, 0.0, 0.0))
    opacity = annot.opacity if annot.opacity >= 0 else 1.0
    width = (annot.border or {}).get("width") or 1.0
    common = dict(rect=tuple(shown_rect(page, annot)), color=stroke, opacity=opacity, width=width,
                  text=annot.info.get("content", ""), author=annot.info.get("title", ""))
    if kind in MARKUPS:
        vertices = [tuple(pymupdf.Point(v) * rotate) for v in annot.vertices or []]
        quads = tuple(tuple(vertices[i:i + 4]) for i in range(0, len(vertices) - 3, 4))
        return AnnotSpec(kind, points=quads, **common)
    if kind == "line":
        start, end = (tuple(pymupdf.Point(v) * rotate) for v in annot.vertices[:2])
        arrow = any(end_style != pymupdf.PDF_ANNOT_LE_NONE for end_style in (annot.line_ends or ()))
        return AnnotSpec("arrow" if arrow else "line", points=((start, end),), **common)
    if kind == "ink":
        strokes = tuple(tuple(tuple(pymupdf.Point(p) * rotate) for p in s) for s in annot.vertices or [])
        return AnnotSpec("ink", points=strokes, **common)
    if kind in ("rect", "ellipse"):
        # PyMuPDF élargit le cadre de la moitié du trait : sans correction, chaque recréation
        # (déplacement, annulation) agrandirait la forme.
        half = width / 2
        shape = shown_rect(page, annot) + (half, half, -half, -half)
        return AnnotSpec(kind, **{**common, "rect": tuple(shape)})
    if kind == "text":
        da = page.parent.xref_get_key(annot.xref, "DA")[1] or ""
        size = _DA_SIZE.search(da)
        rgb, gray = _DA_RGB.search(da), _DA_GRAY.search(da)
        color = tuple(float(v) for v in rgb.groups()) if rgb else (float(gray.group(1)),) * 3 if gray else (0, 0, 0)
        return AnnotSpec("text", fontsize=float(size.group(1)) if size else 12.0,
                         **{**common, "color": color})
    return AnnotSpec(kind, **common)


def supported_annots(page: pymupdf.Page) -> list[tuple[pymupdf.Annot, AnnotSpec]]:
    pairs = []
    for annot in page.annots():
        spec = capture(page, annot)
        if spec is not None:
            pairs.append((annot, spec))
    return pairs


def page_specs(page: pymupdf.Page) -> list[AnnotSpec]:
    return [spec for _, spec in supported_annots(page)]


def remove(page: pymupdf.Page, xref: int) -> None:
    page.delete_annot(page.load_annot(xref))


def recreate(page: pymupdf.Page, xref: int, spec: AnnotSpec) -> int:
    """Remplace l'annotation `xref` par `spec` ; retourne le xref de la nouvelle annotation."""
    remove(page, xref)
    return create(page, spec).xref


def annot_at(page: pymupdf.Page, point: Point, tolerance: float = 3) -> int | None:
    """Indice (dans `page_specs`) de l'annotation sous `point`, la plus récente en premier."""
    target = pymupdf.Point(point)
    margin = (-tolerance, -tolerance, tolerance, tolerance)
    hits = [i for i, spec in enumerate(page_specs(page)) if target in pymupdf.Rect(spec.rect) + margin]
    return hits[-1] if hits else None


def describe(page: pymupdf.Page, spec: AnnotSpec, limit: int = 60) -> str:
    """Résumé lisible pour la liste des annotations."""
    excerpt = spec.text.strip()
    if spec.kind in MARKUPS and not excerpt:  # le texte surligné, zone par zone
        excerpt = " ".join(page.get_textbox(to_page(page, bounds([quad]))).strip() for quad in spec.points)
    excerpt = " ".join(excerpt.split())
    if len(excerpt) > limit:
        excerpt = excerpt[: limit - 1] + "…"
    return f"{KIND_LABELS[spec.kind]} — « {excerpt} »" if excerpt else KIND_LABELS[spec.kind]


# ------------------------------------------------------------- historique

HISTORY_MAX = 200
SNAPSHOT_MAX = 30  # instantanés gardés (modifications de contenu) : chacun pèse la taille du document


@dataclass
class Change:
    """Une action sur UNE annotation : ajout (before=None), suppression (after=None) ou modification.

    `xref` désigne l'annotation visée au moment de l'action (0 pour un ajout). Ensuite, l'historique
    la suit par un identifiant stable (`slot`) : chaque recréation lui donne un nouveau xref.
    """
    page: int
    before: AnnotSpec | None
    after: AnnotSpec | None
    xref: int = 0
    slot: int = -1


@dataclass
class Snapshot:
    """Modification du contenu des pages : document complet avant et après.

    Enregistré sans ramasse-miettes (`tobytes()`), le document garde ses numéros d'objets :
    le suivi des annotations par xref reste valable après restauration.
    """
    page: int
    before: bytes
    after: bytes


class History:
    """Annuler / rétablir, sans dépendance à l'interface.

    `restore(données)` remplace le document ouvert par ces données (instantanés) ; c'est la vue
    qui le fournit, car elle détient le document.
    """

    def __init__(self, restore: Callable[[bytes], None] | None = None) -> None:
        self.undo_stack: list[Change | Snapshot] = []
        self.redo_stack: list[Change | Snapshot] = []
        self._xrefs: dict[int, int] = {}  # slot → xref actuel de l'annotation
        self._next_slot = 0
        self._restore = restore

    def clear(self) -> None:
        self.undo_stack.clear()
        self.redo_stack.clear()
        self._xrefs.clear()

    def _slot_for(self, xref: int) -> int:
        for slot, current in self._xrefs.items():
            if xref and current == xref:
                return slot
        self._next_slot += 1
        if xref:
            self._xrefs[self._next_slot] = xref
        return self._next_slot

    def _apply(self, doc: pymupdf.Document, change: Change | Snapshot, forward: bool) -> None:
        if isinstance(change, Snapshot):
            self._restore(change.after if forward else change.before)
            return
        page = doc[change.page]
        current = self._xrefs.pop(change.slot, 0)
        if current:
            remove(page, current)
        target = change.after if forward else change.before
        if target is not None:
            self._xrefs[change.slot] = create(page, target).xref

    def do(self, doc: pymupdf.Document, change: Change) -> Change:
        change.slot = self._slot_for(change.xref)
        self._apply(doc, change, forward=True)
        self._push(change)
        return change

    def record(self, snapshot: Snapshot) -> None:
        """Modification de contenu DÉJÀ appliquée au document."""
        self._push(snapshot)

    def _push(self, entry: Change | Snapshot) -> None:
        self.undo_stack = (self.undo_stack + [entry])[-HISTORY_MAX:]
        while sum(isinstance(e, Snapshot) for e in self.undo_stack) > SNAPSHOT_MAX:
            self.undo_stack.pop(0)  # au-delà, on ne peut plus remonter aussi loin
        self.redo_stack.clear()

    def undo(self, doc: pymupdf.Document) -> int | None:
        """Annule la dernière action ; retourne la page touchée."""
        if not self.undo_stack:
            return None
        change = self.undo_stack.pop()
        self._apply(doc, change, forward=False)
        self.redo_stack.append(change)
        return change.page

    def redo(self, doc: pymupdf.Document) -> int | None:
        if not self.redo_stack:
            return None
        change = self.redo_stack.pop()
        self._apply(doc, change, forward=True)
        self.undo_stack.append(change)
        return change.page


def xref_at(page: pymupdf.Page, index: int) -> int:
    """xref de la index-ième annotation prise en charge (ordre de `page_specs`)."""
    return supported_annots(page)[index][0].xref


_FONT = pymupdf.Font("helv")
TEXT_PADDING = 4  # points : marge intérieure d'une zone de texte


def fit_textbox(rect: tuple[float, float, float, float], text: str, fontsize: float) -> tuple[float, float, float, float]:
    """Agrandit la zone en hauteur pour que tout le texte, renvoyé à la ligne, y tienne."""
    x0, y0, x1, y1 = rect
    width = max(x1 - x0 - 2 * TEXT_PADDING, fontsize)
    lines = 0
    for paragraph in text.split("\n") or [""]:
        line, count = "", 1
        for word in paragraph.split(" "):
            candidate = f"{line} {word}".strip()
            if line and _FONT.text_length(candidate, fontsize) > width:
                count, line = count + 1, word
            else:
                line = candidate
        lines += count
    needed = lines * fontsize * 1.35 + 2 * TEXT_PADDING
    return (x0, y0, x1, max(y1, y0 + needed))
