"""Barres d'outils flottantes posées sur le document, et messages éphémères (« toasts »)."""

from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QEvent, QObject, Qt, QTimer, QVariantAnimation
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication, QFrame, QGraphicsDropShadowEffect, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QWidget

from docflex.ui import theme

MARGIN = 12          # écart entre une barre et le bord de la zone du document
EDGE = 6             # écart minimal sur les côtés, quand la place manque
GAP = 8              # écart entre la barre du bas et le message
SLIDE_MS = 220
FADE_MS = 180


class FloatingBar(QFrame):
    """Pilule arrondie avec ombre portée ; ses boutons se rangent de gauche à droite."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("floatingBar")
        self.row = QHBoxLayout(self)
        self.row.setContentsMargins(6, 6, 6, 6)
        self.row.setSpacing(3)
        self._shadow = QGraphicsDropShadowEffect(self)
        self._shadow.setBlurRadius(26)
        self._shadow.setOffset(0, 4)
        self.setGraphicsEffect(self._shadow)
        theme.current().changed.connect(self._on_theme)
        self._on_theme()

    def _on_theme(self) -> None:
        self._shadow.setColor(QColor(0, 0, 0, 150 if theme.current().scheme == "dark" else 38))


class Toast(QLabel):
    """Message qui apparaît au-dessus de la barre du bas puis s'efface tout seul.

    `text()` garde le dernier message, même une fois effacé.
    """

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("toast")
        self.setAlignment(Qt.AlignCenter)
        self.setWordWrap(True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._opacity = QGraphicsOpacityEffect(self)
        self._opacity.setOpacity(0.0)
        self.setGraphicsEffect(self._opacity)
        self._fade = QVariantAnimation(self)
        self._fade.setDuration(FADE_MS)
        self._fade.valueChanged.connect(self._opacity.setOpacity)
        self._fade.finished.connect(lambda: self.setVisible(self._opacity.opacity() > 0))
        self._hold = QTimer(self)
        self._hold.setSingleShot(True)
        self._hold.timeout.connect(lambda: self._fade_to(0.0))
        self.hide()

    def setText(self, text: str) -> None:  # noqa: N802 (nom Qt)
        super().setText(text)
        if not text:
            self._hold.stop()
            self._fade_to(0.0)
            return
        self.adjustSize()
        self.show()
        self.raise_()
        self._fade_to(1.0)
        self._hold.start(min(2500 + 40 * len(text), 6000))  # le temps de lire
        if self.parent() is not None:  # replacé par la superposition, au-dessus de la barre du bas
            QApplication.postEvent(self.parent(), QEvent(QEvent.LayoutRequest))

    def _fade_to(self, value: float) -> None:
        self._fade.stop()
        self._fade.setStartValue(self._opacity.opacity())
        self._fade.setEndValue(value)
        self._fade.start()


class FloatingOverlay(QObject):
    """Place deux barres sur `host` (en haut et en bas, centrées) et les fait glisser hors champ.

    Le tiroir (`drawer`, la recherche) sort de sous la barre du haut, aligné sur son bord droit.
    `on_insets(top, bottom)` reçoit la hauteur masquée par chaque barre : le document peut
    ainsi commencer et finir hors d'elles tout en défilant dessous.
    """

    def __init__(self, host: QWidget, top: QWidget, bottom: QWidget, toast: Toast, drawer: QWidget,
                 on_insets=None) -> None:
        super().__init__(host)
        self.host, self.top, self.bottom, self.toast, self.drawer = host, top, bottom, toast, drawer
        self._on_insets = on_insets
        self._insets = (-1, -1)
        self.shown = 1.0         # 1 : barres visibles, 0 : rangées hors de la zone
        self.visible = True
        self.drawer_shown = 0.0  # 1 : tiroir sorti
        self.drawer_open = False
        for widget in (top, bottom, toast, drawer):
            widget.setParent(host)
            widget.installEventFilter(self)
        host.installEventFilter(self)
        self._anim = self._tween(lambda v: setattr(self, "shown", v))
        self._drawer_anim = self._tween(lambda v: setattr(self, "drawer_shown", v))
        self._later = QTimer(self)  # détruit avec la zone : jamais de placement posthume
        self._later.setSingleShot(True)
        self._later.timeout.connect(self.place)

    def _tween(self, setter) -> QVariantAnimation:
        anim = QVariantAnimation(self)
        anim.setDuration(SLIDE_MS)
        anim.valueChanged.connect(lambda value: (setter(float(value)), self.place()))
        return anim

    def _run(self, anim: QVariantAnimation, start: float, opened: bool, animated: bool, setter) -> None:
        anim.stop()
        target = 1.0 if opened else 0.0
        if animated and self.host.isVisible():
            anim.setEasingCurve(QEasingCurve.OutCubic if opened else QEasingCurve.InCubic)
            anim.setStartValue(start)
            anim.setEndValue(target)
            anim.start()
        else:
            setter(target)
            self.place()

    def set_visible(self, visible: bool, animated: bool = True) -> None:
        self.visible = visible
        self._run(self._anim, self.shown, visible, animated, lambda v: setattr(self, "shown", v))

    def set_drawer_open(self, opened: bool, animated: bool = True) -> None:
        self.drawer_open = opened
        self._run(self._drawer_anim, self.drawer_shown, opened, animated,
                  lambda v: setattr(self, "drawer_shown", v))

    def eventFilter(self, obj, event) -> bool:
        if event.type() in (QEvent.Resize, QEvent.LayoutRequest, QEvent.Show):
            if obj is self.host:
                self.place()
            else:
                self._later.start(0)  # après le calcul de la nouvelle taille de la barre
        return False

    def place(self) -> None:
        width, height = self.host.width(), self.host.height()
        heights = []
        for bar, at_top in ((self.top, True), (self.bottom, False)):
            hint = bar.sizeHint()
            bar.resize(min(hint.width(), max(width - 2 * EDGE, 0)), hint.height())
            shown_y = MARGIN if at_top else height - hint.height() - MARGIN
            hidden_y = -hint.height() - 2 * MARGIN if at_top else height + 2 * MARGIN  # ombre comprise
            bar.move((width - bar.width()) // 2, round(hidden_y + (shown_y - hidden_y) * self.shown))
            bar.setVisible(self.shown > 0)
            bar.raise_()
            heights.append(hint.height() + MARGIN + GAP)
        self._place_drawer()
        self._place_toast(width, height)
        insets = tuple(heights)
        if insets != self._insets and self._on_insets is not None:
            self._insets = insets
            self._on_insets(*insets)

    def _place_drawer(self) -> None:
        drawer, top = self.drawer, self.top
        drawer.resize(drawer.sizeHint())
        hidden_y, shown_y = top.y(), top.y() + top.height() + GAP  # caché derrière la barre du haut
        drawer.move(top.x() + top.width() - drawer.width(), round(hidden_y + (shown_y - hidden_y) * self.drawer_shown))
        drawer.setVisible(self.drawer_shown > 0 and self.shown > 0)
        drawer.raise_()
        top.raise_()

    def _place_toast(self, width: int, height: int) -> None:
        toast = self.toast
        toast.setMaximumWidth(max(min(560, width - 2 * MARGIN), 0))
        toast.adjustSize()
        bottom = self.bottom.y() if self.shown > 0 else height - MARGIN
        toast.move((width - toast.width()) // 2, min(bottom, height - MARGIN) - GAP - toast.height())
        toast.raise_()
