"""Run work on Cascadeur's GUI thread.

MCP requests arrive on a background HTTP thread. Anything that touches Qt or the scene has to
run inside Cascadeur's event loop, so the server queues jobs here and a QTimer on the GUI thread
drains them. Keeping the queue in its own module lets tool modules call back onto the GUI thread
without importing server.py (which imports the tools, so that would be a circular import).
"""

from __future__ import annotations

import queue
import sys
import threading
import traceback

from PySide6 import QtCore

from .errors import ToolError

STATE_KEY = "_cascadeur_mcp_plus_state"  # survives module reloads


def state() -> dict | None:
    """The live server state, shared through sys so a hot reload can still find it."""
    return getattr(sys, STATE_KEY, None)


class Job:
    def __init__(self, fn, args):
        self.fn = fn
        self.args = args
        self.cancelled = False
        self.done = threading.Event()
        self.result = None
        self.error = None

    def run(self):
        if self.cancelled:
            return
        try:
            self.result = self.fn(**self.args)
        except ToolError as e:
            self.error = str(e)
        except Exception:
            self.error = traceback.format_exc()
        finally:
            self.done.set()


class Dispatcher(QtCore.QObject):
    def __init__(self, jobs: queue.Queue):
        super().__init__()
        self.jobs = jobs
        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(25)
        self.timer.timeout.connect(self.drain)
        self.timer.start()

    def drain(self):
        # Re-entrant on purpose: a job that opens a nested event loop (e.g. a modal
        # dialog) must not block the next job, which may be the one closing it.
        while True:
            try:
                job = self.jobs.get_nowait()
            except queue.Empty:
                return
            job.run()

    def stop(self):
        self.timer.stop()


def drain_idle_jobs(scene=None):
    """scene_idle subscriber: runs scene-editing jobs the way the official script server does."""
    current = state()
    if not current:
        return
    while True:
        try:
            job = current["idle_jobs"].get_nowait()
        except queue.Empty:
            return
        job.run()


def call_on_gui(fn, args: dict, timeout: float, idle: bool = False):
    """Run fn(**args) on Cascadeur's GUI thread and return its result.

    idle=True queues the job for the scene-idle event instead of the 25 ms QTimer, which is
    what scene-graph edits (e.g. entering Rig mode) need. Raises ToolError with the callback's
    message or traceback, and TimeoutError if the GUI does not finish in time.
    """
    current = state()
    if current is None:
        raise RuntimeError("Server is not running")
    job = Job(fn, args)
    current["idle_jobs" if idle else "jobs"].put(job)
    if not job.done.wait(timeout):
        job.cancelled = True
        raise TimeoutError(f"Cascadeur did not finish within {timeout:.0f}s (the GUI may be busy)")
    if job.error is not None:
        raise ToolError(job.error)
    return job.result


# Old server-level names, kept so code written against the pre-registry layout keeps working.
_Job = Job
_Dispatcher = Dispatcher
_drain_idle_jobs = drain_idle_jobs
_call_on_gui = call_on_gui
