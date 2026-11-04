"""Couleurs et feuille de style, en clair et en sombre.

Toutes les couleurs de l'interface viennent de `TOKENS` : les widgets
référencent des noms d'objet (`objectName`) ou des propriétés dynamiques,
jamais des couleurs en dur.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

ASSETS = (Path(__file__).parent / "assets").as_posix()  # QSS n'accepte que des « / »

TOKENS = {
    "light": {
        "bg": "#f5f6f8", "surface": "#ffffff", "surface2": "#f0f2f5", "border": "#dde0e6",
        "text": "#1c1e22", "muted": "#6b7280", "accent": "#2563eb", "accent_hover": "#1d4ed8",
        "accent_soft": "#e3ebfd", "on_accent": "#ffffff", "danger": "#dc2626", "danger_hover": "#b91c1c",
        "success": "#15803d", "sidebar": "#eceef2", "nav_hover": "#e0e3e9", "drop": "#eef3fe",
    },
    "dark": {
        "bg": "#16181c", "surface": "#1f2227", "surface2": "#272b31", "border": "#343840",
        "text": "#e6e7ea", "muted": "#9aa0aa", "accent": "#3b82f6", "accent_hover": "#60a5fa",
        "accent_soft": "#1e2a44", "on_accent": "#ffffff", "danger": "#f87171", "danger_hover": "#fca5a5",
        "success": "#4ade80", "sidebar": "#111316", "nav_hover": "#1f232a", "drop": "#18243a",
    },
}

STYLESHEET = """
QWidget {{ color: {text}; font-size: 13px; }}
QMainWindow, #content {{ background: {bg}; }}
QToolTip {{ background: {surface}; color: {text}; border: 1px solid {border}; padding: 4px; }}

#sidebar {{ background: {sidebar}; }}
#sidebar QLabel#brand {{ font-size: 24px; font-weight: 700; }}
#navButton {{
    text-align: left; padding: 9px 12px; border: none; border-radius: 8px;
    background: transparent; font-size: 14px;
}}
#navButton:hover {{ background: {nav_hover}; }}
#navButton:checked {{ background: {accent_soft}; color: {accent}; font-weight: 600; }}

QLabel[role="title"] {{ font-size: 24px; font-weight: 700; }}
QLabel[role="muted"] {{ color: {muted}; }}
QLabel[role="field"] {{ font-weight: 600; }}
QLabel[role="ok"] {{ color: {success}; }}
QLabel[role="error"] {{ color: {danger}; }}
QLabel#badge {{
    background: {surface2}; border: 1px solid {border}; border-radius: 4px;
    font-size: 10px; font-weight: 700; padding: 2px 0;
}}

#card {{ background: {surface}; border: 1px solid {border}; border-radius: 10px; }}
#toolCard {{ background: {surface}; border: 1px solid {border}; border-radius: 10px; }}
#toolCard:hover, #toolCard:focus {{ border-color: {accent}; background: {accent_soft}; }}
#dropZone {{ background: {surface}; border: 1.5px dashed {border}; border-radius: 10px; }}
#dropZone[dragging="true"] {{ background: {drop}; border-color: {accent}; }}
#fileRow {{ background: {surface2}; border-radius: 6px; }}

QPushButton {{
    background: {surface}; border: 1px solid {border}; border-radius: 7px; padding: 6px 14px;
}}
QPushButton:hover {{ border-color: {accent}; }}
QPushButton:disabled {{ color: {muted}; border-color: {border}; background: {surface2}; }}
QPushButton[kind="primary"] {{
    background: {accent}; color: {on_accent}; border: none; font-weight: 600; padding: 8px 22px;
}}
QPushButton[kind="primary"]:hover {{ background: {accent_hover}; }}
QPushButton[kind="primary"]:disabled {{ background: {border}; color: {muted}; }}
QPushButton[kind="danger"] {{ color: {danger}; }}
QPushButton[kind="danger"]:hover {{ border-color: {danger}; }}
QPushButton[kind="danger"]:disabled {{ color: {muted}; border-color: {border}; }}
QPushButton[kind="ghost"] {{ border: none; background: transparent; color: {muted}; padding: 2px 6px; }}
QPushButton[kind="ghost"]:hover {{ color: {danger}; }}
QPushButton[kind="square"] {{ padding: 0; font-size: 15px; }}
QPushButton[kind="tool"] {{ padding: 0; border: 1px solid transparent; background: transparent; border-radius: 7px; }}
QPushButton[kind="tool"]:hover {{ border-color: {border}; background: {surface}; }}
QPushButton[kind="tool"]:checked {{ background: {accent_soft}; border-color: {accent}; }}
QPushButton[kind="tool"]:disabled {{ background: transparent; border-color: transparent; }}
QFrame#separator {{ background: {border}; }}
QPushButton::menu-indicator {{ image: none; width: 0; }}
QMenu {{ background: {surface}; border: 1px solid {border}; border-radius: 8px; padding: 4px; }}
QMenu::item {{ padding: 6px 22px 6px 10px; border-radius: 5px; }}
QMenu::item:selected {{ background: {accent_soft}; color: {text}; }}
QMenu::separator {{ height: 1px; background: {border}; margin: 4px 6px; }}
QPushButton[kind="icon"] {{ border: none; background: transparent; color: {muted}; padding: 2px 5px; }}
QPushButton[kind="icon"]:hover {{ color: {accent}; }}
QPushButton[kind="segment"] {{ border-radius: 0; padding: 6px 18px; }}
QPushButton[kind="segment"][position="first"] {{ border-top-left-radius: 7px; border-bottom-left-radius: 7px; }}
QPushButton[kind="segment"][position="last"] {{ border-top-right-radius: 7px; border-bottom-right-radius: 7px; }}
QPushButton[kind="segment"]:checked {{ background: {accent}; color: {on_accent}; border-color: {accent}; }}

