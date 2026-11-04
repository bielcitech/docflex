"""Vue « Compresser »."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QCheckBox, QGridLayout, QVBoxLayout, QWidget

from docflex.core import compress
from docflex.core.tasks import run_batch
from docflex.ui.widgets import FileList, OutputChooser, SegmentedControl, TaskPanel, ViewHeader, card, label
from docflex.utils.files import ext_of


class CompressView(QWidget):
    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        layout.addWidget(ViewHeader(
            "Compresser",
            "Réduire le poids de PDF et d'images (JPG, PNG, WebP). Les originaux ne sont pas modifiés.",
        ))
        layout.addSpacing(4)

        self.files = FileList(compress.ACCEPTED_EXTS, "Fichiers à compresser")
        self.files.changed.connect(self._on_files_changed)
        self.files.rejected.connect(self._on_rejected)
        layout.addWidget(self.files, 1)

        settings = card()
        grid = QGridLayout(settings)
        grid.setContentsMargins(16, 14, 16, 14)
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(8)
        grid.setColumnStretch(1, 1)
        grid.addWidget(label("Niveau", "field"), 0, 0)
        self.level = SegmentedControl((level.label, key) for key, level in compress.LEVELS.items())
        self.level.set_value("recommended")
        self.level.changed.connect(lambda _: self._on_level_changed())
        grid.addWidget(self.level, 0, 1)
        self.level_hint = label("", "muted")
        grid.addWidget(self.level_hint, 1, 1)
        self.gray = QCheckBox("Convertir les PDF en niveaux de gris (gain supplémentaire)")
        grid.addWidget(self.gray, 2, 1)
        grid.addWidget(label("Enregistrer", "field"), 3, 0)
        self.output = OutputChooser()
        grid.addWidget(self.output, 3, 1)
        layout.addWidget(settings)

        self.panel = TaskPanel(
            "Compresser", self._start,
            "Chaque fichier compressé est nommé « nom_compressé ». S'il n'est pas plus léger, il n'est pas créé.",
        )
        self.panel.busy_changed.connect(self._on_busy)
        layout.addWidget(self.panel)

        self._on_level_changed()
        self._on_files_changed([])

    def _on_files_changed(self, paths: list[Path]) -> None:
        self.panel.set_ready(bool(paths), "")
        self._refresh_gray()

    def _on_rejected(self, paths: list[Path]) -> None:
        names = ", ".join(p.name for p in paths[:3]) + ("…" if len(paths) > 3 else "")
        self.panel.set_status(f"Seuls les PDF, JPG, PNG et WebP se compressent, ignoré : {names}")

    def _on_level_changed(self) -> None:
        self.level_hint.setText(compress.LEVELS[self.level.value()].description)
        self._refresh_gray()

    def _refresh_gray(self) -> None:
        # Le passage en gris se fait pendant la recompression des images : pas au niveau « Légère ».
        has_pdf = any(ext_of(p) == "pdf" for p in self.files.paths)
        lossy = compress.LEVELS[self.level.value()].dpi_threshold is not None
        if not (has_pdf and lossy):
            self.gray.setChecked(False)
        self.gray.setEnabled(has_pdf and lossy and not self.panel.busy)

    def _on_busy(self, busy: bool) -> None:
        self.level.setEnabled(not busy)
        self.files.setEnabled(not busy)
        self._refresh_gray()

    def _start(self) -> None:
        paths, level, out_dir = list(self.files.paths), self.level.value(), self.output.out_dir
        gray = self.gray.isChecked()
        self.panel.start(lambda progress: run_batch(
            paths, lambda path, prog: compress.compress_file(path, out_dir, level, gray, prog), progress
        ))
