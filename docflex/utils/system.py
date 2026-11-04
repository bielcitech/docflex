"""Interactions avec le système d'exploitation."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def reveal(path: Path) -> None:
    """Ouvre le dossier contenant `path` (ou `path` lui-même si c'est un dossier)."""
    folder = path if path.is_dir() else path.parent
    if sys.platform == "darwin":
        subprocess.Popen(["open", str(folder)])
    elif sys.platform == "win32":
        os.startfile(folder)  # type: ignore[attr-defined]
    else:
        subprocess.Popen(["xdg-open", str(folder)])
