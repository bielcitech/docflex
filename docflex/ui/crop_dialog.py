"""Fenêtre de recadrage : un cadre à tracer, déplacer et redimensionner sur l'aperçu de la page."""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QHBoxLayout, QSizePolicy, QVBoxLayout, QWidget

from docflex.core.organize import FULL, Box
from docflex.ui import theme
from docflex.ui.widgets import button, combo, label

HANDLE = 8         # px : zone de prise des bords et des coins
MIN_SIZE = 0.03    # fraction : plus petit cadre autorisé


class CropCanvas(QWidget):
    """Aperçu de page avec un cadre de recadrage, en fractions de la page (0..1)."""

    changed = Signal()

    def __init__(self, image: QImage, box: Box = FULL) -> None:
        super().__init__()
        self._image = image
        self.box = box
        self._drag: tuple[str, QPointF, Box] | None = None  # (mode, point de départ, cadre de départ)
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumSize(320, 320)

    def sizeHint(self) -> QSize:
        return self._image.size().scaled(QSize(760, 660), Qt.KeepAspectRatio) + QSize(24, 24)

    def set_box(self, box: Box) -> None:
        self.box = box
        self.update()
        self.changed.emit()

    # ------------------------------------------------- géométrie

    def image_rect(self) -> QRectF:
        size = self._image.size().scaled(self.size() - QSize(24, 24), Qt.KeepAspectRatio)
        return QRectF((self.width() - size.width()) / 2, (self.height() - size.height()) / 2,
                      size.width(), size.height())

    def box_rect(self) -> QRectF:
        r = self.image_rect()
        x0, y0, x1, y1 = self.box
        return QRectF(r.left() + x0 * r.width(), r.top() + y0 * r.height(),
                      (x1 - x0) * r.width(), (y1 - y0) * r.height())

    def _to_fraction(self, point: QPointF) -> tuple[float, float]:
        r = self.image_rect()
        return (min(1.0, max(0.0, (point.x() - r.left()) / r.width())),
                min(1.0, max(0.0, (point.y() - r.top()) / r.height())))

    def _hit(self, point: QPointF) -> str:
        """Partie du cadre sous le pointeur : combinaison de l/r/t/b, « move », ou « new »."""
        b = self.box_rect()
        near = lambda a, v: abs(a - v) <= HANDLE  # noqa: E731
        inside_x = b.left() - HANDLE <= point.x() <= b.right() + HANDLE
        inside_y = b.top() - HANDLE <= point.y() <= b.bottom() + HANDLE
        edges = ""
        if inside_y and near(point.x(), b.left()):
            edges += "l"
        elif inside_y and near(point.x(), b.right()):
            edges += "r"
        if inside_x and near(point.y(), b.top()):
            edges += "t"
        elif inside_x and near(point.y(), b.bottom()):
            edges += "b"
        if edges:
            return edges
        return "move" if b.contains(point) else "new"

    # ------------------------------------------------- souris

    _CURSORS = {
        "l": Qt.SizeHorCursor, "r": Qt.SizeHorCursor, "t": Qt.SizeVerCursor, "b": Qt.SizeVerCursor,
        "lt": Qt.SizeFDiagCursor, "rb": Qt.SizeFDiagCursor, "rt": Qt.SizeBDiagCursor, "lb": Qt.SizeBDiagCursor,
        "move": Qt.SizeAllCursor, "new": Qt.CrossCursor,
    }

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.LeftButton:
            return
        point = event.position()
        self._drag = (self._hit(point), point, self.box)

    def mouseMoveEvent(self, event) -> None:
        point = event.position()
        if self._drag is None:
            self.setCursor(self._CURSORS[self._hit(point)])
            return
        mode, start, (x0, y0, x1, y1) = self._drag
        r = self.image_rect()
        dx = (point.x() - start.x()) / r.width()
        dy = (point.y() - start.y()) / r.height()
        if mode == "new":  # nouveau cadre, tracé depuis le point de départ dans n'importe quelle direction
            (ax, ay), (bx, by) = self._to_fraction(start), self._to_fraction(point)
            self.box = (min(ax, bx), min(ay, by), max(ax, bx), max(ay, by))
        elif mode == "move":
            dx = min(max(dx, -x0), 1 - x1)
            dy = min(max(dy, -y0), 1 - y1)
            self.box = (x0 + dx, y0 + dy, x1 + dx, y1 + dy)
        else:
            if "l" in mode:
                x0 = min(max(0.0, x0 + dx), x1 - MIN_SIZE)
            if "r" in mode:
                x1 = max(min(1.0, x1 + dx), x0 + MIN_SIZE)
            if "t" in mode:
                y0 = min(max(0.0, y0 + dy), y1 - MIN_SIZE)
            if "b" in mode:
                y1 = max(min(1.0, y1 + dy), y0 + MIN_SIZE)
            self.box = (x0, y0, x1, y1)
        self.update()
        self.changed.emit()

    def mouseReleaseEvent(self, event) -> None:
        if self._drag and self._drag[0] == "new":
            x0, y0, x1, y1 = self.box
            if x1 - x0 < MIN_SIZE or y1 - y0 < MIN_SIZE:  # simple clic : on garde le cadre précédent
                self.box = self._drag[2]
                self.update()
                self.changed.emit()
        self._drag = None

    # ------------------------------------------------- dessin

    def paintEvent(self, event) -> None:
        t = theme.current()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        painter.setRenderHint(QPainter.Antialiasing)
        page = self.image_rect()
        painter.drawImage(page, self._image)
        painter.setPen(QPen(QColor(t.color("border")), 1))
        painter.drawRect(page)

        crop = self.box_rect()
        shade = QColor(0, 0, 0, 120)
        painter.fillRect(QRectF(page.left(), page.top(), page.width(), crop.top() - page.top()), shade)
        painter.fillRect(QRectF(page.left(), crop.bottom(), page.width(), page.bottom() - crop.bottom()), shade)
        painter.fillRect(QRectF(page.left(), crop.top(), crop.left() - page.left(), crop.height()), shade)
        painter.fillRect(QRectF(crop.right(), crop.top(), page.right() - crop.right(), crop.height()), shade)

        accent = QColor(t.color("accent"))
        painter.setPen(QPen(accent, 2))
        painter.drawRect(crop)
        painter.setBrush(QColor("#ffffff"))
        for x in (crop.left(), crop.center().x(), crop.right()):
            for y in (crop.top(), crop.center().y(), crop.bottom()):
                if (x, y) != (crop.center().x(), crop.center().y()):
                    painter.drawRect(QRectF(x - 4, y - 4, 8, 8))
        painter.end()