QComboBox, QLineEdit {{
    background: {surface}; border: 1px solid {border}; border-radius: 7px; padding: 5px 10px; min-height: 18px;
}}
QComboBox:focus, QLineEdit:focus {{ border-color: {accent}; }}
QComboBox::drop-down {{ border: none; width: 28px; }}
QComboBox::down-arrow {{ image: url({assets}/chevron.svg); width: 12px; height: 12px; }}
QComboBox:disabled, QLineEdit:disabled {{ color: {muted}; background: {surface2}; }}
QComboBox QAbstractItemView {{
    background: {surface}; border: 1px solid {border}; selection-background-color: {accent_soft};
    selection-color: {text}; outline: none;
}}
QLineEdit[invalid="true"] {{ border-color: {danger}; }}

QCheckBox, QRadioButton {{ spacing: 8px; }}
QCheckBox::indicator, QRadioButton::indicator {{
    width: 16px; height: 16px; background: {surface}; border: 1px solid {muted};
}}
QCheckBox::indicator {{ border-radius: 4px; }}
QRadioButton::indicator {{ border-radius: 9px; }}
QCheckBox::indicator:hover, QRadioButton::indicator:hover {{ border-color: {accent}; }}
QCheckBox::indicator:checked {{ background: {accent}; border-color: {accent}; image: url({assets}/check.svg); }}
QRadioButton::indicator:checked {{ background: {on_accent}; border: 5px solid {accent}; width: 8px; height: 8px; }}
QCheckBox::indicator:disabled, QRadioButton::indicator:disabled {{ background: {surface2}; border-color: {border}; }}
QCheckBox:disabled, QRadioButton:disabled {{ color: {muted}; }}

QProgressBar {{ background: {surface2}; border: none; border-radius: 3px; max-height: 6px; }}
QProgressBar::chunk {{ background: {accent}; border-radius: 3px; }}

QPlainTextEdit, QTextEdit, QListWidget {{
    background: {surface}; border: 1px solid {border}; border-radius: 10px; padding: 6px;
}}
QListWidget#fileList {{ background: transparent; border: none; padding: 0; }}
QListWidget#thumbList {{ background: {surface2}; }}
QListWidget#recentList {{ background: transparent; border: none; padding: 0; }}
QListWidget#thumbList::item {{ color: {muted}; border-radius: 6px; }}
QListWidget#thumbList::item:selected {{ background: {accent_soft}; color: {accent}; }}
QListWidget#pageGrid::item {{ border-radius: 8px; padding: 4px; color: {muted}; }}
QListWidget#pageGrid::item:hover {{ background: {surface2}; }}
QListWidget#pageGrid::item:selected {{ background: {accent_soft}; color: {accent}; }}
QTextEdit#preview {{ border: none; background: transparent; }}

