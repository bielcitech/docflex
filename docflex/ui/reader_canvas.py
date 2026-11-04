"""Affichage d'un PDF : pages empilées, zoom, rendu à la demande, sélection de texte, recherche, annotations.

La scène est en points PDF (repère de la page affichée) : une page de 595 × 842 pt occupe
595 × 842 unités. Le zoom n'est qu'une transformation de la vue ; seuls les rendus des pages
visibles sont recalculés à la bonne résolution.
"""

from __future__ import annotations

from pathlib import Path

import pymupdf
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap, QTransform
from PySide6.QtWidgets import QFrame, QGraphicsItem, QGraphicsScene, QGraphicsView

from docflex.core.annotations import MARKUPS, AnnotSpec, annot_at, page_specs
from docflex.core.editing import Field, TextBlock, TextLine, fields, text_blocks, text_lines
from docflex.core.reader import Link, Target, Word, link_at, page_links, page_words, selection_text, word_at
from docflex.ui import theme
from docflex.utils.files import ext_of

PAGE_GAP = 14          # points entre deux pages
SCREEN_DPI = 96 / 72   # 100 % = taille réelle à l'écran
ZOOM_MIN, ZOOM_MAX = 0.25, 4.0
KEEP_AROUND = 4        # pages gardées en mémoire de part et d'autre de la zone visible
FIELD_TOOLS = ("field_text", "field_check", "field_radio", "field_choice")  # création de champs de formulaire
DRAW_TOOLS = ("rect", "ellipse", "line", "arrow", "text", "ink", "add_image", "whiteout") + FIELD_TOOLS
FIELD_CLICK_SIZES = {"field_text": (180, 22), "field_check": (14, 14), "field_radio": (14, 14),
                     "field_choice": (150, 22)}
CLICK_TOOLS = ("note", "add_text")  # un clic suffit
MIN_SHAPE = 4          # points : en dessous, un tracé est considéré comme un simple clic


class PageItem(QGraphicsItem):
    def __init__(self, canvas: DocumentCanvas, index: int, size: QRectF) -> None:
        super().__init__()
        self.canvas = canvas
        self.index = index
        self.size = size
        self.pixmap: QPixmap | None = None
        self.scale = 0.0  # résolution du rendu actuel (pixels par point)

    def boundingRect(self) -> QRectF:
        return self.size

    def paint(self, painter: QPainter, option, widget=None) -> None:
        t = theme.current()
        painter.setPen(QPen(QColor(t.color("border")), 0))
        if self.pixmap is not None:
            painter.drawPixmap(self.size, self.pixmap, QRectF(self.pixmap.rect()))
        else:
            painter.fillRect(self.size, QColor("#ffffff"))
        painter.drawRect(self.size)
        self.canvas.paint_overlays(self.index, painter)


