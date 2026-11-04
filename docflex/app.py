"""Fenêtre principale : barre latérale de navigation + zone de contenu."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QEasingCurve, QEvent, Qt, QVariantAnimation
from PySide6.QtGui import QIcon, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QFrame, QHBoxLayout, QMainWindow, QStackedWidget, QVBoxLayout, QWidget,
)

from docflex import APP_NAME, __version__
from docflex.core import office
from docflex.ui import theme
from docflex.ui.compress_view import CompressView
from docflex.ui.convert_view import ConvertView
from docflex.ui.organize_view import OrganizeView
from docflex.ui.reader_view import ReaderView
from docflex.ui.settings import settings
from docflex.ui.split_view import SplitView
from docflex.ui.tools_view import ToolsView
from docflex.ui.widgets import ask, button, combo, label

APPEARANCES = (("Système", "system"), ("Clair", "light"), ("Sombre", "dark"))
ICON = Path(__file__).resolve().parent / "ui" / "assets" / "icon.png"
SIDEBAR_WIDTH = 224
SIDEBAR_ANIMATION_MS = 220
# Raccourci habituel : ⌃⌘S sur Mac (Finder, Xcode) ; Ctrl+Alt+S ailleurs. Qt : « Ctrl » = ⌘, « Meta » = ⌃.
SIDEBAR_SHORTCUT = "Meta+Ctrl+S" if sys.platform == "darwin" else "Ctrl+Alt+S"


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1280, 840)
        self.setMinimumSize(1024, 700)

        self.views: dict[str, QWidget] = {
            "reader": ReaderView(),
            "convert": ConvertView(),
            "split": SplitView(),
            "organize": OrganizeView(),
            "compress": CompressView(),
            "tools": ToolsView(),
        }
        nav = (
            ("reader", "Lire et annoter"), ("convert", "Convertir"), ("split", "Découper un PDF"), ("organize", "Organiser les pages"),
            ("compress", "Compresser"), ("tools", "Outils PDF"),
        )

        root = QWidget()
        row = QHBoxLayout(root)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        self.sidebar = self._build_sidebar(nav)
        row.addWidget(self.sidebar)
        row.addWidget(self._build_sidebar_handle())

        self.stack = QStackedWidget()
        self.stack.setObjectName("content")
        for view in self.views.values():
            page = QWidget()
            page.setObjectName("content")
            margins = QVBoxLayout(page)
            margins.setContentsMargins(6, 24, 28, 24)  # la poignée de la barre latérale fait la marge gauche
            margins.addWidget(view)
            self.stack.addWidget(page)
        row.addWidget(self.stack, 1)
        self.setCentralWidget(root)
        self.show_view("reader")

        self._sidebar_anim = QVariantAnimation(self)
        self._sidebar_anim.setDuration(SIDEBAR_ANIMATION_MS)
        self._sidebar_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._sidebar_anim.valueChanged.connect(lambda width: self.sidebar.setFixedWidth(int(width)))
        self._sidebar_anim.finished.connect(self._sidebar_settled)
        shortcut = QShortcut(QKeySequence(SIDEBAR_SHORTCUT), self)
        shortcut.activated.connect(self.toggle_sidebar)
        self.set_sidebar_visible(settings().value("sidebar/visible", True, bool), animated=False)

        # « Ouvrir avec DocFlex » / double-clic dans le Finder : macOS envoie un événement FileOpen.
        QApplication.instance().installEventFilter(self)

    def eventFilter(self, obj, event) -> bool:
        if event.type() == QEvent.FileOpen:
            path = Path(event.file())
            # macOS transmet aussi le script lancé (« python main.py ») : ce n'est pas un document.
            if sys.argv and path.resolve() != Path(sys.argv[0]).resolve():
                self.open_path(path)
            return True
        return super().eventFilter(obj, event)

    def open_path(self, path: Path) -> None:
        """Un PDF s'ouvre dans le lecteur ; tout autre fichier est proposé à la conversion."""
        if not path.is_file():
            return  # dossiers et arguments divers (macOS transmet aussi la ligne de commande)
        if path.suffix.lower() == ".pdf":
            self.show_view("reader")
            self.views["reader"].open(path)
        else:
            self.show_view("convert")
            self.views["convert"].files.add([path])

    # ------------------------------------------------------- barre latérale

    @property
    def sidebar_visible(self) -> bool:
        return self._sidebar_target > 0

    def toggle_sidebar(self) -> None:
        self.set_sidebar_visible(not self.sidebar_visible)

    def set_sidebar_visible(self, visible: bool, animated: bool = True) -> None:
        self._sidebar_target = SIDEBAR_WIDTH if visible else 0
        keys = QKeySequence(SIDEBAR_SHORTCUT).toString(QKeySequence.NativeText)
        self.sidebar_handle.setText("‹" if visible else "›")
        self.sidebar_handle.setToolTip(f"{'Masquer' if visible else 'Afficher'} le menu ({keys})")
        settings().setValue("sidebar/visible", visible)
        self._sidebar_anim.stop()
        if animated:
            self._sidebar_anim.setStartValue(0 if self.sidebar.isHidden() else self.sidebar.width())
            self._sidebar_anim.setEndValue(self._sidebar_target)
            self.sidebar.show()  # visible pendant l'animation, masqué une fois replié
            self._sidebar_anim.start()
        else:
            self.sidebar.setFixedWidth(self._sidebar_target)
            self._sidebar_settled()

    def _sidebar_settled(self) -> None:
        # Replié : masqué pour de bon, afin que Tab ne s'arrête plus sur ses boutons.
        self.sidebar.setVisible(self._sidebar_target > 0)

    def _build_sidebar_handle(self) -> QWidget:
        strip = QWidget()
        strip.setObjectName("content")
        strip.setFixedWidth(42)
        box = QVBoxLayout(strip)
        box.setContentsMargins(8, 24, 4, 0)
        self.sidebar_handle = button("‹", "square", self.toggle_sidebar)  # même style que les boutons texte
        self.sidebar_handle.setFixedSize(30, 30)
        box.addWidget(self.sidebar_handle, alignment=Qt.AlignTop)
        return strip

    def _build_sidebar(self, nav) -> QFrame:
        bar = QFrame()
        bar.setObjectName("sidebar")
        bar.setFixedWidth(SIDEBAR_WIDTH)
        bar.setMinimumWidth(0)
        layout = QVBoxLayout(bar)
        layout.setContentsMargins(14, 24, 14, 18)
        layout.setSpacing(2)

        title = QHBoxLayout()
        title.setSpacing(8)
        logo = label()
        pixmap = QPixmap(str(ICON))
        ratio = self.devicePixelRatioF()
        pixmap = pixmap.scaled(round(36 * ratio), round(36 * ratio), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        pixmap.setDevicePixelRatio(ratio)
        logo.setPixmap(pixmap)
        title.addWidget(logo)
        brand = label(APP_NAME)
        brand.setObjectName("brand")
        title.addWidget(brand)
        title.addStretch()
        layout.addLayout(title)
        layout.addWidget(label(f"Documents hors ligne · v{__version__}", "muted"))
        layout.addSpacing(22)

        self.nav_buttons: dict[str, object] = {}
        group = QButtonGroup(bar)
        for key, text in nav:
            btn = button(text, on_click=lambda k=key: self.show_view(k))
            btn.setObjectName("navButton")
            btn.setCheckable(True)
            group.addButton(btn)
            self.nav_buttons[key] = btn
            layout.addWidget(btn)
        layout.addStretch()

        lo = office.find_soffice()
        status = label("● LibreOffice détecté" if lo else "○ LibreOffice non installé", "ok" if lo else "muted")
        status.setToolTip(lo or "Installez LibreOffice (gratuit) pour convertir Word, Excel et PowerPoint en PDF.")
        layout.addWidget(status)
        layout.addSpacing(12)
        layout.addWidget(label("Apparence", "muted"))
        appearance = combo(APPEARANCES, width=196)
        saved = settings().value("appearance", "system")
        appearance.setCurrentIndex(max(0, appearance.findData(saved)))
        theme.current().set_mode(appearance.currentData())
        appearance.currentIndexChanged.connect(lambda _: self._set_appearance(appearance.currentData()))
        layout.addWidget(appearance)
        return bar

    def _set_appearance(self, mode: str) -> None:
        theme.current().set_mode(mode)
        settings().setValue("appearance", mode)

    def closeEvent(self, event) -> None:
        if not self.views["reader"].confirm_close():
            event.ignore()
            return
        organizer = self.views["organize"]
        if organizer.dirty and not organizer.confirm_discard():
            event.ignore()
            return
        busy = [panel for panel in self._panels() if panel.busy]
        if busy and not ask(self, "Opération en cours", "Un traitement est en cours. L'interrompre et quitter ?",
                            "Quitter"):
            event.ignore()
            return
        for view in self.views.values():
            if hasattr(view, "shutdown"):
                view.shutdown()
        for panel in self._panels():
            panel.shutdown()
        event.accept()

    def _panels(self) -> list:
        """Toutes les barres d'action : une par vue, plusieurs pour « Outils PDF »."""
        panels = []
        for view in self.views.values():
            panels += view.panels if hasattr(view, "panels") else [view.panel]
        return panels

    def show_view(self, key: str) -> None:
        self.stack.setCurrentIndex(list(self.views).index(key))
        self.nav_buttons[key].setChecked(True)


def create_app(argv: list[str] | None = None) -> QApplication:
    app = QApplication.instance() or QApplication(argv or sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setWindowIcon(QIcon(str(ICON)))  # fenêtre, barre des tâches, Dock (y compris en développement)
    theme.install(app)
    return app


def run() -> None:
    app = create_app()
    window = MainWindow()
    window.show()
    if sys.platform != "darwin":  # macOS envoie déjà les fichiers de la ligne de commande en FileOpen
        for arg in sys.argv[1:]:  # « DocFlex.exe fichier.pdf » (Windows : Ouvrir avec)
            window.open_path(Path(arg))
    sys.exit(app.exec())
