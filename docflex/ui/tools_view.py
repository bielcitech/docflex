"""Vue « Outils PDF » : une grille de cartes, puis l'outil choisi."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLineEdit, QSizePolicy, QSpinBox, QStackedWidget,
    QVBoxLayout, QWidget,
)

from docflex.core import pdf_tools
from docflex.core.tasks import Cancelled, Progress, Report
from docflex.ui.tool_page import FileJob, ToolPage, dest_dir
from docflex.ui.tools_extra import BlankPagesPage, ComparePage, FlattenPage, RedactPage, SignPage
from docflex.ui.widgets import SegmentedControl, TaskPanel, ViewHeader, button, combo, label
from docflex.utils.files import unique_path

# --------------------------------------------------------------- fusion

class MergePage(ToolPage):
    run_label = "Fusionner…"
    intro = "Les PDF sont assemblés dans l'ordre de la liste : utilisez ▲▼ pour le modifier."
    has_output = False
    reorderable = True

    def build_settings(self) -> None:
        self.bookmarks = self.row("", QCheckBox("Ajouter un signet par fichier (les signets d'origine y sont rangés)"))
        self.bookmarks.setChecked(True)

    def problem(self) -> str | None:
        if len(self.files.paths) < 2:
            return "Ajoutez au moins deux PDF." if self.files.paths else ""
        return None

    def default_output(self) -> Path:
        first = self.files.paths[0]
        return unique_path(first.with_name(f"{first.stem} (fusion).pdf"))

    def start(self) -> None:
        chosen, _ = QFileDialog.getSaveFileName(self, "Enregistrer le PDF fusionné", str(self.default_output()),
                                                "PDF (*.pdf)")
        if chosen:
            self.merge_to(Path(chosen).with_suffix(".pdf"))

    def merge_to(self, out: Path) -> None:
        paths, bookmarks = list(self.files.paths), self.bookmarks.isChecked()

        def work(progress) -> Report:
            try:
                return Report(outputs=[pdf_tools.merge_pdfs(paths, out, bookmarks, progress)])
            except Cancelled:
                raise
            except Exception as exc:
                return Report(errors=[(out, str(exc) or exc.__class__.__name__)])

        self.panel.start(work)


# ------------------------------------------------------------ filigrane

class WatermarkPage(ToolPage):
    run_label = "Ajouter le filigrane"
    intro = "Le filigrane est ajouté par-dessus le contenu, en transparence. Résultat : « nom_filigrane.pdf »."

    def build_settings(self) -> None:
        self.kind = self.row("Type", SegmentedControl((("Texte", "text"), ("Image / logo", "image"))))
        self.kind.set_value("text")
        self.kind.changed.connect(lambda _: self._on_kind())

        self.text = self.row("Texte", QLineEdit("CONFIDENTIEL"))
        self.text.setFixedWidth(280)
        self.text.textChanged.connect(lambda _: self.refresh())
        self.layout_menu = self.row("Disposition", combo((("En diagonale", True), ("À l'horizontale", False))))
        self.color = self.row("Couleur", combo((
            ("Gris", (0.45, 0.45, 0.45)), ("Rouge", (0.8, 0.1, 0.1)), ("Bleu", (0.15, 0.3, 0.8)),
        )))

        self.image_path: Path | None = None
        picker = QWidget()
        box = QHBoxLayout(picker)
        box.setContentsMargins(0, 0, 0, 0)
        box.addWidget(button("Choisir une image…", on_click=self._choose_image))
        self.image_label = label("Aucune image choisie (PNG ou JPG)", "muted")
        self.image_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        box.addWidget(self.image_label, 1)
        self.picker = self.row("Image", picker)
        self.scale = self.row("Taille", combo((
            ("Petite (15 % de la largeur)", 0.15), ("Moyenne (30 %)", 0.3), ("Grande (50 %)", 0.5),
        ), 1))
        self.position = self.row("Position", combo((v, k) for k, v in pdf_tools.IMAGE_POSITIONS.items()))

        self.opacity = self.row("Intensité", combo((
            ("Discrète", 0.12), ("Moyenne", 0.25), ("Marquée", 0.45), ("Opaque", 1.0),
        ), 1))
        self.pages = self.row("Pages", QLineEdit())
        self.pages.setPlaceholderText("Toutes — ou par exemple 1-3, 5")
        self.pages.setFixedWidth(280)
        self._on_kind()

    @property
    def is_image(self) -> bool:
        return self.kind.value() == "image"

    def _on_kind(self) -> None:
        for widget in (self.text, self.layout_menu, self.color):
            self.show_row(widget, not self.is_image)
        for widget in (self.picker, self.scale, self.position):
            self.show_row(widget, self.is_image)
        if hasattr(self, "panel"):  # pas encore créé lors de la construction des réglages
            self.refresh()

    def _choose_image(self) -> None:
        chosen, _ = QFileDialog.getOpenFileName(self, "Image à apposer", "", "Images (*.png *.jpg *.jpeg *.webp)")
        if chosen:
            self.set_image(Path(chosen))

    def set_image(self, path: Path) -> None:
        self.image_path = path
        self.image_label.setText(path.name)
        self.image_label.setToolTip(str(path))
        self.refresh()

    def problem(self) -> str | None:
        if self.files.paths and self.is_image and self.image_path is None:
            return "Choisissez l'image à apposer (un logo PNG transparent rend le mieux)."
        if self.files.paths and not self.is_image and not self.text.text().strip():
            return "Saisissez le texte du filigrane."
        return super().problem()

    def job(self) -> FileJob:
        if self.is_image:
            mark = pdf_tools.ImageMark(self.image_path, self.scale.currentData(), self.position.currentData(),
                                       self.opacity.currentData(), self.pages.text())
            return lambda path, out_dir, prog: (
                [pdf_tools.image_watermark_pdf(path, dest_dir(path, out_dir), mark, prog)], None)
        mark = pdf_tools.Watermark(
            self.text.text(), self.layout_menu.currentData(), self.opacity.currentData(),
            self.color.currentData(), self.pages.text(),
        )
        return lambda path, out_dir, prog: ([pdf_tools.watermark_pdf(path, dest_dir(path, out_dir), mark, prog)], None)


# --------------------------------------------------------- numérotation

class NumberingPage(ToolPage):
    run_label = "Numéroter"
    intro = "Les numéros sont ajoutés sur chaque page. Résultat : « nom_numéroté.pdf »."

    def build_settings(self) -> None:
        self.template = self.row("Format", combo((v, k) for k, v in pdf_tools.NUMBER_FORMATS.items()))
        self.position = self.row("Position", combo((v, k) for k, v in pdf_tools.POSITIONS.items()))
        self.start_at = self.row("Premier numéro", QSpinBox())
        self.start_at.setRange(0, 99999)
        self.start_at.setValue(1)
        self.start_at.setFixedWidth(120)
        self.size = self.row("Taille", combo((("Petite", 8), ("Normale", 10), ("Grande", 12)), 1))
        self.skip_first = self.row("", QCheckBox("Ne pas numéroter la première page (couverture)"))

    def job(self) -> FileJob:
        numbering = pdf_tools.Numbering(
            self.template.currentData(), self.position.currentData(), self.start_at.value(),
            self.skip_first.isChecked(), self.size.currentData(),
        )
        return lambda path, out_dir, prog: ([pdf_tools.number_pages(path, dest_dir(path, out_dir), numbering, prog)], None)


# ---------------------------------------------------------- mot de passe

class PasswordPage(ToolPage):
    run_label = "Protéger"
    intro = "Chiffrement AES-256. Conservez le mot de passe : sans lui, le fichier protégé est illisible."

    def build_settings(self) -> None:
        self.mode = self.row("Action", SegmentedControl((("Protéger", "protect"), ("Retirer la protection", "remove"))))
        self.mode.set_value("protect")
        self.mode.changed.connect(lambda _: self._on_mode())
        self.password = self.row("Mot de passe", QLineEdit())
        self.confirm_label = label("Confirmation", "field")
        self.grid.addWidget(self.confirm_label, self.grid.rowCount(), 0)
        self.confirm = QLineEdit()
        self.grid.addWidget(self.confirm, self.grid.rowCount() - 1, 1)
        for field in (self.password, self.confirm):
            field.setEchoMode(QLineEdit.Password)
            field.setFixedWidth(280)
            field.textChanged.connect(lambda _: self.refresh())
        self.show_password = self.row("", QCheckBox("Afficher le mot de passe"))
        self.show_password.toggled.connect(lambda shown: [
            f.setEchoMode(QLineEdit.Normal if shown else QLineEdit.Password) for f in (self.password, self.confirm)
        ])
        self.allow_print = QCheckBox("Impression")
        self.allow_print.setChecked(True)
        self.allow_copy = QCheckBox("Copie du texte")
        self.allow_modify = QCheckBox("Modification")
        rights = QWidget()
        box = QGridLayout(rights)
        box.setContentsMargins(0, 0, 0, 0)
        for i, widget in enumerate((self.allow_print, self.allow_copy, self.allow_modify)):
            box.addWidget(widget, 0, i)
        box.setColumnStretch(3, 1)
        self.rights_label = label("Autoriser", "field")
        r = self.grid.rowCount()
        self.grid.addWidget(self.rights_label, r, 0)
        self.grid.addWidget(rights, r, 1)
        self.rights = rights
        self.mode_hint = self.row("", label("", "muted", wrap=True))
        self._on_mode()

    @property
    def protecting(self) -> bool:
        return self.mode.value() == "protect"

    def _on_mode(self) -> None:
        for widget in (self.confirm_label, self.confirm, self.rights_label, self.rights):
            widget.setVisible(self.protecting)
        self.mode_hint.setText(
            "Le PDF ne s'ouvrira qu'avec ce mot de passe. Les droits non cochés seront refusés par les lecteurs PDF."
            if self.protecting else
            "Saisissez le mot de passe d'ouverture actuel : une copie sans protection sera créée "
            "(« nom_déverrouillé.pdf »)."
        )
        if hasattr(self, "panel"):  # pas encore créé lors de la construction des réglages
            self.panel.run_btn.setText("Protéger" if self.protecting else "Retirer la protection")
            self.refresh()

    def problem(self) -> str | None:
        if not self.files.paths:
            return ""
        if not self.password.text():
            return "Saisissez le mot de passe."
        if self.protecting and self.password.text() != self.confirm.text():
            return "Les deux mots de passe ne correspondent pas."
        return None

    def refresh(self) -> None:
        problem = self.problem()
        weak = problem is None and self.protecting and len(self.password.text()) < 8
        self.panel.set_ready(problem is None, "Conseil : 8 caractères ou plus." if weak else problem or "")

    def job(self) -> FileJob:
        password = self.password.text()
        if not self.protecting:
            return lambda path, out_dir, prog: (
                [pdf_tools.unprotect_pdf(path, dest_dir(path, out_dir), password, prog)], None)
        permissions = pdf_tools.Permissions(
            self.allow_print.isChecked(), self.allow_copy.isChecked(), self.allow_modify.isChecked())
        return lambda path, out_dir, prog: (
            [pdf_tools.protect_pdf(path, dest_dir(path, out_dir), password, permissions, prog)], None)


# ------------------------------------------------------------------- OCR

class OcrPage(ToolPage):
    run_label = "Reconnaître le texte"
    intro = ("Rend les PDF numérisés sélectionnables et cherchables. L'aspect des pages ne change pas. "
             "Compter quelques secondes par page.")

    def build_settings(self) -> None:
        self.language = self.row("Langue", combo((v, k) for k, v in pdf_tools.OCR_LANGUAGES.items()))
        self.quality = self.row("Précision", combo((("Rapide (200 ppp)", 200), ("Précise (300 ppp)", 300)), 1))
        self.skip_text = self.row("", QCheckBox("Ignorer les pages qui contiennent déjà du texte"))
        self.skip_text.setChecked(True)

    def job(self) -> FileJob:
        language, dpi, skip = self.language.currentData(), self.quality.currentData(), self.skip_text.isChecked()

        def run(path: Path, out_dir: Path | None, prog: Progress):
            out, pages = pdf_tools.ocr_pdf(path, dest_dir(path, out_dir), language, dpi, skip, prog)
            return [out], f"{path.name} : {pages} page{'s' if pages > 1 else ''} reconnue{'s' if pages > 1 else ''}"

        return run


# ----------------------------------------------------- extraction d'images

class ImagesPage(ToolPage):
    run_label = "Extraire les images"
    intro = ("Chaque image est enregistrée une seule fois dans un dossier « nom_images ». "
             "Les JPEG sont copiés sans perte, les autres formats convertis en PNG.")

    def build_settings(self) -> None:
        self.skip_small = self.row("", QCheckBox("Ignorer les très petites images (puces, icônes, filets)"))
        self.skip_small.setChecked(True)

    def job(self) -> FileJob:
        skip = self.skip_small.isChecked()

        def run(path: Path, out_dir: Path | None, prog: Progress):
            outputs = pdf_tools.extract_images(path, dest_dir(path, out_dir), skip, prog)
            n = len(outputs)
            return outputs, f"{path.name} : {n} image{'s' if n > 1 else ''} extraite{'s' if n > 1 else ''}"

        return run


# -------------------------------------------------------------- métadonnées

class MetadataPage(ToolPage):
    run_label = "Enregistrer"
    intro = ("Un seul PDF : ses informations s'affichent et se modifient. Plusieurs PDF : seuls les champs "
             "remplis sont appliqués à tous. Résultat : « nom_métadonnées.pdf ».")

    def build_settings(self) -> None:
        self.fields: dict[str, QLineEdit] = {}
        for key, text in pdf_tools.METADATA_FIELDS.items():
            field = self.row(text, QLineEdit())
            field.setFixedWidth(420)
            self.fields[key] = field
        self.wipe = self.row("", QCheckBox(
            "Effacer toutes les informations d'origine (auteur, dates, logiciel…) avant de partager"))
        # Ce qui est affiché est ce qui sera enregistré : cocher vide les champs, décocher les remplit à nouveau.
        self.wipe.toggled.connect(lambda _: self.refresh())

    @property
    def single(self) -> bool:
        return len(self.files.paths) == 1

    def refresh(self) -> None:
        # Pré-remplissage à chaque changement de la liste de fichiers.
        values = {}
        if self.single and not self.wipe.isChecked():
            try:
                values = pdf_tools.read_metadata(self.files.paths[0])
            except Exception as exc:
                self.panel.set_ready(False, f"{self.files.paths[0].name} : {exc}")
                return
        for key, field in self.fields.items():
            field.setText(values.get(key, ""))
            field.setPlaceholderText("" if self.single or not self.files.paths else "inchangé")
        super().refresh()

    def job(self) -> FileJob:
        if self.single:
            values: dict[str, str | None] = {k: f.text() for k, f in self.fields.items()}
        else:
            values = {k: f.text() or None for k, f in self.fields.items()}
        wipe = self.wipe.isChecked()
        return lambda path, out_dir, prog: (
            [pdf_tools.edit_metadata(path, dest_dir(path, out_dir), values, wipe, prog)], None)


# ------------------------------------------------------------------ vue

# (clé, titre, description de la carte, page)
TOOLS = (
    ("merge", "Fusionner", "Assembler plusieurs PDF en un seul, dans l'ordre voulu", MergePage),
    ("watermark", "Filigrane", "Apposer un texte ou un logo en transparence", WatermarkPage),
    ("numbering", "Numéroter", "Ajouter les numéros de page", NumberingPage),
    ("sign", "Signer", "Placer l'image de votre signature sur une page", SignPage),
    ("password", "Mot de passe", "Protéger un PDF, ou retirer sa protection", PasswordPage),
    ("redact", "Caviarder", "Supprimer définitivement noms, e-mails, IBAN…", RedactPage),
    ("ocr", "OCR", "Rendre un PDF numérisé cherchable et sélectionnable", OcrPage),
    ("compare", "Comparer", "Surligner les différences entre deux versions", ComparePage),
    ("blank", "Pages blanches", "Retirer les pages vides d'un scan", BlankPagesPage),
    ("images", "Extraire les images", "Enregistrer toutes les images d'un PDF", ImagesPage),
    ("metadata", "Métadonnées", "Modifier ou effacer titre, auteur, dates…", MetadataPage),
    ("flatten", "Aplatir", "Figer formulaires et annotations dans les pages", FlattenPage),
)
COLUMNS = 3


class ToolCard(QFrame):
    """Carte cliquable de la grille d'accueil."""

    def __init__(self, title: str, description: str, on_click) -> None:
        super().__init__()
        self.setObjectName("toolCard")
        self.setAttribute(Qt.WA_Hover)  # nécessaire pour le style :hover d'un QFrame
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self._on_click = on_click
        box = QVBoxLayout(self)
        box.setContentsMargins(16, 14, 16, 14)
        box.setSpacing(4)
        name = label(title)
        name.setStyleSheet("font-size: 15px; font-weight: 600;")
        box.addWidget(name)
        box.addWidget(label(description, "muted", wrap=True))
        box.addStretch()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            self._on_click()

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self._on_click()
        else:
            super().keyPressEvent(event)