class DocumentCanvas(QGraphicsView):
    page_changed = Signal(int)
    zoom_changed = Signal(float)
    selection_changed = Signal(str)
    pdf_dropped = Signal(Path)
    # Intentions d'annotation : la vue crée, modifie et mémorise pour l'annulation.
    markup_requested = Signal(int, list)        # page, rectangles des mots sélectionnés
    shape_drawn = Signal(int, str, object)      # page, outil, géométrie (rectangle ou points)
    annotation_selected = Signal(object)        # (page, indice) ou None
    annotation_moved = Signal(int, int, float, float)  # page, indice, dx, dy
    annotation_activated = Signal(int, int)     # double-clic : modifier le texte
    line_clicked = Signal(int, object)          # outil « modifier le texte » : page, point
    text_moved = Signal(int, object, float, float)  # même outil, paragraphe glissé : page, TextBlock, dx, dy
    field_clicked = Signal(int, object)         # champ de formulaire cliqué : page, Field
    uri_clicked = Signal(str)                   # lien externe : la vue l'ouvre dans le navigateur
    field_menu = Signal(int, object, object)    # clic droit sur un champ : page, Field, position écran

    def __init__(self) -> None:
        super().__init__()
        self.setScene(QGraphicsScene(self))
        self.setFrameShape(QFrame.NoFrame)
        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        self.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        self.setViewportUpdateMode(QGraphicsView.SmartViewportUpdate)
        self.setAcceptDrops(True)
        self.setMouseTracking(True)

        self.doc: pymupdf.Document | None = None
        self.pages: list[PageItem] = []
        self.zoom = 1.0
        self.fit: str | None = "width"  # « width », « page » ou None (zoom libre)
        self._current = 0
        self._height = 0.0
        self.insets = (0, 0)  # pixels couverts en haut et en bas par les barres flottantes
        self._words: dict[int, list[Word]] = {}
        self.selection: tuple[int, int, int] | None = None  # (page, premier mot, dernier mot)
        self._selecting = False
        self.hits: list[tuple[int, pymupdf.Rect]] = []
        self.current_hit = -1

        self.tool = "select"
        self.style_color = (0.9, 0.15, 0.15)     # aperçu des tracés en cours
        self.style_width = 2.0
        self.selected_annot: tuple[int, int] | None = None
        self._specs: dict[int, list[AnnotSpec]] = {}
        self._fields: dict[int, list[Field]] = {}
        self._lines: dict[int, list[TextLine]] = {}
        self._blocks: dict[int, list[TextBlock]] = {}
        self._links: dict[int, list[Link]] = {}
        self.field_focus: tuple[int, int] | None = None  # (page, xref) du champ en cours de saisie
        self._hover_line: tuple[int, tuple] | None = None  # ligne survolée avec « modifier le texte »
        self._gesture: dict | None = None        # tracé ou déplacement en cours

        self._queue: list[int] = []
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._render_next)
        self.verticalScrollBar().valueChanged.connect(lambda _: self._on_scroll())
        theme.current().changed.connect(self._on_theme)
        self._on_theme()

    # ------------------------------------------------------------ document

    def set_document(self, doc: pymupdf.Document | None) -> None:
        self.scene().clear()
        self.pages, self._words, self.hits, self.selection = [], {}, [], None
        self._specs, self.selected_annot, self._gesture = {}, None, None
        self._fields, self._lines, self._blocks, self._hover_line = {}, {}, {}, None
        self._links = {}
        self._queue.clear()
        self.doc = doc
        if doc is None:
            return
        y = PAGE_GAP
        for page in doc:
            item = PageItem(self, page.number, QRectF(0, 0, page.rect.width, page.rect.height))
            item.setPos(-page.rect.width / 2, y)  # centrées sur x = 0
            self.scene().addItem(item)
            self.pages.append(item)
            y += page.rect.height + PAGE_GAP
        self._height = y
        self._update_scene_rect()
        self._current = -1
        self.set_fit(self.fit or "width")
        self.go_to(0)

    def set_insets(self, top: int, bottom: int) -> None:
        """Le document défile sous les barres flottantes, mais commence et finit hors d'elles."""
        if (top, bottom) != self.insets:
            self.insets = (top, bottom)
            self._update_scene_rect()
            self._apply_fit()

    def _update_scene_rect(self) -> None:
        if not self.pages:
            return
        # Les marges sont en pixels d'écran : converties en points au zoom courant.
        top, bottom = (inset / self.pixels_per_point for inset in self.insets)
        widest = max(p.size.width() for p in self.pages)
        self.scene().setSceneRect(QRectF(-widest / 2 - PAGE_GAP, -top, widest + 2 * PAGE_GAP,
                                         self._height + top + bottom))

    def refresh_page(self, index: int) -> None:
        """Le contenu de la page a changé (annotations…) : nouveau rendu et nouveaux mots."""
        self._words.pop(index, None)
        self._specs.pop(index, None)
        self._fields.pop(index, None)
        self._lines.pop(index, None)
        self._blocks.pop(index, None)
        self._links.pop(index, None)
        self._hover_line = None
        self.pages[index].scale = 0
        self.pages[index].update()
        self._schedule()

    def specs(self, index: int) -> list[AnnotSpec]:
        if index not in self._specs:
            self._specs[index] = page_specs(self.doc[index])
        return self._specs[index]

    def fields(self, index: int) -> list[Field]:
        if index not in self._fields:
            self._fields[index] = fields(self.doc[index])
        return self._fields[index]

    def field_at(self, index: int, point) -> Field | None:
        target = pymupdf.Point(point)
        return next((f for f in self.fields(index) if target in pymupdf.Rect(f.rect)), None)

    def update_field_focus(self, target: tuple[int, int] | None) -> None:
        previous, self.field_focus = self.field_focus, target
        for page in {t[0] for t in (previous, target) if t}:
            if page < len(self.pages):
                self.pages[page].update()

    def link_at(self, index: int, point) -> Link | None:
        if index not in self._links:
            self._links[index] = page_links(self.doc[index])
        return link_at(self._links[index], point)

    def follow(self, target: Target) -> None:
        """Lien interne ou entrée de la table des matières : le titre visé en haut de l'écran."""
        self.go_to(target.page, top=target.y)

    def line_at(self, index: int, point) -> TextLine | None:
        """Lignes mises en cache : le survol ne relit pas la page à chaque mouvement de souris."""
        if index not in self._lines:
            self._lines[index] = text_lines(self.doc[index])
        target = pymupdf.Point(point)
        return next((line for line in self._lines[index] if target in pymupdf.Rect(line.rect) + (-2, -2, 2, 2)),
                    None)

    def block_at(self, index: int, point) -> TextBlock | None:
        """Paragraphe sous le point (celui qu'un glisser déplace)."""
        if index not in self._blocks:
            self._blocks[index] = text_blocks(self.doc[index])
        target = pymupdf.Point(point)
        return next((b for b in self._blocks[index] if target in pymupdf.Rect(b.rect) + (-2, -2, 2, 2)), None)

    def set_tool(self, tool: str) -> None:
        self.tool = tool
        self.select_annotation(None)
        self._set_hover_line(None)
        self.viewport().setCursor(Qt.CrossCursor if tool in DRAW_TOOLS + CLICK_TOOLS else Qt.ArrowCursor)

    def _set_hover_line(self, value) -> None:
        if value != self._hover_line:
            for page in {v[0] for v in (self._hover_line, value) if v}:
                self.pages[page].update()
            self._hover_line = value

    def select_annotation(self, target: tuple[int, int] | None) -> None:
        previous = self.selected_annot
        self.selected_annot = target
        for page in {t[0] for t in (previous, target) if t}:
            self.pages[page].update()
        if previous != target:
            self.annotation_selected.emit(target)

    # ------------------------------------------------------------- zoom

    @property
    def pixels_per_point(self) -> float:
        return self.zoom * SCREEN_DPI

    def set_zoom(self, zoom: float, keep_fit: bool = False) -> None:
        if not keep_fit:
            self.fit = None
        zoom = min(max(zoom, ZOOM_MIN), ZOOM_MAX)
        if abs(zoom - self.zoom) < 1e-4 and not self.transform().isIdentity():
            return
        center = self.mapToScene(self.viewport().rect().center())
        self.zoom = zoom
        self.setTransform(QTransform.fromScale(self.pixels_per_point, self.pixels_per_point))
        self._update_scene_rect()
        self.centerOn(center)
        self.zoom_changed.emit(zoom)
        self._schedule()

    def set_fit(self, mode: str) -> None:
        self.fit = mode
        self._apply_fit()

    def _apply_fit(self) -> None:
        if not self.pages or self.fit is None:
            return
        # Page + marges (en points) doivent tenir dans la zone visible : zoom calculé d'un coup,
        # sans dépendre du zoom précédent.
        page = self.pages[max(self._current, 0)].size
        zoom = (self.viewport().width() - 4) / (SCREEN_DPI * (page.width() + 2 * PAGE_GAP))
        if self.fit == "page":
            free = self.viewport().height() - 4 - sum(self.insets)
            zoom = min(zoom, free / (SCREEN_DPI * (page.height() + 2 * PAGE_GAP)))
        self.set_zoom(zoom, keep_fit=True)

    def zoom_by(self, factor: float) -> None:
        self.set_zoom(self.zoom * factor)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._apply_fit()
        self._schedule()

    def wheelEvent(self, event) -> None:
        if event.modifiers() & Qt.ControlModifier:  # ⌘ sur Mac
            anchor = self.transformationAnchor()
            self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
            self.zoom_by(1.1 if event.angleDelta().y() > 0 else 1 / 1.1)
            self.setTransformationAnchor(anchor)
            event.accept()
        else:
            super().wheelEvent(event)

    def event(self, event) -> bool:
        if event.type() == event.Type.NativeGesture and event.gestureType() == Qt.ZoomNativeGesture:
            self.zoom_by(1 + event.value())  # pincement sur pavé tactile
            return True
        return super().event(event)

    # ------------------------------------------------------------ navigation

    @property
    def current_page(self) -> int:
        return max(self._current, 0)

    def go_to(self, index: int, rect: pymupdf.Rect | None = None, top: float | None = None) -> None:
        """Affiche la page `index` : depuis son haut, depuis la hauteur `top`, ou centrée sur `rect`."""
        if not self.pages:
            return
        item = self.pages[min(max(index, 0), len(self.pages) - 1)]
        if rect is None:
            margin = PAGE_GAP / 2 if top is None else PAGE_GAP  # un titre visé garde un peu d'air au-dessus
            top = item.mapToScene(QPointF(0, top or 0)).y() - margin - self.insets[0] / self.pixels_per_point
            self.verticalScrollBar().setValue(round(self.mapFromScene(QPointF(0, top)).y()
                                                    + self.verticalScrollBar().value()))
            # Page plus large que la fenêtre : on part du bord gauche, comme dans Aperçu.
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().minimum())
        else:  # un résultat de recherche : centré à l'écran, comme dans Aperçu
            self.centerOn(item.mapRectToScene(QRectF(rect.x0, rect.y0, rect.width, rect.height)).center())
        self._on_scroll()

    def _visible_pages(self) -> list[int]:
        area = self.mapToScene(self.viewport().rect()).boundingRect()
        return [p.index for p in self.pages if p.sceneBoundingRect().intersects(area)]

    def _on_scroll(self) -> None:
        visible = self._visible_pages()
        if visible:
            area = self.mapToScene(self.viewport().rect()).boundingRect()
            # Page courante : celle qui occupe le plus de hauteur visible.
            current = max(visible, key=lambda i: self.pages[i].sceneBoundingRect().intersected(area).height())
            if current != self._current:
                self._current = current
                self.page_changed.emit(current)
        self._schedule()

    # ------------------------------------------------------------- rendu

    def _schedule(self) -> None:
        if not self.pages:
            return
        visible = self._visible_pages()
        if not visible:
            return
        wanted = self.pixels_per_point * self.devicePixelRatioF()
        around = range(max(0, visible[0] - 1), min(len(self.pages), visible[-1] + 2))
        self._queue = [i for i in around if abs(self.pages[i].scale - wanted) > 1e-3]
        self._queue.sort(key=lambda i: (i not in visible, abs(i - self.current_page)))
        keep = range(visible[0] - KEEP_AROUND, visible[-1] + KEEP_AROUND + 1)
        for item in self.pages:
            if item.index not in keep and item.pixmap is not None:
                item.pixmap, item.scale = None, 0.0  # libère la mémoire des pages éloignées
        if self._queue and not self._timer.isActive():
            self._timer.start(0)

    def _render_next(self) -> None:
        if not self._queue or self.doc is None:
            return
        index = self._queue.pop(0)
        scale = self.pixels_per_point * self.devicePixelRatioF()
        pix = self.doc[index].get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
        image = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format_RGB888).copy()
        item = self.pages[index]
        item.pixmap, item.scale = QPixmap.fromImage(image), scale
        item.update()
        if self._queue:
            self._timer.start(0)  # une page par passage : l'interface reste réactive

    def wait_rendered(self) -> None:
        """Rendu immédiat des pages en attente (tests et captures)."""
        while self._queue:
            self._render_next()

    def _on_theme(self) -> None:
        self.setBackgroundBrush(QColor(theme.current().color("surface2")))
        for item in self.pages:
            item.update()

    # --------------------------------------------------------- texte

    def words(self, index: int) -> list[Word]:
        if index not in self._words:
            self._words[index] = page_words(self.doc[index])
        return self._words[index]

    def page_point(self, view_pos) -> tuple[int, pymupdf.Point] | None:
        scene = self.mapToScene(view_pos)
        for item in self.pages:
            if item.sceneBoundingRect().contains(scene):
                local = item.mapFromScene(scene)
                return item.index, pymupdf.Point(local.x(), local.y())
        return None

    def selected_text(self) -> str:
        if self.selection is None:
            return ""
        page, a, b = self.selection
        return selection_text(self.words(page), a, b)

    def clear_selection(self) -> None:
        if self.selection is not None:
            page = self.selection[0]
            self.selection = None
            self.pages[page].update()
            self.selection_changed.emit("")

    def select_words(self, page: int, first: int, last: int) -> None:
        previous = self.selection[0] if self.selection else None
        self.selection = (page, first, last)
        if previous is not None and previous != page:
            self.pages[previous].update()
        self.pages[page].update()
        self.selection_changed.emit(self.selected_text())

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.LeftButton:
            return super().mousePressEvent(event)
        hit = self.page_point(event.position().toPoint())
        self.clear_selection()
        if not hit:
            self.select_annotation(None)
            return
        page, point = hit
        xy = (point.x, point.y)
        if self.tool == "select":
            field = self.field_at(page, xy)
            if field is not None:
                self.field_clicked.emit(page, field)
                return
            index = annot_at(self.doc[page], xy)
            if index is not None:
                self.select_annotation((page, index))
                if self.specs(page)[index].movable:
                    self._gesture = {"mode": "move", "page": page, "start": xy, "end": xy}
                return
            self.select_annotation(None)
            link = self.link_at(page, xy)
            if link is not None:  # suivi au relâchement, si la souris n'a pas glissé
                self._gesture = {"mode": "link", "page": page, "start": xy, "end": xy, "link": link}
                return
        if self.tool in ("select",) + tuple(MARKUPS):
            index = word_at(self.words(page), point)
            if index is not None:
                self._selecting = True
                self.select_words(page, index, index)
        elif self.tool == "note":
            self.shape_drawn.emit(page, "note", (point.x, point.y, point.x + 20, point.y + 20))
        elif self.tool == "add_text":
            self.shape_drawn.emit(page, "add_text", (point.x, point.y, point.x, point.y))
        elif self.tool == "edit_text":
            block = self.block_at(page, xy) if self.line_at(page, xy) else None
            if block is None:
                self.line_clicked.emit(page, xy)  # rien à modifier ici : la vue le dit
            else:  # au relâchement : un clic modifie la ligne, un glisser déplace le paragraphe
                self._gesture = {"mode": "move_text", "page": page, "start": xy, "end": xy, "block": block}
                self.viewport().setCursor(Qt.ClosedHandCursor)
        elif self.tool in DRAW_TOOLS:
            self._gesture = {"mode": self.tool, "page": page, "start": xy, "end": xy, "points": [xy]}

    def mouseMoveEvent(self, event) -> None:
        hit = self.page_point(event.position().toPoint())
        gesture = self._gesture
        if gesture is not None:
            # Hors de la page de départ, on borne aux limites de cette page.
            scene = self.mapToScene(event.position().toPoint())
            local = self.pages[gesture["page"]].mapFromScene(scene)
            size = self.pages[gesture["page"]].size
            x = min(max(local.x(), 0), size.width())
            y = min(max(local.y(), 0), size.height())
            gesture["end"] = (x, y)
            if gesture["mode"] == "ink":
                last = gesture["points"][-1]
                if abs(x - last[0]) + abs(y - last[1]) > 1.5:
                    gesture["points"].append((x, y))
            self.pages[gesture["page"]].update()
            return
        if self._selecting and self.selection and hit and hit[0] == self.selection[0]:
            page, point = hit
            index = word_at(self.words(page), point, reach=400)
            if index is not None and index != self.selection[2]:
                self.select_words(page, self.selection[1], index)
        self._update_cursor(hit)
        super().mouseMoveEvent(event)

    def _update_cursor(self, hit) -> None:
        link = self.link_at(hit[0], (hit[1].x, hit[1].y)) if hit and self.tool == "select" else None
        field = self.field_at(hit[0], (hit[1].x, hit[1].y)) if hit and self.tool == "select" else None
        self.viewport().setToolTip(link.description if link else field.name if field else "")
        if self.tool == "edit_text":
            line = self.line_at(hit[0], (hit[1].x, hit[1].y)) if hit else None
            self._set_hover_line((hit[0], line.rect) if line else None)
            cursor = Qt.IBeamCursor if line else Qt.ArrowCursor
        elif self.tool in DRAW_TOOLS + CLICK_TOOLS:
            cursor = Qt.CrossCursor
        elif self.tool == "select" and hit and self.field_at(hit[0], (hit[1].x, hit[1].y)):
            cursor = Qt.PointingHandCursor
        elif hit is None:
            cursor = Qt.ArrowCursor
        elif self.tool == "select" and (link or annot_at(self.doc[hit[0]], (hit[1].x, hit[1].y)) is not None):
            cursor = Qt.PointingHandCursor
        elif word_at(self.words(hit[0]), hit[1], reach=2) is not None:
            cursor = Qt.IBeamCursor
        else:
            cursor = Qt.ArrowCursor
        self.viewport().setCursor(cursor)

    def mouseReleaseEvent(self, event) -> None:
        gesture, self._gesture = self._gesture, None
        if self._selecting and self.tool in MARKUPS and self.selection:
            page, a, b = self.selection
            rects = [w.rect for w in self.words(page)[min(a, b):max(a, b) + 1]]
            self.clear_selection()
            self.markup_requested.emit(page, rects)
        self._selecting = False
        if gesture is not None:
            self._finish_gesture(gesture)
        super().mouseReleaseEvent(event)

    def _finish_gesture(self, gesture: dict) -> None:
        page, mode = gesture["page"], gesture["mode"]
        (x0, y0), (x1, y1) = gesture["start"], gesture["end"]
        moved = abs(x1 - x0) + abs(y1 - y0)
        self.pages[page].update()
        if mode == "link":
            if moved < MIN_SHAPE:
                link = gesture["link"]
                if link.target is not None:
                    self.follow(link.target)
                else:
                    self.uri_clicked.emit(link.uri)
        elif mode == "move":
            if moved > 1 and self.selected_annot:
                self.annotation_moved.emit(page, self.selected_annot[1], x1 - x0, y1 - y0)
        elif mode == "move_text":
            self.viewport().setCursor(Qt.IBeamCursor)
            if moved < MIN_SHAPE:
                self.line_clicked.emit(page, (x0, y0))
            else:
                self.text_moved.emit(page, gesture["block"], x1 - x0, y1 - y0)
        elif mode == "ink":
            if len(gesture["points"]) >= 2:
                self.shape_drawn.emit(page, "ink", [gesture["points"]])
        elif mode in ("line", "arrow"):
            if moved >= MIN_SHAPE:
                self.shape_drawn.emit(page, mode, [[(x0, y0), (x1, y1)]])
        else:
            rect = (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
            if mode == "text" and moved < MIN_SHAPE:  # simple clic : zone de texte de taille courante
                rect = (x0, y0, x0 + 200, y0 + 40)
            if mode == "add_image" and moved < MIN_SHAPE:  # simple clic : image de 200 pt de large
                rect = (x0, y0, x0 + 200, y0 + 150)
            if mode in FIELD_CLICK_SIZES and moved < MIN_SHAPE:  # simple clic : champ de taille usuelle
                width, height = FIELD_CLICK_SIZES[mode]
                rect = (x0, y0 - height / 2, x0 + width, y0 + height / 2)
            if rect[2] - rect[0] >= MIN_SHAPE and rect[3] - rect[1] >= MIN_SHAPE:
                self.shape_drawn.emit(page, mode, rect)

    def contextMenuEvent(self, event) -> None:  # noqa: N802 (nom Qt)
        hit = self.page_point(event.pos())
        field = self.field_at(hit[0], (hit[1].x, hit[1].y)) if hit else None
        if field is not None:
            self.field_menu.emit(hit[0], field, event.globalPos())
        else:
            super().contextMenuEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        hit = self.page_point(event.position().toPoint())
        if not hit:
            return
        page, point = hit
        index = annot_at(self.doc[page], (point.x, point.y)) if self.tool == "select" else None
        if index is not None:
            self.annotation_activated.emit(page, index)
            return
        words = self.words(page)  # double-clic sur du texte : toute la ligne
        index = word_at(words, point)
        if index is not None:
            line = [i for i, w in enumerate(words) if (w.block, w.line) == (words[index].block, words[index].line)]
            self.select_words(page, line[0], line[-1])

    # ------------------------------------------------------- recherche

    def set_hits(self, hits: list[tuple[int, pymupdf.Rect]], current: int = -1) -> None:
        touched = {page for page, _ in self.hits} | {page for page, _ in hits}
        self.hits, self.current_hit = hits, current
        for page in touched:
            self.pages[page].update()
        if 0 <= current < len(hits):
            self.go_to(*hits[current])

    # ---------------------------------------------------------- dessin

    def paint_overlays(self, index: int, painter: QPainter) -> None:
        t = theme.current()
        for n, (page, rect) in enumerate(self.hits):
            if page == index:
                color = QColor("#ff9500" if n == self.current_hit else "#ffd60a")
                color.setAlpha(140 if n == self.current_hit else 90)
                painter.fillRect(QRectF(rect.x0, rect.y0, rect.width, rect.height), color)
        if self.selection and self.selection[0] == index:
            _, a, b = self.selection
            color = QColor(t.color("accent"))
            color.setAlpha(70)
            for word in self.words(index)[min(a, b):max(a, b) + 1]:
                r = word.rect
                painter.fillRect(QRectF(r.x0, r.y0, r.width, r.height), color)
        accent = QColor(t.color("accent"))
        if (self.tool == "select" or self.tool in FIELD_TOOLS) and self.doc is not None and self.fields(index):
            tint = QColor(t.color("accent"))
            tint.setAlpha(28)  # champs de formulaire repérables, comme dans Acrobat
            for field in self.fields(index):
                if field.readonly:
                    continue  # calculé ou verrouillé : rien à remplir
                x0, y0, x1, y1 = field.rect
                painter.fillRect(QRectF(x0, y0, x1 - x0, y1 - y0), tint)
        if self._hover_line and self._hover_line[0] == index:
            x0, y0, x1, y1 = self._hover_line[1]
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(accent, 0.8, Qt.DashLine))
            painter.drawRect(QRectF(x0 - 2, y0 - 1, x1 - x0 + 4, y1 - y0 + 2))
        if self.selected_annot and self.selected_annot[0] == index:
            specs = self.specs(index)
            if self.selected_annot[1] < len(specs):
                x0, y0, x1, y1 = specs[self.selected_annot[1]].rect
                gesture = self._gesture
                if gesture and gesture["mode"] == "move":  # aperçu du déplacement
                    dx = gesture["end"][0] - gesture["start"][0]
                    dy = gesture["end"][1] - gesture["start"][1]
                    x0, y0, x1, y1 = x0 + dx, y0 + dy, x1 + dx, y1 + dy
                painter.setBrush(Qt.NoBrush)
                painter.setPen(QPen(accent, 0.8, Qt.DashLine))
                painter.drawRect(QRectF(x0 - 3, y0 - 3, x1 - x0 + 6, y1 - y0 + 6))
        gesture = self._gesture
        if gesture and gesture["page"] == index and gesture["mode"] == "move_text":
            (sx, sy), (ex, ey) = gesture["start"], gesture["end"]
            if abs(ex - sx) + abs(ey - sy) >= MIN_SHAPE:  # aperçu : le paragraphe et sa future place
                x0, y0, x1, y1 = gesture["block"].rect
                painter.setBrush(Qt.NoBrush)
                painter.setPen(QPen(accent, 0.6, Qt.DotLine))
                painter.drawRect(QRectF(x0 - 2, y0 - 1, x1 - x0 + 4, y1 - y0 + 2))
                tint = QColor(accent)
                tint.setAlpha(30)
                painter.setBrush(tint)
                painter.setPen(QPen(accent, 1.2, Qt.DashLine))
                painter.drawRect(QRectF(x0 - 2 + ex - sx, y0 - 1 + ey - sy, x1 - x0 + 4, y1 - y0 + 2))
            return
        if gesture and gesture["page"] == index and gesture["mode"] != "move":
            color = QColor.fromRgbF(*self.style_color)
            painter.setPen(QPen(color, self.style_width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            painter.setBrush(Qt.NoBrush)
            (x0, y0), (x1, y1) = gesture["start"], gesture["end"]
            box = QRectF(min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0))
            mode = gesture["mode"]
            if mode == "rect":
                painter.drawRect(box)
            elif mode == "ellipse":
                painter.drawEllipse(box)
            elif mode in ("line", "arrow"):
                painter.drawLine(QPointF(x0, y0), QPointF(x1, y1))
            elif mode in ("text", "add_image"):
                painter.setPen(QPen(accent, 0.8, Qt.DashLine))
                painter.drawRect(box)
            elif mode == "whiteout":
                painter.fillRect(box, QColor("#ffffff"))
                painter.setPen(QPen(accent, 0.8, Qt.DashLine))
                painter.drawRect(box)
            elif mode == "ink":
                points = [QPointF(x, y) for x, y in gesture["points"]]
                for a, b in zip(points, points[1:]):
                    painter.drawLine(a, b)

    # ------------------------------------------------- glisser-déposer

    def dragEnterEvent(self, event) -> None:
        urls = event.mimeData().urls() if event.mimeData().hasUrls() else []
        if any(u.isLocalFile() and ext_of(Path(u.toLocalFile())) == "pdf" for u in urls):
            event.acceptProposedAction()

    def dragMoveEvent(self, event) -> None:
        event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        for url in event.mimeData().urls():
            path = Path(url.toLocalFile())
            if url.isLocalFile() and ext_of(path) == "pdf":
                self.pdf_dropped.emit(path)
                break
        event.acceptProposedAction()
