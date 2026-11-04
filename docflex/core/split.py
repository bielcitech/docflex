"""Découpage de PDF.

`plan_split` calcule les fichiers à produire sans rien écrire (l'interface
l'utilise pour l'aperçu) ; `split_pdf` les écrit.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pymupdf

from docflex.core.tasks import Progress, no_progress
from docflex.utils.files import unique_path

MODES = {
    "ranges": "Par plages (un fichier par plage)",
    "extract": "Extraire des pages (un seul fichier)",
    "each": "Une page par fichier",
    "chunk": "Toutes les N pages",
    "bookmarks": "Par signets (chapitres)",
}

METADATA_KEYS = ("title", "author", "subject", "keywords", "creator", "producer")

# (début, fin) : numéros de page à partir de 0, fin incluse.
PageRange = tuple[int, int]


@dataclass(frozen=True)
class Part:
    suffix: str  # ajouté au nom du fichier : « rapport_p1-3.pdf »
    ranges: tuple[PageRange, ...]

    @property
    def page_count(self) -> int:
        return sum(b - a + 1 for a, b in self.ranges)

    def describe(self) -> str:
        return ", ".join(f"{a + 1}" if a == b else f"{a + 1}-{b + 1}" for a, b in self.ranges)


_ITEM_RE = re.compile(r"^(\d*)\s*-\s*(\d*)$|^(\d+)$")


def parse_page_spec(spec: str, page_count: int) -> list[PageRange]:
    """« 1-3, 5, 8- » → [(0, 2), (4, 4), (7, dernière)].

    « -3 » va du début à la page 3, « 8- » de la page 8 à la fin.
    Lève ValueError avec un message lisible par l'utilisateur.
    """
    items = [s.strip() for s in re.split(r"[,;]", spec) if s.strip()]
    if not items:
        raise ValueError("Indiquez des pages, par exemple : 1-3, 5, 8-")
    ranges: list[PageRange] = []
    for item in items:
        m = _ITEM_RE.match(item)
        if not m:
            raise ValueError(f"« {item} » n'est pas compris. Exemples valides : 4   2-6   9-   -3")
        if m.group(3):
            start = end = int(m.group(3))
        else:
            start = int(m.group(1)) if m.group(1) else 1
            end = int(m.group(2)) if m.group(2) else page_count
        if start < 1 or end > page_count:
            raise ValueError(f"« {item} » : le document a {page_count} page{'s' if page_count > 1 else ''}.")
        if start > end:
            raise ValueError(f"« {item} » : la première page doit précéder la dernière.")
        ranges.append((start - 1, end - 1))
    return ranges


def _range_suffix(a: int, b: int) -> str:
    return f"p{a + 1}" if a == b else f"p{a + 1}-{b + 1}"


def _safe(title: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|\s]+', " ", title).strip()
    return cleaned[:60].rstrip() or "sans titre"


def plan_split(page_count: int, mode: str, value: str = "", toc: list | None = None) -> list[Part]:
    """Liste des fichiers que produira le découpage. Lève ValueError si `value` est invalide."""
    if mode == "ranges":
        return [Part(_range_suffix(a, b), ((a, b),)) for a, b in parse_page_spec(value, page_count)]
    if mode == "extract":
        ranges = parse_page_spec(value, page_count)
        return [Part("extrait", tuple(ranges))]
    if mode == "each":
        return [Part(f"p{i + 1}", ((i, i),)) for i in range(page_count)]
    if mode == "chunk":
        try:
            n = int(value)
        except ValueError:
            n = 0
        if n < 1:
            raise ValueError("Indiquez un nombre de pages par fichier (1 ou plus).")
        return [
            Part(_range_suffix(a, min(a + n, page_count) - 1), ((a, min(a + n, page_count) - 1),))
            for a in range(0, page_count, n)
        ]
    if mode == "bookmarks":
        # Signets de premier niveau dont la page cible existe, dans l'ordre des pages.
        starts: list[tuple[int, str]] = []
        for level, title, page, *_ in toc or []:
            if level == 1 and 1 <= page <= page_count and (not starts or page - 1 > starts[-1][0]):
                starts.append((page - 1, title))
        if not starts:
            raise ValueError("Ce PDF n'a pas de signets de premier niveau.")
        parts = []
        if starts[0][0] > 0:
            parts.append(Part("00 Début", ((0, starts[0][0] - 1),)))
        width = max(2, len(str(len(starts))))
        for i, (start, title) in enumerate(starts):
            end = starts[i + 1][0] - 1 if i + 1 < len(starts) else page_count - 1
            parts.append(Part(f"{i + 1:0{width}d} {_safe(title)}", ((start, end),)))
        return parts
    raise ValueError(f"Mode inconnu : {mode}")


def open_pdf(src: Path) -> pymupdf.Document:
    doc = pymupdf.open(src)
    if not doc.is_pdf:
        doc.close()
        raise ValueError("Ce fichier n'est pas un PDF.")
    if doc.needs_pass:
        doc.close()
        raise ValueError("Le PDF est protégé par un mot de passe.")
    return doc


def split_pdf(src: Path, parts: list[Part], out_dir: Path | None = None, progress: Progress = no_progress) -> list[Path]:
    """Écrit un PDF par `Part`. Au-delà d'un fichier, ils sont rangés dans « <nom> (découpé) »."""
    dest = out_dir or src.parent
    if len(parts) > 1:
        dest = unique_path(dest / f"{src.stem} (découpé)")
        dest.mkdir(parents=True)
    outputs = []
    with open_pdf(src) as doc:
        for i, part in enumerate(parts):
            progress(i / len(parts), f"Fichier {i + 1}/{len(parts)}")
            with pymupdf.open() as new:
                for a, b in part.ranges:
                    new.insert_pdf(doc, from_page=a, to_page=b)
                meta = {k: v for k, v in doc.metadata.items() if k in METADATA_KEYS and v}
                meta["title"] = f"{meta.get('title') or src.stem} — {part.suffix}"
                new.set_metadata(meta)
                out = unique_path(dest / f"{src.stem}_{part.suffix}.pdf")
                new.save(out, garbage=3, deflate=True)
            outputs.append(out)
    return outputs
