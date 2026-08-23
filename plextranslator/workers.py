"""Small threading utilities shared by the live caption engines."""

from __future__ import annotations

import logging
import threading

logger = logging.getLogger(__name__)


class LatestOnlyWorker(threading.Thread):
    """Background worker that always processes only the newest submitted item.

    Both live engines use this to keep slow post-processing (Claude refinement,
    subtitle translation) off the hot path: the capture/OCR loop submits work and
    moves on, and if items arrive faster than they can be processed, intermediate
    ones are silently dropped — for live captions only the newest matters.
    """

    def __init__(self, func, name: str = "plextranslator-worker") -> None:
        super().__init__(name=name, daemon=True)
        self._func = func
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._item = None
        self._has_item = False
        self._stopped = False

    def submit(self, item) -> None:
        """Queue ``item``, replacing any not-yet-processed previous item."""
        with self._lock:
            self._item = item
            self._has_item = True
        self._wake.set()

    def stop(self) -> None:
        self._stopped = True
        self._wake.set()

    def _drain_once(self) -> bool:
        """Process the newest pending item, if any. Returns True if one ran."""
        with self._lock:
            item, has = self._item, self._has_item
            self._item = None
            self._has_item = False
            # Never clear after stop() has set the event, or its wakeup would be
            # swallowed and run() would park in wait() forever.
            if not has and not self._stopped:
                self._wake.clear()
        if not has:
            return False
        try:
            self._func(item)
        except Exception:  # noqa: BLE001 - a failed task must not kill the worker
            logger.debug("background task failed", exc_info=True)
        return True

    def run(self) -> None:
        while True:
            self._wake.wait()
            if self._stopped:
                return
            self._drain_once()
