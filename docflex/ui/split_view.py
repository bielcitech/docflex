"""Vue « Découper un PDF »."""

from __future__ import annotations

import html
from pathlib import Path

from PySide6.QtWidgets import QGridLayout, QLineEdit, QTextEdit, QVBoxLayout, QWidget

from docflex.core import split
from docflex.core.tasks import run_batch
from docflex.ui import theme
from docflex.ui.widgets import FileList, OutputChooser, TaskPanel, ViewHeader, card, combo, label
from docflex.utils.files import human_size

PREVIEW_MAX = 300  # au-delà, l'aperçu est tronqué
VALUE_FIELDS = {
    # mode → (libellé du champ, texte d'exemple, valeur par défaut)
    "ranges": ("Plages", "ex. 1-3, 4-6, 7-", ""),
    "extract": ("Pages", "ex. 1, 3, 5-8", ""),
    "chunk": ("Pages par fichier", "ex. 2", "2"),
}


class SplitView(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self._page_count = 0
        self._toc: list = []
        self._parts: list[split.Part] = []
        self._values = {mode: default for mode, (_, _, default) in VALUE_FIELDS.items()}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        layout.addWidget(ViewHeader(
            "Découper un PDF",
            "Séparer un PDF en plusieurs fichiers, ou en extraire certaines pages. L'original n'est pas modifié.",
        ))
        layout.addSpacing(4)

        self.files = FileList({"pdf"}, "PDF à découper", single=True)
        self.files.setFixedHeight(130)
        self.files.changed.connect(self._on_file_changed)
        self.files.rejected.connect(lambda _: self.panel.set_status("Seuls les fichiers PDF peuvent être découpés."))
        layout.addWidget(self.files)

        settings = card()
        grid = QGridLayout(settings)
        grid.setContentsMargins(16, 14, 16, 14)
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(10)
        grid.setColumnStretch(1, 1)
        grid.addWidget(label("Découper", "field"), 0, 0)
        self.mode_menu = combo(((text, mode) for mode, text in split.MODES.items()), width=320)
        self.mode_menu.currentIndexChanged.connect(lambda _: self._on_mode_changed())
        grid.addWidget(self.mode_menu, 0, 1)
        self.value_label = label("", "field")
        grid.addWidget(self.value_label, 1, 0)
        self.value_entry = QLineEdit()
        self.value_entry.setFixedWidth(320)
        self.value_entry.textChanged.connect(self._on_value_changed)
        grid.addWidget(self.value_entry, 1, 1)
        grid.addWidget(label("Enregistrer", "field"), 2, 0)
        self.output = OutputChooser()
        grid.addWidget(self.output, 2, 1)
        layout.addWidget(settings)

        preview = card()
        box = QVBoxLayout(preview)
        box.setContentsMargins(16, 12, 10, 8)
        self.preview_title = label("Aperçu", "field")
        box.addWidget(self.preview_title)
        self.preview = QTextEdit()
        self.preview.setObjectName("preview")
        self.preview.setReadOnly(True)
        self.preview.setLineWrapMode(QTextEdit.NoWrap)
        box.addWidget(self.preview)
        layout.addWidget(preview, 1)

        self.panel = TaskPanel("Découper", self._start,
                               "Plusieurs fichiers produits sont rangés dans un dossier « nom (découpé) ».")
        self.panel.busy_changed.connect(self._on_busy)
        layout.addWidget(self.panel)

        theme.current().changed.connect(self._refresh_preview)  # couleurs de l'aperçu
        self._on_mode_changed()

    # ------------------------------------------------------------ réactions

    @property
    def mode(self) -> str:
        return self.mode_menu.currentData()

    @property
    def source(self) -> Path | None:
        return self.files.paths[0] if self.files.paths else None

    def _on_file_changed(self, paths: list[Path]) -> None:
        self._page_count, self._toc = 0, []
        self.panel.set_status("")
        if paths:
            try:
                with split.open_pdf(paths[0]) as doc:
                    self._page_count, self._toc = doc.page_count, doc.get_toc()
            except Exception as exc:
                self.files.clear()  # rappelle _on_file_changed([])
                self.panel.set_status(f"{paths[0].name} : {exc}")
                return
        self._refresh_preview()

    def _on_mode_changed(self) -> None:
        field = VALUE_FIELDS.get(self.mode)
        self.value_label.setVisible(field is not None)
        self.value_entry.setVisible(field is not None)
        if field:
            text, hint, _ = field
            self.value_label.setText(text)
            self.value_entry.setPlaceholderText(hint)
            self.value_entry.blockSignals(True)
            self.value_entry.setText(self._values[self.mode])
            self.value_entry.blockSignals(False)
        self._refresh_preview()

    def _on_value_changed(self, text: str) -> None:
        if self.mode in self._values:
            self._values[self.mode] = text
        self._refresh_preview()

    def _refresh_preview(self) -> None:
        t = theme.current()
        lines: list[str] = []

        def line(text: str, tone: str | None = None, extra: str = "") -> None:
            color = f' style="color:{t.color(tone)}"' if tone else ""
            lines.append(f"<span{color}>{html.escape(text)}</span>{extra}")

        self._parts = []
        invalid = False
        src = self.source
        if src is None:
            self.preview_title.setText("Aperçu")
            line("Ajoutez un PDF pour voir les fichiers qui seront créés.", "muted")
        else:
            bookmarks = sum(1 for entry in self._toc if entry[0] == 1)
            info = f"{self._page_count} page{'s' if self._page_count > 1 else ''} · {human_size(src.stat().st_size)}"
            if bookmarks:
                info += f" · {bookmarks} signet{'s' if bookmarks > 1 else ''}"
            value = self.value_entry.text() if self.mode in VALUE_FIELDS else ""
            try:
                self._parts = split.plan_split(self._page_count, self.mode, value, self._toc)
            except ValueError as exc:
                self.preview_title.setText(f"Aperçu — {info}")
                invalid = bool(value.strip()) or self.mode == "bookmarks"
                line(str(exc), "danger" if invalid else "muted")
            else:
                n = len(self._parts)
                self.preview_title.setText(f"Aperçu — {info} → {n} fichier{'s' if n > 1 else ''}")
                if n > 1:
                    line(f"Dans le dossier « {src.stem} (découpé) » :", "muted")
                muted = t.color("muted")
                for part in self._parts[:PREVIEW_MAX]:
                    pages = f"page {part.describe()}" if part.page_count == 1 else f"pages {part.describe()}"
                    detail = f'<span style="color:{muted}">&nbsp;&nbsp;&nbsp;{pages} ({part.page_count} p.)</span>'
                    line(f"{src.stem}_{part.suffix}.pdf", extra=detail)
                if n > PREVIEW_MAX:
                    line(f"… et {n - PREVIEW_MAX} autres fichiers", "muted")
        self.preview.setHtml("<br>".join(lines))
        self.value_entry.setProperty("invalid", invalid)
        theme.repolish(self.value_entry)
        self.panel.set_ready(bool(self._parts))

    def _on_busy(self, busy: bool) -> None:
        for widget in (self.mode_menu, self.value_entry, self.files):
            widget.setEnabled(not busy)

    # -------------------------------------------------------------- travail

    def _start(self) -> None:
        src, parts, out_dir = self.source, list(self._parts), self.output.out_dir
        self.panel.start(lambda progress: run_batch(
            [src], lambda path, prog: (split.split_pdf(path, parts, out_dir, prog), None), progress
        ))
