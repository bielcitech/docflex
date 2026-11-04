"""Remplir un formulaire directement dans la page : la saisie se fait dans le champ lui-même.

Un petit éditeur (ligne, zone de texte, liste ou case) se pose exactement sur le champ, au
zoom courant, et le suit quand le document défile. Tab / ⇧Tab passent au champ suivant ou
précédent, Entrée valide, Échap abandonne la saisie en cours.
"""

from __future__ import annotations

import pymupdf
from PySide6.QtCore import QEvent, QObject, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit,
    QSpinBox, QWidget,
)

from docflex.core.editing import Field
from docflex.core.forms import OPERATIONS
from docflex.ui import theme

AUTO_FONT_RATIO = 0.62
EDITOR_KEYS = (Qt.Key_Escape, Qt.Key_Tab, Qt.Key_Backtab, Qt.Key_Return, Qt.Key_Enter)  # taille automatique : part de la hauteur d'un champ d'une ligne


class CheckFocus(QWidget):
    """Anneau de focus sur une case à cocher : Espace ou un clic la cochent ou la décochent."""

    toggled = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.PointingHandCursor)

    def mousePressEvent(self, event) -> None:  # noqa: N802 (nom Qt)
        if event.button() == Qt.LeftButton:
            self.toggled.emit()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key_Space:
            self.toggled.emit()
        else:
            super().keyPressEvent(event)

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(QColor(theme.current().color("accent")), 2))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 3, 3)


class FieldEditor(QObject):
    committed = Signal(int, object, object)  # page, Field, nouvelle valeur (texte, ou booléen pour une case)

    def __init__(self, canvas) -> None:
        super().__init__(canvas)
        self.canvas = canvas
        self.widget: QWidget | None = None
        self.target: tuple[int, Field] | None = None
        self._original = None
        canvas.verticalScrollBar().valueChanged.connect(lambda _: self._place())
        canvas.horizontalScrollBar().valueChanged.connect(lambda _: self._place())
        canvas.zoom_changed.connect(lambda _: self._place())
        # Référence gardée : pendant la destruction, interroger le canevas planterait.
        self._viewport = canvas.viewport()
        self._viewport.installEventFilter(self)  # clic ailleurs dans la page : saisie gardée

    # ------------------------------------------------------------ ordre de saisie

    def order(self) -> list[tuple[int, Field]]:
        """Tous les champs du document, page après page, de haut en bas puis de gauche à droite."""
        result = []
        for page in range(len(self.canvas.pages)):
            fields = sorted((f for f in self.canvas.fields(page) if not f.readonly),  # calculés : sautés
                            key=lambda f: (round(f.rect[1] / 4), f.rect[0]))
            result += [(page, field) for field in fields]
        return result

    def fresh(self, page: int, xref: int) -> Field | None:
        """Le champ tel qu'il est maintenant dans le document (valeur à jour)."""
        return next((f for f in self.canvas.fields(page) if f.xref == xref), None)

    # ------------------------------------------------------------ édition

    def edit(self, page: int, field: Field, popup: bool = False) -> None:
        self.close(commit=True)
        field = self.fresh(page, field.xref) or field
        if field.readonly:
            return
        self.target = (page, field)
        if field.checkable:
            widget = CheckFocus()
            widget.toggled.connect(self.toggle)
            self._original = field.checked
        elif field.choices:
            widget = QComboBox()
            widget.addItems(list(field.choices))
            widget.setCurrentIndex(max(field.choices.index(field.value) if field.value in field.choices else 0, 0))
            widget.activated.connect(lambda _: self.close(commit=True, refocus=True))
            self._original = field.value
        elif field.multiline:
            widget = QPlainTextEdit(field.value)
            widget.setTabChangesFocus(True)
            self._original = field.value
        else:
            widget = QLineEdit(field.value)
            if field.maxlen:
                widget.setMaxLength(field.maxlen)
            widget.selectAll()
            self._original = field.value
        widget.setObjectName("fieldEditor")
        widget.setParent(self.canvas.viewport())
        widget.installEventFilter(self)
        self.widget = widget
        self._scroll_into_view()
        self._place()
        widget.show()
        widget.setFocus(Qt.OtherFocusReason)
        self.canvas.update_field_focus((page, field.xref))
        if popup and isinstance(widget, QComboBox):
            QTimer.singleShot(0, widget.showPopup)

    def value(self):
        widget = self.widget
        if isinstance(widget, CheckFocus):
            return self._original
        if isinstance(widget, QComboBox):
            return widget.currentText()
        if isinstance(widget, QPlainTextEdit):
            return widget.toPlainText()
        return widget.text()

    def flush(self) -> None:
        """Enregistre la saisie en cours sans fermer l'éditeur (avant ⌘S, par exemple)."""
        if self.widget is None or self.target is None:
            return
        value = self.value()
        if value != self._original:
            page, field = self.target
            self._original = value
            self.committed.emit(page, field, value)
            self.target = (page, self.fresh(page, field.xref) or field)

    def toggle(self) -> None:
        if self.target is not None:
            page, field = self.target
            # Une case s'inverse ; une option radio se choisit (on en choisit une autre pour changer).
            if field.kind == pymupdf.PDF_WIDGET_TYPE_RADIOBUTTON:
                if self._original:
                    return  # déjà cochée : rien à enregistrer, rien à annuler
                self._original = True
            else:
                self._original = not self._original
            self.committed.emit(page, field, self._original)
            self.target = (page, self.fresh(page, field.xref) or field)

    def close(self, commit: bool = True, refocus: bool = False) -> None:
        widget = self.widget
        if widget is None:
            return
        if commit:
            self.flush()
        self.widget, self.target = None, None
        widget.removeEventFilter(self)
        widget.hide()
        widget.deleteLater()
        self.canvas.update_field_focus(None)
        if refocus:
            self.canvas.setFocus()

    def move(self, step: int) -> None:
        """Tab / ⇧Tab : champ suivant ou précédent (on reboucle en fin de document)."""
        if self.target is None:
            return
        page, field = self.target
        self.flush()
        order = self.order()
        keys = [(p, f.xref) for p, f in order]
        index = keys.index((page, field.xref)) if (page, field.xref) in keys else -1
        if order:
            self.edit(*order[(index + step) % len(order)])

    # ------------------------------------------------------------ position

    def _scene_rect(self) -> QRectF:
        page, field = self.target
        x0, y0, x1, y1 = field.rect
        return self.canvas.pages[page].mapRectToScene(QRectF(x0, y0, x1 - x0, y1 - y0))

    def _scroll_into_view(self) -> None:
        top, bottom = self.canvas.insets  # pas sous les barres flottantes
        self.canvas.ensureVisible(self._scene_rect(), 40, max(top, bottom) + 20)

    def _place(self) -> None:
        if self.widget is None or self.target is None:
            return
        _page, field = self.target
        rect = self.canvas.mapFromScene(self._scene_rect()).boundingRect()
        if isinstance(self.widget, QComboBox):
            rect.setHeight(max(rect.height(), 22))
        self.widget.setGeometry(rect.adjusted(-1, -1, 1, 1))
        if not isinstance(self.widget, CheckFocus):
            height = field.rect[3] - field.rect[1]
            auto = 11 if field.multiline else min(height * AUTO_FONT_RATIO, 12)
            size = field.fontsize or auto
            # La feuille de style générale fixe la taille du texte : on la remplace ici.
            pixels = max(round(size * self.canvas.pixels_per_point), 8)
            padding = max(round(2 * self.canvas.pixels_per_point), 2)
            self.widget.setStyleSheet(f"font-size: {pixels}px; padding: 0 {padding}px;")

    # ------------------------------------------------------------ clavier, focus

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        kind = event.type()
        if obj is self._viewport:
            if kind == QEvent.MouseButtonPress and self.widget is not None:
                self.close(commit=True)
            return False
        if obj is not self.widget:
            return False
        if kind == QEvent.ShortcutOverride and event.key() in EDITOR_KEYS:
            event.accept()  # Échap, Tab, Entrée : pour l'éditeur, pas pour les raccourcis du lecteur
            return True
        if kind == QEvent.KeyPress:
            key, mods = event.key(), event.modifiers()
            if key == Qt.Key_Tab and not mods & Qt.ShiftModifier:
                self.move(1)
                return True
            if key == Qt.Key_Backtab or (key == Qt.Key_Tab and mods & Qt.ShiftModifier):
                self.move(-1)
                return True
            if key == Qt.Key_Escape:
                self.close(commit=False, refocus=True)
                return True
            if key in (Qt.Key_Return, Qt.Key_Enter):
                if isinstance(obj, QPlainTextEdit) and not mods & (Qt.ControlModifier | Qt.MetaModifier):
                    return False  # retour à la ligne ; ⌘ Entrée valide
                if not isinstance(obj, QComboBox):
                    self.close(commit=True, refocus=True)
                    return True
        elif kind == QEvent.FocusOut and event.reason() not in (Qt.PopupFocusReason, Qt.ActiveWindowFocusReason,
                                                                 Qt.TabFocusReason, Qt.BacktabFocusReason):
            # Clic ailleurs : la saisie est gardée. Après coup, pour ne pas détruire l'éditeur
            # pendant qu'il traite son propre événement.
            QTimer.singleShot(0, lambda w=obj: self.widget is w and self.close(commit=True))
        return False


