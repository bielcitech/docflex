"""Accueil du lecteur : documents récents et favoris, en grille de cartes ou en liste."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pymupdf
from PySide6.QtCore import (
    QEasingCurve, QPoint, QPointF, QRect, QRectF, QSize, Qt, QThread, QVariantAnimation, Signal,
)
from PySide6.QtGui import QColor, QFont, QFontMetrics, QImage, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QLineEdit, QListView, QListWidget, QListWidgetItem, QMenu, QStackedLayout,
    QStyle, QStyledItemDelegate, QVBoxLayout, QWidget,
)

from docflex.core.recent import SORTS, RecentEntry, RecentStore, when
from docflex.ui import theme
from docflex.ui.icons import icon
from docflex.ui.widgets import SegmentedControl, button, card, combo, label
from docflex.utils.files import ext_of, human_size
from docflex.utils.system import reveal

CARD = QSize(214, 300)      # case de la grille (marges comprises)
ROW_HEIGHT = 68             # ligne en mode liste
THUMB_PX = 420              # largeur des miniatures en cache (assez pour un écran Retina)
HOVER_MS = 150
TILT_DEGREES = 3          # inclinaison de la miniature au survol
ENTRY_ROLE = Qt.UserRole


# --------------------------------------------------------------- miniatures

class ThumbnailWorker(QThread):
    """Première page de chaque document, rendue une fois puis gardée en cache sur le disque."""

    ready = Signal(str, QImage)

    def __init__(self, paths: list[str], cache: Path, parent=None) -> None:
        super().__init__(parent)
        self._paths = paths
        self._cache = cache

    @staticmethod
    def cache_file(cache: Path, path: str) -> Path | None:
        try:
            stat = Path(path).stat()
        except OSError:
            return None
        # La clé change si le fichier est modifié : la miniature est alors refaite.
        key = hashlib.sha1(f"{path}|{stat.st_mtime_ns}|{stat.st_size}".encode()).hexdigest()
        return cache / f"{key}.png"

    def run(self) -> None:
        self._cache.mkdir(parents=True, exist_ok=True)
        for path in self._paths:
            if self.isInterruptionRequested():
                return
            target = self.cache_file(self._cache, path)
            if target is None:
                continue
            image = QImage(str(target)) if target.exists() else QImage()
            if image.isNull():
                try:
                    with pymupdf.open(path) as doc:
                        if doc.needs_pass or doc.page_count == 0:
                            continue
                        page = doc[0]
                        zoom = THUMB_PX / page.rect.width
                        pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
                        image = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format_RGB888).copy()
                    image.save(str(target))
                except Exception:
                    continue
            self.ready.emit(path, image)


# ---------------------------------------------------------------- dessin

def _elide_lines(text: str, metrics: QFontMetrics, width: int, lines: int) -> list[str]:
    """Coupe `text` en au plus `lines` lignes ; la dernière se termine par « … » si besoin.

    Les noms de fichiers sans espaces (« Chart.11.Aerodrome.Chart.en ») passent à la ligne
    après un point, un tiret ou un souligné.
    """
    tokens = re.findall(r"[^\s._\-]+[._\-]*|[._\-]+|\s+", text)
    result, current, i = [], "", 0
    while i < len(tokens) and len(result) < lines - 1:
        candidate = current + tokens[i]
        if not current.strip() or metrics.horizontalAdvance(candidate.rstrip()) <= width:
            current, i = candidate, i + 1
        else:
            result.append(current.strip())
            current = ""
    result.append((current + "".join(tokens[i:])).strip())
    return [metrics.elidedText(line, Qt.ElideRight, width) for line in result if line]


def _short_folder(path: str) -> str:
    folder, home = Path(path).parent, Path.home()
    return f"~/{folder.relative_to(home)}" if folder.is_relative_to(home) else str(folder)


class RecentDelegate(QStyledItemDelegate):
    """Carte (grille) ou ligne (liste) d'un document récent."""

    def __init__(self, home: ReaderHome) -> None:
        super().__init__(home)
        self.home = home

    def sizeHint(self, option, index) -> QSize:
        return CARD if self.home.grid_mode else QSize(option.rect.width(), ROW_HEIGHT)

    def more_rect(self, rect: QRect) -> QRect:
        """Zone du bouton « ⋯ »."""
        if self.home.grid_mode:
            card_rect = rect.adjusted(8, 8, -8, -8)
            return QRect(card_rect.right() - 40, card_rect.bottom() - 40, 30, 30)
        return QRect(rect.right() - 44, rect.center().y() - 15, 30, 30)

    def paint(self, painter: QPainter, option, index) -> None:
        entry: RecentEntry = index.data(ENTRY_ROLE)
        hover = self.home.hover_amount(index.row())
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        if self.home.grid_mode:
            self._paint_card(painter, option.rect, entry, hover, option.state & QStyle.State_Selected)
        else:
            self._paint_row(painter, option.rect, entry, hover)
        painter.restore()

    def _paint_card(self, painter: QPainter, rect: QRect, entry: RecentEntry, hover: float, selected) -> None:
        t = theme.current()
        lift = round(3 * hover)  # la carte se soulève au survol
        box = QRectF(rect.adjusted(8, 8 - lift, -8, -8 - lift))
        if hover > 0:  # ombre douce, plus marquée à mesure que la carte se soulève
            for i, alpha in enumerate((10, 16, 22)):
                shadow = QColor(0, 0, 0, round(alpha * hover * (1.6 if t.scheme == "dark" else 1)))
                painter.setPen(Qt.NoPen)
                painter.setBrush(shadow)
                spread = 3 - i
                painter.drawRoundedRect(box.adjusted(-spread, 4 + lift - spread, spread, 4 + lift + spread), 12, 12)
        path = QPainterPath()
        path.addRoundedRect(box, 10, 10)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(t.color("surface")))
        painter.drawPath(path)

        if not entry.exists:
            painter.setOpacity(0.5)
        pad = 14
        inner = box.adjusted(pad, pad, -pad, 0)
        title_font = QFont(painter.font())
        title_font.setPointSizeF(title_font.pointSizeF() + 1)
        title_font.setWeight(QFont.DemiBold)
        painter.setFont(title_font)
        painter.setPen(QColor(t.color("text")))
        metrics = QFontMetrics(title_font)
        y = inner.top()
        for line in _elide_lines(entry.name, metrics, int(inner.width()) - (16 if entry.favorite else 0), 2):
            painter.drawText(QRectF(inner.left(), y, inner.width(), metrics.height()), Qt.AlignLeft | Qt.AlignTop,
                             line)
            y += metrics.height()
        if entry.favorite:
            painter.setPen(QColor("#f5a524"))
            painter.drawText(QRectF(inner.right() - 14, inner.top(), 16, metrics.height()), Qt.AlignRight, "★")

        meta_font = QFont(painter.font())
        meta_font.setPointSizeF(title_font.pointSizeF() - 2)
        meta_font.setWeight(QFont.Normal)
        painter.setFont(meta_font)
        painter.setPen(QColor(t.color("muted")))
        meta_top = inner.top() + 2 * metrics.height() + 8
        meta = (f"{entry.last_page + 1}/{entry.page_count}  |  {human_size(entry.size)}" if entry.exists
                else "Fichier introuvable")
        painter.drawText(QRectF(inner.left(), meta_top, inner.width(), 18), Qt.AlignLeft, meta)

        # Miniature : haut de la première page, coupé par le bas de la carte (comme une feuille posée).
        area = QRectF(inner.left(), meta_top + 30, inner.width(), box.bottom() - meta_top - 30)
        painter.save()
        painter.setClipPath(path)
        image = self.home.thumbnail(entry.path)
        page_rect = QRectF(area.left(), area.top(), area.width(), area.width() * 1.414)
        if image is not None and not image.isNull():
            page_rect.setHeight(area.width() * image.height() / image.width())
        if hover > 0:
            # Au survol, la feuille pivote légèrement, comme saisie du bout des doigts.
            pivot = QPointF(page_rect.center().x(), area.top() + area.height() * 0.6)
            painter.translate(pivot)
            painter.rotate(-TILT_DEGREES * hover)
            painter.translate(-pivot)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(0, 0, 0, round(28 * hover)))
            painter.drawRect(page_rect.translated(2, 3))
        if image is not None and not image.isNull():
            painter.drawImage(page_rect, image)
        else:
            painter.fillRect(page_rect, QColor("#ffffff"))
        painter.setPen(QPen(QColor(t.color("border")), 1))
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(page_rect)
        painter.restore()
        painter.setOpacity(1)

        border = QColor(t.color("accent")) if hover > 0.5 or selected else QColor(t.color("border"))
        painter.setPen(QPen(border, 1.2 if hover > 0.5 else 1))
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(path)
        if hover > 0.05:  # bouton « ⋯ » qui apparaît en fondu
            more = QRectF(self.more_rect(rect).translated(0, -lift))
            painter.setOpacity(hover)
            painter.setPen(QPen(QColor(t.color("border")), 1))
            painter.setBrush(QColor(t.color("surface")))
            painter.drawRoundedRect(more, 8, 8)
            painter.setPen(QColor(t.color("text")))
            painter.drawText(more, Qt.AlignCenter, "⋯")
            painter.setOpacity(1)

    def _paint_row(self, painter: QPainter, rect: QRect, entry: RecentEntry, hover: float) -> None:
        t = theme.current()
        if hover > 0:
            tint = QColor(t.color("accent_soft"))
            tint.setAlphaF(hover)
            painter.setPen(Qt.NoPen)
            painter.setBrush(tint)
            painter.drawRoundedRect(QRectF(rect.adjusted(4, 2, -4, -2)), 8, 8)
        if not entry.exists:
            painter.setOpacity(0.5)
        thumb = QRectF(rect.left() + 14, rect.top() + 6, 42, ROW_HEIGHT - 12)
        image = self.home.thumbnail(entry.path)
        if image is not None and not image.isNull():
            painter.drawImage(thumb, image, QRectF(0, 0, image.width(), image.width() * thumb.height() / thumb.width()))
        else:
            painter.fillRect(thumb, QColor("#ffffff"))
        painter.setPen(QPen(QColor(t.color("border")), 1))
        painter.setBrush(Qt.NoBrush)  # sinon la teinte de survol recouvre la miniature
        painter.drawRect(thumb)

        text_left = thumb.right() + 14
        right_columns = 330
        name_font = QFont(painter.font())
        name_font.setWeight(QFont.DemiBold)
        painter.setFont(name_font)
        painter.setPen(QColor(t.color("text")))
        width = rect.right() - right_columns - text_left
        star = "★ " if entry.favorite else ""
        painter.drawText(QRectF(text_left, rect.top() + 12, width, 20), Qt.AlignLeft,
                         QFontMetrics(name_font).elidedText(star + entry.name, Qt.ElideRight, int(width)))
        small = QFont(painter.font())
        small.setWeight(QFont.Normal)
        painter.setFont(small)
        painter.setPen(QColor(t.color("muted")))
        folder = _short_folder(entry.path) if entry.exists else "Fichier introuvable"
        painter.drawText(QRectF(text_left, rect.top() + 34, width, 20), Qt.AlignLeft,
                         QFontMetrics(small).elidedText(folder, Qt.ElideMiddle, int(width)))
        columns = (
            (when(entry.opened_at), 130),
            (f"{entry.last_page + 1}/{entry.page_count} p.", 70),
            (human_size(entry.size), 70),
        )
        x = rect.right() - right_columns + 10
        for text, column in columns:
            painter.drawText(QRectF(x, rect.top(), column, ROW_HEIGHT), Qt.AlignVCenter | Qt.AlignLeft, text)
            x += column
        painter.setOpacity(1)
        if hover > 0.05:
            more = QRectF(self.more_rect(rect))
            painter.setOpacity(hover)
            painter.setPen(QColor(t.color("text")))
            painter.drawText(more, Qt.AlignCenter, "⋯")
            painter.setOpacity(1)

    def editorEvent(self, event, model, option, index) -> bool:
        # Clic sur « ⋯ » : menu de la carte (et pas ouverture du document).
        if event.type() == event.Type.MouseButtonRelease and self.more_rect(option.rect).contains(
                event.position().toPoint()):
            self.home.show_menu(index.data(ENTRY_ROLE), event.globalPosition().toPoint())
            return True
        return super().editorEvent(event, model, option, index)


