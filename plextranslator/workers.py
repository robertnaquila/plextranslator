"""Small threading utilities shared by the live caption engines."""

from __future__ import annotations

import logging
import threading
from collections import deque

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


class OrderedWorker(threading.Thread):
    """FIFO background worker: processes EVERY submitted item, in order.

    Subtitle translation needs this rather than :class:`LatestOnlyWorker` —
    each emitted line is a caption the viewer should see, and during fast
    dialogue a latest-only worker silently drops the lines in between (and
    discards the in-flight one as stale). A bounded backlog keeps captions
    near-live: when more than ``max_backlog`` items are waiting, the oldest
    are dropped and counted in ``dropped``.
    """

    def __init__(self, func, name: str = "plextranslator-worker", max_backlog: int = 4) -> None:
        super().__init__(name=name, daemon=True)
        self._func = func
        self._cv = threading.Condition()
        self._items: "deque" = deque()
        self._stopped = False
        self.max_backlog = max_backlog
        self.dropped = 0

    def submit(self, item) -> None:
        with self._cv:
            self._items.append(item)
            while len(self._items) > self.max_backlog:
                self._items.popleft()
                self.dropped += 1
                logger.info("Translation backlog full; dropped the oldest line.")
            self._cv.notify()

    def stop(self) -> None:
        with self._cv:
            self._stopped = True
            self._cv.notify()

    def process_next(self) -> bool:
        """Run the oldest pending item synchronously, if any. For tests/run()."""
        with self._cv:
            if not self._items:
                return False
            item = self._items.popleft()
        try:
            self._func(item)
        except Exception:  # noqa: BLE001 - a failed task must not kill the worker
            logger.debug("background task failed", exc_info=True)
        return True

    def run(self) -> None:
        while True:
            with self._cv:
                while not self._items and not self._stopped:
                    self._cv.wait()
                if self._stopped:
                    return
            self.process_next()
