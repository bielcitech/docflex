"""Grille de miniatures de pages, réordonnables par glisser-déposer."""

from __future__ import annotations

from pathlib import Path

import pymupdf
from PySide6.QtCore import QPoint, QRect, QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPen, QPixmap, QTransform
from PySide6.QtWidgets import QAbstractItemView, QApplication, QListView, QListWidget, QListWidgetItem

from docflex.core.organize import PageRef, render_thumbnail
from docflex.ui import theme
from docflex.utils.files import ext_of

PAGE_RATIO = 1.414  # A4 portrait : hauteur / largeur de la case


class ThumbnailLoader(QThread):
    """Rend les miniatures d'un PDF une à une, sans bloquer l'interface."""

    rendered = Signal(int, int, QImage)  # source, page, image

    def __init__(self, source: int, path: Path, parent=None, password: str | None = None) -> None:
        super().__init__(parent)
        self._source = source
        self._path = path
        self._password = password  # PDF protégé : le mot de passe déjà saisi par l'utilisateur

    def run(self) -> None:
        # Document ouvert dans ce thread : un document PyMuPDF ne se partage pas entre threads.
        with pymupdf.open(self._path) as doc:
            if doc.needs_pass and not doc.authenticate(self._password or ""):
                return
            for index in range(doc.page_count):
                if self.isInterruptionRequested():
                    return
                pix = render_thumbnail(doc, index)
                image = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format_RGB888).copy()
                self.rendered.emit(self._source, index, image)