# ----------------------------------------------------------------- accueil

class ReaderHome(QWidget):
    open_requested = Signal(Path)
    browse_requested = Signal()

    def __init__(self, store: RecentStore, cache: Path) -> None:
        super().__init__()
        self.store = store
        self.cache = cache
        self.grid_mode = True
        self._thumbs: dict[str, QImage] = {}
        self._worker: ThumbnailWorker | None = None
        self._hover: dict[int, float] = {}
        self._animations: dict[int, QVariantAnimation] = {}
        self._hovered = -1
        self.setAcceptDrops(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)

        top = QHBoxLayout()
        self.tabs = SegmentedControl((("Récent", "recent"), ("Favoris", "favorites")))
        self.tabs.set_value("recent")
        self.tabs.changed.connect(lambda _: self.refresh())
        top.addWidget(self.tabs)
        top.addStretch()
        self.open_btn = button("  Ouvrir un fichier", "primary", self.browse_requested.emit)
        self.open_btn.setMinimumHeight(40)
        top.addWidget(self.open_btn)
        layout.addLayout(top)

        panel = card()
        box = QVBoxLayout(panel)
        box.setContentsMargins(16, 14, 16, 8)
        tools = QHBoxLayout()
        tools.setSpacing(6)
        self.count = label("", "muted")
        tools.addWidget(self.count)
        tools.addStretch()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Rechercher un document…")
        self.search.setClearButtonEnabled(True)
        self.search.setFixedWidth(220)
        self.search.textChanged.connect(lambda _: self.refresh())
        tools.addWidget(self.search)
        self.sort = combo(((text, key) for key, text in SORTS.items()), width=170)
        self.sort.setToolTip("Trier")
        self.sort.currentIndexChanged.connect(lambda _: self.refresh())
        tools.addWidget(self.sort)
        self.grid_btn = button("", "tool", lambda: self.set_grid_mode(True))
        self.list_btn = button("", "tool", lambda: self.set_grid_mode(False))
        self.clear_btn = button("", "tool", self._clear)
        for btn, tip in ((self.grid_btn, "Affichage en grille"), (self.list_btn, "Affichage en liste"),
                         (self.clear_btn, "Effacer l'historique (les favoris sont conservés)")):
            btn.setFixedSize(34, 32)
            btn.setToolTip(tip)
            tools.addWidget(btn)
        self.grid_btn.setCheckable(True)
        self.list_btn.setCheckable(True)
        box.addLayout(tools)

        self.body = QStackedLayout()
        self.list = QListWidget()
        self.list.setObjectName("recentList")
        self.list.setMouseTracking(True)
        self.list.setSelectionMode(QAbstractItemView.NoSelection)
        self.list.setFocusPolicy(Qt.NoFocus)
        self.list.setItemDelegate(RecentDelegate(self))
        self.list.itemClicked.connect(self._on_click)
        self.list.itemEntered.connect(lambda item: self._set_hovered(self.list.row(item)))
        self._viewport = self.list.viewport()  # référence directe : sûre même pendant la destruction
        self._viewport.installEventFilter(self)
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._on_context_menu)
        self.body.addWidget(self.list)
        self.empty = self._build_empty()
        self.body.addWidget(self.empty)
        box.addLayout(self.body, 1)
        layout.addWidget(panel, 1)

        self.status = label("", "muted")
        layout.addWidget(self.status)

        theme.current().changed.connect(self._paint_icons)
        self._paint_icons()
        self.set_grid_mode(True)

    # ------------------------------------------------- construction

    def _build_empty(self) -> QWidget:
        page = QWidget()
        box = QVBoxLayout(page)
        box.setAlignment(Qt.AlignCenter)
        self.empty_title = label("Aucun document récent")
        self.empty_title.setStyleSheet("font-size: 17px; font-weight: 600;")
        self.empty_hint = label("Glissez-déposez un PDF ici, ou cliquez sur « Ouvrir un fichier ».", "muted")
        for widget in (self.empty_title, self.empty_hint):
            box.addWidget(widget, alignment=Qt.AlignCenter)
        return page

    def _paint_icons(self) -> None:
        color = theme.current().color("text")
        self.grid_btn.setIcon(icon("grid", color))
        self.list_btn.setIcon(icon("list", color))
        self.clear_btn.setIcon(icon("broom", color))
        self.open_btn.setIcon(icon("plus", "#ffffff"))
        self.list.viewport().update()

    # ------------------------------------------------------- affichage

    def set_grid_mode(self, grid: bool) -> None:
        self.grid_mode = grid
        self.grid_btn.setChecked(grid)
        self.list_btn.setChecked(not grid)
        if grid:
            self.list.setViewMode(QListView.IconMode)
            self.list.setResizeMode(QListView.Adjust)
            self.list.setMovement(QListView.Static)
            self.list.setWrapping(True)
            self.list.setGridSize(CARD)
            self.list.setSpacing(0)
        else:
            self.list.setViewMode(QListView.ListMode)
            self.list.setWrapping(False)
            self.list.setGridSize(QSize())
            self.list.setSpacing(1)
        self.refresh()

    def refresh(self) -> None:
        favorites = self.tabs.value() == "favorites"
        entries = self.store.entries(favorites, self.search.text(), self.sort.currentData())
        self.list.clear()
        self._hover.clear()
        self._hovered = -1
        for entry in entries:
            item = QListWidgetItem(self.list)
            item.setData(ENTRY_ROLE, entry)
            item.setToolTip(entry.path)
            item.setSizeHint(CARD if self.grid_mode else QSize(10, ROW_HEIGHT))
        n = len(entries)
        self.count.setText(f"{n} document{'s' if n > 1 else ''}" if n else "")
        if not n:
            searching = bool(self.search.text().strip())
            self.empty_title.setText("Aucun résultat" if searching else
                                     "Aucun favori" if favorites else "Aucun document récent")
            self.empty_hint.setText(
                "Essayez un autre nom." if searching else
                "Dans le menu « ⋯ » d'un document, choisissez « Ajouter aux favoris »." if favorites else
                "Glissez-déposez un PDF ici, ou cliquez sur « Ouvrir un fichier ».")
        self.body.setCurrentIndex(0 if n else 1)
        self._load_thumbnails([e.path for e in entries if e.exists and e.path not in self._thumbs])

    def thumbnail(self, path: str) -> QImage | None:
        return self._thumbs.get(path)

    def _load_thumbnails(self, paths: list[str]) -> None:
        if not paths or (self._worker is not None and self._worker.isRunning()):
            return
        self._worker = ThumbnailWorker(paths, self.cache, self)
        self._worker.ready.connect(self._on_thumbnail)
        self._worker.finished.connect(self._after_worker)
        self._worker.start()

    def _after_worker(self) -> None:
        # Des documents ont pu s'ajouter pendant le rendu : on complète.
        missing = [self.list.item(i).data(ENTRY_ROLE).path for i in range(self.list.count())]
        self._load_thumbnails([p for p in missing if p not in self._thumbs and Path(p).is_file()])

    def _on_thumbnail(self, path: str, image: QImage) -> None:
        self._thumbs[path] = image
        self.list.viewport().update()

    def forget_thumbnail(self, path: Path) -> None:
        """Le document a pu changer (annotations…) : miniature à refaire."""
        self._thumbs.pop(str(path.resolve()), None)

    def shutdown(self) -> None:
        if self._worker is not None:
            self._worker.requestInterruption()
            self._worker.wait()

    # ------------------------------------------------- survol animé

    def hover_amount(self, row: int) -> float:
        return self._hover.get(row, 0.0)

    def _set_hovered(self, row: int) -> None:
        if row == self._hovered:
            return
        previous, self._hovered = self._hovered, row
        for target, end in ((previous, 0.0), (row, 1.0)):
            if target < 0:
                continue
            animation = self._animations.pop(target, None)
            if animation is not None:
                animation.stop()
            animation = QVariantAnimation(self)
            animation.setDuration(HOVER_MS)
            animation.setEasingCurve(QEasingCurve.OutCubic)
            animation.setStartValue(self._hover.get(target, 0.0))
            animation.setEndValue(end)
            animation.valueChanged.connect(lambda value, r=target: self._on_hover_step(r, value))
            animation.start()
            self._animations[target] = animation

    def _on_hover_step(self, row: int, value: float) -> None:
        self._hover[row] = value
        item = self.list.item(row)
        if item is not None:
            self.list.viewport().update(self.list.visualItemRect(item).adjusted(-12, -12, 12, 12))

    def eventFilter(self, obj, event) -> bool:
        if obj is self._viewport and event.type() in (event.Type.Leave, event.Type.MouseMove):
            if event.type() == event.Type.Leave or self.list.itemAt(event.position().toPoint()) is None:
                self._set_hovered(-1)
        return False

    # ------------------------------------------------------- actions

    def _on_click(self, item: QListWidgetItem) -> None:
        entry: RecentEntry = item.data(ENTRY_ROLE)
        if entry.exists:
            self.open_requested.emit(Path(entry.path))
        else:
            self.status.setText(f"« {Path(entry.path).name} » est introuvable : il a été déplacé ou supprimé.")

    def _on_context_menu(self, pos: QPoint) -> None:
        item = self.list.itemAt(pos)
        if item is not None:
            self.show_menu(item.data(ENTRY_ROLE), self.list.viewport().mapToGlobal(pos))

    def menu_for(self, entry: RecentEntry) -> QMenu:
        menu = QMenu(self)
        opened = menu.addAction("Ouvrir", lambda: self.open_requested.emit(Path(entry.path)))
        shown = menu.addAction("Afficher dans le dossier", lambda: reveal(Path(entry.path)))
        opened.setEnabled(entry.exists)
        shown.setEnabled(entry.exists)
        menu.addAction("Retirer des favoris" if entry.favorite else "Ajouter aux favoris",
                       lambda: (self.store.toggle_favorite(entry.path), self.refresh()))
        menu.addSeparator()
        menu.addAction("Retirer de la liste", lambda: (self.store.remove(entry.path), self.refresh()))
        return menu

    def show_menu(self, entry: RecentEntry, global_pos: QPoint) -> None:
        self.menu_for(entry).exec(global_pos)

    def _clear(self) -> None:
        self.store.clear(keep_favorites=True)
        self.refresh()

    def set_status(self, text: str) -> None:
        self.status.setText(text)

    # ------------------------------------------------- glisser-déposer

    def dragEnterEvent(self, event) -> None:
        if any(u.isLocalFile() and ext_of(Path(u.toLocalFile())) == "pdf" for u in event.mimeData().urls()):
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        for url in event.mimeData().urls():
            path = Path(url.toLocalFile())
            if url.isLocalFile() and ext_of(path) == "pdf":
                self.open_requested.emit(path)
                break
        event.acceptProposedAction()
