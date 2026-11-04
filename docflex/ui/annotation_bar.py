"""Barres d'outils du lecteur : annotation (outils, couleur, style) et édition du contenu."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QActionGroup, QColor, QIcon, QKeySequence, QPainter, QPixmap
from PySide6.QtWidgets import QButtonGroup, QColorDialog, QHBoxLayout, QMenu, QPushButton, QWidget

from docflex.core.editing import FAMILIES
from docflex.ui import theme
from docflex.ui.icons import icon
from docflex.ui.widgets import button, combo, separator

TOOLS = (
    ("select", "Sélectionner du texte ou une annotation (Échap)"),
    ("highlight", "Surligner le texte"), ("underline", "Souligner le texte"), ("strikeout", "Barrer le texte"),
    ("note", "Note (commentaire)"), ("text", "Zone de texte"),
    ("rect", "Rectangle"), ("ellipse", "Ellipse"), ("line", "Ligne"), ("arrow", "Flèche"),
    ("ink", "Dessin à main levée"),
)
COLORS = (
    ("Jaune", (1.0, 0.84, 0.0)), ("Vert", (0.2, 0.75, 0.35)), ("Bleu", (0.15, 0.45, 0.95)),
    ("Rose", (1.0, 0.4, 0.7)), ("Rouge", (0.9, 0.15, 0.15)), ("Noir", (0.1, 0.1, 0.1)),
)
WIDTHS = (("Fin", 1.0), ("Moyen", 2.0), ("Épais", 4.0))
OPACITIES = (("100 %", 1.0), ("70 %", 0.7), ("40 %", 0.4))
FONT_SIZES = (("10 pt", 10.0), ("12 pt", 12.0), ("16 pt", 16.0), ("24 pt", 24.0))


def _swatch(rgb, size: int = 16, ratio: float = 2.0) -> QIcon:
    pixmap = QPixmap(QSize(size, size) * ratio)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(QColor(0, 0, 0, 60))
    painter.setBrush(QColor.fromRgbF(*rgb))
    painter.drawEllipse(1, 1, size * ratio - 2, size * ratio - 2)
    painter.end()
    pixmap.setDevicePixelRatio(ratio)
    return QIcon(pixmap)


class ColorButton(QPushButton):
    """Bouton couleur : six teintes usuelles et « Autre couleur… »."""

    chosen = Signal(tuple)

    def __init__(self) -> None:
        super().__init__()
        self.setProperty("kind", "tool")
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(40, 30)
        self.setToolTip("Couleur")
        menu = QMenu(self)
        for name, rgb in COLORS:
            menu.addAction(_swatch(rgb), name, lambda *_, c=rgb: self.chosen.emit(c))  # triggered(bool) ignoré
        menu.addSeparator()
        menu.addAction("Autre couleur…", self._custom)
        self.setMenu(menu)
        self.set_color(COLORS[0][1])

    def set_color(self, rgb) -> None:
        self.color = tuple(rgb)
        self.setIcon(_swatch(rgb, size=18))

    def _custom(self) -> None:
        chosen = QColorDialog.getColor(QColor.fromRgbF(*self.color), self, "Couleur")
        if chosen.isValid():
            self.chosen.emit((chosen.redF(), chosen.greenF(), chosen.blueF()))


def _tool_buttons(bar: QWidget, row: QHBoxLayout, tools, separators_after, emit) -> dict:
    buttons: dict[str, QPushButton] = {}
    group = QButtonGroup(bar)
    for name, tip in tools:
        btn = button("", "tool", lambda n=name: emit(n))
        btn.setCheckable(True)
        btn.setFixedSize(30, 30)
        btn.setToolTip(tip)
        group.addButton(btn)
        buttons[name] = btn
        row.addWidget(btn)
        if name in separators_after:
            row.addWidget(separator())
    buttons[tools[0][0]].setChecked(True)
    return buttons


def _nearest(actions: dict, value: float):
    return actions[min(actions, key=lambda v: abs(v - value))]


class AnnotationBar(QWidget):
    tool_changed = Signal(str)
    color_chosen = Signal(tuple)
    width_chosen = Signal(float)
    opacity_chosen = Signal(float)
    fontsize_chosen = Signal(float)
    delete_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(2)

        self.tools = _tool_buttons(self, row, TOOLS, ("select", "strikeout", "text"), self.tool_changed.emit)
        row.addWidget(separator())
        self.color_btn = ColorButton()
        self.color_btn.chosen.connect(self.color_chosen.emit)
        row.addWidget(self.color_btn)

        # Épaisseur, opacité et taille du texte : un seul menu, pour une barre compacte.
        self.style_btn = button("", "tool")
        self.style_btn.setFixedSize(30, 30)
        self.style_btn.setToolTip("Style : épaisseur, opacité, taille du texte")
        self.style_menu = QMenu(self.style_btn)
        self.width_actions = self._section("Épaisseur du trait", WIDTHS, 1, self.width_chosen)
        self.opacity_actions = self._section("Opacité", OPACITIES, 0, self.opacity_chosen)
        start = len(self.style_menu.actions())
        self.font_actions = self._section("Taille du texte", FONT_SIZES, 1, self.fontsize_chosen)
        self._font_section = self.style_menu.actions()[start:]  # séparateur, titre et tailles
        self.style_btn.setMenu(self.style_menu)
        row.addWidget(self.style_btn)

        self.delete_btn = button("", "tool", self.delete_requested.emit)
        native = QKeySequence(QKeySequence.Delete).toString(QKeySequence.NativeText)
        self.delete_btn.setToolTip(f"Supprimer l'annotation sélectionnée ({native})")
        self.delete_btn.setFixedSize(30, 30)
        row.addWidget(self.delete_btn)

        theme.current().changed.connect(self._paint_icons)
        self._paint_icons()
        self.set_selection(False)

    def _section(self, title: str, choices, default: int, signal) -> dict:
        """Titre grisé puis choix exclusifs ; retourne {valeur: action}."""
        if self.style_menu.actions():
            self.style_menu.addSeparator()
        header = self.style_menu.addAction(title)
        header.setEnabled(False)
        group = QActionGroup(self.style_menu)
        actions = {}
        for i, (text, value) in enumerate(choices):
            action = self.style_menu.addAction(text, lambda *_, v=value: signal.emit(v))  # triggered(bool) ignoré
            action.setCheckable(True)
            action.setChecked(i == default)
            group.addAction(action)
            actions[value] = action
        return actions

    def _paint_icons(self) -> None:
        color = theme.current().color("text")
        for name, btn in self.tools.items():
            btn.setIcon(icon(name, color))
        self.delete_btn.setIcon(icon("trash", color))
        self.style_btn.setIcon(icon("sliders", color))

    # ------------------------------------------------------------ état

    def set_tool(self, tool: str) -> None:
        self.tools[tool].setChecked(True)
        self._show_font_sizes(tool == "text")

    def _show_font_sizes(self, visible: bool) -> None:
        for action in self._font_section:
            action.setVisible(visible)

    def show_style(self, color, width: float, opacity: float, fontsize: float | None) -> None:
        """Reflète le style de l'outil courant ou de l'annotation sélectionnée."""
        self.color_btn.set_color(color)
        _nearest(self.width_actions, width).setChecked(True)
        _nearest(self.opacity_actions, opacity).setChecked(True)
        if fontsize is not None:
            _nearest(self.font_actions, fontsize).setChecked(True)
        self._show_font_sizes(fontsize is not None)

    def set_selection(self, has_selection: bool) -> None:
        self.delete_btn.setEnabled(has_selection)


EDIT_TOOLS = (
    ("select", "Sélectionner du texte, remplir un formulaire (Échap)"),
    ("edit_text", "Modifier le texte : cliquez sur une ligne pour la modifier, glissez pour déplacer son paragraphe"),
    ("add_text", "Ajouter du texte dans la page : cliquez à l'endroit voulu"),
    ("add_image", "Ajouter une image : tracez son cadre"),
    ("whiteout", "Blanc correcteur : efface définitivement la zone tracée"),
    ("field_text", "Nouveau champ texte : tracez-le, ou cliquez"),
    ("field_check", "Nouvelle case à cocher : cliquez à l'endroit voulu"),
    ("field_radio", "Nouveau bouton radio : une seule option cochée par groupe"),
    ("field_choice", "Nouvelle liste déroulante : tracez-la, ou cliquez"),
)
EDIT_SIZES = tuple((f"{n} pt", float(n)) for n in (8, 10, 11, 12, 14, 16, 20, 24, 32))


class EditBar(QWidget):
    """Modifier le contenu des pages : texte, images, blanc correcteur.

    La mise en forme s'applique au texte ajouté, ou à la ligne en cours de modification ; ses
    contrôles ne prennent pas le focus, pour que la saisie dans la page continue."""

    tool_changed = Signal(str)
    family_chosen = Signal(str)
    size_chosen = Signal(float)
    color_chosen = Signal(tuple)
    bold_chosen = Signal(bool)
    italic_chosen = Signal(bool)

    def __init__(self) -> None:
        super().__init__()
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(2)
        self.tools = _tool_buttons(self, row, EDIT_TOOLS, ("select", "add_image", "whiteout"), self.tool_changed.emit)
        row.addWidget(separator())
        self.family_menu = combo(((label_, key) for key, label_ in FAMILIES.items()), width=150)
        self.family_menu.setToolTip("Police du texte")
        self.family_menu.activated.connect(lambda i: self.family_chosen.emit(self.family_menu.itemData(i)))
        self.size_menu = combo(EDIT_SIZES, 3, width=82)
        self.size_menu.setToolTip("Taille du texte")
        self.size_menu.activated.connect(lambda i: self.size_chosen.emit(self.size_menu.itemData(i)))
        self.bold_btn = self._toggle("G", "Gras", "font-weight: 700;", self.bold_chosen)
        self.italic_btn = self._toggle("I", "Italique", "font-style: italic; font-family: serif;", self.italic_chosen)
        self.color_btn = ColorButton()
        self.color_btn.setToolTip("Couleur du texte")
        self.color_btn.set_color(COLORS[-1][1])
        self.color_btn.chosen.connect(self._on_color)
        for widget in (self.family_menu, self.size_menu, self.bold_btn, self.italic_btn, self.color_btn):
            widget.setFocusPolicy(Qt.NoFocus)
            row.addWidget(widget)
        theme.current().changed.connect(self._paint_icons)
        self._paint_icons()

    def _toggle(self, text: str, tip: str, css: str, signal) -> QPushButton:
        btn = button(text, "tool")
        btn.setCheckable(True)
        btn.setFixedSize(30, 30)
        btn.setToolTip(tip)
        btn.setStyleSheet(f"QPushButton {{ {css} font-size: 15px; }}")
        btn.clicked.connect(lambda checked: signal.emit(checked))
        return btn

    def show_style(self, style: dict) -> None:
        """Affiche une mise en forme (sans rien émettre) : celle du texte à ajouter, ou celle d'origine
        de la ligne en cours de modification."""
        self.family_menu.setCurrentIndex(max(self.family_menu.findData(style["family"]), 0))
        size = round(style["size"], 1)
        index = self.size_menu.findData(size)
        if index < 0:  # taille d'origine hors de la liste (13,5 pt…) : ajoutée à sa place
            index = sum(1 for i in range(self.size_menu.count()) if self.size_menu.itemData(i) < size)
            self.size_menu.insertItem(index, f"{size:g} pt".replace(".", ","), size)
        self.size_menu.setCurrentIndex(index)
        self.bold_btn.setChecked(style.get("bold", False))
        self.italic_btn.setChecked(style.get("italic", False))
        self.color_btn.set_color(style["color"])

    def _on_color(self, rgb) -> None:
        self.color_btn.set_color(rgb)
        self.color_chosen.emit(rgb)

    def _paint_icons(self) -> None:
        color = theme.current().color("text")
        for name, btn in self.tools.items():
            btn.setIcon(icon(name, color))

    def set_tool(self, tool: str) -> None:
        self.tools[tool].setChecked(True)
