"""Widgets réutilisables entre les vues."""

from __future__ import annotations

import html
from pathlib import Path
from typing import Any, Callable, Iterable

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup, QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QMessageBox, QProgressBar, QPushButton, QRadioButton, QSizePolicy, QStackedLayout, QTextEdit, QVBoxLayout,
    QWidget,
)

from docflex.core.tasks import Progress, Report
from docflex.ui import theme
from docflex.ui.worker import Task
from docflex.utils.files import ext_of, human_size
from docflex.utils.system import reveal


def label(text: str = "", role: str | None = None, wrap: bool = False) -> QLabel:
    widget = QLabel(text)
    if role:
        widget.setProperty("role", role)
    widget.setWordWrap(wrap)
    return widget


def button(text: str, kind: str | None = None, on_click: Callable[[], None] | None = None) -> QPushButton:
    widget = QPushButton(text)
    if kind:
        widget.setProperty("kind", kind)
    widget.setCursor(Qt.PointingHandCursor)
    if on_click:
        # clicked(bool) remplirait le premier paramètre d'un « lambda k=clé: … » : on l'ignore.
        widget.clicked.connect(lambda *_: on_click())
    return widget


def ask(parent, title: str, text: str, confirm: str, cancel: str = "Annuler") -> bool:
    """Demande de confirmation aux libellés français (Qt n'embarque pas ses traductions)."""
    box = QMessageBox(QMessageBox.Question, title, text, parent=parent)
    yes = box.addButton(confirm, QMessageBox.AcceptRole)
    box.setDefaultButton(box.addButton(cancel, QMessageBox.RejectRole))
    box.exec()
    return box.clickedButton() is yes


def choose(parent, title: str, text: str, options: list[tuple[str, str, QMessageBox.ButtonRole]]) -> str:
    """Question à plusieurs réponses (libellés français) ; retourne la clé de la réponse choisie."""
    box = QMessageBox(QMessageBox.Question, title, text, parent=parent)
    buttons = {box.addButton(label, role): key for label, key, role in options}
    default = next(btn for btn in buttons if buttons[btn] == options[0][1])
    box.setDefaultButton(default)
    box.exec()
    return buttons.get(box.clickedButton(), options[-1][1])


def separator() -> QFrame:
    """Trait vertical entre deux groupes de boutons."""
    line = QFrame()
    line.setObjectName("separator")
    line.setFixedSize(1, 22)
    return line


def card() -> QFrame:
    frame = QFrame()
    frame.setObjectName("card")
    return frame


def combo(choices: Iterable[tuple[str, Any]], default: int = 0, width: int = 280) -> QComboBox:
    """Liste déroulante de (libellé, valeur) ; la valeur se lit avec `currentData()`."""
    widget = QComboBox()
    for text, value in choices:
        widget.addItem(text, value)
    widget.setCurrentIndex(default)
    widget.setFixedWidth(width)
    return widget


class ViewHeader(QWidget):
    def __init__(self, title: str, subtitle: str) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addWidget(label(title, "title"))
        layout.addWidget(label(subtitle, "muted", wrap=True))


class SegmentedControl(QWidget):
    """Boutons exclusifs accolés, façon macOS."""

    changed = Signal(str)

    def __init__(self, options: Iterable[tuple[str, str]], expand: bool = False) -> None:  # (libellé, valeur)
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._group = QButtonGroup(self)
        self._buttons: dict[str, QPushButton] = {}
        options = list(options)
        for i, (text, value) in enumerate(options):
            btn = button(text, "segment")
            btn.setCheckable(True)
            btn.setProperty("position", "first" if i == 0 else "last" if i == len(options) - 1 else "middle")
            btn.clicked.connect(lambda _=False, v=value: self.changed.emit(v))
            self._group.addButton(btn)
            self._buttons[value] = btn
            if expand:  # segments de même largeur sur toute la place disponible
                btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            layout.addWidget(btn)
        if not expand:
            layout.addStretch()

    def value(self) -> str:
        return next(v for v, b in self._buttons.items() if b.isChecked())

    def set_value(self, value: str) -> None:
        self._buttons[value].setChecked(True)