class CalculationDialog(QDialog):
    """Champ calculé : opération, champs sources (cochés) et nombre de décimales."""

    def __init__(self, parent, target: str, names: list[str]) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Calcul automatique de « {target} »")
        form = QFormLayout(self)
        self.operation = QComboBox()
        for key, text in OPERATIONS.items():
            self.operation.addItem(text, key)
        form.addRow("Opération :", self.operation)
        self.sources = QListWidget()
        for name in names:
            item = QListWidgetItem(name, self.sources)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Unchecked)
        self.sources.itemChanged.connect(lambda _: self._update())
        form.addRow("Champs :", self.sources)
        self.decimals = QSpinBox()
        self.decimals.setRange(0, 6)
        self.decimals.setValue(2)
        form.addRow("Décimales :", self.decimals)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setText("Calculer")
        self.buttons.button(QDialogButtonBox.Cancel).setText("Annuler")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        form.addRow(self.buttons)
        self._update()

    def checked(self) -> list[str]:
        return [self.sources.item(i).text() for i in range(self.sources.count())
                if self.sources.item(i).checkState() == Qt.Checked]

    def _update(self) -> None:
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(bool(self.checked()))

    def result_value(self) -> tuple[str, list[str], int]:
        return self.operation.currentData(), self.checked(), self.decimals.value()

    @classmethod
    def ask(cls, parent, target: str, names: list[str]) -> tuple[str, list[str], int] | None:
        dialog = cls(parent, target, names)
        return dialog.result_value() if dialog.exec() == QDialog.Accepted else None