class ToolsView(QWidget):
    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        self.header = ViewHeader("Outils PDF", "Choisissez un outil. Tout est traité sur cet ordinateur.")
        layout.addWidget(self.header)

        # Barre de retour, visible dans un outil.
        self.back_bar = QWidget()
        bar = QHBoxLayout(self.back_bar)
        bar.setContentsMargins(0, 0, 0, 0)
        bar.addWidget(button("← Tous les outils", "icon", self.show_index))
        self.tool_title = label("", "field")
        self.tool_title.setStyleSheet("font-size: 16px;")
        bar.addWidget(self.tool_title)
        self.tool_hint = label("", "muted")
        bar.addWidget(self.tool_hint, 1)
        layout.addWidget(self.back_bar)

        self.stack = QStackedWidget()
        index = QWidget()
        grid = QGridLayout(index)
        grid.setContentsMargins(0, 4, 0, 0)
        grid.setSpacing(12)
        self.pages: dict[str, QWidget] = {}
        self._titles: dict[str, tuple[str, str]] = {}
        for i, (key, title, description, page_class) in enumerate(TOOLS):
            grid.addWidget(ToolCard(title, description, lambda k=key: self.show_tool(k)), i // COLUMNS, i % COLUMNS)
            self.pages[key] = page_class()
            self._titles[key] = (title, description)
            self.stack.addWidget(self.pages[key])
        grid.setRowStretch(len(TOOLS) // COLUMNS + 1, 1)
        self.index = index
        self.stack.insertWidget(0, index)
        layout.addWidget(self.stack, 1)
        self.show_index()

    @property
    def panels(self) -> list[TaskPanel]:
        return [page.panel for page in self.pages.values()]

    def show_index(self) -> None:
        self.stack.setCurrentWidget(self.index)
        self.back_bar.hide()
        self.header.show()

    def show_tool(self, key: str) -> None:
        title, description = self._titles[key]
        self.tool_title.setText(title)
        self.tool_hint.setText(f"— {description}")
        self.header.hide()
        self.back_bar.show()
        self.stack.setCurrentWidget(self.pages[key])

    def shutdown(self) -> None:
        for panel in self.panels:
            panel.shutdown()
