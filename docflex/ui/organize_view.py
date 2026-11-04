"""Vue « Organiser les pages »."""

from __future__ import annotations

import string
import sys
from pathlib import Path
from typing import Callable

import pymupdf
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QKeySequence, QShortcut, QTransform
from PySide6.QtWidgets import (
    QApplication, QDialog, QFileDialog, QHBoxLayout, QSizePolicy, QSlider, QStackedLayout,
    QVBoxLayout, QWidget,
)

from docflex.core import organize
from docflex.core.organize import PageRef
from docflex.core.split import open_pdf
from docflex.core.tasks import Cancelled, Report
from docflex.ui.crop_dialog import CropDialog
from docflex.ui.page_grid import PageGrid, ThumbnailLoader
from docflex.ui.widgets import FileList, TaskPanel, ViewHeader, ask, button, label
from docflex.utils.files import unique_path

HISTORY_MAX = 200
PREVIEW_SIDE = 1000  # px : plus grand côté de l'aperçu de recadrage


def _keys(sequence) -> str:
    """Raccourci dans la notation du système : « ⌘D » sur Mac, « Ctrl+D » ailleurs."""
    if isinstance(sequence, QKeySequence.StandardKey):
        bindings = QKeySequence.keyBindings(sequence)
        sequence = bindings[0] if bindings else QKeySequence()
    return QKeySequence(sequence).toString(QKeySequence.NativeText)


