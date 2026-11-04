"""Lecture : mots d'une page, sélection de texte, recherche, liens et table des matières.

Tout est exprimé dans le repère de la page AFFICHÉE (rotation comprise), en points.
PyMuPDF donne le texte dans le repère de la page non tournée : on convertit avec
`page.rotation_matrix`.
"""

from __future__ import annotations

from dataclasses import dataclass

import pymupdf


@dataclass(frozen=True)
class Word:
    rect: pymupdf.Rect  # repère affiché
    text: str
    block: int
    line: int


def page_words(page: pymupdf.Page) -> list[Word]:
    """Mots de la page, dans l'ordre de lecture."""
    rotate = page.rotation_matrix
    words = sorted(page.get_text("words"), key=lambda w: (w[5], w[6], w[7]))
    return [Word(pymupdf.Rect(w[:4]) * rotate, w[4], w[5], w[6]) for w in words]


def word_at(words: list[Word], point: pymupdf.Point, reach: float = 24) -> int | None:
    """Indice du mot sous `point` ; sinon le plus proche à moins de `reach` points ; sinon None."""
    best, best_distance = None, reach
    for i, word in enumerate(words):
        if point in word.rect:
            return i
        dx = max(word.rect.x0 - point.x, 0, point.x - word.rect.x1)
        dy = max(word.rect.y0 - point.y, 0, point.y - word.rect.y1)
        distance = (dx * dx + dy * dy) ** 0.5
        if distance < best_distance:
            best, best_distance = i, distance
    return best


def selection_text(words: list[Word], first: int, last: int) -> str:
    """Texte des mots first..last (bornes incluses, dans n'importe quel ordre), lignes respectées."""
    a, b = sorted((first, last))
    lines: list[list[str]] = []
    previous = None
    for word in words[a:b + 1]:
        if (word.block, word.line) != previous:
            lines.append([])
            previous = (word.block, word.line)
        lines[-1].append(word.text)
    return "\n".join(" ".join(line) for line in lines)


def search(doc: pymupdf.Document, query: str) -> list[tuple[int, pymupdf.Rect]]:
    """Toutes les occurrences (majuscules indifférentes), dans l'ordre du document."""
    query = query.strip()
    if not query:
        return []
    hits = []
    for page in doc:
        rotate = page.rotation_matrix
        hits += [(page.number, rect * rotate) for rect in page.search_for(query)]
    return hits


@dataclass(frozen=True)
class Target:
    """Destination d'un lien interne ou d'une entrée de la table des matières."""
    page: int
    y: float | None = None  # hauteur visée dans la page affichée ; None : haut de page


@dataclass(frozen=True)
class Link:
    rect: pymupdf.Rect      # repère affiché
    target: Target | None   # lien interne
    uri: str = ""           # lien externe (site web, courriel)

    @property
    def description(self) -> str:
        return self.uri if self.uri else f"Aller à la page {self.target.page + 1}"


@dataclass(frozen=True)
class OutlineEntry:
    level: int
    title: str
    target: Target | None   # None : entrée sans destination dans ce document


def _target(doc: pymupdf.Document, page: int, to) -> Target | None:
    if page is None or not 0 <= page < doc.page_count:
        return None
    if to is None or (to.x == 0 and to.y == 0):
        return Target(page)
    point = pymupdf.Point(to) * doc[page].rotation_matrix  # repère de la page non tournée → affiché
    return Target(page, max(point.y, 0.0))


def page_links(page: pymupdf.Page) -> list[Link]:
    """Liens cliquables : vers une page du document ou vers une adresse externe."""
    links = []
    for item in page.get_links():
        rect = pymupdf.Rect(item["from"])  # PyMuPDF le donne déjà dans le repère affiché
        if item["kind"] in (pymupdf.LINK_GOTO, pymupdf.LINK_NAMED):
            target = _target(page.parent, item.get("page"), item.get("to"))
            if target is not None:
                links.append(Link(rect, target))
        elif item["kind"] == pymupdf.LINK_URI and item.get("uri"):
            links.append(Link(rect, None, item["uri"]))
    return links


