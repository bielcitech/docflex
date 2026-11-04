"""Génère les icônes de DocFlex à partir d'un dessin vectoriel.

    .venv/bin/python packaging/icon/build_icons.py

Produit :
  packaging/icon/docflex.svg         dessin source (modifiable)
  packaging/macos/DocFlex.icns       icône macOS (style Big Sur : marge et ombre portée)
  packaging/windows/DocFlex.ico      icône Windows (16 à 256 px)
  docflex/ui/assets/icon.png         icône de la fenêtre pendant l'exécution
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image, ImageFilter  # noqa: E402
from PySide6.QtCore import QByteArray, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QGuiApplication, QImage, QPainter  # noqa: E402
from PySide6.QtSvg import QSvgRenderer  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
SIZE = 1024


def svg(inset: float, radius: float) -> str:
    """Dessin 1024 × 1024. `inset` : marge autour du fond (grande sur macOS, faible sur Windows)."""
    side = SIZE - 2 * inset
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1024 1024">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#4C8DFF"/>
      <stop offset="0.55" stop-color="#2563EB"/>
      <stop offset="1" stop-color="#3B2FC9"/>
    </linearGradient>
    <linearGradient id="shine" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#FFFFFF" stop-opacity="0.22"/>
      <stop offset="0.5" stop-color="#FFFFFF" stop-opacity="0"/>
    </linearGradient>
    <linearGradient id="badge" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#FFB547"/>
      <stop offset="1" stop-color="#F2551F"/>
    </linearGradient>
    <linearGradient id="paper" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#FFFFFF"/>
      <stop offset="1" stop-color="#EEF3FF"/>
    </linearGradient>
  </defs>

  <!-- Fond -->
  <rect x="{inset}" y="{inset}" width="{side}" height="{side}" rx="{radius}" fill="url(#bg)"/>
  <rect x="{inset}" y="{inset}" width="{side}" height="{side}" rx="{radius}" fill="url(#shine)"/>

  <!-- Feuille arrière : plusieurs documents -->
  <g transform="rotate(-9 470 520)">
    <rect x="262" y="262" width="390" height="500" rx="34" fill="#FFFFFF" fill-opacity="0.38"/>
  </g>

  <!-- Feuille principale, coin corné en haut à droite -->
  <path d="M 344 222 L 600 222 L 712 334 L 712 742 Q 712 776 678 776 L 344 776 Q 310 776 310 742
           L 310 256 Q 310 222 344 222 Z" fill="url(#paper)"/>
  <path d="M 600 222 L 600 300 Q 600 334 634 334 L 712 334 Z" fill="#C9D8FF"/>

  <!-- Lignes de texte -->
  <rect x="370" y="300" width="170" height="30" rx="15" fill="#2563EB"/>
  <rect x="370" y="382" width="282" height="22" rx="11" fill="#B9C8EE"/>
  <rect x="370" y="436" width="282" height="22" rx="11" fill="#B9C8EE"/>
  <rect x="370" y="490" width="210" height="22" rx="11" fill="#B9C8EE"/>
  <rect x="370" y="544" width="150" height="22" rx="11" fill="#B9C8EE"/>

  <!-- Pastille : deux flèches circulaires (transformer, convertir) -->
  <circle cx="682" cy="700" r="176" fill="#FFFFFF"/>
  <circle cx="682" cy="700" r="156" fill="url(#badge)"/>
  <g fill="none" stroke="#FFFFFF" stroke-width="30" stroke-linecap="round">
    <path d="M 600 666 A 86 86 0 0 1 752 640"/>
    <path d="M 764 734 A 86 86 0 0 1 612 760"/>
  </g>
  <g fill="#FFFFFF">
    <path d="M 784 600 L 790 684 L 712 660 Z"/>
    <path d="M 580 800 L 574 716 L 652 740 Z"/>
  </g>
</svg>"""


def render(source: str, size: int) -> Image.Image:
    renderer = QSvgRenderer(QByteArray(source.encode()))
    image = QImage(size, size, QImage.Format_RGBA8888)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing)
    renderer.render(painter, QRectF(0, 0, size, size))
    painter.end()
    return Image.frombytes("RGBA", (size, size), bytes(image.constBits()))


def with_shadow(icon: Image.Image) -> Image.Image:
    """Ombre portée douce sous le fond, comme les icônes macOS."""
    alpha = icon.getchannel("A").point(lambda a: int(a * 0.38))
    shadow = Image.new("RGBA", icon.size, (10, 20, 60, 0))
    shadow.putalpha(alpha.filter(ImageFilter.GaussianBlur(icon.width / 60)))
    canvas = Image.new("RGBA", icon.size, (0, 0, 0, 0))
    canvas.alpha_composite(shadow, (0, round(icon.width / 85)))
    canvas.alpha_composite(icon)
    return canvas


def main() -> None:
    QGuiApplication.instance() or QGuiApplication(sys.argv)
    mac_svg = svg(inset=100, radius=185)    # grille des icônes macOS : 824 px utiles sur 1024
    win_svg = svg(inset=24, radius=200)
    (ROOT / "packaging/icon/docflex.svg").write_text(mac_svg, encoding="utf-8")

    master = with_shadow(render(mac_svg, SIZE * 2).resize((SIZE, SIZE), Image.LANCZOS))
    master.resize((512, 512), Image.LANCZOS).save(ROOT / "docflex/ui/assets/icon.png")
    master.save(ROOT / "packaging/icon/docflex-1024.png")

    if shutil.which("iconutil"):
        with tempfile.TemporaryDirectory() as tmp:
            iconset = Path(tmp) / "DocFlex.iconset"
            iconset.mkdir()
            for base in (16, 32, 128, 256, 512):
                for scale in (1, 2):
                    name = f"icon_{base}x{base}{'@2x' if scale == 2 else ''}.png"
                    master.resize((base * scale, base * scale), Image.LANCZOS).save(iconset / name)
            subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o",
                            str(ROOT / "packaging/macos/DocFlex.icns")], check=True)
    else:
        print("iconutil absent (hors macOS) : DocFlex.icns non régénéré.")

    windows = render(win_svg, SIZE)
    windows.save(ROOT / "packaging/windows/DocFlex.ico",
                 sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])
    print("Icônes générées.")


if __name__ == "__main__":
    main()
