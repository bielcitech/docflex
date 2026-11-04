"""Exécution de tâches longues hors du thread de l'interface.

Le thread de travail ne touche jamais aux widgets : il émet des signaux, que
Qt livre dans le thread de l'interface.
"""

from __future__ import annotations

import threading
import traceback
from typing import Any, Callable

from PySide6.QtCore import QThread, Signal

from docflex.core.tasks import Cancelled, Progress


class Task(QThread):
    progressed = Signal(float, str)
    succeeded = Signal(object)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, fn: Callable[[Progress], Any], parent=None) -> None:
        super().__init__(parent)
        self._fn = fn
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:
        def progress(fraction: float, message: str | None) -> None:
            if self._cancel.is_set():
                raise Cancelled
            self.progressed.emit(fraction, message or "")

        try:
            result = self._fn(progress)
        except Cancelled:
            self.cancelled.emit()
        except Exception:
            self.failed.emit(traceback.format_exc(limit=3))
        else:
            self.succeeded.emit(result)
