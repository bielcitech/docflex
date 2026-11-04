# -*- mode: python ; coding: utf-8 -*-
# Construction : pyinstaller docflex.spec --noconfirm
# Produit dist/DocFlex.app (macOS) ou dist/DocFlex/DocFlex.exe (Windows).
# PyInstaller ne compile pas d'un système à l'autre : construire sur chaque OS.

import os
import sys

sys.path.insert(0, SPECPATH)
from docflex import APP_NAME, __version__  # noqa: E402

# Modules optionnels de PyMuPDF (analyse de mise en page par IA, ~100 Mo),
# jamais utilisés par DocFlex. Vérifié par : DOCFLEX_BLOCK_OPTIONAL=1 pytest
EXCLUDES = [
    "pymupdf4llm", "pymupdf_layout", "pymupdf.layout", "onnxruntime", "networkx",
    "pandas", "matplotlib", "scipy", "IPython", "pytest", "pytestqt",
    # L'interface est en Qt : Tk et les modules Qt non utilisés n'ont rien à faire dans l'installeur.
    "tkinter", "_tkinter",
    "PySide6.QtNetwork", "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtOpenGL", "PySide6.QtSql",
    "PySide6.QtTest", "PySide6.QtDBus", "PySide6.QtConcurrent", "PySide6.QtDesigner", "PySide6.QtHelp",
    "PySide6.QtUiTools", "PySide6.QtXml",
]

ICON = {"darwin": "packaging/macos/DocFlex.icns", "win32": "packaging/windows/DocFlex.ico"}.get(sys.platform)

# Signature macOS (hardened runtime + horodatage) si une identité est fournie : voir build_dmg.sh.
SIGN_IDENTITY = os.environ.get("DOCFLEX_SIGN_IDENTITY") or None
ENTITLEMENTS = "packaging/macos/entitlements.plist" if SIGN_IDENTITY else None
if ICON and not os.path.exists(ICON):
    ICON = None

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=[
        ("docflex/ui/assets", "docflex/ui/assets"),
        ("docflex/resources/tessdata", "docflex/resources/tessdata"),  # langues de l'OCR
    ],
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    debug=False,
    strip=False,
    upx=False,
    console=False,
    icon=ICON,
    codesign_identity=SIGN_IDENTITY,
    entitlements_file=ENTITLEMENTS,
)

coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name=APP_NAME)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name=f"{APP_NAME}.app",
        icon=ICON,
        bundle_identifier="com.xenovialabs.docflex",
        version=__version__,
        info_plist={
            "CFBundleDisplayName": APP_NAME,
            "CFBundleShortVersionString": __version__,
            "NSHighResolutionCapable": True,
            "NSRequiresAquaSystemAppearance": False,  # suit le mode sombre
            "LSMinimumSystemVersion": "11.0",
            # « Ouvrir avec » DocFlex pour les PDF, sans en devenir l'application par défaut.
            "CFBundleDocumentTypes": [{
                "CFBundleTypeName": "Document PDF",
                "CFBundleTypeRole": "Editor",
                "LSItemContentTypes": ["com.adobe.pdf"],
                "LSHandlerRank": "Alternate",
            }],
        },
    )
