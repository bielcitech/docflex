"""Édition du contenu des pages : modifier une ligne de texte, ajouter du texte ou une image,
blanc correcteur, remplissage de formulaires.

Les positions sont dans le repère de la page AFFICHÉE (comme dans le lecteur). Ces opérations
modifient le contenu des pages lui-même : l'annulation se fait par instantanés du document.

Modifier un texte existant est volontairement limité aux corrections ponctuelles : la ligne est
effacée puis réécrite au même endroit, avec sa mise en forme d'origine (police, taille, couleur),
sauf si l'utilisateur en choisit une autre. La police d'origine est réutilisée si elle contient
tous les caractères voulus, sinon une police standard proche. Il n'y a pas de recomposition des
paragraphes.

Déplacer un paragraphe : il est effacé puis réécrit plus loin, fragment par fragment, chacun avec
sa police, sa taille et sa couleur (police d'origine si possible, sinon police standard proche).

Un style est un dictionnaire {"family", "size", "color", "bold", "italic"} ; `family` est une
clé de FAMILIES.
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from pathlib import Path

import pymupdf

from docflex.core.annotations import to_page, to_page_point

FAMILIES = {"sans": "Sans empattement", "serif": "Avec empattement", "mono": "Chasse fixe"}
# Polices standard présentes dans tout lecteur PDF : (famille, gras, italique) → nom PyMuPDF.
_BASE14 = {
    ("sans", False, False): "helv", ("sans", True, False): "hebo", ("sans", False, True): "heit",
    ("sans", True, True): "hebi", ("serif", False, False): "tiro", ("serif", True, False): "tibo",
    ("serif", False, True): "tiit", ("serif", True, True): "tibi", ("mono", False, False): "cour",
    ("mono", True, False): "cobo", ("mono", False, True): "coit", ("mono", True, True): "cobi",
}
FLAG_ITALIC, FLAG_SERIF, FLAG_MONO, FLAG_BOLD = 2, 4, 8, 16
_MONO_NAMES = ("mono", "courier", "consol", "menlo")
_SANS_NAMES = ("sans", "helv", "arial", "verdana", "calibri", "segoe", "roboto", "tahoma")
_SERIF_NAMES = ("times", "roman", "serif", "georgia", "garamond", "cambria")


class EditError(ValueError):
    """Opération impossible, avec un message destiné à l'utilisateur."""


def _family(font: str, flags: int) -> str:
    # Le nom de la police d'abord : MuPDF marque « avec empattement » des polices qui n'en ont
    # pas (NimbusSans, celle qu'il embarque pour Helvetica).
    name = font.split("+")[-1].lower()
    for words, family in ((_MONO_NAMES, "mono"), (_SANS_NAMES, "sans"), (_SERIF_NAMES, "serif")):
        if any(word in name for word in words):
            return family
    return "mono" if flags & FLAG_MONO else "serif" if flags & FLAG_SERIF else "sans"


@dataclass(frozen=True)
class TextLine:
    rect: tuple[float, float, float, float]   # repère affiché
    text: str
    page_rect: pymupdf.Rect                   # repère non tourné (pour effacer)
    origin: pymupdf.Point                     # ligne de base du premier fragment, repère non tourné
    direction: tuple[float, float]
    font: str
    size: float
    color: tuple[float, float, float]
    flags: int

    @property
    def family(self) -> str:
        return _family(self.font, self.flags)

    @property
    def style(self) -> dict:
        """Mise en forme d'origine de la ligne (celle de son premier fragment)."""
        return {"family": self.family, "size": self.size, "color": self.color,
                "bold": bool(self.flags & FLAG_BOLD), "italic": bool(self.flags & FLAG_ITALIC)}


@dataclass(frozen=True)
class Span:
    """Fragment de texte d'une seule mise en forme, repère non tourné."""
    origin: pymupdf.Point
    text: str
    font: str
    size: float
    color: tuple[float, float, float]
    flags: int
    direction: tuple[float, float]


@dataclass(frozen=True)
class TextBlock:
    """Paragraphe (bloc de texte de MuPDF) : ce qu'on déplace d'un glisser."""
    rect: tuple[float, float, float, float]   # repère affiché
    page_rect: pymupdf.Rect                   # repère non tourné (pour effacer)
    spans: tuple[Span, ...]

    @property
    def text(self) -> str:
        return " ".join(s.text for s in self.spans)


