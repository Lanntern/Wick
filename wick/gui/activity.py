"""Sidebar activity and a live dashboard; samples existing job state on the GUI thread."""

import threading
import time

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QDialog, QHeaderView, QTableWidget, QTableWidgetItem, QVBoxLayout

from ..activity import Activity
from .style import Type
from .widgets import button, hbox, text


class ActivityMonitor:
    def __init__(self, window):
        self.window = window
        self.tasks: list[Activity] = []
        self.started: dict[str, float] = {}
        self.dialog = None
        self.stopping: set[str] = set()      # tasks asked to stop that haven't yet
        self.timer = QTimer(window, interval=500)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
        window.sidebar.activity_requested.connect(self.show)

    def refresh(self):
        win, tasks, seen = self.window, [], set()

        def add(name, page, worker, status=None, section=None, stop=None):
            if worker is None or not worker.isRunning() or id(worker) in seen:
                return
            seen.add(id(worker))
            key = str(id(worker))
            self.started.setdefault(key, time.monotonic())
            bar = getattr(status, "bar", None)
            fraction = bar.value() / bar.maximum() if bar is not None and bar.maximum() > 0 else None
            detail = getattr(status, "detail", None) or getattr(status, "status", None)
            stage = detail.text() if detail is not None else getattr(worker, "stage", "Working…")
            if getattr(worker, "combined", False):
                name = "Shared cleanup scan"
            if stop is None and isinstance(getattr(worker, "cancel", None), threading.Event):
                stop = worker.cancel.set
            tasks.append(Activity(key, name, stage, fraction, bool(getattr(status, "_paused_reason", "")),
                                  time.monotonic() - self.started[key], page, section, stop))

        for name, page in (("Compress", "compress"), ("Convert", "converter"), ("Metadata", "metadata")):
            obj = win.compress if page == "compress" else win.built(page)
            if obj is not None:
                halt = obj.cancel if page == "compress" else obj.stop      # (the page's own Stop: it says so)
                add(name, page, obj.job_thread, obj.job_status, stop=halt)
                add(name + " scan", page, obj.scan_thread, obj.job_status, stop=halt)
        add("Home analysis", "home", win.home.worker, stop=win.home.stop)
        cleanup = win.built("cleanup")
        if cleanup is not None:
            for i, sec in cleanup._built.items():
                if getattr(sec.worker, "combined", False) and getattr(sec.worker, "active_key", "") \
                        and sec.tool.key != sec.worker.active_key:
                    continue
                add(sec.tool.tab, "cleanup", sec.worker, sec, i, stop=sec.stop)
        add("Recompress updated games", "cleanup", getattr(win, "_recompress", None))     # (its .cancel: Event)
        # Watch Folders exposes its own live worker, separate from the interactive Compress page.
        watch = getattr(win, "watch", None)
        add("Watch Folders", "watch", getattr(watch, "job", None), getattr(watch, "job_status", None))
        self.tasks = tasks
        active = {t.key for t in tasks}
        self.started = {k: v for k, v in self.started.items() if k in active}
        self.stopping &= active
        win.sidebar.set_activity(tasks)
        if self.dialog is not None and self.dialog.isVisible():
            self._fill()

    def show(self):
        if self.dialog is None:
            self.dialog = QDialog(self.window)
            self.dialog.setWindowTitle("Running tasks")
            self.dialog.resize(760, 360)
            layout = QVBoxLayout(self.dialog)
            layout.addWidget(text("Live work across every page. Double-click a task to return to its controls.",
                                  Type.BODY, wrap=True))
            self.table = QTableWidget(0, 5)
            self.table.setAccessibleName("Running tasks")
            self.table.setHorizontalHeaderLabels(["Task", "Stage", "Progress", "Elapsed", "State"])
            self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
            self.table.setEditTriggers(QTableWidget.NoEditTriggers)
            self.table.setSelectionBehavior(QTableWidget.SelectRows)
            self.table.cellDoubleClicked.connect(self.go)
            layout.addWidget(self.table)
            go = button("Go to Task", "", None, "Open the selected task")
            go.clicked.connect(self.go)
            self.stop_btn = button("Stop", "", "stop", "Stop the selected task (the file being processed keeps "
                                                       "its original)")
            self.stop_btn.clicked.connect(lambda: self.stop_task())
            self.stop_all_btn = button("Stop All", "", None, "Stop every running task")
            self.stop_all_btn.clicked.connect(self.stop_all)
            close = button("Close")
            close.clicked.connect(self.dialog.close)
            layout.addLayout(hbox(go, self.stop_btn, self.stop_all_btn, None, close))
            self.table.itemSelectionChanged.connect(self._update_buttons)
        self.refresh()
        self._fill()
        self.dialog.show()
        self.dialog.raise_()

    def _fill(self):
        selected = self.table.currentRow()
        self.table.setRowCount(len(self.tasks))
        for row, task in enumerate(self.tasks):
            state = "Stopping…" if task.key in self.stopping else "Paused" if task.paused else "Running"
            values = [task.name, task.stage, "Unknown" if task.fraction is None else f"{task.fraction:.0%}",
                      f"{int(task.elapsed) // 60}:{int(task.elapsed) % 60:02d}", state]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                self.table.setItem(row, col, item)
        if 0 <= selected < len(self.tasks):
            self.table.selectRow(selected)
        elif self.tasks:
            self.table.selectRow(0)
        if not self.tasks:
            self.table.setRowCount(1)
            self.table.setItem(0, 0, QTableWidgetItem("Nothing is running."))
        self._update_buttons()

    def _selected_task(self) -> Activity | None:
        row = self.table.currentRow()
        return self.tasks[row] if 0 <= row < len(self.tasks) else None

    def _update_buttons(self) -> None:
        task = self._selected_task()
        self.stop_btn.setEnabled(task is not None and task.stop is not None and task.key not in self.stopping)
        self.stop_all_btn.setEnabled(any(t.stop is not None and t.key not in self.stopping for t in self.tasks))

    def stop_task(self, task: Activity | None = None) -> None:
        """Ask one task to stop. It finishes the file it's on (that file keeps its original) and then ends."""
        task = task or self._selected_task()
        if task is None or task.stop is None or task.key in self.stopping:
            return
        try:
            task.stop()
        except Exception:  # noqa: BLE001 - a task that can't be asked must not break the panel
            from ..log import log

            log.exception("Couldn't stop %s", task.name)
            return
        self.stopping.add(task.key)
        if self.dialog is not None and self.dialog.isVisible():
            self._fill()

    def stop_all(self) -> None:
        for task in list(self.tasks):
            self.stop_task(task)

    def go(self, row=None, _column=None):
        row = self.table.currentRow() if row is None or isinstance(row, bool) else row
        if 0 <= row < len(self.tasks):
            task = self.tasks[row]
            page = getattr(self.window, task.page)
            self.window.go_to(page)
            if task.page == "cleanup" and task.section is not None:
                page.tabs.setCurrentIndex(task.section)
            self.dialog.hide()
