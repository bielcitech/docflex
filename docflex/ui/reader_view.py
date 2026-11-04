"""Vue « Lire et annoter » : accueil et documents ouverts, un onglet par document."""

from __future__ import annotations

import sys
from pathlib import Path

import pymupdf
from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QFileDialog, QHBoxLayout, QInputDialog, QLineEdit, QPushButton, QStackedWidget, QTabBar, QVBoxLayout, QWidget,
)

from docflex.core.recent import RecentStore
from docflex.ui import theme
from docflex.ui.document_tab import DocumentTab
from docflex.ui.icons import icon
from docflex.ui.reader_home import ReaderHome
from docflex.ui.settings import data_dir, settings
from docflex.ui.widgets import button

PASSWORD_TRIES = 3
TAB_MAX_WIDTH = 240
BARS_SHORTCUT = "Meta+Ctrl+T" if sys.platform == "darwin" else "Ctrl+Alt+T"  # ⌃⌘T sur Mac
if sys.platform == "darwin":  # ⌃Tab comme dans Safari, et ⌘⇧] / ⌘⇧[
    NEXT_TAB, PREVIOUS_TAB = ("Meta+Tab", "Ctrl+}"), ("Meta+Shift+Tab", "Meta+Shift+Backtab", "Ctrl+{")
else:
    NEXT_TAB, PREVIOUS_TAB = ("Ctrl+Tab",), ("Ctrl+Shift+Tab", "Ctrl+Shift+Backtab")


class TabBar(QTabBar):
    """Onglets de largeur bornée : les noms longs sont raccourcis au milieu."""

    def tabSizeHint(self, index: int) -> QSize:  # noqa: N802 (nom Qt)
        size = super().tabSizeHint(index)
        return QSize(min(size.width(), TAB_MAX_WIDTH), size.height())