def _rgb(value: int) -> tuple[float, float, float]:
    return ((value >> 16 & 255) / 255, (value >> 8 & 255) / 255, (value & 255) / 255)


def text_lines(page: pymupdf.Page) -> list[TextLine]:
    lines = []
    rotate = page.rotation_matrix
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            spans = [s for s in line["spans"] if s["text"].strip()]
            if not spans:
                continue
            first = spans[0]
            box = pymupdf.Rect(line["bbox"])
            lines.append(TextLine(
                rect=tuple((box * rotate).normalize()),
                text="".join(s["text"] for s in line["spans"]).replace("\xa0", " ").strip(),
                page_rect=box, origin=pymupdf.Point(first["origin"]), direction=tuple(line["dir"]),
                font=first["font"], size=first["size"], color=_rgb(first["color"]), flags=first["flags"],
            ))
    return lines


def text_blocks(page: pymupdf.Page) -> list[TextBlock]:
    blocks: list[TextBlock] = []
    rotate = page.rotation_matrix
    for block in page.get_text("dict")["blocks"]:
        spans = tuple(
            Span(origin=pymupdf.Point(s["origin"]), text=s["text"], font=s["font"], size=s["size"],
                 color=_rgb(s["color"]), flags=s["flags"], direction=tuple(line["dir"]))
            for line in block.get("lines", []) for s in line["spans"] if s["text"].strip())
        if not spans:
            continue
        box = pymupdf.Rect(block["bbox"])
        current = TextBlock(rect=tuple((box * rotate).normalize()), page_rect=box, spans=spans)
        if blocks and _continues(blocks[-1], current):
            previous = blocks.pop()
            box = previous.page_rect | box
            current = TextBlock(rect=tuple((box * rotate).normalize()), page_rect=box,
                                spans=previous.spans + spans)
        blocks.append(current)
    return blocks


def _continues(previous: TextBlock, block: TextBlock) -> bool:
    """MuPDF met chaque ligne de texte non horizontal (page tournée) dans un bloc à part : on
    regroupe celles qui se suivent à l'écran, alignées à gauche, à interligne normal."""
    direction = block.spans[0].direction
    if direction[1] == 0 or any(s.direction != direction for s in previous.spans + block.spans):
        return False
    size = max(s.size for s in block.spans)
    return abs(block.rect[0] - previous.rect[0]) < 2 and abs(block.rect[1] - previous.rect[3]) < 0.5 * size


def line_at(page: pymupdf.Page, point) -> TextLine | None:
    target = pymupdf.Point(point)
    for line in text_lines(page):
        if target in pymupdf.Rect(line.rect) + (-2, -2, 2, 2):
            return line
    return None


def _rotation(direction: tuple[float, float]) -> int:
    """Paramètre `rotate` de PyMuPDF qui donne une ligne de cette direction."""
    return round(math.degrees(math.atan2(-direction[1], direction[0]))) % 360


def _refuse_pending_redactions(page: pymupdf.Page) -> None:
    # apply_redactions appliquerait AUSSI les caviardages en attente d'autres logiciels.
    if any(a.type[1] == "Redact" for a in page.annots()):
        raise EditError("Cette page contient des caviardages en attente (créés par un autre logiciel) : "
                        "appliquez-les ou supprimez-les d'abord.")


def _erase_text(page: pymupdf.Page, rect: pymupdf.Rect) -> None:
    """Retire le texte de la zone, sans toucher aux images ni aux traits."""
    page.add_redact_annot(rect, fill=False)
    page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE, graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,
                          text=pymupdf.PDF_REDACT_TEXT_REMOVE)


_NAME_SUFFIXES = ("psmt", "mt", "regular", "roman", "book", "normal")
_STANDARD_KEYS = ("helvetica", "nimbussans", "nimbusroman", "nimbusmono", "courier", "times")


def _is_standard(name: str) -> bool:
    """Police standard du PDF (ou son équivalent MuPDF) : la police de remplacement est la même."""
    key = _font_key(name)
    return key.startswith(_STANDARD_KEYS) and not key.startswith(("timesnew", "couriernew"))


def _font_key(name: str) -> str:
    """« ABCDEF+Arial-BoldMT » et « Arial Bold » → « arialbold » : le texte donne le nom PostScript,
    la liste des polices de la page un nom d'affichage."""
    key = "".join(c for c in name.split("+")[-1].lower() if c.isalnum())
    stripped = True
    while stripped:
        stripped = False
        for suffix in _NAME_SUFFIXES:
            if key.endswith(suffix) and len(key) > len(suffix):
                key, stripped = key[: -len(suffix)], True
    return key


