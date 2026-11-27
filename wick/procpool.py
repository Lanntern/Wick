"""A shared pool of helper processes for GIL-holding encoders (see _workers.py).

Started on first use, sized to the CPU, at low priority like the other encoders. If helper processes
can't be started (locked-down PC, antivirus) the work simply runs in the calling thread instead.
"""

from __future__ import annotations

import atexit
import os
import threading
from typing import TYPE_CHECKING

from .log import ignored

if TYPE_CHECKING:
    from concurrent.futures import ProcessPoolExecutor

_lock = threading.Lock()


class _State:
    """The worker processes, made on first use; broken once one can't be started."""

    def __init__(self) -> None:
        self.pool: ProcessPoolExecutor | None = None
        self.broken = False


_state = _State()


def _get():
    with _lock:
        if _state.pool is None and not _state.broken:
            import multiprocessing
            from concurrent.futures import ProcessPoolExecutor

            from . import _workers, tools

            _state.pool = ProcessPoolExecutor(max_workers=max(1, min(os.cpu_count() or 2, 16)),
                                              mp_context=multiprocessing.get_context("spawn"),
                                              initializer=_workers.set_low_priority,
                                              initargs=(tools.LOW_PRIORITY,))
            atexit.register(shutdown)
        return _state.pool


def call(fn, *args):
    """Run ``fn(*args)`` in a helper process (fn must live in _workers); in this thread if that fails."""
    pool = None if os.environ.get("WICK_NO_PROCESSES") else _get()
    if pool is not None:
        try:
            return pool.submit(fn, *args).result()
        except Exception as e:  # noqa: BLE001
            from concurrent.futures.process import BrokenProcessPool

            if not isinstance(e, (BrokenProcessPool, OSError, PermissionError, EOFError)):
                raise  # the encoder itself failed: report it like any other failure
            from .log import log

            log.warning("Helper processes unavailable (%s); working in-process", e)
            _state.broken = True
            shutdown()
    return fn(*args)


def stop(pool) -> None:
    """End ``pool`` and its processes now. They hold both ends of their own job queue, so they never notice the
    app has ended; without this they'd outlive it when it exits with os._exit (seen on macOS: the leftover
    processes kept a terminal's output open). A stop request is ignored when the app was started with that
    signal ignored (children inherit it), so whatever is still there after a moment is ended outright."""
    processes = list((getattr(pool, "_processes", None) or {}).values())
    pool.shutdown(wait=False, cancel_futures=True)
    for p in processes:
        try:
            p.terminate()
        except (OSError, ValueError):  # already gone
            pass
    for p in processes:
        try:
            p.join(2)
            if p.is_alive():
                p.kill()
                p.join(2)
        except Exception:  # noqa: BLE001
            ignored("procpool: stopping a worker")


def shutdown() -> None:
    with _lock:
        if _state.pool is not None:
            stop(_state.pool)
            _state.pool = None