class ReaderView(QWidget):
    panels: list = []  # pas de traitement en arrière-plan ici

    def __init__(self) -> None:
        super().__init__()
        self.recent = RecentStore(data_dir() / "recent.json")
        self.home = ReaderHome(self.recent, data_dir() / "thumbnails")
        self.home.open_requested.connect(self.open)
        self.home.browse_requested.connect(self.browse)
        self.bars_visible = settings().value("reader/toolbars", True, bool)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        self.strip = QWidget()
        strip = QHBoxLayout(self.strip)
        strip.setContentsMargins(0, 0, 0, 0)
        strip.setSpacing(6)
        self.tabs = TabBar()
        self.tabs.setObjectName("documentTabs")
        self.tabs.setDocumentMode(True)
        self.tabs.setDrawBase(False)
        self.tabs.setExpanding(False)
        self.tabs.setElideMode(Qt.ElideMiddle)
        self.tabs.setUsesScrollButtons(True)
        self.tabs.addTab("")  # accueil : toujours le premier, sans bouton de fermeture
        self.tabs.setTabToolTip(0, "Accueil : documents récents et favoris")
        self.tabs.currentChanged.connect(self._on_tab)
        strip.addWidget(self.tabs)
        self.new_btn = self._strip_button("add", f"Ouvrir un PDF ({self._native(QKeySequence.Open)})", self.browse)
        strip.addWidget(self.new_btn)
        strip.addStretch(1)
        self.bars_btn = self._strip_button("toolbars", "", self.toggle_bars)
        self.bars_btn.setCheckable(True)
        strip.addWidget(self.bars_btn)
        layout.addWidget(self.strip)

        self.pages = QStackedWidget()
        self.pages.addWidget(self.home)
        layout.addWidget(self.pages, 1)

        for keys, action in (
            ((QKeySequence.Close,), self.close_current),
            ((QKeySequence.Open,), self.browse),
            (NEXT_TAB, lambda: self._step_tab(1)),
            (PREVIOUS_TAB, lambda: self._step_tab(-1)),
            ((BARS_SHORTCUT,), self.toggle_bars),
        ):
            for key in keys:
                shortcut = QShortcut(QKeySequence(key), self)
                shortcut.setContext(Qt.WidgetWithChildrenShortcut)
                shortcut.activated.connect(action)

        theme.current().changed.connect(self._paint_icons)
        self._paint_icons()
        self._show_bars_state()
        self._sync()

    @staticmethod
    def _native(seq) -> str:
        return QKeySequence(seq).toString(QKeySequence.NativeText)

    def _strip_button(self, name: str, tip: str, on_click) -> QPushButton:
        btn = button("", "tool", on_click)
        btn.setFixedSize(30, 30)
        btn.setToolTip(tip)
        btn.setProperty("icon_name", name)
        return btn

    def _paint_icons(self) -> None:
        t = theme.current()
        self.tabs.setTabIcon(0, icon("home", t.color("text")))
        for btn in self.strip.findChildren(QPushButton):  # boutons de la bande d'onglets
            name = btn.property("icon_name")
            if name:
                btn.setIcon(icon(name, t.color("muted") if name == "close" else t.color("text")))

    # ------------------------------------------------- onglets

    @property
    def documents(self) -> list[DocumentTab]:
        return [self.tabs.tabData(i) for i in range(1, self.tabs.count())]

    @property
    def current(self) -> DocumentTab | None:
        return self.tabs.tabData(self.tabs.currentIndex()) if self.tabs.currentIndex() > 0 else None

    @property
    def doc(self) -> pymupdf.Document | None:
        return self.current.doc if self.current else None

    @property
    def path(self) -> Path | None:
        return self.current.path if self.current else None

    @property
    def dirty(self) -> bool:
        return any(tab.dirty for tab in self.documents)

    @dirty.setter
    def dirty(self, value: bool) -> None:
        for tab in self.documents:
            tab.dirty = value

    def show_home(self) -> None:
        self.tabs.setCurrentIndex(0)

    def _index_of(self, tab: DocumentTab) -> int:
        return next(i for i in range(1, self.tabs.count()) if self.tabs.tabData(i) is tab)

    def _on_tab(self, index: int) -> None:
        tab = self.tabs.tabData(index) if index > 0 else None
        self.pages.setCurrentWidget(tab or self.home)
        if tab is None:
            self.home.refresh()
        else:
            tab.canvas.setFocus()
        self._sync()

    def _step_tab(self, step: int) -> None:
        if self.tabs.count() > 1:
            self.tabs.setCurrentIndex((self.tabs.currentIndex() + step) % self.tabs.count())

    def _add_tab(self, tab: DocumentTab) -> None:
        index = self.tabs.addTab(tab.title)
        self.tabs.setTabData(index, tab)
        close = self._strip_button("close", f"Fermer ({self._native(QKeySequence.Close)})",
                                   lambda: self.close_tab(tab))
        close.setObjectName("tabClose")
        close.setFixedSize(18, 18)
        close.setIconSize(QSize(14, 14))
        holder = QWidget(self.tabs)  # Qt colle le bouton au bord de l'onglet : on lui laisse de l'air
        box = QHBoxLayout(holder)
        box.setContentsMargins(2, 0, 6, 0)
        box.addWidget(close)
        self.tabs.setTabButton(index, QTabBar.RightSide, holder)
        self.pages.addWidget(tab)
        tab.state_changed.connect(lambda: self._on_state(tab))
        tab.open_requested.connect(self.open)
        tab.close_requested.connect(lambda: self.close_tab(tab))
        tab.bars_requested.connect(lambda: self.set_bars_visible(True))
        tab.set_bars_visible(self.bars_visible, animated=False)
        self._paint_icons()

    def _on_state(self, tab: DocumentTab) -> None:
        index = self._index_of(tab)
        self.tabs.setTabText(index, ("• " if tab.dirty else "") + tab.title)  # point : modifications en cours
        self.tabs.setTabToolTip(index, f"{tab.path}\n{tab.summary()}")
        self._sync()

    def _sync(self) -> None:
        """Onglets visibles dès qu'un document est ouvert ; titre de la fenêtre selon l'onglet courant."""
        self.strip.setVisible(self.tabs.count() > 1)
        window = self.window()
        if window is None or window is self:
            return
        tab = self.current
        window.setWindowTitle(f"{tab.title}[*] — DocFlex" if tab else "DocFlex")
        window.setWindowModified(bool(tab and tab.dirty))  # point dans le bouton de fermeture (Mac) ou « * »

    # ------------------------------------------------- barres d'outils

    def toggle_bars(self) -> None:
        self.set_bars_visible(not self.bars_visible)

    def set_bars_visible(self, visible: bool) -> None:
        self.bars_visible = visible
        settings().setValue("reader/toolbars", visible)
        for tab in self.documents:
            tab.set_bars_visible(visible, animated=tab is self.current)
        self._show_bars_state()

    def _show_bars_state(self) -> None:
        self.bars_btn.setChecked(self.bars_visible)
        self.bars_btn.setToolTip(f"{'Masquer' if self.bars_visible else 'Afficher'} les barres d'outils "
                                 f"({QKeySequence(BARS_SHORTCUT).toString(QKeySequence.NativeText)})")

    # ------------------------------------------------- documents

    def browse(self) -> None:
        chosen, _ = QFileDialog.getOpenFileNames(self, "Ouvrir des PDF", str(self.path.parent if self.path else ""),
                                                 "PDF (*.pdf)")
        for path in chosen:
            self.open(Path(path))

    def ask_password(self, name: str, retry: bool) -> str | None:
        """Mot de passe d'ouverture ; None si l'utilisateur renonce (remplacé dans les tests)."""
        text, ok = QInputDialog.getText(
            self, "PDF protégé",
            ("Mot de passe incorrect. " if retry else "") + f"« {name} » est protégé. Mot de passe :",
            QLineEdit.Password,
        )
        return text if ok else None

    def open(self, path: Path) -> bool:
        """Ouvre `path` dans un nouvel onglet, ou revient à son onglet s'il est déjà ouvert."""
        for tab in self.documents:
            if tab.path is not None and tab.path.resolve() == path.resolve():
                self.tabs.setCurrentIndex(self._index_of(tab))
                return True
        try:
            doc = pymupdf.open(path)
        except Exception as exc:
            self._fail(f"{path.name} : impossible d'ouvrir ce fichier ({exc}).")
            return False
        if not doc.is_pdf:
            doc.close()
            self._fail(f"{path.name} n'est pas un PDF.")
            return False
        password = None
        if doc.needs_pass:
            for attempt in range(PASSWORD_TRIES):
                password = self.ask_password(path.name, retry=attempt > 0)
                if password is None:
                    doc.close()
                    self._fail("")
                    return False
                if doc.authenticate(password):
                    break
            else:
                doc.close()
                self._fail(f"{path.name} : mot de passe incorrect.")
                return False

        tab = DocumentTab(self.recent)
        self._add_tab(tab)
        self.tabs.setCurrentIndex(self._index_of(tab))  # visible avant le chargement : zoom à la bonne largeur
        tab.load(doc, path, password)
        self.home.set_status("")
        return True

    def _fail(self, message: str) -> None:
        if self.current is None:
            self.home.set_status(message)
        else:
            self.current.status.setText(message)

    def close_tab(self, tab: DocumentTab, confirm: bool = True) -> bool:
        if confirm:
            self.tabs.setCurrentIndex(self._index_of(tab))  # on montre le document concerné
            if not tab.confirm_close():
                return False
        path = tab.path
        tab.release()
        self.tabs.removeTab(self._index_of(tab))
        self.pages.removeWidget(tab)
        tab.deleteLater()
        if path is not None:
            self.home.forget_thumbnail(path)  # annotations : miniature à refaire
        if self.current is None:
            self.home.refresh()
        self._sync()
        return True

    def close_current(self) -> None:
        if self.current is not None:
            self.close_tab(self.current)

    def close_document(self) -> None:
        """Ferme l'onglet courant sans question (les modifications non enregistrées sont perdues)."""
        if self.current is not None:
            self.close_tab(self.current, confirm=False)

    def confirm_close(self) -> bool:
        """True si tous les documents peuvent être fermés (enregistrés, ou modifications abandonnées)."""
        for tab in self.documents:
            if tab.dirty:
                self.tabs.setCurrentIndex(self._index_of(tab))
                if not tab.confirm_close():
                    return False
        return True

    def shutdown(self) -> None:
        for tab in self.documents:
            self.close_tab(tab, confirm=False)
        self.home.shutdown()
