"""Staying up, and telling people when something goes wrong:

- a crash (a fault in a native library) leaves every thread's stack in ``crash.txt`` next to the log, and the
  next start says so once, with where the details are;
- a window that stops responding for a while gets every thread's stack written to the log (so a freeze can be
  found, not just felt);
- an error in background work is shown, not only logged (a task that silently never finishes looks frozen);
- quitting stops all background work with one short, shared deadline, and never lets Qt abort the process by
  destroying a thread that's still running.
"""

from __future__ import annotations

import faulthandler
import os
import sys
import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, QThread, QTimer, Signal

STALL_SECONDS = 8.0         # the window not responding this long: record where every thread is
QUIT_DEADLINE = 8.0         # quitting: how long background work gets to stop, all together

_crash_file = None
_heartbeat = time.monotonic()
_stuck: list[QThread] = []  # threads that didn't stop in time at quit (kept alive until the process ends)


def crash_path() -> Path:
    from ..log import log_dir

    return log_dir() / "crash.txt"


def _marker() -> Path:
    return crash_path().with_name("session-running")


def previous_crash() -> str:
    """What the last session left in crash.txt, if it didn't end normally (else empty). Read once at start-up.
    (On Windows the record can also hold exceptions that were caught and harmless: only a session that never
    reached its normal end, its marker still there, counts as a crash.)"""
    p = crash_path()
    try:
        text = p.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""
    if not _marker().exists():
        return ""
    if text:
        try:
            p.replace(p.with_name("crash-previous.txt"))      # (kept for a bug report; the next one starts empty)
        except OSError:
            pass
    return text


class _Bridge(QObject):
    """Carries an error from a background thread to the GUI thread (signals are thread-safe)."""

    error = Signal(str, str)


_bridge: _Bridge | None = None


def install() -> str:
    """Set up crash records, the stall watch and background-error reporting. Returns the previous session's
    crash record, if it had one (to mention once)."""
    global _crash_file, _bridge
    previous = previous_crash()
    try:
        _marker().write_text(str(os.getpid()), encoding="utf-8")
        _crash_file = open(crash_path(), "w", encoding="utf-8")  # noqa: SIM115 - open for the whole session
        faulthandler.enable(file=_crash_file, all_threads=True)
    except OSError:
        _crash_file = None
    _bridge = _Bridge()
    _bridge.error.connect(_show_background_error)
    previous_thread_hook = threading.excepthook

    def thread_hook(args):
        previous_thread_hook(args)
        if args.exc_type is not None and not issubclass(args.exc_type, (KeyboardInterrupt, SystemExit)) \
                and _bridge is not None:
            _bridge.error.emit(args.exc_type.__name__, str(args.exc_value))

    threading.excepthook = thread_hook
    _log_qt_messages()
    _start_stall_watch()
    return previous


QT_REPEATS_LOGGED = 3       # the same Qt message (some come on every resize): logged this often per session


def _log_qt_messages() -> None:
    """Qt's own warnings and errors (a plugin that didn't load, a thread destroyed while running…) go to the
    console, which the installed app doesn't have: send them to the log."""
    import logging

    from PySide6.QtCore import QtMsgType, qInstallMessageHandler

    from ..log import log

    levels = {QtMsgType.QtWarningMsg: logging.WARNING, QtMsgType.QtCriticalMsg: logging.ERROR,
              QtMsgType.QtFatalMsg: logging.CRITICAL, QtMsgType.QtInfoMsg: logging.INFO}
    seen: dict[str, int] = {}

    def handler(kind, context, message) -> None:
        level = levels.get(kind)
        if level is None:                       # (debug messages)
            return
        n = seen[message] = seen.get(message, 0) + 1
        if n > QT_REPEATS_LOGGED:
            return
        where = f" ({context.file}:{context.line})" if getattr(context, "file", None) else ""
        more = " (further repeats aren't logged)" if n == QT_REPEATS_LOGGED else ""
        log.log(level, "Qt: %s%s%s", message, where, more)

    qInstallMessageHandler(handler)
    _log_qt_messages.handler = handler  # type: ignore[attr-defined]   # (kept alive)


def _show_background_error(kind: str, message: str) -> None:
    from ..log import log_file
    from .dialogs import warn

    if getattr(_show_background_error, "showing", False):
        return
    _show_background_error.showing = True  # type: ignore[attr-defined]
    try:
        warn(None, "A background task stopped with an error",
             f"{kind}: {message}\n\nWhat was being done may be unfinished; your files are as the task left them "
             f"(each file is only replaced once its result is checked). The details are in the log ({log_file()}).")
    finally:
        _show_background_error.showing = False  # type: ignore[attr-defined]


