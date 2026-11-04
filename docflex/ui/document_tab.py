"""Un document ouvert dans le lecteur (un onglet) : parcours, recherche, annotations, édition.

Les barres d'outils flottent sur le document : navigation et recherche en haut, outils
d'annotation ou d'édition en bas. Elles glissent hors champ pour une lecture sans distraction.
"""

from __future__ import annotations

import os
from pathlib import Path

import pymupdf
from PySide6.QtCore import QEvent, QSize, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QIcon, QImage, QKeySequence, QPixmap, QShortcut
from PySide6.QtPrintSupport import QPrintDialog, QPrinter
from PySide6.QtWidgets import (
    QApplication, QComboBox, QFileDialog, QHeaderView, QInputDialog, QLineEdit, QListWidget, QListWidgetItem,
    QMenu, QMessageBox, QProgressDialog, QPushButton, QSpinBox, QSplitter, QStackedWidget, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from docflex.core import annotations as notes
from docflex.core import editing, forms
from docflex.core.annotations import MARKUPS, AnnotSpec, Change, History, Snapshot
from docflex.core.organize import render_thumbnail
from docflex.core.reader import detect_headings, outline, search
from docflex.core.recent import RecentStore
from docflex.ui import theme
from docflex.ui.annotation_bar import AnnotationBar, EditBar
from docflex.ui.field_editor import CalculationDialog, FieldEditor
from docflex.ui.floating import EDGE, FloatingBar, FloatingOverlay, Toast
from docflex.ui.icons import icon
from docflex.ui.page_grid import ThumbnailLoader
from docflex.ui.printing import pages_to_print, print_pages
from docflex.ui.reader_canvas import DocumentCanvas
from docflex.ui.text_editor import TextEditor
from docflex.ui.widgets import SegmentedControl, button, choose, label, separator

ZOOM_PRESETS = (("Largeur", "width"), ("Page entière", "page"), ("50 %", 0.5), ("75 %", 0.75),
                ("100 %", 1.0), ("125 %", 1.25), ("150 %", 1.5), ("200 %", 2.0), ("300 %", 3.0), ("400 %", 4.0))
THUMB_WIDTH = 120
SIDE_WIDTH = 180      # panneau latéral : une colonne de miniatures
SEARCH_WIDTH = 200

YELLOW, BLUE, RED, BLACK = (1.0, 0.84, 0.0), (0.15, 0.45, 0.95), (0.9, 0.15, 0.15), (0.1, 0.1, 0.1)
# Style de départ de chaque outil ; chaque outil retient ensuite le dernier style choisi.
DEFAULT_STYLES = {
    "highlight": YELLOW, "underline": BLUE, "strikeout": RED, "note": YELLOW, "text": BLACK,
    "rect": RED, "ellipse": RED, "line": RED, "arrow": RED, "ink": BLUE,
}


def _native(seq) -> str:
    return QKeySequence(seq).toString(QKeySequence.NativeText)


class DocumentTab(QWidget):
    state_changed = Signal()        # enregistré / modifié, nom du fichier
    open_requested = Signal(Path)   # PDF déposé sur le document : nouvel onglet
    close_requested = Signal()
    bars_requested = Signal()       # ⌘F alors que les barres sont masquées

    def __init__(self, recent: RecentStore) -> None:
        super().__init__()
        self.recent = recent
        self.doc: pymupdf.Document | None = None
        self.path: Path | None = None
        self._loader: ThumbnailLoader | None = None
        self._hits: list = []
        self._hit = -1
        self._query = ""
        self._password: str | None = None
        self.history = History(restore=self._restore_bytes)
        self.dirty = False
        self.tool = "select"
        self.styles = {tool: {"color": color, "width": 2.0, "opacity": 1.0, "fontsize": 12.0}
                       for tool, color in DEFAULT_STYLES.items()}
        self.edit_style = {"family": "sans", "size": 12.0, "color": (0.1, 0.1, 0.1), "bold": False, "italic": False}
        # Après un instantané (tobytes), l'enregistrement incrémental de PyMuPDF oublie les
        # modifications antérieures : il faut alors réécrire le fichier entier.
        self._full_save_needed = False
        self.image_path: Path | None = None
        self._headings_pending = False
        self.side_wanted = True  # choix de l'utilisateur ; le panneau s'efface si la place manque

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.addWidget(self._build_side())
        self.splitter.addWidget(self._build_document())
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([SIDE_WIDTH, 900])
        layout.addWidget(self.splitter)
        theme.current().changed.connect(self._paint_icons)
        self._paint_icons()

        for keys, action in (
            (QKeySequence.Find, self.focus_search),
            (QKeySequence.Copy, self.copy_selection),
            (QKeySequence.ZoomIn, lambda: self.canvas.zoom_by(1.2)),
            (QKeySequence("Ctrl+="), lambda: self.canvas.zoom_by(1.2)),
            (QKeySequence.ZoomOut, lambda: self.canvas.zoom_by(1 / 1.2)),
            (QKeySequence("Ctrl+0"), lambda: self._choose_zoom("width")),
            (QKeySequence.FindNext, lambda: self.next_hit(1)),
            (QKeySequence.FindPrevious, lambda: self.next_hit(-1)),
            (QKeySequence.Save, self.save),
            (QKeySequence.Print, self.print_document),
            (QKeySequence.Undo, self.undo),
            (QKeySequence.Redo, self.redo),
            (QKeySequence.Delete, self.delete_selected),
            (QKeySequence(Qt.Key_Backspace), self.delete_selected),
            (QKeySequence(Qt.Key_Escape), self._escape),
        ):
            shortcut = QShortcut(keys, self)
            shortcut.setContext(Qt.WidgetWithChildrenShortcut)
            shortcut.activated.connect(action)

    # ------------------------------------------------- construction

    def _build_side(self) -> QWidget:
        self.side = QWidget()
        self.side.setObjectName("sidePanel")
        side = QVBoxLayout(self.side)
        side.setContentsMargins(0, 0, 4, 0)
        # Onglets en icônes : le panneau reste aussi étroit que ses miniatures.
        self.side_mode = SegmentedControl((("", "pages"), ("", "outline"), ("", "notes")), expand=True)
        for value, tip in (("pages", "Pages"), ("outline", "Signets : table des matières du document"),
                           ("notes", "Annotations")):
            self.side_mode._buttons[value].setToolTip(tip)
        self.side_mode.set_value("pages")
        self.side_mode.changed.connect(self._show_side)
        side.addWidget(self.side_mode)
        self.side_stack = QStackedWidget()
        self.thumbs = QListWidget()
        self.thumbs.setObjectName("thumbList")  # fond gris : les pages blanches s'en détachent
        self.thumbs.setViewMode(QListWidget.IconMode)
        self.thumbs.setFlow(QListWidget.TopToBottom)
        self.thumbs.setWrapping(False)
        self.thumbs.setMovement(QListWidget.Static)
        self.thumbs.setIconSize(QSize(THUMB_WIDTH, round(THUMB_WIDTH * 1.414)))
        self.thumbs.setGridSize(QSize(THUMB_WIDTH + 24, round(THUMB_WIDTH * 1.414) + 30))
        self.thumbs.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.thumbs.viewport().installEventFilter(self)  # miniatures centrées dans la largeur du panneau
        self.thumbs.itemClicked.connect(lambda item: self.canvas.go_to(self.thumbs.row(item)))
        self.outline = QTreeWidget()
        self.outline.setHeaderHidden(True)
        self.outline.setColumnCount(2)  # titre, numéro de page
        self.outline.setIndentation(12)  # panneau étroit : la place va aux titres (complets en info-bulle)
        self.outline.header().setStretchLastSection(False)
        self.outline.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.outline.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.outline.itemClicked.connect(self._on_outline_item)
        self.annot_list = QListWidget()
        self.annot_list.setWordWrap(True)
        self.annot_list.setSpacing(3)
        self.annot_list.itemClicked.connect(self._on_annot_item)
        self.side_stack.addWidget(self.thumbs)
        contents = QWidget()  # signets du PDF, ou titres détectés avec une mention
        box = QVBoxLayout(contents)
        box.setContentsMargins(0, 0, 0, 0)
        self.outline_note = label("Titres détectés d'après la mise en forme : ce PDF n'a pas de signets.",
                                  "muted", wrap=True)
        self.outline_note.hide()
        box.addWidget(self.outline_note)
        box.addWidget(self.outline)
        self.side_stack.addWidget(contents)
        self.side_stack.addWidget(self.annot_list)
        side.addWidget(self.side_stack)
        return self.side

    def _build_document(self) -> QWidget:
        """Le document occupe toute la zone ; les barres et les messages flottent par-dessus."""
        area = QWidget()
        area.setObjectName("documentArea")
        box = QVBoxLayout(area)
        box.setContentsMargins(0, 0, 0, 0)
        self.canvas = DocumentCanvas()
        self.canvas.page_changed.connect(self._on_page_changed)
        self.canvas.zoom_changed.connect(self._on_zoom_changed)
        self.canvas.selection_changed.connect(
            lambda text: self.status.setText(
                f"{len(text)} caractères sélectionnés — {_native(QKeySequence.Copy)} pour copier" if text else ""))
        self.canvas.pdf_dropped.connect(self.open_requested.emit)
        self.canvas.markup_requested.connect(self._on_markup)
        self.canvas.shape_drawn.connect(self._on_shape)
        self.canvas.annotation_selected.connect(lambda _: self._refresh_bar())
        self.canvas.annotation_moved.connect(self._on_moved)
        self.canvas.annotation_activated.connect(self._on_activated)
        self.canvas.line_clicked.connect(self._on_line_clicked)
        self.canvas.text_moved.connect(self._move_text)
        self.canvas.field_clicked.connect(self._on_field_clicked)
        self.canvas.field_menu.connect(lambda page, field, pos: self.field_menu_for(page, field).exec(pos))
        self.canvas.uri_clicked.connect(self._open_uri)
        self.field_editor = FieldEditor(self.canvas)  # saisie directement dans les champs
        self.field_editor.committed.connect(self._set_field)
        self.text_editor = TextEditor(self.canvas)  # texte tapé directement dans la page
        self.text_editor.added.connect(self._add_text)
        self.text_editor.replaced.connect(self._replace_line)
        self.text_editor.closed.connect(lambda: self.edit_bar.show_style(self.edit_style))
        box.addWidget(self.canvas)

        self.status = Toast()
        self.overlay = FloatingOverlay(area, self._build_top_bar(), self._build_bottom_bar(), self.status,
                                       self._build_search_bar(), on_insets=self.canvas.set_insets)
        return area

    def _icon_button(self, name: str, tip: str, on_click) -> object:
        btn = button("", "tool", on_click)
        btn.setFixedSize(30, 30)
        btn.setToolTip(tip)
        btn.setProperty("icon_name", name)
        return btn

    def _build_top_bar(self) -> FloatingBar:
        """Navigation, zoom, recherche et enregistrement."""
        self.top_bar = bar = FloatingBar()
        row = bar.row
        self.panel_btn = self._icon_button("panel", "Afficher ou masquer les miniatures et les signets",
                                           self._toggle_panel)
        row.addWidget(self.panel_btn)
        row.addWidget(separator())

        row.addWidget(self._icon_button("prev", "Page précédente", lambda: self.canvas.go_to(self.canvas.current_page - 1)))
        self.page_spin = QSpinBox()
        self.page_spin.setButtonSymbols(QSpinBox.NoButtons)
        self.page_spin.setAlignment(Qt.AlignCenter)
        self.page_spin.setFixedWidth(46)
        self.page_spin.editingFinished.connect(lambda: self.canvas.go_to(self.page_spin.value() - 1))
        row.addWidget(self.page_spin)
        self.page_total = label("", "muted")
        row.addWidget(self.page_total)
        row.addWidget(self._icon_button("next", "Page suivante", lambda: self.canvas.go_to(self.canvas.current_page + 1)))
        row.addWidget(separator())

        row.addWidget(self._icon_button("minus", f"Zoom arrière ({_native(QKeySequence.ZoomOut)})",
                                        lambda: self.canvas.zoom_by(1 / 1.2)))
        self.zoom_menu = QComboBox()
        self.zoom_menu.setFixedWidth(124)
        self.zoom_menu.setEditable(True)  # pour afficher un zoom libre (« 137 % ») ; saisie désactivée
        self.zoom_menu.lineEdit().setReadOnly(True)
        for text, value in ZOOM_PRESETS:
            self.zoom_menu.addItem(text, value)
        self.zoom_menu.activated.connect(lambda i: self._choose_zoom(self.zoom_menu.itemData(i)))
        row.addWidget(self.zoom_menu)
        row.addWidget(self._icon_button("add", f"Zoom avant ({_native(QKeySequence.ZoomIn)})",
                                        lambda: self.canvas.zoom_by(1.2)))
        row.addWidget(separator())

        self.search_btn = self._icon_button("search", f"Rechercher ({_native(QKeySequence.Find)})",
                                            self.toggle_search)
        self.search_btn.setCheckable(True)
        row.addWidget(self.search_btn)
        row.addWidget(separator())

        row.addWidget(self._icon_button("print", f"Imprimer ({_native(QKeySequence.Print)})", self.print_document))
        more = self._icon_button("more", "Plus d'actions", None)
        menu = QMenu(more)
        menu.addAction("Imprimer…", self.print_document)
        menu.addAction("Enregistrer sous…", self.save_as)
        menu.addSeparator()
        menu.addAction("Fermer le document", self.close_requested.emit)
        more.setMenu(menu)
        row.addWidget(more)
        self.save_btn = button("Enregistrer", "primary", self.save)
        self.save_btn.setToolTip(_native(QKeySequence.Save))
        row.addWidget(self.save_btn)
        return bar

    def _build_search_bar(self) -> FloatingBar:
        """Tiroir de recherche : il sort de sous la barre du haut."""
        self.search_bar = bar = FloatingBar()
        row = bar.row
        self.search_field = QLineEdit()
        self.search_field.setPlaceholderText("Rechercher dans le document…")
        self.search_field.setClearButtonEnabled(True)
        self.search_field.setFixedWidth(SEARCH_WIDTH)
        self.search_field.returnPressed.connect(self._on_search)
        self.search_field.textChanged.connect(lambda text: text or self._clear_search())
        row.addWidget(self.search_field)
        self.search_count = label("", "muted")
        row.addWidget(self.search_count)
        row.addWidget(self._icon_button("up", "Résultat précédent", lambda: self.next_hit(-1)))
        row.addWidget(self._icon_button("down", "Résultat suivant", lambda: self.next_hit(1)))
        row.addWidget(separator())
        row.addWidget(self._icon_button("close", "Fermer la recherche (Échap)", lambda: self._set_search_open(False)))
        return bar

    def _build_bottom_bar(self) -> FloatingBar:
        """Annoter ou modifier : les outils du mode choisi, puis annuler / rétablir."""
        self.bottom_bar = bar = FloatingBar()
        row = bar.row
        self.mode = SegmentedControl((("Annoter", "annotate"), ("Modifier", "edit")))
        self.mode.set_value("annotate")
        self.mode.changed.connect(self.set_mode)
        row.addWidget(self.mode)
        row.addWidget(separator())
        self.annot_bar = AnnotationBar()
        self.annot_bar.tool_changed.connect(self.set_tool)
        self.annot_bar.color_chosen.connect(lambda c: self._set_style(color=c))
        self.annot_bar.width_chosen.connect(lambda v: self._set_style(width=v))
        self.annot_bar.opacity_chosen.connect(lambda v: self._set_style(opacity=v))
        self.annot_bar.fontsize_chosen.connect(lambda v: self._set_style(fontsize=v))
        self.annot_bar.delete_requested.connect(self.delete_selected)
        self.edit_bar = EditBar()
        self.edit_bar.tool_changed.connect(self.set_tool)
        self.edit_bar.family_chosen.connect(lambda v: self._set_edit_style(family=v))
        self.edit_bar.size_chosen.connect(lambda v: self._set_edit_style(size=v))
        self.edit_bar.color_chosen.connect(lambda v: self._set_edit_style(color=v))
        self.edit_bar.bold_chosen.connect(lambda v: self._set_edit_style(bold=v))
        self.edit_bar.italic_chosen.connect(lambda v: self._set_edit_style(italic=v))
        # Une seule des deux barres est visible : la pilule épouse sa largeur.
        row.addWidget(self.annot_bar)
        row.addWidget(self.edit_bar)
        self.edit_bar.hide()
        row.addWidget(separator())
        self.undo_btn = self._icon_button("undo", f"Annuler ({_native(QKeySequence.Undo)})", self.undo)
        self.redo_btn = self._icon_button("redo", f"Rétablir ({_native(QKeySequence.Redo)})", self.redo)
        row.addWidget(self.undo_btn)
        row.addWidget(self.redo_btn)
        return bar

    def _show_side(self, mode: str) -> None:
        self.side_stack.setCurrentIndex(("pages", "outline", "notes").index(mode))
        if mode == "outline" and self._headings_pending and self.doc is not None:
            self._detect_headings()
        self._paint_icons()

    def eventFilter(self, obj, event) -> bool:
        if event.type() == QEvent.Resize and obj is self.thumbs.viewport():
            grid = self.thumbs.gridSize()
            self.thumbs.setGridSize(QSize(max(obj.width() - 1, THUMB_WIDTH + 24), grid.height()))
        return super().eventFilter(obj, event)

    def _paint_icons(self) -> None:
        t = theme.current()
        for value, name in (("pages", "pages"), ("outline", "bookmark"), ("notes", "note")):
            btn = self.side_mode._buttons[value]
            btn.setIcon(icon(name, t.color("on_accent") if btn.isChecked() else t.color("text")))
        color = t.color("text")
        for bar in (self.top_bar, self.bottom_bar, self.search_bar):
            for btn in bar.findChildren(QPushButton):
                name = btn.property("icon_name")
                if name:
                    btn.setIcon(icon(name, color))

    # ------------------------------------------------- barres flottantes

    def set_bars_visible(self, visible: bool, animated: bool = True) -> None:
        self.overlay.set_visible(visible, animated)

    def toggle_search(self) -> None:
        self._set_search_open(not self.overlay.drawer_open)

    def focus_search(self) -> None:
        self._set_search_open(True)
        self.search_field.setFocus()
        self.search_field.selectAll()

    def _set_search_open(self, opened: bool) -> None:
        self.search_btn.setChecked(opened)
        if opened and not self.overlay.visible:
            self.bars_requested.emit()  # la recherche part de la barre du haut : elle revient
        self.overlay.set_drawer_open(opened)
        if opened:
            self.search_field.setFocus()
        else:
            self.search_field.clear()
            self.canvas.setFocus()

    def _escape(self) -> None:
        if self.search_field.hasFocus():
            self._set_search_open(False)
        else:
            self.set_tool("select")

    # ------------------------------------------------- document

    def load(self, doc: pymupdf.Document, path: Path, password: str | None) -> None:
        """Affiche `doc`, déjà ouvert (et déverrouillé) ; reprend à la page de la dernière lecture."""
        self.doc, self.path, self._password = doc, path, password
        self._full_save_needed = False
        n = doc.page_count
        self.page_spin.setRange(1, n)
        self.page_total.setText(f"/ {n}")
        self.canvas.set_document(doc)
        self._fill_outline()
        self._fill_thumbnails()
        self._on_page_changed(self.canvas.current_page)  # miniature courante, une fois la liste remplie
        self.history.clear()
        self._set_dirty(False)
        self._refresh_annotations()
        self.set_tool(self.tool)
        entry = self.recent.touch(path, n)
        if entry.last_page > 0:  # reprise là où la lecture s'était arrêtée
            self.canvas.go_to(entry.last_page)
            self.status.setText(f"Reprise à la page {entry.last_page + 1}.")
        if any(page.first_widget for page in doc):
            self.status.setText("Ce PDF contient un formulaire : cliquez dans un champ (surligné en bleu) pour le remplir, "
                                "Tab pour passer au suivant.")

    @property
    def title(self) -> str:
        return self.path.name if self.path else ""

    def summary(self) -> str:
        n = self.doc.page_count if self.doc else 0
        return f"{self.title} · {n} page{'s' if n > 1 else ''}"

    def release(self) -> None:
        """Libère le document ; la page courante est retenue pour la prochaine ouverture."""
        if self.path is not None:
            self.recent.set_page(self.path, self.canvas.current_page)
            self.recent.save()
        self.field_editor.close(commit=False)
        self.text_editor.close(commit=False)
        self._stop_loader()
        self._clear_search()
        self.canvas.set_document(None)
        if self.doc is not None and not self.doc.is_closed:
            self.doc.close()
        self.doc = None
        self.history.clear()
        self.dirty = False

    def _stop_loader(self) -> None:
        if self._loader is not None:
            self._loader.requestInterruption()
            self._loader.wait()
            self._loader = None

    # ------------------------------------------------- panneau latéral

    def _toggle_panel(self) -> None:
        self.side_wanted = not self.side.isVisible()
        self._fit_side()
        if self.side_wanted and self.side.isHidden():
            self.status.setText("Fenêtre trop étroite pour le panneau : agrandissez-la.")

    def _fit_side(self) -> None:
        """Le panneau latéral cède sa place quand la barre du bas ne tiendrait plus à côté."""
        if self.side.isVisible() and self.splitter.sizes()[0] > 0:
            self._side_width = self.splitter.sizes()[0]  # largeur choisie à la souris
        side = max(getattr(self, "_side_width", SIDE_WIDTH), self.side.minimumSizeHint().width()) + self.splitter.handleWidth()
        room = self.width() - side >= self.bottom_bar.sizeHint().width() + 2 * EDGE
        self.side.setVisible(self.side_wanted and room)

    def resizeEvent(self, event) -> None:  # noqa: N802 (nom Qt)
        super().resizeEvent(event)
        self._fit_side()

    def _fill_thumbnails(self) -> None:
        placeholder = QPixmap(THUMB_WIDTH, round(THUMB_WIDTH * 1.414))
        placeholder.fill(Qt.white)
        for i in range(self.doc.page_count):
            QListWidgetItem(QIcon(placeholder), str(i + 1), self.thumbs).setTextAlignment(Qt.AlignHCenter)
        self._loader = ThumbnailLoader(0, self.path, self, password=self._password)
        self._loader.rendered.connect(self._on_thumbnail)
        self._loader.start()

    def _on_thumbnail(self, _source: int, index: int, image: QImage) -> None:
        if index < self.thumbs.count():
            ratio = self.thumbs.devicePixelRatioF()  # écran Retina : deux fois plus de pixels
            pixmap = QPixmap.fromImage(image.scaledToWidth(round(THUMB_WIDTH * ratio), Qt.SmoothTransformation))
            pixmap.setDevicePixelRatio(ratio)
            thumb = QIcon(pixmap)
            thumb.addPixmap(pixmap, QIcon.Selected)
            self.thumbs.item(index).setIcon(thumb)

    def _fill_outline(self) -> None:
        """Table des matières du PDF : un clic mène au titre, à sa position exacte dans la page.

        Sans signets (Word imprimé en PDF…), les titres sont devinés d'après leur mise en forme,
        à la première ouverture de l'onglet : inutile de lire tout le document avant.
        """
        entries = outline(self.doc)
        self._headings_pending = not entries
        self.outline_note.hide()
        self.outline.clear()
        if entries:
            self._show_outline(entries)
        elif self.side_stack.currentIndex() == 1:  # onglet déjà ouvert : on cherche tout de suite
            self._detect_headings()

    def _detect_headings(self) -> None:
        self._headings_pending = False
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            entries = detect_headings(self.doc)
        finally:
            QApplication.restoreOverrideCursor()
        self.outline_note.setVisible(bool(entries))
        self._show_outline(entries)

    def _show_outline(self, entries) -> None:
        self.outline.clear()
        parents: list[QTreeWidgetItem | None] = [None]
        for entry in entries:
            parent = parents[min(entry.level - 1, len(parents) - 1)]
            item = QTreeWidgetItem([entry.title])
            item.setToolTip(0, entry.title)
            if entry.target is not None:
                item.setData(0, Qt.UserRole, entry.target)
                item.setText(1, str(entry.target.page + 1))
                item.setForeground(1, QColor(theme.current().color("muted")))
            else:
                item.setFlags(Qt.ItemIsEnabled)  # sans destination : affichée, mais inerte
            (parent.addChild(item) if parent else self.outline.addTopLevelItem(item))
            del parents[entry.level:]
            parents.append(item)
        if self.outline.topLevelItemCount() == 0:
            placeholder = QTreeWidgetItem(["Ce PDF n'a ni signets ni titres reconnaissables."])
            placeholder.setFlags(Qt.NoItemFlags)
            self.outline.addTopLevelItem(placeholder)
        # Un seul titre principal (le titre du document) : ses chapitres sont dépliés d'emblée.
        self.outline.expandToDepth(1 if self.outline.topLevelItemCount() == 1 else 0)

    def _on_outline_item(self, item: QTreeWidgetItem, _column: int) -> None:
        target = item.data(0, Qt.UserRole)
        if target is not None:
            self.canvas.follow(target)

    def _open_uri(self, uri: str) -> None:
        """Lien externe du document : ouvert dans le navigateur ou la messagerie."""
        if QDesktopServices.openUrl(QUrl(uri)):
            self.status.setText(f"Ouverture de {uri}")
        else:
            self.status.setText(f"Impossible d'ouvrir {uri}")

    # ------------------------------------------------- état

    def _on_page_changed(self, index: int) -> None:
        self.page_spin.blockSignals(True)
        self.page_spin.setValue(index + 1)
        self.page_spin.blockSignals(False)
        if index < self.thumbs.count():
            self.thumbs.setCurrentRow(index)

    def _on_zoom_changed(self, zoom: float) -> None:
        fit = self.canvas.fit
        index = self.zoom_menu.findData(fit) if fit else -1
        self.zoom_menu.blockSignals(True)
        if index >= 0:
            self.zoom_menu.setCurrentIndex(index)
        else:
            self.zoom_menu.setEditText(f"{round(zoom * 100)} %")
        self.zoom_menu.blockSignals(False)

    def _choose_zoom(self, value) -> None:
        if isinstance(value, str):
            self.canvas.set_fit(value)
        else:
            self.canvas.set_zoom(value)

    def copy_selection(self) -> None:
        text = self.canvas.selected_text()
        if text:
            QApplication.clipboard().setText(text)
            self.status.setText("Texte copié.")

    # ------------------------------------------------- recherche

    def _on_search(self) -> None:
        if self.doc is None:
            return
        query = self.search_field.text()
        if self._hits and query == self._query:
            self.next_hit(1)  # Entrée à nouveau : occurrence suivante
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            self._hits = search(self.doc, query)
        finally:
            QApplication.restoreOverrideCursor()
        self._query = query
        self._hit = 0 if self._hits else -1
        self.canvas.set_hits(self._hits, self._hit)
        self._show_count()

    def next_hit(self, step: int) -> None:
        if not self._hits:
            return
        self._hit = (self._hit + step) % len(self._hits)
        self.canvas.set_hits(self._hits, self._hit)
        self._show_count()

    def _show_count(self) -> None:
        if not self.search_field.text().strip():
            self.search_count.setText("")
        elif not self._hits:
            self.search_count.setText("Aucun résultat")
        else:
            self.search_count.setText(f"{self._hit + 1} / {len(self._hits)}")

    def _clear_search(self) -> None:
        self._hits, self._hit, self._query = [], -1, ""
        if self.canvas.pages:
            self.canvas.set_hits([])
        self.search_count.setText("")

    # ------------------------------------------------- annotations

    def set_tool(self, tool: str) -> None:
        self.field_editor.close(commit=True)
        self.text_editor.close(commit=True)
        if tool in MARKUPS and self.canvas.selection:
            # Du texte est déjà sélectionné : on l'annote tout de suite.
            page, a, b = self.canvas.selection
            rects = [w.rect for w in self.canvas.words(page)[min(a, b):max(a, b) + 1]]
            self.canvas.clear_selection()
            self.tool = tool
            self._on_markup(page, rects)
            tool = "select"
        if tool == "add_image":
            path = self.ask_image()
            if path is None:
                tool = "select"
            else:
                self.image_path = path
                self.status.setText(f"Tracez le cadre de « {path.name} » (ou cliquez pour 200 pt de large).")
        self.tool = tool
        self.canvas.set_tool(tool)
        bar = self.edit_bar if self.mode.value() == "edit" else self.annot_bar
        if tool in bar.tools:
            bar.set_tool(tool)
        self._refresh_bar()

    def set_mode(self, mode: str) -> None:
        self.mode.set_value(mode)
        self.annot_bar.setVisible(mode == "annotate")
        self.edit_bar.setVisible(mode == "edit")
        self.set_tool("select")

    def _style(self, tool: str | None = None) -> dict:
        return self.styles.get(tool or self.tool, self.styles["rect"])

    def _selected(self) -> tuple[int, int, AnnotSpec] | None:
        target = self.canvas.selected_annot
        if target is None:
            return None
        page, index = target
        specs = self.canvas.specs(page)
        return (page, index, specs[index]) if index < len(specs) else None

    def _refresh_bar(self) -> None:
        selected = self._selected()
        if selected:
            spec = selected[2]
            self.annot_bar.show_style(spec.color, spec.width, spec.opacity, spec.fontsize if spec.kind == "text" else None)
        else:
            style = self._style()
            self.annot_bar.show_style(style["color"], style["width"], style["opacity"],
                                      style["fontsize"] if self.tool == "text" else None)
        self.canvas.style_color = self._style()["color"]
        self.canvas.style_width = self._style()["width"]
        self.annot_bar.set_selection(selected is not None)
        self.undo_btn.setEnabled(bool(self.history.undo_stack))
        self.redo_btn.setEnabled(bool(self.history.redo_stack))

    def _set_style(self, **changes) -> None:
        selected = self._selected()
        if selected:  # une annotation est sélectionnée : c'est elle qu'on modifie
            page, index, spec = selected
            updated = AnnotSpec(**{**spec.__dict__, **changes})
            if updated.kind == "text":
                updated = AnnotSpec(**{**updated.__dict__, "rect": notes.fit_textbox(updated.rect, updated.text,
                                                                                    updated.fontsize)})
            self._record(Change(page, spec, updated, notes.xref_at(self.doc[page], index)), keep_selection=True)
        else:
            self._style().update(changes)
            self._refresh_bar()

    def _new_spec(self, kind: str, **geometry) -> AnnotSpec:
        style = self._style(kind)
        return AnnotSpec(kind, color=style["color"], opacity=style["opacity"], width=style["width"],
                         fontsize=style["fontsize"], **geometry)

    def _on_markup(self, page: int, rects: list) -> None:
        if rects:
            quads = notes.markup_quads(rects)
            spec = self._new_spec(self.tool, rect=notes.bounds(quads), points=quads)
            self._record(Change(page, None, spec))

    def _on_shape(self, page: int, kind: str, geometry) -> None:
        if kind == "add_text":
            self.text_editor.add(page, geometry[:2], self.edit_style)
            return
        if kind == "add_image":
            if self.image_path is not None:
                image = self.image_path
                self._content_edit(page, lambda p: editing.add_image(p, geometry, image))
            return
        if kind in editing.NEW_FIELD_KINDS:
            self._add_field(page, kind, geometry)
            return
        if kind == "whiteout":
            self._content_edit(page, lambda p: editing.whiteout(p, geometry))
            return
        if kind in ("line", "arrow", "ink"):
            spec = self._new_spec(kind, rect=notes.bounds(geometry), points=tuple(tuple(s) for s in geometry))
        elif kind in ("note", "text"):
            text = self.ask_text("Nouvelle note" if kind == "note" else "Zone de texte", "")
            if not text:
                return
            rect = notes.fit_textbox(geometry, text, self._style(kind)["fontsize"]) if kind == "text" else geometry
            spec = self._new_spec(kind, rect=rect, text=text)
        else:
            spec = self._new_spec(kind, rect=geometry)
        self._record(Change(page, None, spec))

    def _on_moved(self, page: int, index: int, dx: float, dy: float) -> None:
        spec = self.canvas.specs(page)[index]
        self._record(Change(page, spec, spec.moved(dx, dy), notes.xref_at(self.doc[page], index)),
                     keep_selection=True)

    def _on_activated(self, page: int, index: int) -> None:
        """Double-clic : texte de la note ou de la zone, commentaire des autres annotations."""
        spec = self.canvas.specs(page)[index]
        title = {"note": "Modifier la note", "text": "Modifier le texte"}.get(spec.kind, "Commentaire")
        text = self.ask_text(title, spec.text)
        if text is None or text == spec.text or (spec.kind == "text" and not text):
            return
        rect = notes.fit_textbox(spec.rect, text, spec.fontsize) if spec.kind == "text" else spec.rect
        updated = AnnotSpec(**{**spec.__dict__, "text": text, "rect": rect})
        self._record(Change(page, spec, updated, notes.xref_at(self.doc[page], index)), keep_selection=True)

    def delete_selected(self) -> None:
        selected = self._selected()
        if selected:
            page, index, spec = selected
            self.canvas.select_annotation(None)
            self._record(Change(page, spec, None, notes.xref_at(self.doc[page], index)))

    def undo(self) -> None:
        if self.doc is not None:
            self.field_editor.close(commit=False)
            self.text_editor.close(commit=False)
            self.canvas.select_annotation(None)
            self._after_change(self.history.undo(self.doc))

    def redo(self) -> None:
        if self.doc is not None:
            self.field_editor.close(commit=False)
            self.text_editor.close(commit=False)
            self.canvas.select_annotation(None)
            self._after_change(self.history.redo(self.doc))

    def _record(self, change: Change, keep_selection: bool = False) -> None:
        self.history.do(self.doc, change)
        self._after_change(change.page)
        if keep_selection and change.after is not None:
            # L'annotation recréée passe au premier plan : dernière de la page.
            self.canvas.select_annotation((change.page, len(self.canvas.specs(change.page)) - 1))
        self._refresh_bar()

    def _after_change(self, page: int | None) -> None:
        if page is None:
            return
        self.canvas.refresh_page(page)
        self._refresh_thumbnail(page)
        self._refresh_annotations()
        self._set_dirty(True)
        self._refresh_bar()

    # ------------------------------------------------- édition du contenu

    def _content_edit(self, page: int, edit) -> object:
        """Applique `edit(page)` au contenu, avec un instantané pour pouvoir l'annuler.

        Retourne le résultat de `edit` (True s'il n'en a pas), ou None en cas d'échec. Les
        champs calculés sont mis à jour dans la même étape : un seul ⌘Z annule le tout."""
        before = self.doc.tobytes()  # sans ramasse-miettes : numéros d'objets conservés
        self._full_save_needed = True
        try:
            result = edit(self.doc[page])
            recalculated = forms.recalculate(self.doc)
        except editing.EditError as exc:
            self.status.setText(str(exc))
            return None
        self.history.record(Snapshot(page, before, self.doc.tobytes()))
        for other in sorted(recalculated - {page}):  # total sur une autre page
            self.canvas.refresh_page(other)
            self._refresh_thumbnail(other)
        self._after_change(page)
        return True if result is None else result

    def _restore_bytes(self, data: bytes) -> None:
        """Annuler / rétablir une modification de contenu : on recharge l'instantané."""
        doc = pymupdf.open("pdf", data)
        if doc.needs_pass and self._password:
            doc.authenticate(self._password)
        page, zoom, fit = self.canvas.current_page, self.canvas.zoom, self.canvas.fit
        old, self.doc = self.doc, doc
        self.canvas.set_document(doc)
        if fit:
            self.canvas.set_fit(fit)
        else:
            self.canvas.set_zoom(zoom)
        self.canvas.go_to(page)
        self.canvas.set_tool(self.tool)
        old.close()

    def _set_edit_style(self, **changes) -> None:
        """La barre change la ligne en cours de modification (elle seule), sinon le texte ajouté :
        celui en cours de saisie et les suivants."""
        if self.text_editor.line is None:
            self.edit_style.update(changes)
        if self.text_editor.active:
            self.text_editor.restyle(**changes)

    def _add_text(self, page: int, point, text: str, style: dict) -> None:
        self._content_edit(page, lambda p: editing.add_text(p, point, text, **style))

    def _on_line_clicked(self, page: int, point) -> None:
        """Clic sur une ligne : on la modifie sur place ; la barre montre sa mise en forme d'origine."""
        line = self.canvas.line_at(page, point)
        if line is None:
            self.status.setText("Pas de texte modifiable ici. Pour une page numérisée, faites d'abord un OCR.")
            return
        self.text_editor.edit(page, line, click=point)
        self.edit_bar.show_style(line.style)

    def _move_text(self, page: int, block: editing.TextBlock, dx: float, dy: float) -> None:
        kept = self._content_edit(page, lambda p: editing.move_block(p, block, dx, dy))
        if kept is not None:
            self.status.setText("Texte déplacé." if kept else
                                "Texte déplacé, avec une police proche pour une partie (police d'origine "
                                "indisponible dans le PDF).")

    def _replace_line(self, page: int, line: editing.TextLine, text: str, style: dict) -> None:
        restyled = style != line.style
        reused = self._content_edit(page, lambda p: editing.replace_line(p, line, text, style))
        if reused is not None:
            self.status.setText("Texte modifié avec la nouvelle mise en forme." if restyled else
                                "Texte modifié avec la police d'origine." if reused else
                                "Texte modifié avec une police proche (la police d'origine ne contient pas "
                                "tous les caractères).")

    # ------------------------------------------------- formulaires

    def _on_field_clicked(self, page: int, field: editing.Field) -> None:
        """Clic sur un champ : on y saisit directement ; une case se coche tout de suite."""
        if field.readonly:
            self.status.setText(f"« {field.name} » est en lecture seule (calculé automatiquement ou verrouillé).")
            return
        if field.checkable:
            radio = field.kind == pymupdf.PDF_WIDGET_TYPE_RADIOBUTTON
            if not (radio and field.checked):  # une option radio cochée le reste
                self._set_field(page, field, True if radio else not field.checked)
        self.field_editor.edit(page, field, popup=True)

    def _set_field(self, page: int, field: editing.Field, value) -> None:
        self._content_edit(page, lambda p: editing.set_field(p, field.xref, value))

    def field_menu_for(self, page: int, field: editing.Field) -> QMenu:
        menu = QMenu(self)
        menu.addAction("Remplir", lambda *_: self.field_editor.edit(page, field, popup=True))  # triggered(bool) ignoré
        menu.addAction("Renommer le champ…", lambda *_: self._rename_field(page, field))
        if field.kind == pymupdf.PDF_WIDGET_TYPE_TEXT:
            menu.addAction("Calcul automatique…", lambda *_: self._calculate_field(page, field))
        menu.addSeparator()
        menu.addAction("Supprimer le champ", lambda *_: self._delete_field(page, field))
        return menu

    def _rename_field(self, page: int, field: editing.Field) -> None:
        name = self.ask_line(field.name, title="Nom du champ")
        if name and name.strip() and name.strip() != field.name:
            self.field_editor.close(commit=True)
            self._content_edit(page, lambda p: editing.rename_field(p, field.xref, name.strip()))

    def _delete_field(self, page: int, field: editing.Field) -> None:
        self.field_editor.close(commit=False)
        if self._content_edit(page, lambda p: editing.delete_field(p, field.xref)) is None:
            return
        self.status.setText(f"Champ « {field.name} » supprimé ({_native(QKeySequence.Undo)} pour annuler).")

    def _add_radio(self, page: int, rect) -> None:
        """Une option de plus dans un groupe : on propose le dernier groupe utilisé."""
        group = self.ask_line(getattr(self, "_last_radio_group", None) or editing.unique_field_name(self.doc, "Groupe"),
                              title="Groupe du bouton radio (une seule option cochée par groupe)")
        if not group or not group.strip():
            return
        group = group.strip()
        if self._content_edit(page, lambda p: editing.add_field(p, rect, "field_radio", group)) is not None:
            self._last_radio_group = group
            count = len(editing.radio_options(self.doc, group))
            self.status.setText(f"Option {count} ajoutée au groupe « {group} ». Cliquez encore pour en ajouter.")

    def _calculate_field(self, page: int, field: editing.Field) -> None:
        names = sorted({f.name for p in range(self.doc.page_count) for f in self.canvas.fields(p)} - {field.name})
        if not names:
            self.status.setText("Il faut d'autres champs à additionner, multiplier…")
            return
        choice = self.ask_calculation(field.name, names)
        if choice is None:
            return
        operation, sources, decimals = choice
        self.field_editor.close(commit=True)
        if self._content_edit(page, lambda p: forms.set_calculation(self.doc, p, field.xref, operation,
                                                                     sources, decimals)) is not None:
            self.status.setText(f"« {field.name} » = {forms.OPERATIONS[operation].lower()} de "
                                f"{', '.join(sources)}, recalculé à chaque saisie.")

    def ask_calculation(self, target: str, names: list[str]) -> tuple[str, list[str], int] | None:
        """(opération, champs sources, décimales) ; None si annulé (remplacé dans les tests)."""
        return CalculationDialog.ask(self, target, names)

    def _add_field(self, page: int, kind: str, rect) -> None:
        if kind == "field_radio":
            self._add_radio(page, rect)
            return
        name = self.ask_line(editing.unique_field_name(self.doc, "Champ"), title="Nom du nouveau champ")
        if not name or not name.strip():
            return
        choices: tuple[str, ...] = ()
        if kind == "field_choice":
            text = self.ask_text("Valeurs de la liste (une par ligne)", "Oui\nNon")
            choices = tuple(v.strip() for v in (text or "").splitlines() if v.strip())
            if not choices:
                return
        if self._content_edit(page, lambda p: editing.add_field(p, rect, kind, name.strip(), choices)) is not None:
            self.status.setText(f"Champ « {name.strip()} » ajouté. Outil Sélection pour le remplir, "
                                "clic droit pour le renommer ou le supprimer.")

    def ask_line(self, initial: str, title: str = "Modifier le texte") -> str | None:
        """Saisie sur une ligne ; None si annulé (remplacé dans les tests)."""
        text, ok = QInputDialog.getText(self, title, "Texte :", QLineEdit.Normal, initial)
        return text if ok else None

    def ask_image(self) -> Path | None:
        chosen, _ = QFileDialog.getOpenFileName(self, "Image à ajouter", "", "Images (*.png *.jpg *.jpeg *.webp)")
        return Path(chosen) if chosen else None

    def ask_text(self, title: str, initial: str) -> str | None:
        """Saisie d'un texte de note ou de zone ; None si annulé (remplacé dans les tests)."""
        text, ok = QInputDialog.getMultiLineText(self, title, "Texte :", initial)
        return text.strip() if ok else None

    # ------------------------------------------------- liste des annotations

    def _refresh_annotations(self) -> None:
        self.annot_list.clear()
        if self.doc is None:
            return
        for page in range(self.doc.page_count):
            for index, spec in enumerate(self.canvas.specs(page)):
                item = QListWidgetItem(f"p. {page + 1} · {notes.describe(self.doc[page], spec)}", self.annot_list)
                item.setData(Qt.UserRole, (page, index))
        if self.annot_list.count() == 0:
            item = QListWidgetItem("Aucune annotation. Choisissez un outil dans la barre du bas.", self.annot_list)
            item.setFlags(Qt.NoItemFlags)

    def _on_annot_item(self, item: QListWidgetItem) -> None:
        target = item.data(Qt.UserRole)
        if target:
            page, index = target
            self.set_tool("select")
            spec = self.canvas.specs(page)[index]
            self.canvas.go_to(page, pymupdf.Rect(spec.rect))
            self.canvas.select_annotation((page, index))

    def _refresh_thumbnail(self, page: int) -> None:
        pix = render_thumbnail(self.doc, page)
        self._on_thumbnail(0, page, QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format_RGB888).copy())

    # ------------------------------------------------- enregistrement

    def _set_dirty(self, dirty: bool) -> None:
        self.dirty = dirty
        self.save_btn.setEnabled(dirty)
        self.state_changed.emit()

    def save(self) -> bool:
        self.field_editor.flush()  # saisie en cours dans un champ ou dans la page
        self.text_editor.close(commit=True)
        if self.doc is None or not self.dirty:
            return True
        try:
            if self.doc.can_save_incrementally() and not self._full_save_needed:
                self.doc.saveIncr()  # rapide, et les signatures numériques existantes restent valides
            else:
                self._rewrite(self.path)
        except Exception as exc:
            self.status.setText(f"Enregistrement impossible : {exc}")
            return False
        self._set_dirty(False)
        self.status.setText("Enregistré.")
        return True

    def save_as(self) -> None:
        if self.doc is None:
            return
        default = self.path.with_name(f"{self.path.stem}_annoté.pdf")
        chosen, _ = QFileDialog.getSaveFileName(self, "Enregistrer sous", str(default), "PDF (*.pdf)")
        if chosen:
            self.save_to(Path(chosen).with_suffix(".pdf"))

    def save_to(self, target: Path) -> bool:
        try:
            if target == self.path:
                self._rewrite(target)
            else:
                self.doc.save(target, garbage=3, deflate=True)
                self._reload(target)
        except Exception as exc:
            self.status.setText(f"Enregistrement impossible : {exc}")
            return False
        self._set_dirty(False)
        self.status.setText(f"Enregistré sous {target.name}.")
        return True

    def _rewrite(self, target: Path) -> None:
        """Réécriture complète du fichier ouvert (quand l'ajout incrémental n'est pas possible)."""
        tmp = target.with_name(f".{target.stem}.docflex-tmp.pdf")
        self.doc.save(tmp, garbage=3, deflate=True)
        self.doc.close()
        os.replace(tmp, target)
        self._reload(target)

    def _reload(self, path: Path) -> None:
        """Rouvre `path` à la même page et au même zoom ; les numéros internes ont pu changer."""
        page, zoom, fit = self.canvas.current_page, self.canvas.zoom, self.canvas.fit
        self._stop_loader()
        if self.doc is not None and not self.doc.is_closed:
            self.doc.close()
        doc = pymupdf.open(path)
        if doc.needs_pass and self._password:
            doc.authenticate(self._password)
        self.doc, self.path = doc, path
        self.history.clear()
        self._full_save_needed = False
        self.canvas.set_document(doc)
        if fit:
            self.canvas.set_fit(fit)
        else:
            self.canvas.set_zoom(zoom)
        self.canvas.go_to(page)
        self.thumbs.clear()
        self._fill_thumbnails()
        self._refresh_annotations()
        self._refresh_bar()
        self.state_changed.emit()  # nom de l'onglet (« Enregistrer sous »)

    # ------------------------------------------------- impression

    def ask_printer(self, printer: QPrinter) -> bool:
        """Dialogue d'impression du système (remplacé dans les tests)."""
        dialog = QPrintDialog(printer, self)
        dialog.setWindowTitle(f"Imprimer « {self.title} »")
        dialog.setOption(QPrintDialog.PrintCurrentPage, True)
        dialog.setOption(QPrintDialog.PrintPageRange, True)
        return dialog.exec() == QPrintDialog.Accepted

    def print_document(self) -> bool:
        if self.doc is None:
            return False
        self.field_editor.flush()  # la saisie en cours est imprimée aussi
        self.text_editor.close(commit=True)
        printer = QPrinter(QPrinter.HighResolution)
        printer.setDocName(self.title)
        printer.setFromTo(1, self.doc.page_count)
        if not self.ask_printer(printer):
            return False
        pages = pages_to_print(printer, self.doc.page_count, self.canvas.current_page)
        progress = QProgressDialog("Préparation de l'impression…", "Annuler", 0, len(pages), self)
        progress.setWindowTitle("Impression")
        progress.setMinimumDuration(400)  # les documents courts partent sans fenêtre
        progress.setWindowModality(Qt.WindowModal)

        def step(n: int) -> bool:
            progress.setValue(n)
            progress.setLabelText(f"Page {n + 1} sur {len(pages)}…")
            QApplication.processEvents()
            return not progress.wasCanceled()

        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            done = print_pages(self.doc, printer, pages, step)
        finally:
            QApplication.restoreOverrideCursor()
            progress.close()
        n = len(pages)
        self.status.setText(f"Impression lancée : {n} page{'s' if n > 1 else ''}." if done
                            else "Impression annulée.")
        return done

    def ask_save(self) -> str:
        """« save », « discard » ou « cancel » (remplacé dans les tests)."""
        return choose(self, "Modifications non enregistrées",
                      f"Enregistrer les annotations de « {self.path.name} » ?",
                      [("Enregistrer", "save", QMessageBox.AcceptRole),
                       ("Ne pas enregistrer", "discard", QMessageBox.DestructiveRole),
                       ("Annuler", "cancel", QMessageBox.RejectRole)])

    def confirm_close(self) -> bool:
        """True si l'on peut fermer le document (enregistré, ou modifications abandonnées)."""
        self.field_editor.flush()
        self.text_editor.close(commit=True)
        if self.doc is None or not self.dirty:
            return True
        answer = self.ask_save()
        if answer == "save":
            return self.save()
        return answer == "discard"
