"""Outils complémentaires : pages blanches, caviardage, comparaison, aplatissement, signature."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPen
from PySide6.QtWidgets import (
    QBoxLayout, QCheckBox, QFileDialog, QGridLayout, QHBoxLayout, QPlainTextEdit, QSpinBox, QVBoxLayout, QWidget,
)

from docflex.core import pdf_extras
from docflex.core.organize import Box, render_thumbnail
from docflex.core.split import open_pdf
from docflex.core.tasks import Cancelled, Progress, Report
from docflex.ui import theme
from docflex.ui.crop_dialog import CropCanvas
from docflex.ui.tool_page import FileJob, ToolPage, dest_dir
from docflex.ui.widgets import FileList, OutputChooser, TaskPanel, button, card, combo, label


# ------------------------------------------------------------ pages blanches

class BlankPagesPage(ToolPage):
    run_label = "Retirer les pages blanches"
    intro = ("Pratique après une numérisation recto verso. Une page portant du texte n'est jamais retirée. "
             "Résultat : « nom_sans_pages_blanches.pdf ».")

    def build_settings(self) -> None:
        self.sensitivity = self.row("Détection", combo(
            ((text, value) for text, value in pdf_extras.BLANK_SENSITIVITY.values()), width=360))

    def job(self) -> FileJob:
        max_ink = self.sensitivity.currentData()

        def run(path: Path, out_dir: Path | None, prog: Progress):
            out, removed = pdf_extras.remove_blank_pages(path, dest_dir(path, out_dir), max_ink, prog)
            s = "s" if removed > 1 else ""
            return [out], f"{path.name} : {removed} page{s} blanche{s} retirée{s}"

        return run


# ---------------------------------------------------------------- caviardage

class RedactPage(ToolPage):
    run_label = "Caviarder"
    intro = ("Le texte visé est supprimé du fichier produit, pas seulement recouvert : il ne peut plus être "
             "copié ni retrouvé. Pour un document numérisé, faites d'abord un OCR.")

    def build_settings(self) -> None:
        self.terms = self.row("Mots à masquer", QPlainTextEdit())
        self.terms.setPlaceholderText("Un mot ou une expression par ligne — majuscules indifférentes")
        self.terms.setFixedSize(420, 84)
        self.terms.textChanged.connect(self.refresh)
        self.patterns: dict[str, QCheckBox] = {}
        boxes = QWidget()
        grid = QGridLayout(boxes)
        grid.setContentsMargins(0, 0, 0, 0)
        for i, (key, (text, _)) in enumerate(pdf_extras.REDACT_PATTERNS.items()):
            box = QCheckBox(text)
            box.toggled.connect(lambda _: self.refresh())
            self.patterns[key] = box
            grid.addWidget(box, 0, i)
        grid.setColumnStretch(len(self.patterns), 1)
        self.row("Masquer aussi", boxes)

    def term_list(self) -> list[str]:
        return [line.strip() for line in self.terms.toPlainText().splitlines() if line.strip()]

    def problem(self) -> str | None:
        if self.files.paths and not self.term_list() and not any(b.isChecked() for b in self.patterns.values()):
            return "Indiquez des mots à masquer ou cochez un type d'information."
        return super().problem()

    def job(self) -> FileJob:
        terms = self.term_list()
        patterns = [key for key, box in self.patterns.items() if box.isChecked()]

        def run(path: Path, out_dir: Path | None, prog: Progress):
            out, count = pdf_extras.redact_pdf(path, dest_dir(path, out_dir), terms, patterns, prog)
            return [out], f"{path.name} : {count} zone{'s' if count > 1 else ''} caviardée{'s' if count > 1 else ''}"

        return run


# --------------------------------------------------------------- aplatissement

class FlattenPage(ToolPage):
    run_label = "Aplatir"
    intro = ("Les champs de formulaire et les annotations deviennent partie intégrante des pages : "
             "le PDF s'affiche partout de la même façon et n'est plus modifiable. Résultat : « nom_aplati.pdf ».")

    def job(self) -> FileJob:
        return lambda path, out_dir, prog: ([pdf_extras.flatten_pdf(path, dest_dir(path, out_dir), prog)], None)


# ---------------------------------------------------------------- comparaison

class ComparePage(QWidget):
    """Deux documents : version d'origine et nouvelle version."""

    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        pair = QHBoxLayout()
        pair.setSpacing(12)
        self.old = FileList({"pdf"}, "Version d'origine", single=True)
        self.new = FileList({"pdf"}, "Nouvelle version", single=True)
        for title, files in (("Version d'origine", self.old), ("Nouvelle version", self.new)):
            column = QVBoxLayout()
            column.addWidget(label(title, "field"))
            column.addWidget(files, 1)
            files.changed.connect(lambda _: self.refresh())
            files.rejected.connect(lambda _: self.panel.set_status("Seuls les fichiers PDF sont acceptés ici."))
            pair.addLayout(column, 1)
        layout.addLayout(pair, 1)

        settings = card()
        grid = QGridLayout(settings)
        grid.setContentsMargins(16, 14, 16, 14)
        grid.setColumnStretch(1, 1)
        legend = label("", wrap=True)
        legend.setTextFormat(Qt.RichText)
        self._legend = legend
        grid.addWidget(label("Résultat", "field"), 0, 0)
        grid.addWidget(legend, 0, 1)
        grid.addWidget(label("Enregistrer", "field"), 1, 0)
        self.output = OutputChooser()
        grid.addWidget(self.output, 1, 1)
        layout.addWidget(settings)
        theme.current().changed.connect(self._update_legend)
        self._update_legend()

        self.panel = TaskPanel("Comparer", self.start, "La comparaison porte sur le texte : un passage qui glisse "
                               "simplement sur la page suivante n'est pas compté comme modifié.", log_height=80)
        layout.addWidget(self.panel)
        self.refresh()

    def _update_legend(self) -> None:
        red = QColor.fromRgbF(*pdf_extras.REMOVED).name()
        green = QColor.fromRgbF(*pdf_extras.ADDED).name()
        self._legend.setText(
            f'Deux copies annotées : <span style="background:{red}; color:#000">&nbsp;retraits&nbsp;</span> '
            f'dans la version d\'origine, <span style="background:{green}; color:#000">&nbsp;ajouts&nbsp;</span> '
            "dans la nouvelle (« nom_différences.pdf »)."
        )

    def refresh(self) -> None:
        ready = bool(self.old.paths and self.new.paths)
        self.panel.set_ready(ready, "" if ready or not (self.old.paths or self.new.paths)
                             else "Ajoutez les deux versions à comparer.")

    def start(self) -> None:
        old, new = self.old.paths[0], self.new.paths[0]
        out_dir = self.output.out_dir or new.parent

        def work(progress) -> Report:
            try:
                outputs, changes = pdf_extras.compare_pdfs(old, new, out_dir, progress)
            except Cancelled:
                raise
            except Exception as exc:
                return Report(errors=[(new, str(exc) or exc.__class__.__name__)])
            note = (f"{changes} différence{'s' if changes > 1 else ''} trouvée{'s' if changes > 1 else ''}"
                    if changes else "Aucune différence de texte entre les deux versions.")
            return Report(outputs=outputs, notes=[note])

        self.panel.start(work)