def _original_font(page: pymupdf.Page, name: str, text: str) -> bytes | None:
    """Police embarquée d'origine, si elle contient tous les caractères de `text` (souvent elle
    n'est qu'un sous-ensemble limité aux caractères déjà utilisés)."""
    for xref, _ext, _type, basefont, *_ in page.get_fonts(full=True):
        if _font_key(basefont) != _font_key(name):
            continue
        _, ext, _, buffer = page.parent.extract_font(xref)
        if not buffer or ext not in ("ttf", "otf", "cff", "pfa", "pfb"):
            return None
        try:
            font = pymupdf.Font(fontbuffer=buffer)
        except Exception:
            return None
        return buffer if all(font.has_glyph(ord(c)) for c in text if not c.isspace()) else None
    return None


def _insert_original(page: pymupdf.Page, buffer: bytes) -> str:
    """Police d'origine ajoutée à la page sous un nom propre à son contenu : PyMuPDF ne remplace
    pas une police déjà présente sous le même nom (deux polices différentes se confondraient)."""
    name = "DF" + hashlib.md5(buffer).hexdigest()[:10]
    page.insert_font(fontname=name, fontbuffer=buffer)
    return name


def _write(page: pymupdf.Page, origin: pymupdf.Point, text: str, base14: str, size: float, color,
           rotate: int, lineheight: float = 1.25) -> None:
    """Écrit `text` (plusieurs lignes possibles) à partir de la ligne de base `origin`.

    Les polices standard sont utilisées sous leur forme complète (embarquée) : `insert_text`
    les encoderait en Latin-1, sans « € », « œ » ni guillemets typographiques.
    """
    writer = pymupdf.TextWriter(page.rect, color=color)
    font = pymupdf.Font(base14)
    for i, line in enumerate(text.split("\n")):
        writer.append(origin + (0, i * size * lineheight), line, font=font, fontsize=size)
    writer.write_text(page, morph=(origin, pymupdf.Matrix(rotate)) if rotate else None)


def replace_line(page: pymupdf.Page, line: TextLine, new_text: str, style: dict | None = None) -> bool:
    """Remplace la ligne par `new_text`, avec sa mise en forme d'origine sauf ce que `style` change.

    Retourne True si la police d'origine a pu être réutilisée (pas si la famille, le gras ou
    l'italique ont été changés)."""
    _refuse_pending_redactions(page)
    new_text = new_text.replace("\n", " ").strip()
    style = {**line.style, **(style or {})}
    same_face = all(style[k] == line.style[k] for k in ("family", "bold", "italic"))
    buffer = _original_font(page, line.font, new_text) if same_face else None
    # Zone à effacer resserrée en hauteur : ne pas mordre sur les lignes voisines.
    inset = line.page_rect.height * 0.15 if line.direction[1] == 0 else 0
    side = line.page_rect.width * 0.15 if line.direction[0] == 0 else 0
    _erase_text(page, line.page_rect + (side, inset, -side, -inset))
    if not new_text:
        return buffer is not None
    if buffer is not None:
        page.insert_text(line.origin, new_text, fontsize=style["size"], fontname=_insert_original(page, buffer),
                         color=style["color"], rotate=_rotation(line.direction))
        return True
    base14 = _BASE14[(style["family"], style["bold"], style["italic"])]
    _write(page, line.origin, new_text, base14, style["size"], style["color"], _rotation(line.direction))
    return False