class FileList(QFrame):
    """Liste de fichiers alimentée par glisser-déposer ou par le sélecteur."""

    changed = Signal(list)   # list[Path]
    rejected = Signal(list)  # list[Path] de formats non pris en charge

    def __init__(self, accepted_exts: Iterable[str], dialog_title: str = "Choisir des fichiers",
                 single: bool = False, reorderable: bool = False) -> None:
        super().__init__()
        self.setObjectName("dropZone")
        self.setAcceptDrops(True)
        self._accepted = frozenset(accepted_exts)
        self._dialog_title = dialog_title
        self._single = single  # un seul fichier : un nouvel ajout remplace l'ancien
        self._reorderable = reorderable  # l'ordre compte (fusion) : flèches ▲▼ et tri par nom
        self.paths: list[Path] = []

        self._stack = QStackedLayout(self)
        self._stack.addWidget(self._build_empty())
        self._stack.addWidget(self._build_list())

    # ----------------------------------------------------------- API

    def add(self, candidates: Iterable[Path]) -> None:
        accepted, rejected = [], []
        for path in candidates:
            files = sorted(p for p in path.iterdir() if p.is_file()) if path.is_dir() else [path]
            for f in files:
                if f.name.startswith("."):
                    continue
                if ext_of(f) in self._accepted:
                    if f not in self.paths and f not in accepted:
                        accepted.append(f)
                elif not path.is_dir():
                    rejected.append(f)
        if accepted:
            if self._single:
                self.paths[:] = accepted[:1]
            else:
                self.paths.extend(accepted)
            self._changed()
        if rejected:  # après `changed`, qui réinitialise le message d'état
            self.rejected.emit(rejected)

    def clear(self) -> None:
        self.paths.clear()
        self._changed()

    def remove(self, path: Path) -> None:
        self.paths.remove(path)
        self._changed()

    def move(self, path: Path, delta: int) -> None:
        i = self.paths.index(path)
        j = min(max(i + delta, 0), len(self.paths) - 1)
        self.paths.insert(j, self.paths.pop(i))
        self._changed()

    def sort_by_name(self) -> None:
        self.paths.sort(key=lambda p: p.name.lower())
        self._changed()

    def browse(self) -> None:
        patterns = " ".join(f"*.{e}" for e in sorted(self._accepted))
        filters = f"Fichiers pris en charge ({patterns});;Tous les fichiers (*)"
        if self._single:
            chosen, _ = QFileDialog.getOpenFileName(self, self._dialog_title, "", filters)
            chosen = [chosen] if chosen else []
        else:
            chosen, _ = QFileDialog.getOpenFileNames(self, self._dialog_title, "", filters)
        self.add(Path(c) for c in chosen)

    # ------------------------------------------------- construction

    def _build_empty(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setAlignment(Qt.AlignCenter)
        layout.setSpacing(6)
        icon = label("⇪", "muted")
        icon.setStyleSheet("font-size: 30px;")
        what = "un fichier" if self._single else "vos fichiers"
        hint = label(f"Glissez-déposez {what} ici")
        hint.setStyleSheet("font-size: 15px; font-weight: 600;")
        browse = button("Parcourir…", on_click=self.browse)
        for widget in (icon, hint, label("ou", "muted")):
            layout.addWidget(widget, alignment=Qt.AlignCenter)
        layout.addWidget(browse, alignment=Qt.AlignCenter)
        return page

    def _build_list(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(12, 10, 12, 10)
        bar = QHBoxLayout()
        self._count = label("", "field")
        bar.addWidget(self._count)
        bar.addStretch()
        if self._reorderable:
            bar.addWidget(button("Trier par nom", on_click=self.sort_by_name))
        bar.addWidget(button("Changer…" if self._single else "Ajouter…", on_click=self.browse))
        bar.addWidget(button("Retirer" if self._single else "Tout retirer", on_click=self.clear))
        layout.addLayout(bar)
        self._list = QListWidget()
        self._list.setObjectName("fileList")
        self._list.setSelectionMode(QListWidget.NoSelection)
        self._list.setFocusPolicy(Qt.NoFocus)
        self._list.setSpacing(2)
        layout.addWidget(self._list)
        return page

    def _row(self, path: Path) -> QWidget:
        row = QFrame()
        row.setObjectName("fileRow")
        layout = QHBoxLayout(row)
        layout.setContentsMargins(8, 5, 6, 5)
        badge = QLabel(ext_of(path).upper())
        badge.setObjectName("badge")
        badge.setAlignment(Qt.AlignCenter)
        badge.setFixedWidth(46)
        name = QLabel(path.name)
        name.setToolTip(str(path))
        name.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)  # tronqué plutôt qu'élargir la fenêtre
        try:
            size = human_size(path.stat().st_size)
        except OSError:
            size = "introuvable"
        remove = button("✕", "ghost", lambda: self.remove(path))
        remove.setToolTip("Retirer de la liste")
        if self._reorderable:
            position = label(f"{self.paths.index(path) + 1}.", "muted")
            position.setFixedWidth(22)
            layout.addWidget(position)
        layout.addWidget(badge)
        layout.addSpacing(6)
        layout.addWidget(name, 1)
        layout.addWidget(label(size, "muted"))
        if self._reorderable:
            for text, delta, tip in (("▲", -1, "Monter"), ("▼", 1, "Descendre")):
                arrow = button(text, "icon", lambda d=delta: self.move(path, d))
                arrow.setToolTip(tip)
                layout.addWidget(arrow)
        layout.addWidget(remove)
        return row

    def _changed(self) -> None:
        self._list.clear()
        for path in self.paths:
            item = QListWidgetItem(self._list)
            row = self._row(path)
            item.setSizeHint(QSize(0, row.sizeHint().height()))
            self._list.setItemWidget(item, row)
        n = len(self.paths)
        self._count.setText(f"{n} fichier{'s' if n > 1 else ''}")
        self._stack.setCurrentIndex(1 if self.paths else 0)
        self.changed.emit(list(self.paths))

    # ------------------------------------------------ glisser-déposer

    def _set_dragging(self, dragging: bool) -> None:
        self.setProperty("dragging", dragging)
        theme.repolish(self)

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls() and any(u.isLocalFile() for u in event.mimeData().urls()):
            event.acceptProposedAction()
            self._set_dragging(True)

    def dragLeaveEvent(self, event) -> None:
        self._set_dragging(False)

    def dropEvent(self, event) -> None:
        self._set_dragging(False)
        self.add(Path(u.toLocalFile()) for u in event.mimeData().urls() if u.isLocalFile())
        event.acceptProposedAction()


class OutputChooser(QWidget):
    """Destination des fichiers produits : à côté des originaux, ou dans un dossier choisi."""

    def __init__(self) -> None:
        super().__init__()
        self._folder: Path | None = None
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._beside = QRadioButton("À côté des originaux")
        self._beside.setChecked(True)
        self._in_folder = QRadioButton("Dans un dossier")
        self._in_folder.clicked.connect(self._choose)
        self._beside.clicked.connect(self._refresh)
        self._label = label("", "muted")
        self._label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        layout.addWidget(self._beside)
        layout.addSpacing(12)
        layout.addWidget(self._in_folder)
        layout.addWidget(self._label, 1)

    @property
    def out_dir(self) -> Path | None:
        return self._folder if self._in_folder.isChecked() else None

    def set_folder(self, folder: Path | None) -> None:
        self._folder = folder
        (self._in_folder if folder else self._beside).setChecked(True)
        self._refresh()

    def _choose(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "Dossier de destination", str(self._folder or ""))
        if chosen:
            self._folder = Path(chosen)
        elif self._folder is None:
            self._beside.setChecked(True)
        self._refresh()

    def _refresh(self) -> None:
        self._label.setText(str(self.out_dir or ""))
        self._label.setToolTip(str(self.out_dir or ""))


class TaskPanel(QWidget):
    """Barre d'action (état, progression, boutons) et journal des résultats.

    La vue appelle `set_ready` quand ses réglages changent, puis `start(fn)`
    où `fn(progress)` s'exécute en arrière-plan et retourne un `Report`.
    """

    busy_changed = Signal(bool)
    done = Signal(object)  # Report, après une tâche menée à terme

    def __init__(self, run_label: str, on_run: Callable[[], None], intro: str, log_height: int = 110) -> None:
        super().__init__()
        self.busy = False
        self._ready = False
        self._task: Task | None = None
        self._outputs: list[Path] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        bar = QHBoxLayout()
        left = QVBoxLayout()
        left.setSpacing(6)
        self.status = label("", "muted")
        self.status.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setTextVisible(False)
        left.addWidget(self.status)
        left.addWidget(self.progress)
        bar.addLayout(left, 1)
        bar.addSpacing(12)
        self.open_btn = button("Ouvrir le dossier", on_click=lambda: reveal(self._outputs[0]))
        self.cancel_btn = button("Arrêter", "danger", self._cancel)
        self.run_btn = button(run_label, "primary", on_run)
        self.run_btn.setMinimumHeight(36)
        for widget in (self.open_btn, self.cancel_btn, self.run_btn):
            widget.setEnabled(False)
            bar.addWidget(widget)
        layout.addLayout(bar)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setFixedHeight(log_height)
        layout.addWidget(self.log)
        self.write(intro, "muted")

    # ----------------------------------------------------------- API

    def set_ready(self, ready: bool, status: str | None = None) -> None:
        self._ready = ready
        if status is not None:
            self.status.setText(status)
        if not self.busy:
            self.run_btn.setEnabled(ready)

    def set_status(self, text: str) -> None:
        self.status.setText(text)

    def start(self, fn: Callable[[Progress], Report]) -> None:
        self._set_busy(True)
        self.progress.setValue(0)
        self._task = Task(fn, self)
        self._task.progressed.connect(self._on_progress)
        self._task.succeeded.connect(self._on_done)
        self._task.failed.connect(self._on_error)
        self._task.cancelled.connect(self._on_cancel)
        self._task.finished.connect(self._task.deleteLater)
        self._task.start()

    def shutdown(self) -> None:
        """Annule la tâche en cours et attend sa fin (Qt 6 s'arrête net si un thread tourne encore)."""
        if self._task is not None:
            self._task.cancel()
            self._task.wait()

    def write(self, text: str, tone: str | None = None) -> None:
        """Ajoute une ligne au journal ; `tone` : None, « muted » ou « danger »."""
        escaped = html.escape(text).replace("\n", "<br>")
        color = theme.current().color(tone) if tone else None
        self.log.append(f'<span style="color:{color}">{escaped}</span>' if color else escaped)

    # ------------------------------------------------------- interne

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        self.cancel_btn.setEnabled(busy)
        self.run_btn.setEnabled(self._ready and not busy)
        if busy:
            self.open_btn.setEnabled(False)
        self.busy_changed.emit(busy)

    def _cancel(self) -> None:
        if self._task:
            self._task.cancel()
        self.cancel_btn.setEnabled(False)
        self.status.setText("Annulation après l'étape en cours…")

    def _on_progress(self, fraction: float, message: str) -> None:
        self.progress.setValue(int(fraction * 1000))
        self.status.setText(message)

    def _on_done(self, report: Report) -> None:
        self._task = None
        self._set_busy(False)
        self.progress.setValue(1000)
        n, e = len(report.outputs), len(report.errors)
        summary = f"{n} fichier{'s' if n > 1 else ''} créé{'s' if n > 1 else ''}" if n else "Aucun fichier créé"
        if e:
            summary += f", {e} erreur{'s' if e > 1 else ''}"
        self.status.setText(summary + ".")
        self.write(f"✔ {summary}." if n or not e else f"✖ {summary}.", "danger" if e and not n else None)
        for note in report.notes:
            self.write(f"    {note}")
        for path in report.outputs:
            self.write(f"    {path}", "muted")
        for path, message in report.errors:
            self.write(f"✖ {path.name} : {message}", "danger")
        self._outputs = report.outputs
        self.open_btn.setEnabled(bool(report.outputs))
        self.done.emit(report)

    def _on_error(self, details: str) -> None:
        self._task = None
        self._set_busy(False)
        self.status.setText("Erreur inattendue, voir le journal.")
        self.write(details, "danger")

    def _on_cancel(self) -> None:
        self._task = None
        self._set_busy(False)
        self.progress.setValue(0)
        self.status.setText("Opération annulée.")
        self.write("Opération annulée. Les fichiers déjà créés sont conservés.", "muted")