class OrganizeView(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.sources: list[Path] = []
        self.pages: list[PageRef] = []
        self._undo: list[list[PageRef]] = []
        self._redo: list[list[PageRef]] = []
        self._saved: list[PageRef] = []
        self._loaders: list[ThumbnailLoader] = []
        # Documents ouverts dans le thread de l'interface (aperçus, marges) ; les miniatures ont les leurs.
        self._docs: dict[int, pymupdf.Document] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        layout.addWidget(ViewHeader(
            "Organiser les pages",
            "Réordonnez, pivotez, dupliquez ou supprimez des pages, et ajoutez celles d'autres PDF.",
        ))
        layout.addSpacing(4)

        self._stack = QStackedLayout()
        self.files = FileList({"pdf"}, "PDF à organiser", single=True)
        self.files.changed.connect(lambda paths: paths and self.open(paths[0]))
        self.files.rejected.connect(lambda _: self.panel.set_status("Seuls les fichiers PDF peuvent être organisés."))
        self._stack.addWidget(self.files)
        self._stack.addWidget(self._build_editor())
        layout.addLayout(self._stack, 1)

        self.panel = TaskPanel("Enregistrer…", self._save,
                               "Le PDF organisé est enregistré sous un nouveau nom ; l'original reste intact.",
                               log_height=64)
        self.panel.busy_changed.connect(lambda busy: self.editor.setEnabled(not busy))
        self.panel.done.connect(self._on_saved)
        layout.addWidget(self.panel)

        shortcuts: list[tuple[QKeySequence | str, Callable[[], None]]] = [
            (QKeySequence.Delete, self.delete), (QKeySequence(Qt.Key_Backspace), self.delete),
            (QKeySequence.Undo, self.undo), (QKeySequence.Redo, self.redo),
            (QKeySequence("Ctrl+D"), self.duplicate),
            (QKeySequence("Ctrl+Left"), lambda: self.rotate(-90)), (QKeySequence("Ctrl+Right"), lambda: self.rotate(90)),
        ]
        for keys, action in shortcuts:
            shortcut = QShortcut(keys, self.editor)
            shortcut.setContext(Qt.WidgetWithChildrenShortcut)
            shortcut.activated.connect(action)

        self._refresh()

    # ------------------------------------------------- construction

    def _build_editor(self) -> QWidget:
        self.editor = QWidget()
        box = QVBoxLayout(self.editor)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(8)

        top = QHBoxLayout()
        self.info = label("", "field")
        self.info.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        top.addWidget(self.info, 1)
        self.actions = {"undo": button("Annuler", on_click=self.undo), "redo": button("Rétablir", on_click=self.redo)}
        top.addWidget(self.actions["undo"])
        top.addWidget(self.actions["redo"])
        top.addSpacing(10)
        top.addWidget(button("Ajouter un PDF…", on_click=self._browse_more))
        top.addWidget(button("Fermer", on_click=self.close_document))
        box.addLayout(top)

        tools = QHBoxLayout()
        tools.setSpacing(6)
        groups = [
            {"left": button("↺", on_click=lambda: self.rotate(-90)),
             "right": button("↻", on_click=lambda: self.rotate(90))},
            {"crop": button("Recadrer…", on_click=self.crop_dialog),
             "trim": button("Marges auto", on_click=self.trim_margins)},
            {"duplicate": button("Dupliquer", on_click=self.duplicate),
             "blank": button("Page blanche", on_click=self.add_blank),
             "delete": button("Supprimer", "danger", self.delete)},
            {"reverse": button("Inverser", on_click=self.reverse)},
        ]
        for group in groups:
            for widget in group.values():
                tools.addWidget(widget)
            tools.addSpacing(10)
            self.actions.update(group)
        tools.addStretch()
        box.addLayout(tools)
        tips = {
            "left": f"Pivoter de 90° vers la gauche ({_keys('Ctrl+Left')})",
            "right": f"Pivoter de 90° vers la droite ({_keys('Ctrl+Right')})",
            "crop": "Recadrer la page à la main (double-clic sur une page)",
            "trim": "Rogner automatiquement les marges blanches de chaque page sélectionnée",
            "duplicate": f"Dupliquer la sélection ({_keys('Ctrl+D')})",
            "blank": "Insérer une page blanche après la sélection",
            "delete": f"Supprimer la sélection ({_keys(QKeySequence.Delete)})",
            "reverse": "Inverser la sélection, ou tout le document si une seule page est choisie",
            "undo": f"Annuler ({_keys(QKeySequence.Undo)})", "redo": f"Rétablir ({_keys(QKeySequence.Redo)})",
        }
        for key, widget in self.actions.items():
            widget.setToolTip(tips[key])

        self.grid = PageGrid()
        self.grid.itemSelectionChanged.connect(self._refresh_actions)
        self.grid.moved.connect(lambda rows, target: self._apply(organize.move_pages(self.pages, rows, target)))
        self.grid.files_dropped.connect(self.add_documents)
        self.grid.itemDoubleClicked.connect(lambda item: self.crop_dialog(self.grid.row(item)))
        box.addWidget(self.grid, 1)

        bottom = QHBoxLayout()
        modifier = "⌘" if sys.platform == "darwin" else "Ctrl"
        hint = label(f"Glisser pour déplacer · Maj ou {modifier} : plusieurs pages · "
                     "double-clic : recadrer · déposez un PDF pour l'ajouter", "muted")
        hint.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        bottom.addWidget(hint, 1)
        bottom.addWidget(label("Taille", "muted"))
        self.zoom = QSlider(Qt.Horizontal)
        self.zoom.setRange(90, 220)
        self.zoom.setValue(140)
        self.zoom.setFixedWidth(120)
        self.zoom.valueChanged.connect(lambda w: self.grid.set_thumb_width(w))
        bottom.addWidget(self.zoom)
        box.addLayout(bottom)
        return self.editor

    # ------------------------------------------------- documents

    def open(self, path: Path) -> None:
        self._stop_loaders()
        self.sources, self.pages, self._undo, self._redo = [], [], [], []
        self.grid.forget()
        if self.add_documents([path]):
            self._saved = list(self.pages)
            self.panel.set_status("")
        self._refresh()

    def add_documents(self, paths: list[Path]) -> bool:
        added = []
        for path in paths:
            try:
                with open_pdf(path) as doc:
                    count = doc.page_count
            except Exception as exc:
                self.panel.set_status(f"{path.name} : {exc}")
                continue
            source = len(self.sources)
            self.sources.append(path)
            added += organize.pages_of(source, count)
            loader = ThumbnailLoader(source, path, self)
            loader.rendered.connect(self.grid.add_thumbnail)
            self._loaders.append(loader)
            loader.start()
        if not added:
            if not self.sources:
                self.files.clear()
            return False
        if len(self.pages) == 0 and not self._undo:  # ouverture : pas d'étape d'annulation
            self.pages = added
            self._show(selected=[])
        else:
            start = len(self.pages)
            self._apply((self.pages + added, list(range(start, start + len(added)))))
        return True

    def close_document(self) -> None:
        if self.dirty and not self.confirm_discard():
            return
        self._stop_loaders()
        self.sources, self.pages, self._undo, self._redo, self._saved = [], [], [], [], []
        self.grid.forget()
        self.files.clear()
        self._refresh()

    def shutdown(self) -> None:
        """À la fermeture de l'application : Qt 6 s'arrête net si un thread tourne encore."""
        self._stop_loaders()
        self.panel.shutdown()

    @property
    def dirty(self) -> bool:
        return self.pages != self._saved

    def confirm_discard(self) -> bool:
        return ask(self, "Modifications non enregistrées",
                   "Le document a été modifié. Fermer sans enregistrer ?", "Fermer sans enregistrer")

    def _browse_more(self) -> None:
        chosen, _ = QFileDialog.getOpenFileNames(self, "Ajouter les pages d'un PDF", "", "PDF (*.pdf)")
        if chosen:
            self.add_documents([Path(c) for c in chosen])

    def _stop_loaders(self) -> None:
        for loader in self._loaders:
            loader.requestInterruption()
        for loader in self._loaders:
            loader.wait()
        self._loaders.clear()
        for doc in self._docs.values():
            doc.close()
        self._docs.clear()

    def _doc(self, source: int) -> pymupdf.Document:
        if source not in self._docs:
            self._docs[source] = open_pdf(self.sources[source])
        return self._docs[source]

    # --------------------------------------------------- édition

    def rotate(self, degrees: int) -> None:
        if self.grid.selected_rows():
            self._apply(organize.rotate_pages(self.pages, self.grid.selected_rows(), degrees))

    def delete(self) -> None:
        if self.grid.selected_rows():
            self._apply(organize.delete_pages(self.pages, self.grid.selected_rows()))

    def duplicate(self) -> None:
        if self.grid.selected_rows():
            self._apply(organize.duplicate_pages(self.pages, self.grid.selected_rows()))

    def reverse(self) -> None:
        if len(self.pages) > 1:
            self._apply(organize.reverse_pages(self.pages, self.grid.selected_rows()))

    def add_blank(self) -> None:
        rows = self.grid.selected_rows()
        neighbour = self.pages[max(rows)] if rows else (self.pages[-1] if self.pages else None)
        width, height = organize.A4_SIZE
        if neighbour is not None:
            if neighbour.is_blank:
                width, height = neighbour.size
            else:
                rect = self._doc(neighbour.source)[neighbour.index].rect
                width, height = rect.width, rect.height
            if neighbour.rotation % 180:
                width, height = height, width
        self._apply(organize.insert_blank(self.pages, rows, (width, height)))

    def trim_margins(self) -> None:
        rows = [r for r in self.grid.selected_rows() if not self.pages[r].is_blank]
        if not rows:
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            boxes = {r: organize.content_box(self._doc(self.pages[r].source), self.pages[r].index) for r in rows}
        finally:
            QApplication.restoreOverrideCursor()
        # Page vide : rien à rogner, on la laisse telle quelle.
        self._apply(organize.crop_pages_each(self.pages, {r: b for r, b in boxes.items() if b is not None}))

    def crop_dialog(self, row: int | None = None) -> None:
        selected = self.grid.selected_rows()
        if row is None:
            if not selected:
                return
            row = selected[0]
        ref = self.pages[row]
        if ref.is_blank:
            self.panel.set_status("Une page blanche ne se recadre pas.")
            return
        doc = self._doc(ref.source)
        page = doc[ref.index]
        width = round(PREVIEW_SIDE * min(1.0, page.rect.width / page.rect.height))
        pix = organize.render_thumbnail(doc, ref.index, width)
        image = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format_RGB888).copy()
        if ref.rotation:
            image = image.transformed(QTransform().rotate(ref.rotation), Qt.SmoothTransformation)

        n = len(selected)
        scopes = [("Cette page", "this")]
        if n > 1 and row in selected:
            scopes.append((f"Les {n} pages sélectionnées", "selected"))
        scopes.append((f"Toutes les pages ({len(self.pages)})", "all"))

        def auto_box():
            found = organize.content_box(doc, ref.index)
            return organize.rotate_box(found, ref.rotation) if found else None

        dialog = CropDialog(self, image, ref.display_crop(), scopes,
                            "selected" if len(scopes) == 3 else "this", auto_box)
        if self._exec(dialog) != QDialog.Accepted:
            return
        rows = {"this": [row], "selected": selected, "all": list(range(len(self.pages)))}[dialog.scope_value]
        self._apply(organize.crop_pages(self.pages, rows, dialog.box))

    def _exec(self, dialog: QDialog) -> int:
        """Point d'entrée unique des fenêtres modales (remplacé dans les tests)."""
        return dialog.exec()

    def undo(self) -> None:
        if self._undo:
            self._redo.append(self.pages)
            self.pages = self._undo.pop()
            self._show(selected=[])

    def redo(self) -> None:
        if self._redo:
            self._undo.append(self.pages)
            self.pages = self._redo.pop()
            self._show(selected=[])

    def _apply(self, edit: organize.Edit) -> None:
        pages, selected = edit
        if pages == self.pages:
            return
        self._undo = (self._undo + [self.pages])[-HISTORY_MAX:]
        self._redo.clear()
        self.pages = pages
        self._show(selected)

    def _show(self, selected: list[int]) -> None:
        multi = len(self.sources) > 1
        letters = string.ascii_uppercase
        labels = [
            "blanche" if r.is_blank else f"{letters[r.source % 26] if multi else ''}{r.index + 1}"
            for r in self.pages
        ]
        self.grid.set_pages(self.pages, labels, selected)
        self._refresh()

    # ------------------------------------------------------- état

    def _refresh(self) -> None:
        has_doc = bool(self.sources)
        self._stack.setCurrentIndex(1 if has_doc else 0)
        self.info.setToolTip("\n".join(str(p) for p in self.sources))
        self.panel.set_ready(has_doc and bool(self.pages), None)
        self._refresh_actions()

    def _refresh_actions(self) -> None:
        count = len(self.grid.selected_rows())
        for key in ("left", "right", "duplicate", "delete", "crop", "trim"):
            self.actions[key].setEnabled(count > 0)
        self.actions["reverse"].setEnabled(len(self.pages) > 1)
        self.actions["undo"].setEnabled(bool(self._undo))
        self.actions["redo"].setEnabled(bool(self._redo))

        n = len(self.pages)
        parts = [f"{n} page{'s' if n > 1 else ''}"]
        if len(self.sources) == 1:
            parts.insert(0, self.sources[0].name)
        if count:
            parts.append(f"{count} sélectionnée{'s' if count > 1 else ''}")
        if self.dirty and self.sources:
            parts.append("modifié")
        info = " · ".join(parts)
        if len(self.sources) > 1:
            names = ", ".join(f"{string.ascii_uppercase[i % 26]} = {p.name}" for i, p in enumerate(self.sources))
            info += f"   ({names})"
        self.info.setText(info)

    # ------------------------------------------------- enregistrement

    def default_output(self) -> Path:
        first = self.sources[0]
        return unique_path(first.with_name(f"{first.stem}_organisé.pdf"))

    def _save(self) -> None:
        chosen, _ = QFileDialog.getSaveFileName(self, "Enregistrer le PDF", str(self.default_output()), "PDF (*.pdf)")
        if chosen:
            self.save_to(Path(chosen))

    def save_to(self, out: Path) -> None:
        out = out.with_suffix(".pdf")
        if out in self.sources:
            # Windows refuse de remplacer un fichier encore ouvert (aperçus, miniatures en cours).
            self._stop_loaders()
        sources, pages = list(self.sources), list(self.pages)

        def work(progress) -> Report:
            try:
                return Report(outputs=[organize.build_pdf(sources, pages, out, progress)])
            except Cancelled:
                raise
            except Exception as exc:
                return Report(errors=[(out, str(exc) or exc.__class__.__name__)])

        self.panel.start(work)

    def _on_saved(self, report: Report) -> None:
        if not report.outputs:
            return
        out = report.outputs[0]
        if out in self.sources:
            # Un fichier source a été remplacé : les numéros de page d'origine ne sont plus valables.
            self._stop_loaders()
            self._saved = []
            self.open(out)
        else:
            self._saved = list(self.pages)
            self._refresh()