def move_block(page: pymupdf.Page, block: TextBlock, dx: float, dy: float) -> bool:
    """Déplace le paragraphe de (dx, dy), repère affiché. Retourne True si toutes les polices
    d'origine ont pu être réutilisées."""
    _refuse_pending_redactions(page)
    shift = pymupdf.Point(dx, dy) * page.derotation_matrix - pymupdf.Point(0, 0) * page.derotation_matrix
    fonts = {}
    for span in block.spans:  # polices lues AVANT d'effacer : une police que plus rien n'utilise disparaît
        fonts[(span.font, span.text)] = _original_font(page, span.font, span.text)
    lines = {round(s.origin.y if s.direction[1] == 0 else s.origin.x, 1) for s in block.spans}
    inset = min(s.size for s in block.spans) * 0.15 if len(lines) == 1 else 0  # même précaution que replace_line
    vertical = block.spans[0].direction[0] == 0
    _erase_text(page, block.page_rect + ((inset, 0, -inset, 0) if vertical else (0, inset, 0, -inset)))
    kept = True
    for span in block.spans:
        origin, rotate = span.origin + shift, _rotation(span.direction)
        buffer = fonts[(span.font, span.text)]
        if buffer is not None:
            page.insert_text(origin, span.text, fontsize=span.size, fontname=_insert_original(page, buffer),
                             color=span.color, rotate=rotate)
            continue
        kept = kept and _is_standard(span.font)
        base14 = _BASE14[(_family(span.font, span.flags), bool(span.flags & FLAG_BOLD), bool(span.flags & FLAG_ITALIC))]
        _write(page, origin, span.text, base14, span.size, span.color, rotate)
    return kept


def add_text(page: pymupdf.Page, point, text: str, family: str = "sans", size: float = 12,
             color=(0, 0, 0), bold: bool = False, italic: bool = False) -> None:
    """Texte ajouté au contenu de la page, horizontal à l'écran ; `point` = haut gauche de la 1re ligne
    (la première ligne de base est à `size` en dessous)."""
    x, y = point
    _write(page, to_page_point(page, (x, y + size)), text, _BASE14[(family, bold, italic)], size, color,
           page.rotation)


def add_image(page: pymupdf.Page, rect, image: Path) -> None:
    page.insert_image(to_page(page, rect), filename=str(image), keep_proportion=True, rotate=page.rotation)


def whiteout(page: pymupdf.Page, rect) -> None:
    """Blanc correcteur : ce qui est sous la zone (texte, image, traits) est supprimé, pas masqué."""
    _refuse_pending_redactions(page)
    page.add_redact_annot(to_page(page, rect), fill=(1, 1, 1))
    page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_PIXELS,
                          graphics=pymupdf.PDF_REDACT_LINE_ART_REMOVE_IF_COVERED,
                          text=pymupdf.PDF_REDACT_TEXT_REMOVE)


# --------------------------------------------------------------- formulaires

FIELD_LABELS = {
    pymupdf.PDF_WIDGET_TYPE_TEXT: "Texte", pymupdf.PDF_WIDGET_TYPE_CHECKBOX: "Case à cocher",
    pymupdf.PDF_WIDGET_TYPE_RADIOBUTTON: "Bouton radio", pymupdf.PDF_WIDGET_TYPE_COMBOBOX: "Liste",
    pymupdf.PDF_WIDGET_TYPE_LISTBOX: "Liste",
}


@dataclass(frozen=True)
class Field:
    xref: int
    rect: tuple[float, float, float, float]  # repère affiché
    kind: int
    name: str
    value: str
    choices: tuple[str, ...]
    multiline: bool
    fontsize: float = 0.0   # 0 : taille automatique (selon la hauteur du champ)
    maxlen: int = 0         # 0 : sans limite
    option: str = ""        # bouton radio : valeur de cette option dans son groupe
    readonly: bool = False  # champ calculé, ou verrouillé par l'auteur du formulaire

    @property
    def checkable(self) -> bool:
        return self.kind in (pymupdf.PDF_WIDGET_TYPE_CHECKBOX, pymupdf.PDF_WIDGET_TYPE_RADIOBUTTON)

    @property
    def checked(self) -> bool:
        return self.value not in ("", "Off", "False")


def fields(page: pymupdf.Page) -> list[Field]:
    result = []
    for widget in page.widgets():
        if widget.field_type not in FIELD_LABELS:
            continue  # boutons poussoirs, signatures : non modifiables ici
        value, option = str(widget.field_value or ""), ""
        if widget.field_type == pymupdf.PDF_WIDGET_TYPE_RADIOBUTTON:
            # La valeur du groupe est partagée : l'état de CETTE option se lit dans /AS.
            option, state = _radio_on(page.parent, widget.xref), _name_at(page.parent, widget.xref, "AS")
            value = option if state == option else "Off"
        result.append(Field(
            widget.xref, tuple((widget.rect * page.rotation_matrix).normalize()), widget.field_type,
            widget.field_label or widget.field_name or "", value,
            tuple(str(c[0] if isinstance(c, (list, tuple)) else c) for c in widget.choice_values or ()),
            bool(widget.field_flags & pymupdf.PDF_TX_FIELD_IS_MULTILINE),
            float(widget.text_fontsize or 0), int(widget.text_maxlen or 0), option,
            bool(widget.field_flags & pymupdf.PDF_FIELD_IS_READ_ONLY),
        ))
    return result


