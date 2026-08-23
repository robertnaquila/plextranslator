import threading

from plextranslator.workers import LatestOnlyWorker


def test_drain_processes_only_latest():
    seen = []
    worker = LatestOnlyWorker(seen.append)
    worker.submit("a")
    worker.submit("b")
    assert worker._drain_once() is True
    assert seen == ["b"]
    assert worker._drain_once() is False  # nothing left


def test_drain_survives_task_errors():
    def boom(item):
        raise RuntimeError("nope")

    worker = LatestOnlyWorker(boom)
    worker.submit("x")
    assert worker._drain_once() is True  # error swallowed
    assert worker._drain_once() is False


def test_running_worker_processes_and_stops():
    done = threading.Event()
    seen = []

    def handle(item):
        seen.append(item)
        done.set()

    worker = LatestOnlyWorker(handle)
    worker.start()
    worker.submit("hello")
    assert done.wait(timeout=2.0)
    assert seen == ["hello"]
    worker.stop()
    worker.join(timeout=2.0)
    assert not worker.is_alive()
