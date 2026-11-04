"""Icônes de trait (SVG 24 × 24), recolorées selon le thème."""

from __future__ import annotations

from PySide6.QtCore import QByteArray, QRectF, QSize, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

_PATHS = {
    "select": '<path d="M6 3l13 7.5-5.8 1.6L10.6 18z"/>',
    "highlight": '<path d="M14.5 4.5l5 5-7.5 7.5H7v-5z"/><path d="M4 21h9" stroke-width="3"/>',
    "underline": '<path d="M7 4v7a5 5 0 0 0 10 0V4"/><path d="M5 20h14"/>',
    "strikeout": '<path d="M16.5 6.5C15.5 5 14 4.5 12 4.5c-2.6 0-4.2 1.4-4.2 3.1 0 1.2.8 2.1 2.4 2.7"/>'
                 '<path d="M4 12h16"/><path d="M8 16.8c.9 1.5 2.4 2.2 4.4 2.2 2.6 0 4.2-1.3 4.2-3.1 0-.6-.2-1.2-.5-1.6"/>',
    "note": '<path d="M4 5h16v11H10l-4.5 4v-4H4z"/><path d="M8 9h8M8 12.5h5"/>',
    "text": '<path d="M5 7V4.5h14V7"/><path d="M12 4.5V20"/><path d="M9 20h6"/>',
    "rect": '<rect x="4" y="6" width="16" height="12" rx="1.5"/>',
    "ellipse": '<ellipse cx="12" cy="12" rx="8.5" ry="6.5"/>',
    "line": '<path d="M5 19L19 5"/>',
    "arrow": '<path d="M5 19L19 5"/><path d="M10 5h9v9"/>',
    "ink": '<path d="M3 16c2.5-6 4.5-7 6.5-3.5S13 17 15 11s3.5-6 6-3.5"/>',
    "undo": '<path d="M9 14L4 9l5-5"/><path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11"/>',
    "redo": '<path d="M15 14l5-5-5-5"/><path d="M20 9H9.5a5.5 5.5 0 0 0 0 11H13"/>',
    "edit_text": '<path d="M4 20h4L19 9l-4-4L4 16z"/><path d="M13.5 6.5l4 4"/>',
    "add_text": '<path d="M4 7V5h10v2"/><path d="M9 5v13"/><path d="M7 18h4"/><path d="M18 12v7M14.5 15.5h7"/>',
    "add_image": '<rect x="3" y="5" width="18" height="14" rx="2"/><circle cx="9" cy="10" r="1.6"/>'
                 '<path d="M21 16l-5-5-8 8"/>',
    "whiteout": '<path d="M15.5 4l5 5-9.5 9.5H6.5L3 15z"/><path d="M9.5 8l6 6"/><path d="M12 20h9"/>',
    "grid": '<rect x="4" y="4" width="7" height="7" rx="1.5"/><rect x="13" y="4" width="7" height="7" rx="1.5"/>'
            '<rect x="4" y="13" width="7" height="7" rx="1.5"/><rect x="13" y="13" width="7" height="7" rx="1.5"/>',
    "list": '<path d="M9 6h11M9 12h11M9 18h11"/><circle cx="4.5" cy="6" r="1"/><circle cx="4.5" cy="12" r="1"/>'
            '<circle cx="4.5" cy="18" r="1"/>',
    "broom": '<path d="M14 4l6 6"/><path d="M17 7l-6.5 6.5"/><path d="M10.5 13.5l-6 2 1.5 4.5 4.5 1.5 2-6z"/>'
             '<path d="M6.5 16.5l3 3"/>',
    "plus": '<circle cx="12" cy="12" r="9"/><path d="M12 8v8M8 12h8"/>',
    "trash": '<path d="M4 7h16"/><path d="M10 11v6M14 11v6"/><path d="M6 7l1 13h10l1-13"/><path d="M9 7V4h6v3"/>',
    "pages": '<rect x="7.5" y="3.5" width="12" height="14.5" rx="1.5"/><path d="M4.5 7v12a1.5 1.5 0 0 0 1.5 1.5h9.5"/>',
    "bookmark": '<path d="M6.5 4.5a1 1 0 0 1 1-1h9a1 1 0 0 1 1 1v16l-5.5-4-5.5 4z"/>',
    "field_text": '<rect x="3" y="7" width="18" height="10" rx="2"/><path d="M7.5 10v4"/>',
    "field_check": '<rect x="4" y="4" width="16" height="16" rx="3"/><path d="M8 12.5l3 3 5-6"/>',
    "field_choice": '<rect x="3" y="7" width="18" height="10" rx="2"/><path d="M14 11l2 2 2-2"/>',
    "field_radio": '<circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="3.2"/>',
    "print": '<path d="M7 9V4h10v5"/><rect x="3.5" y="9" width="17" height="8" rx="2"/><path d="M7 14h10v6H7z"/>',
    "search": '<circle cx="11" cy="11" r="6.5"/><path d="M16 16l4.5 4.5"/>',
    "panel": '<rect x="3.5" y="4.5" width="17" height="15" rx="2.5"/><path d="M9.5 4.5v15"/>',
    "prev": '<path d="M14.5 6l-6 6 6 6"/>',
    "next": '<path d="M9.5 6l6 6-6 6"/>',
    "up": '<path d="M6 14.5l6-6 6 6"/>',
    "down": '<path d="M6 9.5l6 6 6-6"/>',
    "minus": '<path d="M6 12h12"/>',
    "add": '<path d="M12 6v12M6 12h12"/>',
    "more": '<path d="M5.5 12h.01M12 12h.01M18.5 12h.01" stroke-width="3"/>',
    "home": '<path d="M4 10.5L12 4l8 6.5"/><path d="M6.5 9v10.5h11V9"/><path d="M10 19.5v-5h4v5"/>',
    "close": '<path d="M7.5 7.5l9 9M16.5 7.5l-9 9"/>',
    "toolbars": '<rect x="3.5" y="3.5" width="17" height="17" rx="3"/><path d="M7.5 7.5h9M7.5 16.5h9"/>',
    "sliders": '<path d="M4 7h9M17 7h3M4 17h3M11 17h9"/><circle cx="15" cy="7" r="2"/><circle cx="9" cy="17" r="2"/>',
}
_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" '
        'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">{body}</svg>')
_cache: dict[tuple[str, str, int, float], QIcon] = {}


def icon(name: str, color: str, size: int = 20, ratio: float = 2.0) -> QIcon:
    key = (name, color, size, ratio)
    if key not in _cache:
        renderer = QSvgRenderer(QByteArray(_SVG.format(color=color, body=_PATHS[name]).encode()))
        pixmap = QPixmap(QSize(size, size) * ratio)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        renderer.render(painter, QRectF(0, 0, size * ratio, size * ratio))
        painter.end()
        pixmap.setDevicePixelRatio(ratio)
        _cache[key] = QIcon(pixmap)
    return _cache[key]