def link_at(links: list[Link], point) -> Link | None:
    target = pymupdf.Point(point)
    return next((link for link in links if target in link.rect), None)


def outline(doc: pymupdf.Document) -> list[OutlineEntry]:
    """Table des matières (signets) du document, avec la position exacte de chaque titre."""
    entries = []
    for level, title, page, *rest in doc.get_toc(simple=False):
        dest = rest[0] if rest else {}
        to = dest.get("to") if dest.get("kind") in (pymupdf.LINK_GOTO, pymupdf.LINK_NAMED) else None
        entries.append(OutlineEntry(level, title, _target(doc, page - 1, to)))
    return entries


MAX_HEADING_PAGES = 600   # au-delà, la détection serait trop lente pour un simple clic
HEADING_LEVELS = 4


def detect_headings(doc: pymupdf.Document) -> list[OutlineEntry]:
    """Titres devinés d'après la mise en forme, pour un PDF sans signets.

    Un document Word imprimé en PDF perd ses signets mais garde ses styles : les titres
    (Titre 1, Titre 2…) sont plus grands ou en gras que le corps du texte. Le corps est la
    taille la plus fréquente ; chaque taille plus grande (et le gras isolé) devient un niveau.
    """
    lines, sizes, texts_per_page = [], {}, {}
    for page in doc.pages(0, min(doc.page_count, MAX_HEADING_PAGES)):
        rotate = page.rotation_matrix
        for block in page.get_text("dict")["blocks"]:
            if block.get("type") != 0:
                continue
            block_lines = [ln for ln in block["lines"] if "".join(s["text"] for s in ln["spans"]).strip()]
            for line in block_lines:
                spans = [s for s in line["spans"] if s["text"].strip()]
                text = " ".join("".join(s["text"] for s in line["spans"]).split())
                chars = sum(len(s["text"].strip()) for s in spans)
                size = round(max(spans, key=lambda s: len(s["text"].strip()))["size"] * 2) / 2
                bold = all(s["flags"] & 16 or "bold" in s["font"].lower() for s in spans)
                sizes[size] = sizes.get(size, 0) + chars
                y = (pymupdf.Rect(line["bbox"]) * rotate).y0
                lines.append((page.number, block["number"], len(block_lines), size, bold, text, y))
                texts_per_page.setdefault(text, set()).add(page.number)
    if not sizes:
        return []
    body = max(sizes, key=sizes.get)
    pages = min(doc.page_count, MAX_HEADING_PAGES)

    def is_heading(size, bold, text, block_size) -> bool:
        if not 2 <= len(text) <= 150 or not any(c.isalpha() for c in text):
            return False
        if len(texts_per_page[text]) >= 2 and len(texts_per_page[text]) > pages * 0.3:
            return False  # en-tête ou pied de page répété
        if size >= body + 1:
            return True
        # Même taille que le corps mais en gras, seul dans son paragraphe : un petit titre.
        return bold and size >= body and block_size <= 2 and not text.endswith((".", ":", ";", ","))

    found = []  # (page, bloc, taille, gras, texte, y) ; lignes d'un même titre fusionnées
    for page, block, block_size, size, bold, text, y in lines:
        if not is_heading(size, bold, text, block_size):
            continue
        if found and found[-1][:4] == (page, block, size, bold):
            found[-1] = (*found[-1][:4], f"{found[-1][4]} {text}", found[-1][5])
        else:
            found.append((page, block, size, bold, text, y))
    if not found or len(found) > len(lines) * 0.4:
        return []  # tout ressemble à un titre : la mise en forme ne dit rien d'utile

    styles = sorted({(size, bold) for _, _, size, bold, _, _ in found}, reverse=True)[:HEADING_LEVELS]
    level = {style: i + 1 for i, style in enumerate(styles)}
    return [OutlineEntry(level[(size, bold)], text, Target(page, y))
            for page, _, size, bold, text, y in found if (size, bold) in level]
