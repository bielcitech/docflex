"""Point d'entrée de DocFlex (développement et exécutable PyInstaller)."""

import multiprocessing
import sys

if __name__ == "__main__":
    multiprocessing.freeze_support()  # requis par PyInstaller sous Windows

    if len(sys.argv) == 3 and sys.argv[1] == "--selftest":
        from pathlib import Path

        from docflex.selftest import run as selftest

        sys.exit(selftest(Path(sys.argv[2])))

    from docflex.app import run

    run()
