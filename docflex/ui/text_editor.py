"""Taper le texte directement dans la page, comme avec une machine à écrire.

« Ajouter du texte » : un clic pose le curseur à l'endroit voulu et l'on tape dans la page, avec
la police, la taille, la couleur, le gras et l'italique choisis, au zoom courant. « Modifier une
ligne » : la ligne devient modifiable sur place, avec sa mise en forme d'origine ; la changer dans
la barre ne s'applique qu'à cette ligne. L'éditeur suit le défilement et le zoom.

Entrée valide une ligne modifiée ; dans un texte ajouté, Entrée passe à la ligne et ⌘ Entrée
valide. Échap abandonne, un clic ailleurs garde la saisie.
"""

from __future__ import annotations

from functools import cache

import pymupdf
from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFontDatabase, QFontMetricsF, QTextBlockFormat, QTextCursor
from PySide6.QtWidgets import QFrame, QLineEdit, QTextEdit

from docflex.core.editing import TextLine

LINE_HEIGHT = 1.25  # interligne du texte ajouté, comme dans le PDF (editing._write)
BORDER = 1          # pixels du cadre de l'éditeur (feuille de style : #textEditor, #lineEditor)
LINE_MARGIN = 2     # marge horizontale interne d'un QLineEdit, avant le texte
# Polices d'écran proches des polices standard du PDF ; pour une ligne modifiée, sa police
# d'origine est essayée d'abord.
SCREEN_FAMILIES = {
    "sans": ("Helvetica", "Arial", "Liberation Sans", "DejaVu Sans"),
    "serif": ("Times New Roman", "Times", "Liberation Serif", "DejaVu Serif"),
    "mono": ("Courier New", "Courier", "Liberation Mono", "DejaVu Sans Mono"),
}
EDITOR_KEYS = (Qt.Key_Escape, Qt.Key_Return, Qt.Key_Enter)


@cache
def _installed() -> frozenset[str]:
    return frozenset(QFontDatabase.families())


def screen_family(family: str, original: str | None = None) -> str:
    candidates = ((original,) if original else ()) + SCREEN_FAMILIES[family]
    return next((name for name in candidates if name in _installed()), SCREEN_FAMILIES[family][0])


def _original_family(font: str) -> str:
    """« ABCDEF+Arial-BoldMT » → « Arial »."""
    return font.split("+")[-1].split("-")[0].split(",")[0]


