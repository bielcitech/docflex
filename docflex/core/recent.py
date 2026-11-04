"""Historique des documents ouverts : récents, favoris, page de reprise.

Stocké dans un petit fichier JSON local ; rien ne quitte la machine.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, replace
from pathlib import Path

MAX_RECENT = 100  # au-delà, les plus anciens non favoris sont oubliés
SORTS = {"opened": "Ouverts récemment", "name": "Nom", "size": "Taille"}


@dataclass(frozen=True)
class RecentEntry:
    path: str
    opened_at: float          # horodatage Unix de la dernière ouverture
    page_count: int = 0
    last_page: int = 0        # page affichée à la fermeture (à partir de 0)
    size: int = 0             # octets
    favorite: bool = False

    @property
    def name(self) -> str:
        return Path(self.path).stem

    @property
    def exists(self) -> bool:
        return Path(self.path).is_file()


class RecentStore:
    def __init__(self, file: Path) -> None:
        self.file = file
        self._entries: dict[str, RecentEntry] = {}
        self._load()

    # ------------------------------------------------------------ fichier

    def _load(self) -> None:
        try:
            raw = json.loads(self.file.read_text(encoding="utf-8"))
            for item in raw.get("documents", []):
                entry = RecentEntry(**{k: v for k, v in item.items() if k in RecentEntry.__dataclass_fields__})
                self._entries[entry.path] = entry
        except (OSError, ValueError, TypeError):
            self._entries = {}  # fichier absent ou abîmé : on repart d'une liste vide

    def save(self) -> None:
        self.file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.file.with_suffix(".tmp")
        tmp.write_text(json.dumps({"documents": [asdict(e) for e in self._entries.values()]},
                                  ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.file)

    # ------------------------------------------------------------ actions

    def touch(self, path: Path, page_count: int) -> RecentEntry:
        """Document ouvert : en tête de liste, page de reprise conservée."""
        key = str(path.resolve())
        previous = self._entries.get(key)
        size = path.stat().st_size if path.is_file() else 0
        entry = RecentEntry(key, time.time(), page_count, previous.last_page if previous else 0, size,
                            previous.favorite if previous else False)
        if entry.last_page >= page_count:
            entry = replace(entry, last_page=0)  # document raccourci depuis
        self._entries[key] = entry
        self._trim()
        self.save()
        return entry

    def set_page(self, path: Path, page: int) -> None:
        key = str(path.resolve())
        if key in self._entries and self._entries[key].last_page != page:
            self._entries[key] = replace(self._entries[key], last_page=page)

    def toggle_favorite(self, path: str) -> bool:
        entry = self._entries[path]
        self._entries[path] = replace(entry, favorite=not entry.favorite)
        self.save()
        return not entry.favorite

    def remove(self, path: str) -> None:
        self._entries.pop(path, None)
        self.save()

    def clear(self, keep_favorites: bool = True) -> None:
        self._entries = {k: e for k, e in self._entries.items() if keep_favorites and e.favorite}
        self.save()

    def get(self, path: Path) -> RecentEntry | None:
        return self._entries.get(str(path.resolve()))

    def _trim(self) -> None:
        ordinary = sorted((e for e in self._entries.values() if not e.favorite), key=lambda e: e.opened_at)
        for entry in ordinary[: max(0, len(ordinary) - MAX_RECENT)]:
            del self._entries[entry.path]

    # ------------------------------------------------------------ lecture

    def entries(self, favorites_only: bool = False, query: str = "", sort: str = "opened") -> list[RecentEntry]:
        query = query.strip().lower()
        result = [e for e in self._entries.values()
                  if (not favorites_only or e.favorite) and (not query or query in Path(e.path).name.lower())]
        key = {
            "opened": lambda e: -e.opened_at,
            "name": lambda e: e.name.lower(),
            "size": lambda e: -e.size,
        }[sort]
        return sorted(result, key=key)


def when(timestamp: float, now: float | None = None) -> str:
    """« Aujourd'hui, 14:32 », « Hier, 09:05 », « 3 nov. », « 3 nov. 2025 »."""
    months = ("janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc.")
    moment, today = time.localtime(timestamp), time.localtime(now if now is not None else time.time())
    days = (time.mktime(today[:3] + (0, 0, 0, 0, 0, -1)) - time.mktime(moment[:3] + (0, 0, 0, 0, 0, -1))) // 86400
    if days == 0:
        return f"Aujourd'hui, {moment.tm_hour:02d}:{moment.tm_min:02d}"
    if days == 1:
        return f"Hier, {moment.tm_hour:02d}:{moment.tm_min:02d}"
    label = f"{moment.tm_mday} {months[moment.tm_mon - 1]}"
    return label if moment.tm_year == today.tm_year else f"{label} {moment.tm_year}"