class CropDialog(QDialog):
    """Retourne, si validée, le cadre visible (`box`) et la portée (`scope`)."""

    def __init__(self, parent, image: QImage, box: Box, scopes: list[tuple[str, str]], default_scope: str,
                 auto_box: Callable[[], Box | None]) -> None:
        super().__init__(parent)
        self.setWindowTitle("Recadrer")
        self._auto_box = auto_box
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.addWidget(label(
            "Tracez ou ajustez le cadre : seule la zone claire sera conservée. "
            "Le contenu masqué reste dans le fichier et peut être rétabli.", "muted", wrap=True))

        self.canvas = CropCanvas(image, box)
        layout.addWidget(self.canvas, 1)

        row = QHBoxLayout()
        row.addWidget(label("Appliquer à", "field"))
        self.scope = combo(scopes, width=260)
        self.scope.setCurrentIndex(max(0, self.scope.findData(default_scope)))
        row.addWidget(self.scope)
        row.addStretch()
        auto = button("Marges auto", on_click=self._auto)
        auto.setToolTip("Ajuste le cadre au contenu de cette page en retirant les marges blanches")
        row.addWidget(auto)
        row.addWidget(button("Pleine page", on_click=lambda: self.canvas.set_box(FULL)))
        layout.addLayout(row)

        buttons = QDialogButtonBox()
        buttons.addButton("Annuler", QDialogButtonBox.RejectRole)
        ok = buttons.addButton("Recadrer", QDialogButtonBox.AcceptRole)
        ok.setProperty("kind", "primary")
        ok.setDefault(True)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @property
    def box(self) -> Box:
        return self.canvas.box

    @property
    def scope_value(self) -> str:
        return self.scope.currentData()

    def _auto(self) -> None:
        found = self._auto_box()
        if found:
            self.canvas.set_box(found)
