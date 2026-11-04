"""Types communs aux traitements : progression, annulation, rapport."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

# progress(fraction 0..1, message ou None) ; peut lever `Cancelled`.
Progress = Callable[[float, "str | None"], None]


class Cancelled(Exception):
    """Levée par le callback de progression quand l'utilisateur annule."""


@dataclass
class Report:
    outputs: list[Path] = field(default_factory=list)
    errors: list[tuple[Path, str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)  # une ligne par fichier traité


def no_progress(fraction: float, message: str | None) -> None:
    pass


def run_batch(
    paths: list[Path],
    per_file: Callable[[Path, Progress], tuple[list[Path], "str | None"]],
    progress: Progress = no_progress,
) -> Report:
    """Applique `per_file` à chaque fichier ; une erreur n'arrête pas le lot.

    `per_file(path, progress)` retourne (fichiers créés, note éventuelle).
    """
    report = Report()
    total = len(paths) or 1
    for i, path in enumerate(paths):
        def sub_progress(fraction: float, message: str | None, _i=i, _name=path.name) -> None:
            progress((_i + fraction) / total, f"{_name} — {message}" if message else _name)

        sub_progress(0.0, None)
        try:
            outputs, note = per_file(path, sub_progress)
            report.outputs.extend(outputs)
            if note:
                report.notes.append(note)
        except Cancelled:
            raise
        except Exception as exc:
            report.errors.append((path, str(exc) or exc.__class__.__name__))
        progress((i + 1) / total, path.name)
    return report