# ------------------------------------------------------------ signature visuelle

DEFAULT_SIGNATURE_BOX: Box = (0.58, 0.80, 0.92, 0.92)  # en bas à droite, là où l'on signe d'habitude


class SignCanvas(CropCanvas):
    """Aperçu de la page ; le cadre se déplace et se redimensionne, la signature s'y affiche."""

    def __init__(self) -> None:
        blank = QImage(595, 842, QImage.Format_RGB888)
        blank.fill(Qt.white)
        super().__init__(blank, DEFAULT_SIGNATURE_BOX)
        self.signature: QImage | None = None

    def sizeHint(self) -> QSize:
        return QSize(420, 560)

    def set_page(self, image: QImage) -> None:
        self._image = image
        self.update()

    def set_signature(self, image: QImage | None) -> None:
        self.signature = image
        self.update()

    def paintEvent(self, event) -> None:
        t = theme.current()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        painter.setRenderHint(QPainter.Antialiasing)
        page = self.image_rect()
        painter.drawImage(page, self._image)
        painter.setPen(QPen(QColor(t.color("border")), 1))
        painter.drawRect(page)

        box = self.box_rect()
        if self.signature is not None and not self.signature.isNull():
            size = self.signature.size().scaled(box.size().toSize(), Qt.KeepAspectRatio)
            target = QRectF(box.center().x() - size.width() / 2, box.center().y() - size.height() / 2,
                            size.width(), size.height())
            painter.drawImage(target, self.signature)
        accent = QColor(t.color("accent"))
        painter.setPen(QPen(accent, 1.5, Qt.DashLine))
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(box)
        painter.setPen(QPen(accent, 1.5))
        painter.setBrush(QColor("#ffffff"))
        for x, y in ((box.left(), box.top()), (box.right(), box.top()),
                     (box.left(), box.bottom()), (box.right(), box.bottom())):
            painter.drawRect(QRectF(x - 4, y - 4, 8, 8))
        painter.end()


