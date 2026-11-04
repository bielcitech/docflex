"""Préférences et données locales conservées d'un lancement à l'autre."""

from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QSettings, QStandardPaths


def settings() -> QSettings:
    # Les tests pointent vers un fichier temporaire pour ne pas toucher aux préférences réelles.
    path = os.environ.get("DOCFLEX_SETTINGS_FILE")
    return QSettings(path, QSettings.IniFormat) if path else QSettings("XenoviaLabs", "DocFlex")


def data_dir() -> Path:
    """Dossier des données de l'application (historique, miniatures en cache)."""
    override = os.environ.get("DOCFLEX_DATA_DIR")
    base = Path(override) if override else Path(QStandardPaths.writableLocation(QStandardPaths.AppDataLocation))
    base.mkdir(parents=True, exist_ok=True)
    return base