class TextEditor(QObject):
    added = Signal(int, tuple, str, dict)      # page, point (haut gauche, repère affiché), texte, style
    replaced = Signal(int, object, str, dict)  # page, TextLine, nouveau texte, style
    closed = Signal()

    def __init__(self, canvas) -> None:
        super().__init__(canvas)
        self.canvas = canvas
        self.widget: QTextEdit | QLineEdit | None = None
        self.page = 0
        self.line: TextLine | None = None   # None : texte ajouté
        self.point = (0.0, 0.0)              # texte ajouté : haut gauche ; ligne : ligne de base (repère affiché)
        self.style: dict = {}
        self._line_height = 0.0
        canvas.verticalScrollBar().valueChanged.connect(lambda _: self._place())
        canvas.horizontalScrollBar().valueChanged.connect(lambda _: self._place())
        canvas.zoom_changed.connect(lambda _: self._place())
        # Référence gardée : pendant la destruction, interroger le canevas planterait.
        self._viewport = canvas.viewport()
        self._viewport.installEventFilter(self)  # clic ailleurs dans la page : saisie gardée

    @property
    def active(self) -> bool:
        return self.widget is not None

    # ------------------------------------------------------------ ouverture, fermeture

    def add(self, page: int, point, style: dict) -> None:
        """Curseur posé à `point` (haut gauche de la première ligne) : on tape dans la page."""
        self.close(commit=True)
        widget = QTextEdit()
        widget.setAcceptRichText(False)
        widget.setFrameShape(QFrame.NoFrame)
        widget.setLineWrapMode(QTextEdit.NoWrap)
        widget.setTabChangesFocus(True)
        widget.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        widget.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        widget.document().setDocumentMargin(0)
        widget.setObjectName("textEditor")
        self.page, self.line, self.point, self.style = page, None, tuple(point[:2]), dict(style)
        self._open(widget)
        widget.document().clearUndoRedoStacks()  # l'interligne posé à l'ouverture ne s'annule pas
        widget.textChanged.connect(self._place)

    def edit(self, page: int, line: TextLine, click=None) -> None:
        """La ligne devient modifiable sur place, avec sa mise en forme d'origine."""
        self.close(commit=True)
        widget = QLineEdit(line.text)
        widget.setFrame(False)
        widget.setObjectName("lineEditor")
        baseline = pymupdf.Point(line.origin) * self.canvas.doc[page].rotation_matrix
        self.page, self.line, self.point, self.style = page, line, (baseline.x, baseline.y), dict(line.style)
        self._open(widget)
        widget.textChanged.connect(self._place)
        if click is not None:  # curseur là où l'on a cliqué, comme dans un traitement de texte
            pos = self._viewport_point(click)
            widget.setCursorPosition(widget.cursorPositionAt(widget.mapFrom(self._viewport, pos)))

    def _open(self, widget) -> None:
        widget.setParent(self._viewport)
        widget.installEventFilter(self)
        self.widget = widget
        self._place()
        widget.show()
        widget.setFocus(Qt.OtherFocusReason)

    def restyle(self, **changes) -> None:
        """Mise en forme changée dans la barre pendant la saisie."""
        self.style.update(changes)
        self._place()
        if self.widget is not None:
            self.widget.setFocus(Qt.OtherFocusReason)

    def text(self) -> str:
        widget = self.widget
        return widget.toPlainText() if isinstance(widget, QTextEdit) else widget.text()

    def close(self, commit: bool = True, refocus: bool = False) -> None:
        widget = self.widget
        if widget is None:
            return
        page, line, point, style, text = self.page, self.line, self.point, dict(self.style), self.text()
        self.widget = None
        widget.removeEventFilter(self)
        widget.hide()
        widget.deleteLater()
        if refocus:
            self.canvas.setFocus()
        self.closed.emit()
        if not commit:
            return
        if line is None:
            if text.strip():
                self.added.emit(page, point, text.rstrip(), style)
        elif text != line.text or style != line.style:
            self.replaced.emit(page, line, text, style)

    # ------------------------------------------------------------ position et apparence

    def _viewport_point(self, point) -> QPoint:
        return self.canvas.mapFromScene(self.canvas.pages[self.page].mapToScene(QPointF(*point)))

    def _apply_style(self, widget, ppp: float) -> QFontMetricsF:
        style = self.style
        original = None
        if self.line is not None and all(style[k] == self.line.style[k] for k in ("family", "bold", "italic")):
            original = _original_family(self.line.font)
        # La feuille de style générale fixe la taille du texte : on la remplace ici.
        widget.setStyleSheet(
            f'font-family: "{screen_family(style["family"], original)}"; '
            f'font-size: {max(round(style["size"] * ppp), 1)}px; '
            f'font-weight: {700 if style["bold"] else 400}; font-style: {"italic" if style["italic"] else "normal"}; '
            f'color: {QColor.fromRgbF(*style["color"]).name()};')
        widget.ensurePolished()
        return QFontMetricsF(widget.font())

    def _set_line_height(self, widget: QTextEdit, pixels: float) -> None:
        """Interligne fixe, comme dans le PDF : la ligne de base tombe aux 4/5 de chaque ligne."""
        if abs(pixels - self._line_height) < 0.01 and widget.document().blockCount() == 1:
            return
        self._line_height = pixels
        block = QTextBlockFormat()
        block.setLineHeight(pixels, QTextBlockFormat.LineHeightTypes.FixedHeight.value)
        cursor = QTextCursor(widget.document())
        cursor.select(QTextCursor.Document)
        widget.blockSignals(True)  # pas de textChanged → _place en boucle
        cursor.mergeBlockFormat(block)
        widget.blockSignals(False)

    def _place(self) -> None:
        widget = self.widget
        if widget is None:
            return
        ppp = self.canvas.pixels_per_point
        metrics = self._apply_style(widget, ppp)
        size = self.style["size"] * ppp
        anchor = self._viewport_point(self.point)
        if isinstance(widget, QTextEdit):
            self._set_line_height(widget, LINE_HEIGHT * size)
            document = widget.document()
            first = document.firstBlock().layout()
            line = first.lineAt(0) if first.lineCount() else None
            # Première ligne de base `size` sous le point cliqué, comme editing.add_text.
            baseline = first.position().y() + line.y() + line.ascent() if line else 0.8 * LINE_HEIGHT * size
            room = metrics.horizontalAdvance("M") * 2  # place pour le curseur et le caractère suivant
            width = max(document.size().width() + room, metrics.horizontalAdvance("M") * 4)
            widget.setGeometry(anchor.x() - BORDER, round(anchor.y() + size - baseline) - BORDER,
                               round(width) + 2 * BORDER, round(document.size().height()) + 2 * BORDER)
        else:
            # Ligne de base de l'éditeur (texte centré verticalement) sur celle de la ligne d'origine.
            pad = max(round(metrics.height() * 0.15), 2)
            height = round(metrics.height()) + 2 * pad + 2 * BORDER
            x0, _y0, x1, _y1 = self.line.rect
            left = self._viewport_point((x0, self.point[1])).x()
            original = round((x1 - x0) * ppp)
            width = max(original, round(metrics.horizontalAdvance(widget.text() + "MM"))) + 2 * (BORDER + LINE_MARGIN)
            widget.setGeometry(left - BORDER - LINE_MARGIN, round(anchor.y() - metrics.ascent()) - pad - BORDER,
                               width, height)

    # ------------------------------------------------------------ clavier, focus

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 (nom Qt)
        kind = event.type()
        if obj is self._viewport:
            if kind == QEvent.MouseButtonPress and self.widget is not None:
                adding = self.line is None
                self.close(commit=True)
                if adding:
                    return True  # ce clic termine le texte ajouté, le suivant en pose un autre
                # Clic sur une autre ligne : on la modifie aussitôt ; ailleurs, le clic ne fait que valider.
                hit = self.canvas.page_point(event.position().toPoint())
                return hit is None or self.canvas.line_at(hit[0], (hit[1].x, hit[1].y)) is None
            return False
        if obj is not self.widget:
            return False
        if kind == QEvent.ShortcutOverride and event.key() in EDITOR_KEYS:
            event.accept()  # Échap, Entrée : pour l'éditeur, pas pour les raccourcis du lecteur
            return True
        if kind == QEvent.KeyPress:
            key, mods = event.key(), event.modifiers()
            if key == Qt.Key_Escape:
                self.close(commit=False, refocus=True)
                return True
            if key in (Qt.Key_Return, Qt.Key_Enter):
                if isinstance(obj, QTextEdit) and not mods & (Qt.ControlModifier | Qt.MetaModifier):
                    return False  # retour à la ligne ; ⌘ Entrée valide
                self.close(commit=True, refocus=True)
                return True
        elif kind == QEvent.FocusOut and event.reason() not in (Qt.PopupFocusReason, Qt.ActiveWindowFocusReason):
            # Clic ailleurs : la saisie est gardée. Après coup, pour ne pas détruire l'éditeur
            # pendant qu'il traite son propre événement.
            QTimer.singleShot(0, lambda w=obj: self.widget is w and self.close(commit=True))
        return False