def _start_stall_watch() -> None:
    """A timer on the GUI thread marks it alive; a plain thread notices when it stops doing so."""
    global _heartbeat
    app_timer = QTimer()
    app_timer.setInterval(500)

    def beat():
        global _heartbeat
        _heartbeat = time.monotonic()

    app_timer.timeout.connect(beat)
    app_timer.start()
    _start_stall_watch.timer = app_timer  # type: ignore[attr-defined]   # (kept alive)
    _heartbeat = time.monotonic()

    def watch():
        from ..log import log, log_file

        reported = False
        while True:
            time.sleep(1.0)
            late = time.monotonic() - _heartbeat
            if late > STALL_SECONDS and not reported:
                reported = True
                log.warning("The window hasn't responded for %.0f s; where every thread is:", late)
                try:
                    with open(log_file(), "a", encoding="utf-8") as f:
                        faulthandler.dump_traceback(file=f, all_threads=True)
                except OSError:
                    pass
            elif late < 2:
                if reported:
                    log.warning("The window is responding again")
                reported = False

    threading.Thread(target=watch, name="stall-watch", daemon=True).start()


def background_threads(root: QObject) -> list[QThread]:
    """Every QThread under ``root`` that's still running."""
    return [t for t in root.findChildren(QThread) if t.isRunning()]


def ask_to_stop(thread: QThread) -> None:
    """The usual ways a task in this app is told to stop."""
    cancel = getattr(thread, "cancel", None)
    if isinstance(cancel, threading.Event):
        cancel.set()
    elif callable(cancel):
        try:
            cancel()
        except Exception:  # noqa: BLE001 - stopping must never fail the quit
            from wick.log import ignored
            ignored("gui.resilience.ask_to_stop")
            pass
    thread.requestInterruption()


def stop_all(root: QObject, deadline: float = QUIT_DEADLINE) -> list[QThread]:
    """Ask every background thread under ``root`` to stop, and wait (the window still painting) until they have,
    or the deadline passes. Returns the ones still running; those are kept alive until the process ends, so Qt
    never destroys a running thread (which aborts the whole program)."""
    from PySide6.QtWidgets import QApplication

    threads = background_threads(root)
    for t in threads:
        ask_to_stop(t)
    end = time.monotonic() + deadline
    while time.monotonic() < end and any(t.isRunning() for t in threads):
        QApplication.processEvents()
        for t in threads:
            t.wait(50)
    left = [t for t in threads if t.isRunning()]
    if left:
        from ..log import log

        log.warning("Quitting with %d task(s) that didn't stop in time: %s", len(left),
                    ", ".join(type(t).__name__ for t in left))
        for t in left:
            t.setParent(None)           # (not destroyed with the window)
            _stuck.append(t)
    return left


_outliving: list[QThread] = []


def outlive(thread: QThread) -> None:
    """Let a running thread finish after the window or dialog that owned it closes (instead of blocking it, or
    destroying the thread mid-run, which aborts the program). It's released once it ends."""
    if not thread.isRunning():
        return
    ask_to_stop(thread)
    thread.setParent(None)
    _outliving.append(thread)

    def released(t=thread):
        if t in _outliving:
            _outliving.remove(t)
        t.deleteLater()

    thread.finished.connect(released)


def exit_now_if_stuck(code: int) -> None:
    """After the event loop: if a task never stopped, end the process here, before Python's own shutdown would
    destroy that running thread and turn a clean quit into a crash report."""
    _stuck.extend(t for t in _outliving if t.isRunning() and t not in _stuck)
    if not _stuck:
        return
    import logging

    logging.shutdown()
    end_cleanly()                         # (a deliberate exit, not a crash)
    sys.stdout.flush()
    os._exit(code)


def end_cleanly() -> None:
    """A normal exit: the next start won't report a crash."""
    if _crash_file is not None:
        try:
            faulthandler.disable()
            _crash_file.close()
        except OSError:
            pass
    try:
        _marker().unlink(missing_ok=True)
    except OSError:
        pass


__all__ = ["install", "previous_crash", "stop_all", "ask_to_stop", "background_threads", "exit_now_if_stuck",
           "end_cleanly", "crash_path"]
