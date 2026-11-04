"""Gabarit commun des outils PDF : fichiers, réglages, destination, barre d'action."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from PySide6.QtWidgets import QGridLayout, QVBoxLayout, QWidget

from docflex.core.tasks import Progress, run_batch
from docflex.ui.widgets import FileList, OutputChooser, TaskPanel, card, label

# Une tâche par fichier : (fichier, dossier de sortie ou None, progression) → (fichiers créés, note).
FileJob = Callable[[Path, "Path | None", Progress], "tuple[list[Path], str | None]"]


class ToolPage(QWidget):
    """Gabarit commun : fichiers, réglages, destination, barre d'action."""

    run_label = ""
    intro = ""
    has_output = True
    reorderable = False

    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        self.files = FileList({"pdf"}, "Choisir des PDF", reorderable=self.reorderable)
        self.files.changed.connect(lambda _: self.refresh())
        self.files.rejected.connect(lambda _: self.panel.set_status("Seuls les fichiers PDF sont acceptés ici."))
        layout.addWidget(self.files, 1)

        settings = card()
        self._captions: dict[QWidget, QWidget] = {}
        self.grid = QGridLayout(settings)
        self.grid.setContentsMargins(16, 14, 16, 14)
        self.grid.setHorizontalSpacing(14)
        self.grid.setVerticalSpacing(8)
        self.grid.setColumnStretch(1, 1)
        self.build_settings()
        self.output = OutputChooser()
        if self.has_output:
            self.row("Enregistrer", self.output)
        layout.addWidget(settings)

        self.panel = TaskPanel(self.run_label, self.start, self.intro, log_height=80)
        self.panel.busy_changed.connect(lambda busy: (self.files.setEnabled(not busy), settings.setEnabled(not busy)))
        layout.addWidget(self.panel)
        self.refresh()

    def row(self, text: str, widget: QWidget) -> QWidget:
        r = self.grid.rowCount()
        if text:
            caption = label(text, "field")
            self.grid.addWidget(caption, r, 0)
            self._captions[widget] = caption
        self.grid.addWidget(widget, r, 1)
        return widget

    def show_row(self, widget: QWidget, visible: bool) -> None:
        widget.setVisible(visible)
        if widget in self._captions:
            self._captions[widget].setVisible(visible)

    # À redéfinir -------------------------------------------------

    def build_settings(self) -> None:
        pass

    def problem(self) -> str | None:
        """Message expliquant pourquoi on ne peut pas lancer, ou None si tout est prêt."""
        return "" if not self.files.paths else None

    def job(self) -> FileJob:
        raise NotImplementedError

    # -------------------------------------------------------------

    def refresh(self) -> None:
        problem = self.problem()
        self.panel.set_ready(problem is None, problem or "")

    def start(self) -> None:
        paths, out_dir, job = list(self.files.paths), self.output.out_dir, self.job()
        self.panel.start(lambda progress: run_batch(
            paths, lambda path, prog: job(path, out_dir, prog), progress
        ))


def dest_dir(path: Path, out_dir: Path | None) -> Path:
    return out_dir or path.parent