#floatingBar {{ background: {surface}; border: 1px solid {border}; border-radius: 14px; }}
#floatingBar QLabel {{ background: transparent; }}
#floatingBar QComboBox, #floatingBar QLineEdit, #floatingBar QSpinBox {{
    background: {surface2}; border: 1px solid transparent; border-radius: 8px; padding: 4px 8px; min-height: 18px;
}}
#floatingBar QSpinBox {{ padding: 4px 2px; }}
#floatingBar QSpinBox::up-button, #floatingBar QSpinBox::down-button {{ subcontrol-origin: border; width: 0; height: 0; }}
#floatingBar QComboBox:hover, #floatingBar QSpinBox:hover {{ border-color: {border}; }}
#floatingBar QComboBox:focus, #floatingBar QLineEdit:focus, #floatingBar QSpinBox:focus {{ border-color: {accent}; }}
#floatingBar QPushButton[kind="tool"]:hover {{ background: {surface2}; border-color: transparent; }}
#floatingBar QPushButton[kind="tool"]:checked {{ background: {accent_soft}; border-color: transparent; }}
#floatingBar QPushButton[kind="tool"]:checked:hover {{ border-color: {accent}; }}
#floatingBar QPushButton[kind="primary"] {{ padding: 6px 16px; border-radius: 9px; }}
#sidePanel QPushButton[kind="segment"] {{ padding: 5px 10px; }}
#floatingBar QPushButton[kind="segment"] {{ padding: 5px 10px; background: {surface2}; border-color: {surface2}; }}
#floatingBar QPushButton[kind="segment"]:checked {{ background: {accent}; border-color: {accent}; }}
#fieldEditor {{
    background: #ffffff; color: #000000; border: 2px solid {accent}; border-radius: 3px;
    selection-background-color: {accent_soft}; selection-color: #000000;
}}
#textEditor {{
    background: transparent; border: 1px dashed {accent}; border-radius: 0; padding: 0;
    selection-background-color: {accent_soft};
}}
#lineEditor {{
    background: #ffffff; border: 1px solid {accent}; border-radius: 2px; padding: 0; min-height: 0;
    selection-background-color: {accent_soft};
}}
QLabel#toast {{
    background: {text}; color: {bg}; border-radius: 10px; padding: 8px 16px; font-weight: 500;
}}
QTabBar#documentTabs {{ background: transparent; }}
QTabBar#documentTabs::tab {{
    background: transparent; border: 1px solid transparent; border-radius: 9px;
    padding: 6px 10px; margin-right: 4px; color: {muted};
}}
QTabBar#documentTabs::tab:hover {{ background: {nav_hover}; color: {text}; }}
QTabBar#documentTabs::tab:selected {{ background: {surface}; border-color: {border}; color: {text}; }}
QTabBar#documentTabs QToolButton {{ background: {surface}; border: 1px solid {border}; border-radius: 6px; }}
QPushButton#tabClose {{ border: none; background: transparent; border-radius: 5px; padding: 0; }}
QPushButton#tabClose:hover {{ background: {nav_hover}; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {border}; border-radius: 4px; min-height: 24px; }}
QScrollBar::handle:vertical:hover {{ background: {muted}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
"""


class Theme(QObject):
    """Applique la palette et la feuille de style ; suit le thème du système en mode « system »."""

    changed = Signal()

    def __init__(self, app: QApplication) -> None:
        super().__init__(app)
        self._app = app
        self.mode = "system"
        app.setStyle("Fusion")
        app.styleHints().colorSchemeChanged.connect(lambda _: self._apply())
        self._apply()

    @property
    def scheme(self) -> str:
        if self.mode in ("light", "dark"):  # certaines plateformes ignorent setColorScheme
            return self.mode
        return "dark" if self._app.styleHints().colorScheme() == Qt.ColorScheme.Dark else "light"

    def color(self, token: str) -> str:
        return TOKENS[self.scheme][token]

    def set_mode(self, mode: str) -> None:
        if mode == self.mode:
            return  # réappliquer la feuille de style repolirait toute l'interface pour rien
        self.mode = mode
        self._app.styleHints().setColorScheme(
            {"light": Qt.ColorScheme.Light, "dark": Qt.ColorScheme.Dark}.get(mode, Qt.ColorScheme.Unknown)
        )
        self._apply()

    def _apply(self) -> None:
        t = TOKENS[self.scheme]
        palette = QPalette()
        for role, token in [
            (QPalette.Window, "bg"), (QPalette.Base, "surface"), (QPalette.AlternateBase, "surface2"),
            (QPalette.Button, "surface"), (QPalette.Text, "text"), (QPalette.WindowText, "text"),
            (QPalette.ButtonText, "text"), (QPalette.PlaceholderText, "muted"), (QPalette.Highlight, "accent"),
            (QPalette.HighlightedText, "on_accent"), (QPalette.ToolTipBase, "surface"), (QPalette.ToolTipText, "text"),
        ]:
            palette.setColor(role, QColor(t[token]))
        palette.setColor(QPalette.Disabled, QPalette.Text, QColor(t["muted"]))
        palette.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(t["muted"]))
        self._app.setPalette(palette)
        self._app.setStyleSheet(STYLESHEET.format(assets=ASSETS, **t))
        self.changed.emit()


_theme: Theme | None = None


def install(app: QApplication) -> Theme:
    """Installe le thème une seule fois : le réappliquer repolirait toutes les fenêtres existantes."""
    global _theme
    if _theme is None or _theme.parent() is not app:
        _theme = Theme(app)
    return _theme


def current() -> Theme:
    assert _theme is not None, "theme.install() doit être appelé au démarrage"
    return _theme


def repolish(widget) -> None:
    """À appeler après avoir changé une propriété dynamique utilisée par la feuille de style."""
    widget.style().unpolish(widget)
    widget.style().polish(widget)