class SignPage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.image_path: Path | None = None
        self._page_count = 0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        self.files = FileList({"pdf"}, "PDF à signer", single=True)
        self.files.setFixedHeight(110)
        self.files.changed.connect(lambda _: self._load_document())
        self.files.rejected.connect(lambda _: self.panel.set_status("Seuls les fichiers PDF peuvent être signés."))
        layout.addWidget(self.files)

        body = QHBoxLayout()
        body.setSpacing(12)
        self.canvas = SignCanvas()
        body.addWidget(self.canvas, 1)

        settings = card()
        settings.setFixedWidth(330)
        box = QVBoxLayout(settings)
        box.setContentsMargins(16, 14, 16, 14)
        box.setSpacing(8)
        box.addWidget(label("Signature", "field"))
        box.addWidget(button("Choisir une image…", on_click=self._choose_image))
        self.image_label = label("Photo ou scan de votre signature (PNG, JPG)", "muted", wrap=True)
        box.addWidget(self.image_label)
        self.remove_white = QCheckBox("Rendre le fond transparent")
        self.remove_white.setChecked(True)
        self.remove_white.toggled.connect(lambda _: self._load_signature())
        box.addWidget(self.remove_white)
        box.addSpacing(8)
        box.addWidget(label("Page", "field"))
        pages = QHBoxLayout()
        self.page_spin = QSpinBox()
        self.page_spin.setRange(1, 1)
        self.page_spin.valueChanged.connect(lambda _: self._render_page())
        self.page_total = label("", "muted")
        pages.addWidget(self.page_spin)
        pages.addWidget(self.page_total, 1)
        box.addLayout(pages)
        self.all_pages = QCheckBox("Sur toutes les pages (paraphe)")
        box.addWidget(self.all_pages)
        box.addSpacing(8)
        box.addWidget(label("Glissez le cadre pour placer la signature ; tirez un coin pour l'agrandir.",
                            "muted", wrap=True))
        box.addStretch()
        box.addWidget(label("Enregistrer", "field"))
        self.output = OutputChooser()
        self.output.layout().setDirection(QBoxLayout.TopToBottom)  # deux lignes : la colonne est étroite
        box.addWidget(self.output)
        body.addWidget(settings)
        layout.addLayout(body, 1)

        self.panel = TaskPanel(
            "Signer", self.start,
            "Signature visuelle (image), comme une signature scannée : ce n'est pas une signature "
            "électronique certifiée. Résultat : « nom_signé.pdf ».", log_height=64)
        self.panel.busy_changed.connect(lambda busy: (settings.setEnabled(not busy), self.canvas.setEnabled(not busy)))
        layout.addWidget(self.panel)
        self.refresh()

    # ------------------------------------------------------- chargement

    def _load_document(self) -> None:
        self._page_count = 0
        if self.files.paths:
            try:
                with open_pdf(self.files.paths[0]) as doc:
                    self._page_count = doc.page_count
            except Exception as exc:
                self.files.clear()
                self.panel.set_status(f"{exc}")
                return
        self.page_spin.blockSignals(True)
        self.page_spin.setRange(1, max(1, self._page_count))
        self.page_spin.setValue(max(1, self._page_count))  # on signe en général la dernière page
        self.page_spin.blockSignals(False)
        self.page_total.setText(f"sur {self._page_count}" if self._page_count else "")
        self._render_page()
        self.refresh()

    def _render_page(self) -> None:
        if not self._page_count:
            blank = QImage(595, 842, QImage.Format_RGB888)
            blank.fill(Qt.white)
            self.canvas.set_page(blank)
            return
        with open_pdf(self.files.paths[0]) as doc:
            page = doc[self.page_spin.value() - 1]
            width = round(900 * min(1.0, page.rect.width / page.rect.height))
            pix = render_thumbnail(doc, page.number, width)
        self.canvas.set_page(QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format_RGB888).copy())

    def _choose_image(self) -> None:
        chosen, _ = QFileDialog.getOpenFileName(self, "Image de la signature", "", "Images (*.png *.jpg *.jpeg *.webp)")
        if chosen:
            self.set_image(Path(chosen))

    def set_image(self, path: Path) -> None:
        self.image_path = path
        self.image_label.setText(path.name)
        self.image_label.setToolTip(str(path))
        self._load_signature()
        self.refresh()

    def _load_signature(self) -> None:
        if self.image_path is None:
            return
        data = pdf_extras.signature_png(self.image_path, self.remove_white.isChecked())
        self.canvas.set_signature(QImage.fromData(data))

    # ---------------------------------------------------------- action

    def refresh(self) -> None:
        if not self.files.paths:
            self.panel.set_ready(False, "")
        elif self.image_path is None:
            self.panel.set_ready(False, "Choisissez l'image de votre signature.")
        else:
            self.panel.set_ready(True, "")

    def start(self) -> None:
        src, image, out_dir = self.files.paths[0], self.image_path, self.output.out_dir
        page, box = self.page_spin.value() - 1, self.canvas.box
        all_pages, remove_white = self.all_pages.isChecked(), self.remove_white.isChecked()

        def work(progress) -> Report:
            try:
                out = pdf_extras.sign_pdf(src, out_dir or src.parent, image, page, box, all_pages,
                                          remove_white, progress)
            except Cancelled:
                raise
            except Exception as exc:
                return Report(errors=[(src, str(exc) or exc.__class__.__name__)])
            return Report(outputs=[out])

        self.panel.start(work)