# Boutons radio : PyMuPDF donne le même état « coché » à toutes les options d'un groupe ;
# on lit et on écrit donc directement les noms d'état (/AP /N) et la valeur du groupe (/V).

def pdf_name(text: str) -> str:
    """Texte → nom PDF (sans la barre oblique) ; les caractères spéciaux deviennent #xx."""
    return "".join(chr(b) if 33 <= b <= 126 and chr(b) not in "()<>[]{}/%#" else f"#{b:02X}"
                   for b in text.encode("utf-8"))


def _from_pdf_name(name: str) -> str:
    return re.sub(rb"#([0-9A-Fa-f]{2})", lambda m: bytes([int(m.group(1), 16)]), name.encode()).decode("utf-8", "replace")


def _name_at(doc: pymupdf.Document, xref: int, key: str) -> str:
    kind, value = doc.xref_get_key(xref, key)
    return _from_pdf_name(value[1:]) if kind == "name" else ""


def _radio_on(doc: pymupdf.Document, xref: int) -> str:
    kind, value = doc.xref_get_key(xref, "AP/N")
    names = [_from_pdf_name(n) for n in re.findall(r"/([^\s/<>\[\]()]+)\s*(?:\d+\s+\d+\s+R|<<)", value)] if kind == "dict" else []
    return next((n for n in names if n != "Off"), "")


def _group(doc: pymupdf.Document, xref: int) -> tuple[int, list[int]]:
    """(objet qui porte la valeur, options du groupe) d'un bouton radio."""
    kind, parent = doc.xref_get_key(xref, "Parent")
    if kind != "xref":
        return xref, [xref]
    holder = int(parent.split()[0])
    kids = [int(n) for n in re.findall(r"(\d+)\s+\d+\s+R", doc.xref_get_key(holder, "Kids")[1])]
    return holder, kids or [xref]


def select_radio(doc: pymupdf.Document, xref: int) -> None:
    """Coche cette option, décoche les autres options du groupe."""
    holder, kids = _group(doc, xref)
    option = _radio_on(doc, xref)
    for kid in kids:
        doc.xref_set_key(kid, "AS", f"/{pdf_name(option)}" if kid == xref else "/Off")
    doc.xref_set_key(holder, "V", f"/{pdf_name(option)}")


def radio_options(doc: pymupdf.Document, group: str) -> list[str]:
    return [_radio_on(doc, w.xref) for page in doc for w in page.widgets()
            if w.field_type == pymupdf.PDF_WIDGET_TYPE_RADIOBUTTON and w.field_name == group]


def field_at(page: pymupdf.Page, point) -> Field | None:
    target = pymupdf.Point(point)
    for field in fields(page):
        if target in pymupdf.Rect(field.rect):
            return field
    return None


def set_field(page: pymupdf.Page, xref: int, value) -> None:
    """Texte et listes : chaîne ; cases à cocher et boutons radio : True / False."""
    widget = _widget(page, xref)
    if widget.field_type == pymupdf.PDF_WIDGET_TYPE_RADIOBUTTON:
        if value:  # une option de groupe ne se décoche pas : on en choisit une autre
            select_radio(page.parent, xref)
        return
    if widget.field_type in (pymupdf.PDF_WIDGET_TYPE_CHECKBOX, pymupdf.PDF_WIDGET_TYPE_RADIOBUTTON):
        widget.field_value = widget.on_state() if value else "Off"
    else:
        widget.field_value = value
    widget.update()


NEW_FIELD_KINDS = {
    "field_text": pymupdf.PDF_WIDGET_TYPE_TEXT,
    "field_check": pymupdf.PDF_WIDGET_TYPE_CHECKBOX,
    "field_choice": pymupdf.PDF_WIDGET_TYPE_COMBOBOX,
    "field_radio": pymupdf.PDF_WIDGET_TYPE_RADIOBUTTON,
}
FIELD_BORDER = (0.55, 0.6, 0.68)


def field_names(doc: pymupdf.Document) -> set[str]:
    return {w.field_name for page in doc for w in page.widgets() if w.field_name}


