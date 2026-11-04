"""Aides pour les chemins de fichiers."""

from __future__ import annotations

from pathlib import Path


def unique_path(path: Path) -> Path:
    """Retourne `path`, ou `nom (2).ext`, `nom (3).ext`… s'il existe déjà."""
    if not path.exists():
        return path
    n = 2
    while True:
        candidate = path.with_name(f"{path.stem} ({n}){path.suffix}")
        if not candidate.exists():
            return candidate
        n += 1


def human_size(n: float) -> str:
    """Taille lisible, à la française : « 1,4 Mo »."""
    for unit in ("o", "Ko", "Mo", "Go"):
        if n < 1024 or unit == "Go":
            return f"{n:.0f} {unit}" if unit == "o" else f"{n:.1f} {unit}".replace(".", ",")
        n /= 1024
    return ""


def ext_of(path: Path) -> str:
    """Extension en minuscules, sans le point (« PDF » → « pdf »)."""
    return path.suffix.lower().lstrip(".")
