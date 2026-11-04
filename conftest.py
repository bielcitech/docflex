"""Options de test.

`DOCFLEX_BLOCK_OPTIONAL=1` simule l'exécutable empaqueté, d'où sont exclus
les modules optionnels de PyMuPDF (IA de mise en page, ~100 Mo).
"""

import os
import sys
import tempfile

EXCLUDED_FROM_BUNDLE = ("pymupdf4llm", "pymupdf_layout", "pymupdf.layout", "onnxruntime", "networkx")

if os.environ.get("DOCFLEX_BLOCK_OPTIONAL"):
    for name in EXCLUDED_FROM_BUNDLE:
        sys.modules[name] = None  # « import name » lève alors ImportError

# Tests d'interface sans fenêtre visible (et sans écran en intégration continue).
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Préférences de test isolées : ne jamais toucher à celles de l'utilisateur.
_TEST_DIR = tempfile.mkdtemp(prefix="docflex-tests-")
os.environ["DOCFLEX_SETTINGS_FILE"] = os.path.join(_TEST_DIR, "settings.ini")
os.environ["DOCFLEX_DATA_DIR"] = os.path.join(_TEST_DIR, "data")  # historique et miniatures
