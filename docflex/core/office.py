"""Conversion des documents bureautiques via LibreOffice, s'il est installé.

LibreOffice pèse plusieurs centaines de Mo : il n'est pas embarqué dans
l'installeur. DocFlex le détecte et active les conversions correspondantes.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from functools import lru_cache
from pathlib import Path

_CANDIDATES = {
    "darwin": [
        "/Applications/LibreOffice.app/Contents/MacOS/soffice",
        str(Path.home() / "Applications/LibreOffice.app/Contents/MacOS/soffice"),
    ],
    "win32": [
        r"C:\Program Files\LibreOffice\program\soffice.exe",
        r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
    ],
}

TIMEOUT_S = 300


@lru_cache(maxsize=1)
def find_soffice() -> str | None:
    """Chemin de l'exécutable LibreOffice, ou None."""
    for name in ("soffice", "libreoffice"):
        found = shutil.which(name)
        if found:
            return found
    for candidate in _CANDIDATES.get(sys.platform, []):
        if Path(candidate).is_file():
            return candidate
    return None


def is_available() -> bool:
    return find_soffice() is not None


def convert(src: Path, target_ext: str, out_path: Path) -> Path:
    """Convertit `src` au format `target_ext` (ex. « pdf ») vers `out_path`."""
    soffice = find_soffice()
    if soffice is None:
        raise RuntimeError(
            "LibreOffice est requis pour cette conversion. "
            "Installez-le gratuitement depuis libreoffice.org puis relancez DocFlex."
        )

    with tempfile.TemporaryDirectory(prefix="docflex-") as tmp:
        tmp_dir = Path(tmp)
        # Profil dédié : évite les conflits avec une instance LibreOffice déjà ouverte.
        profile = (tmp_dir / "profile").as_uri()
        cmd = [
            soffice,
            f"-env:UserInstallation={profile}",
            "--headless",
            "--norestore",
            "--convert-to",
            target_ext,
            "--outdir",
            str(tmp_dir),
            str(src),
        ]
        kwargs = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=TIMEOUT_S, env=os.environ.copy(), **kwargs
        )
        produced = tmp_dir / f"{src.stem}.{target_ext}"
        if result.returncode != 0 or not produced.exists():
            detail = (result.stderr or result.stdout).strip()
            raise RuntimeError(f"LibreOffice n'a pas pu convertir le fichier. {detail}".strip())
        shutil.move(str(produced), out_path)
    return out_path