def unique_field_name(doc: pymupdf.Document, base: str) -> str:
    """« Champ 1 », « Champ 2 »… : deux champs de même nom partageraient leur valeur."""
    taken = field_names(doc)
    n = 1
    while f"{base} {n}" in taken:
        n += 1
    return f"{base} {n}"


def add_field(page: pymupdf.Page, rect, kind: str, name: str, choices: tuple[str, ...] = ()) -> int:
    """Ajoute un champ (`kind` : clé de NEW_FIELD_KINDS) dans `rect`, repère affiché ; retourne son xref."""
    doc = page.parent
    if kind == "field_radio":
        return _add_radio(page, rect, name, choices[0] if choices else "")
    if name in field_names(doc):
        raise EditError(f"Un champ s'appelle déjà « {name} ».")
    if kind == "field_choice" and not choices:
        raise EditError("Une liste a besoin d'au moins une valeur.")
    widget = pymupdf.Widget()
    widget.field_type = NEW_FIELD_KINDS[kind]
    widget.field_name = name
    widget.rect = (pymupdf.Rect(rect) * page.derotation_matrix).normalize()  # repère non tourné
    widget.border_color = FIELD_BORDER
    widget.border_width = 1
    if kind == "field_check":
        widget.field_value = False
    else:
        widget.text_fontsize = round(min(12.0, max(widget.rect.height * 0.6, 7.0)), 1)
    if kind == "field_choice":
        widget.choice_values = list(choices)
        widget.field_value = choices[0]
    page.add_widget(widget)
    return widget.xref


def _add_radio(page: pymupdf.Page, rect, group: str, option: str) -> int:
    """Option `option` du groupe `group` (créé au besoin) ; un seul choix possible dans un groupe."""
    doc = page.parent
    radios = {w.field_name for p in doc for w in p.widgets() if w.field_type == pymupdf.PDF_WIDGET_TYPE_RADIOBUTTON}
    if group in field_names(doc) and group not in radios:
        raise EditError(f"« {group} » est déjà le nom d'un autre champ.")
    taken = radio_options(doc, group)
    option = option or next(f"Choix {n}" for n in range(1, len(taken) + 2) if f"Choix {n}" not in taken)
    if option in taken:
        raise EditError(f"Le groupe « {group} » a déjà une option « {option} ».")
    widget = pymupdf.Widget()
    widget.field_type = pymupdf.PDF_WIDGET_TYPE_RADIOBUTTON
    widget.field_name = group  # même nom : PyMuPDF range l'option sous le groupe existant
    widget.rect = (pymupdf.Rect(rect) * page.derotation_matrix).normalize()
    widget.border_color = FIELD_BORDER
    widget.border_width = 1
    widget.field_value = False
    page.add_widget(widget)
    xref = widget.xref
    # Apparences « coché » / « non coché » renommées : chaque option a son propre état.
    refs = dict(re.findall(r"/([^\s/<>\[\]()]+)\s*(\d+\s+\d+\s+R)", doc.xref_get_key(xref, "AP/N")[1]))
    on = next(ref for key, ref in refs.items() if key != "Off")
    doc.xref_set_key(xref, "AP/N", f"<</Off {refs.get('Off', on)}/{pdf_name(option)} {on}>>")
    doc.xref_set_key(xref, "AS", "/Off")
    return xref


def _widget(page: pymupdf.Page, xref: int) -> pymupdf.Widget:
    return next(w for w in page.widgets() if w.xref == xref)


def rename_field(page: pymupdf.Page, xref: int, name: str) -> None:
    widget = _widget(page, xref)
    if name != widget.field_name and name in field_names(page.parent):
        raise EditError(f"Un champ s'appelle déjà « {name} ».")
    # PyMuPDF n'écrit pas un nouveau nom : on modifie le dictionnaire du champ (nom, info-bulle).
    doc = page.parent
    holder = widget.xref
    parent = doc.xref_get_key(holder, "Parent")
    if parent[0] == "xref" and doc.xref_get_key(holder, "T")[0] == "null":
        holder = int(parent[1].split()[0])  # case d'un groupe : le nom est porté par le parent
    for key in ("T", "TU"):
        doc.xref_set_key(holder, key, pymupdf.get_pdf_str(name))


def delete_field(page: pymupdf.Page, xref: int) -> None:
    page.delete_widget(_widget(page, xref))