class PageGrid(QListWidget):
    """Affiche une liste de `PageRef` ; signale les déplacements sans modifier l'ordre lui-même.

    La vue applique le déplacement à son modèle puis rappelle `set_pages` :
    l'ordre affiché reste ainsi toujours celui du modèle (et de l'historique).

    Le déplacement des pages est géré ici à la souris (appui, mouvement, relâchement) plutôt que
    par le glisser-déposer du système, dont le comportement varie selon les plateformes. Le
    glisser-déposer système ne sert qu'aux PDF déposés depuis le Finder ou l'Explorateur.
    """

    moved = Signal(list, int)        # positions déplacées, position d'insertion
    files_dropped = Signal(list)     # PDF déposés depuis l'explorateur de fichiers

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("pageGrid")
        self.setViewMode(QListView.IconMode)
        self.setMovement(QListView.Static)
        self.setResizeMode(QListView.Adjust)
        self.setWrapping(True)
        self.setUniformItemSizes(True)
        self.setSpacing(6)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setDragEnabled(False)          # déplacement interne géré à la souris (voir plus bas)
        self.setAcceptDrops(True)           # PDF déposés depuis l'extérieur
        self.setDropIndicatorShown(False)   # remplacé par une barre verticale, plus lisible en grille
        self.setDragDropMode(QAbstractItemView.DropOnly)
        self.setTextElideMode(Qt.ElideMiddle)

        self.thumbs: dict[tuple[int, int], QImage] = {}
        self._icons: dict[tuple, QIcon] = {}
        self._pages: list[PageRef] = []
        self._labels: list[str] = []
        self._drop_row: int | None = None
        self._press: QPoint | None = None   # appui sur une page : départ possible d'un déplacement
        self._dragging = False
        self._cursor = QPoint()
        self._scroll_step = 0
        self._autoscroll = QTimer(self)
        self._autoscroll.setInterval(30)
        self._autoscroll.timeout.connect(self._scroll_while_dragging)
        self.set_thumb_width(140)
        theme.current().changed.connect(self._restyle)

    # ----------------------------------------------------------- API

    def set_thumb_width(self, width: int) -> None:
        self._thumb = QSize(width, round(width * PAGE_RATIO))
        self.setIconSize(self._thumb)
        self.setGridSize(QSize(width + 28, self._thumb.height() + 44))
        self._icons.clear()
        self._refresh_icons()

    def set_pages(self, pages: list[PageRef], labels: list[str], selected: list[int] = ()) -> None:
        self._pages, self._labels = list(pages), list(labels)
        self.clear()
        for ref, text in zip(pages, labels):
            item = QListWidgetItem(self._icon(ref), text)
            item.setTextAlignment(Qt.AlignHCenter | Qt.AlignTop)
            self.addItem(item)
        for row in selected:
            self.item(row).setSelected(True)
        if selected:
            self.scrollToItem(self.item(selected[0]))

    def selected_rows(self) -> list[int]:
        return sorted(self.row(item) for item in self.selectedItems())

    def add_thumbnail(self, source: int, index: int, image: QImage) -> None:
        self.thumbs[(source, index)] = image
        for row, ref in enumerate(self._pages):
            if (ref.source, ref.index) == (source, index):
                self.item(row).setIcon(self._icon(ref))

    def forget(self) -> None:
        """Vide la grille et le cache de miniatures (ne pas nommer `reset` : méthode de Qt)."""
        self.thumbs.clear()
        self._icons.clear()
        self.set_pages([], [])

    # ------------------------------------------------------- rendu

    def _icon(self, ref: PageRef) -> QIcon:
        ready = ref.is_blank or (ref.source, ref.index) in self.thumbs
        key = (ref.source, ref.index, ref.rotation, ref.crop, ref.size) if ready else ("…", ref.rotation % 180)
        icon = self._icons.get(key)
        if icon is None:
            pixmap = self._pixmap(ref, ready)
            icon = QIcon()
            # Même image une fois sélectionnée : Qt la teinterait sinon de la couleur d'accent.
            icon.addPixmap(pixmap, QIcon.Normal)
            icon.addPixmap(pixmap, QIcon.Selected)
            self._icons[key] = icon
        return icon

    def _pixmap(self, ref: PageRef, ready: bool) -> QPixmap:
        t = theme.current()
        box = self._thumb
        canvas = QPixmap(box * self.devicePixelRatioF())
        canvas.setDevicePixelRatio(self.devicePixelRatioF())
        canvas.fill(Qt.transparent)
        painter = QPainter(canvas)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)

        image = None
        if ref.is_blank:
            width, height = ref.size
            if ref.rotation % 180:
                width, height = height, width
            size = QSize(1000, round(1000 * height / width)).scaled(box, Qt.KeepAspectRatio)
        elif ready:
            image = self.thumbs[(ref.source, ref.index)]
            if ref.crop:
                x0, y0, x1, y1 = ref.crop
                w, h = image.width(), image.height()
                image = image.copy(QRect(round(x0 * w), round(y0 * h),
                                         max(1, round((x1 - x0) * w)), max(1, round((y1 - y0) * h))))
            if ref.rotation:
                image = image.transformed(QTransform().rotate(ref.rotation), Qt.SmoothTransformation)
            size = image.size().scaled(box, Qt.KeepAspectRatio)
        else:  # en attente du rendu : page vierge aux proportions A4
            size = box if ref.rotation % 180 == 0 else QSize(box.width(), round(box.width() / PAGE_RATIO))
        rect = QRect(QPoint((box.width() - size.width()) // 2, (box.height() - size.height()) // 2), size)
        if image is not None:
            painter.drawImage(rect, image)
        else:
            painter.fillRect(rect, QColor("#ffffff") if ref.is_blank else QColor(t.color("surface2")))
        painter.setPen(QPen(QColor(t.color("border")), 1))
        painter.drawRect(rect.adjusted(0, 0, -1, -1))
        painter.end()
        return canvas

    def _refresh_icons(self) -> None:
        for row, ref in enumerate(self._pages):
            self.item(row).setIcon(self._icon(ref))

    def _restyle(self) -> None:
        self._icons.clear()  # bordures et pages vierges suivent le thème
        self._refresh_icons()

    # ---------------------------------------------- glisser-déposer

    # ------------------------------------- déplacement des pages à la souris

    def mousePressEvent(self, event) -> None:
        on_page = event.button() == Qt.LeftButton and self.itemAt(event.position().toPoint()) is not None
        self._press = event.position().toPoint() if on_page else None
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        pos = event.position().toPoint()
        if self._press is not None and event.buttons() & Qt.LeftButton:
            if not self._dragging and (pos - self._press).manhattanLength() >= QApplication.startDragDistance():
                self._dragging = True
                self.viewport().setCursor(Qt.ClosedHandCursor)
            if self._dragging:
                self._cursor = pos
                self._drop_row = self.insertion_row(pos)
                margin = 40  # près du bord haut ou bas : la grille défile
                height = self.viewport().height()
                self._scroll_step = -14 if pos.y() < margin else 14 if pos.y() > height - margin else 0
                if self._scroll_step and not self._autoscroll.isActive():
                    self._autoscroll.start()
                self.viewport().update()
                return  # pas de sélection au lasso pendant le déplacement
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._dragging:
            rows = self.selected_rows()
            target = self.insertion_row(event.position().toPoint())
            self._end_drag()
            if rows:
                self.moved.emit(rows, target)
            return  # ne pas laisser Qt réduire une sélection multiple à la seule page saisie
        self._press = None
        super().mouseReleaseEvent(event)

    def _end_drag(self) -> None:
        self._press, self._dragging, self._drop_row = None, False, None
        self._autoscroll.stop()
        self.viewport().unsetCursor()
        self.viewport().update()

    def _scroll_while_dragging(self) -> None:
        if not self._dragging or not self._scroll_step:
            self._autoscroll.stop()
            return
        bar = self.verticalScrollBar()
        bar.setValue(bar.value() + self._scroll_step)
        self._drop_row = self.insertion_row(self._cursor)
        self.viewport().update()

    def keyPressEvent(self, event) -> None:
        if self._dragging and event.key() == Qt.Key_Escape:  # Échap : abandon du déplacement
            self._end_drag()
            return
        super().keyPressEvent(event)

    def insertion_row(self, pos: QPoint) -> int:
        """Position d'insertion (0..count) pour un dépôt au point `pos` de la zone d'affichage."""
        for row in range(self.count()):
            rect = self.visualItemRect(self.item(row))
            if rect.top() > pos.y():                       # pointeur dans une rangée au-dessus
                return row
            if rect.bottom() >= pos.y() and pos.x() < rect.center().x():
                return row
        return self.count()

    @staticmethod
    def _pdf_urls(event) -> list[Path]:
        mime = event.mimeData()
        if not mime.hasUrls():
            return []
        return [Path(u.toLocalFile()) for u in mime.urls() if u.isLocalFile() and ext_of(Path(u.toLocalFile())) == "pdf"]

    def dragEnterEvent(self, event) -> None:  # PDF venant du Finder ou de l'Explorateur
        if self._pdf_urls(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:
        if not self._pdf_urls(event):
            event.ignore()
            return
        self._drop_row = self.insertion_row(event.position().toPoint())
        self.viewport().update()
        event.acceptProposedAction()

    def dragLeaveEvent(self, event) -> None:
        self._drop_row = None
        self.viewport().update()
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:
        self._drop_row = None
        self.viewport().update()
        paths = self._pdf_urls(event)
        if paths:
            self.files_dropped.emit(paths)
        event.acceptProposedAction()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if self._drop_row is None or self.count() == 0:
            return
        if self._drop_row < self.count():
            rect = self.visualItemRect(self.item(self._drop_row))
            x = rect.left() - self.spacing() // 2 - 1
        else:
            rect = self.visualItemRect(self.item(self.count() - 1))
            x = rect.right() + self.spacing() // 2 + 1
        painter = QPainter(self.viewport())
        painter.setPen(QPen(QColor(theme.current().color("accent")), 3, Qt.SolidLine, Qt.RoundCap))
        painter.drawLine(x, rect.top() + 6, x, rect.bottom() - 6)
        if self._dragging:
            self._paint_ghost(painter)
        painter.end()

    def _paint_ghost(self, painter: QPainter) -> None:
        """Miniature translucide des pages déplacées, sous le curseur, avec leur nombre."""
        rows = self.selected_rows()
        if not rows:
            return
        pixmap = self.item(rows[0]).icon().pixmap(self._thumb)
        size = pixmap.deviceIndependentSize().toSize().scaled(QSize(90, 120), Qt.KeepAspectRatio)
        target = QRect(self._cursor + QPoint(12, 12), size)
        painter.setOpacity(0.75)
        painter.drawPixmap(target, pixmap)
        painter.setOpacity(1)
        if len(rows) > 1:
            badge = QRect(target.right() - 14, target.top() - 10, 26, 22)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(theme.current().color("accent")))
            painter.drawRoundedRect(badge, 11, 11)
            painter.setPen(QColor("#ffffff"))
            painter.drawText(badge, Qt.AlignCenter, str(len(rows)))
