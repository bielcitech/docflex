"""Vue « Convertir »."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QComboBox, QGridLayout, QVBoxLayout, QWidget

from docflex.core import converters
from docflex.core.converters import TARGET_LABELS
from docflex.ui.widgets import FileList, OutputChooser, TaskPanel, ViewHeader, card, combo, label
from docflex.utils.files import ext_of

ACCEPTED_EXTS = frozenset().union(*(c.sources for c in converters.CONVERSIONS))
LIBREOFFICE_SUFFIX = "  —  LibreOffice requis"


class ConvertView(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self._option_menus: dict[str, QComboBox] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        layout.addWidget(ViewHeader(
            "Convertir des fichiers",
            "PDF, images, Excel, CSV, Word… Tout est traité sur cet ordinateur, sans connexion.",
        ))
        layout.addSpacing(4)

        self.files = FileList(ACCEPTED_EXTS, "Fichiers à convertir")
        self.files.changed.connect(self._on_files_changed)
        self.files.rejected.connect(self._on_rejected)
        layout.addWidget(self.files, 1)

        settings = card()
        grid = QGridLayout(settings)
        grid.setContentsMargins(16, 14, 16, 14)
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(10)
        grid.setColumnStretch(1, 1)
        grid.addWidget(label("Convertir en", "field"), 0, 0)
        self.target_menu = QComboBox()
        self.target_menu.setFixedWidth(320)
        self.target_menu.currentIndexChanged.connect(lambda _: self._on_target_changed())
        grid.addWidget(self.target_menu, 0, 1)
        self.options = QGridLayout()
        self.options.setHorizontalSpacing(10)
        self.options.setVerticalSpacing(8)
        grid.addLayout(self.options, 1, 1)  # aligné sous le menu « Convertir en »
        grid.addWidget(label("Enregistrer", "field"), 2, 0)
        self.output = OutputChooser()
        grid.addWidget(self.output, 2, 1)
        layout.addWidget(settings)

        self.panel = TaskPanel(
            "Convertir", self._start,
            "Les fichiers convertis sont créés à côté des originaux, sauf si vous choisissez un dossier.",
        )
        self.panel.busy_changed.connect(self._on_busy)
        layout.addWidget(self.panel)

        self._on_files_changed([])

    @property
    def target(self) -> str | None:
        return self.target_menu.currentData()

    # ------------------------------------------------------------ réactions

    def _on_files_changed(self, paths: list[Path]) -> None:
        previous = self.target
        self.target_menu.blockSignals(True)
        self.target_menu.clear()
        status = ""
        for target in converters.targets_for(paths):
            available = all(converters.find_conversion(ext_of(p), target).available() for p in paths)
            text = TARGET_LABELS.get(target, target.upper())
            self.target_menu.addItem(text if available else text + LIBREOFFICE_SUFFIX, target)
        if not paths:
            self.target_menu.addItem("Ajoutez d'abord des fichiers", None)
        elif self.target_menu.count() == 0:
            self.target_menu.addItem("Aucun format commun", None)
            exts = ", ".join(sorted({ext_of(p).upper() for p in paths}))
            status = f"Ces types ne se convertissent pas vers un même format ({exts})."
        index = self.target_menu.findData(previous) if previous else -1
        self.target_menu.setCurrentIndex(max(index, 0))
        self.target_menu.setEnabled(self.target is not None)
        self.target_menu.blockSignals(False)
        self.panel.set_status(status)
        self._on_target_changed()

    def _on_rejected(self, paths: list[Path]) -> None:
        names = ", ".join(p.name for p in paths[:3]) + ("…" if len(paths) > 3 else "")
        self.panel.set_status(f"Format non pris en charge, ignoré : {names}")

    def _on_target_changed(self) -> None:
        while self.options.count():
            old = self.options.takeAt(0).widget()
            old.hide()
            old.deleteLater()
        self._option_menus.clear()
        target = self.target
        for i, opt in enumerate(converters.options_for(self.files.paths, target) if target else []):
            menu = combo(opt.choices, opt.default, width=250)
            self._option_menus[opt.key] = menu
            row, col = divmod(i, 2)
            self.options.addWidget(label(opt.label, "muted"), row, col * 2)
            self.options.addWidget(menu, row, col * 2 + 1)
        self.options.setColumnStretch(4, 1)
        needs_lo = self.target_menu.currentText().endswith(LIBREOFFICE_SUFFIX)
        self.panel.set_ready(
            target is not None and not needs_lo,
            "Installez LibreOffice (gratuit, libreoffice.org) pour activer ce format." if needs_lo else None,
        )

    def _on_busy(self, busy: bool) -> None:
        self.target_menu.setEnabled(self.target is not None and not busy)
        for menu in self._option_menus.values():
            menu.setEnabled(not busy)

    # -------------------------------------------------------------- travail

    def _start(self) -> None:
        target, paths, out_dir = self.target, list(self.files.paths), self.output.out_dir
        opts = {key: menu.currentData() for key, menu in self._option_menus.items()}
        self.panel.start(lambda progress: converters.convert_files(paths, target, opts, out_dir, progress))
