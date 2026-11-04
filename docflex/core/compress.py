"""Compression de PDF et d'images.

Le fichier compressé est écrit à côté de l'original (« nom_compressé.pdf »),
qui n'est jamais modifié. S'il n'est pas plus petit, il est supprimé.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pymupdf
from PIL import Image, ImageOps

from docflex.core.split import open_pdf
from docflex.core.tasks import Progress, no_progress
from docflex.utils.files import ext_of, human_size, unique_path


@dataclass(frozen=True)
class Level:
    label: str
    description: str
    # PDF : images au-delà de `dpi_threshold` ramenées à `dpi_target`. None = sans perte.
    dpi_threshold: int | None
    dpi_target: int
    jpeg_quality: int
    # Images : plus grand côté en pixels (None = taille d'origine).
    max_side: int | None
    png_quantize: bool


LEVELS = {
    "light": Level(
        "Légère", "Sans perte de qualité : nettoie la structure et recompresse sans altérer les images.",
        None, 0, 0, None, False,
    ),
    "recommended": Level(
        "Recommandée", "Images ramenées à 150 ppp : idéal pour l'écran et l'envoi par e-mail.",
        200, 150, 75, 2560, False,
    ),
    "strong": Level(
        "Forte", "Images ramenées à 96 ppp, qualité réduite : le plus léger possible.",
        120, 96, 55, 1600, True,
    ),
}

ACCEPTED_EXTS = frozenset({"pdf", "jpg", "jpeg", "png", "webp"})
ORIENTATION_TAG = 0x0112
MIN_GAIN = 0.01  # en dessous de 1 % de gain, inutile de créer un fichier


def _finish(src: Path, out: Path) -> tuple[list[Path], str]:
    before, after = src.stat().st_size, out.stat().st_size
    if after > before * (1 - MIN_GAIN):
        out.unlink()
        return [], f"{src.name} : déjà optimisé ({human_size(before)}), aucun fichier créé."
    gain = round(100 * (before - after) / before)
    return [out], f"{src.name} : {human_size(before)} → {human_size(after)} (−{gain} %)"


def compress_pdf(
    src: Path, dest: Path, level: Level, grayscale: bool = False, progress: Progress = no_progress
) -> tuple[list[Path], str]:
    out = unique_path(dest / f"{src.stem}_compressé.pdf")
    with open_pdf(src) as doc:
        if level.dpi_threshold is not None:
            progress(0.1, "Recompression des images…")
            doc.rewrite_images(
                dpi_threshold=level.dpi_threshold,
                dpi_target=level.dpi_target,
                quality=level.jpeg_quality,
                bitonal=False,  # les images noir et blanc grossiraient en JPEG
                set_to_gray=grayscale,
            )
        progress(0.6, "Allègement des polices…")
        try:
            doc.subset_fonts()
        except Exception:
            pass  # police non découpable : on la garde entière
        progress(0.8, "Écriture…")
        doc.save(out, garbage=4, clean=True, deflate=True, deflate_images=True, deflate_fonts=True, use_objstms=1)
    return _finish(src, out)


def compress_image(src: Path, dest: Path, level: Level, progress: Progress = no_progress) -> tuple[list[Path], str]:
    fmt = ext_of(src)
    out = unique_path(dest / f"{src.stem}_compressé{src.suffix}")
    with Image.open(src) as raw:
        # exif_transpose copie toujours l'image : on ne l'appelle que si elle est tournée.
        rotated = raw.getexif().get(ORIENTATION_TAG, 1) != 1
        img = ImageOps.exif_transpose(raw) if rotated else raw
        if level.max_side and max(img.size) > level.max_side:
            img.thumbnail((level.max_side, level.max_side), Image.Resampling.LANCZOS)
        if fmt in ("jpg", "jpeg"):
            if level.max_side is None and img is raw:
                # « keep » réutilise les tables de quantification d'origine : aucune perte ajoutée.
                img.save(out, "JPEG", quality="keep", optimize=True, progressive=True)
            else:
                img.convert("RGB").save(out, "JPEG", quality=level.jpeg_quality or 90, optimize=True, progressive=True)
        elif fmt == "png":
            if level.png_quantize:
                img = img.convert("RGBA").quantize(256, method=Image.Quantize.FASTOCTREE)
            img.save(out, "PNG", optimize=True)
        elif fmt == "webp":
            img.save(out, "WEBP", quality=level.jpeg_quality or 90, method=6)
        else:
            raise ValueError(f"Format non pris en charge : {fmt.upper()}")
    return _finish(src, out)


def compress_file(
    src: Path, out_dir: Path | None, level_key: str, grayscale: bool = False, progress: Progress = no_progress
) -> tuple[list[Path], str]:
    level = LEVELS[level_key]
    dest = out_dir or src.parent
    if ext_of(src) == "pdf":
        return compress_pdf(src, dest, level, grayscale, progress)
    return compress_image(src, dest, level, progress)
