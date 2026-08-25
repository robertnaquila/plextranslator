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


def test_drain_never_swallows_stop_wakeup():
    worker = LatestOnlyWorker(lambda item: None)
    worker.stop()  # sets the wake event
    worker._drain_once()  # empty drain must NOT clear it after stop
    assert worker._wake.is_set()


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


# -- OrderedWorker ---------------------------------------------------------


def test_ordered_worker_processes_all_in_order():
    from plextranslator.workers import OrderedWorker

    seen = []
    worker = OrderedWorker(seen.append)
    worker.submit("a")
    worker.submit("b")
    worker.submit("c")
    while worker.process_next():
        pass
    assert seen == ["a", "b", "c"]


def test_ordered_worker_drops_oldest_beyond_backlog():
    from plextranslator.workers import OrderedWorker

    seen = []
    worker = OrderedWorker(seen.append, max_backlog=2)
    for item in ("a", "b", "c", "d"):
        worker.submit(item)
    while worker.process_next():
        pass
    assert seen == ["c", "d"]
    assert worker.dropped == 2


def test_ordered_worker_survives_task_errors():
    from plextranslator.workers import OrderedWorker

    def boom(item):
        raise RuntimeError("nope")

    worker = OrderedWorker(boom)
    worker.submit("x")
    assert worker.process_next() is True
    assert worker.process_next() is False


def test_ordered_worker_thread_runs_and_stops():
    import threading

    from plextranslator.workers import OrderedWorker

    done = threading.Event()
    seen = []

    def handle(item):
        seen.append(item)
        if len(seen) == 2:
            done.set()

    worker = OrderedWorker(handle)
    worker.start()
    worker.submit("one")
    worker.submit("two")
    assert done.wait(timeout=2.0)
    assert seen == ["one", "two"]
    worker.stop()
    worker.join(timeout=2.0)
    assert not worker.is_alive()
