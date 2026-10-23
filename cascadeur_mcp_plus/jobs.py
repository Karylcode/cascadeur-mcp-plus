"""Background jobs for long-running Cascadeur work (T03).

Some Cascadeur operations (AutoPhysics, AI inbetweening, video export, video mocap) run for
minutes. Running them on the GUI thread would freeze Cascadeur and could exceed the MCP call
timeout. A tool marked "background" in its tool dict instead starts a job and returns a job id
immediately; a QTimer on the GUI thread polls the operation, and get_job reports progress and the
result later.

Job records live in the process-wide sys._cascadeur_mcp_plus_jobs dictionary rather than in
this module, because start_mcp_plus.py drops every cascadeur_mcp_plus module when it hot
reloads. Keeping the records outside the modules lets the new server still answer get_job for jobs
started by the old code: jobs that were running when the server stopped are reported as "lost"
instead of vanishing (or crashing the new server). While the server is running the registry is also
available as sys._cascadeur_mcp_plus_state["background_jobs"].

Thread rules: start_job, cancel, shutdown and every poll run on Cascadeur's GUI thread. get_job and
list_jobs may run on the HTTP thread; they only read snapshots under a lock and wait on a
threading.Event, never on the GUI. Nothing here sleeps on the GUI thread.
"""

from __future__ import annotations

import sys
import threading
import time
import traceback

from .errors import ToolError

REGISTRY_ATTR = "_cascadeur_mcp_plus_jobs"
STATE_KEY = "background_jobs"
POLL_INTERVAL_MS = 100
MAX_JOBS = 100
MAX_WAIT_S = 60.0
_TERMINAL = {"done", "failed", "cancelled", "lost"}
_LOCK = threading.RLock()
_TOKEN = object()


# ---------------------------------------------------------------------------
# Registry (kept outside the module so a hot reload still sees it)
# ---------------------------------------------------------------------------


def _registry() -> dict:
    with _LOCK:
        registry = getattr(sys, REGISTRY_ATTR, None)
        if not isinstance(registry, dict) or "jobs" not in registry:
            registry = {"jobs": {}, "order": [], "next_id": 1}
            setattr(sys, REGISTRY_ATTR, registry)
    _link_state(registry)
    return registry


def _link_state(registry: dict) -> None:
    """Expose the registry through the server state, as the ticket's spec asks."""
    dispatch = sys.modules.get(f"{__package__}.dispatch") if __package__ else None
    if dispatch is None:
        return
    try:
        state = dispatch.state()
    except Exception:
        return
    if isinstance(state, dict):
        state[STATE_KEY] = registry


def _prune(registry: dict) -> None:
    """Drop the oldest finished jobs, but never a running one."""
    order = registry["order"]
    while len(order) > MAX_JOBS:
        for index, job_id in enumerate(order):
            if registry["jobs"][job_id]["status"] in _TERMINAL:
                order.pop(index)
                registry["jobs"].pop(job_id, None)
                break
        else:
            return


# ---------------------------------------------------------------------------
# Threads, timers and snapshot helpers
# ---------------------------------------------------------------------------


def _on_gui_thread() -> bool:
    try:
        from PySide6 import QtCore
    except Exception:
        return True
    app = QtCore.QCoreApplication.instance()
    if app is None:
        return True
    return QtCore.QThread.currentThread() is app.thread()


def _require_gui_thread(where: str) -> None:
    if not _on_gui_thread():
        raise ToolError(f"{where} must run on Cascadeur's GUI thread.")


def _make_timer(job: dict):
    """Create the GUI-thread QTimer that polls a job; unit tests replace this."""
    from PySide6 import QtCore

    timer = QtCore.QTimer()
    timer.setInterval(POLL_INTERVAL_MS)
    timer.timeout.connect(lambda: _tick(job))
    timer.start()
    return timer


def _stop_timer(job: dict) -> None:
    timer = job.get("_timer")
    job["_timer"] = None
    if timer is None:
        return
    try:
        timer.stop()
    except Exception:
        pass
    try:
        timer.deleteLater()
    except Exception:
        pass


def _progress(value):
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:  # NaN
        return None
    return max(0.0, min(1.0, number))


def jsonable(value):
    """Make a job result JSON-safe using common.jsonable when the package is available."""
    if value is None:
        return None
    try:
        from . import common
    except Exception:
        return value
    return common.jsonable(value)


def _traceback_error(text):
    """Return the last non-empty line of a traceback (the exception itself)."""
    lines = [line.strip() for line in (text or "").strip().splitlines() if line.strip()]
    return lines[-1] if lines else "The job failed."


class JobStartError(Exception):
    """begin() refused to start; .result is the tool result to return synchronously."""

    def __init__(self, result: dict):
        self.result = dict(result)
        super().__init__(str(self.result.get("error") or "The background job did not start."))


# ---------------------------------------------------------------------------
# Job lifecycle (GUI thread)
# ---------------------------------------------------------------------------


def start_job(kind, begin, poll, cancel=None, timeout=None) -> str:
    """Start a job and return its id.

    begin() runs now on the GUI thread and must return quickly: it only launches Cascadeur's
    asynchronous work. It may return None or {"ok": true, ...}. Returning {"ok": false, ...}
    (common.fail) or raising aborts the call synchronously: start_job raises JobStartError with
    that result and records no job. poll() is then called by a QTimer on the GUI thread roughly
    every 100 ms and returns {"done": bool, "progress": 0..1 or None, "result": ...}; returning
    {"ok": false, "error": ..., "hint": ...} fails the job. cancel() is optional and is called
    when the job is cancelled or times out. timeout is in seconds (None means no timeout).
    Exceptions in poll() fail the job; they never reach the MCP server.
    """
    _require_gui_thread("start_job")
    if not callable(begin):
        raise ToolError("start_job needs a callable begin().")
    if not callable(poll):
        raise ToolError("start_job needs a callable poll(); it returns {done, progress, result}.")
    if cancel is not None and not callable(cancel):
        raise ToolError("start_job's cancel must be callable or None.")
    job_timeout = None
    if timeout is not None:
        try:
            job_timeout = float(timeout)
        except (TypeError, ValueError):
            raise ToolError(f"timeout must be a number of seconds (got {timeout!r}).") from None
        if job_timeout <= 0:
            raise ToolError("timeout must be greater than 0 seconds; omit it for no timeout.")

    # Run begin() before recording anything: a rejected argument must not leave a job polling
    # forever with parameters that never worked.
    started_at = time.time()
    started_mono = time.monotonic()
    try:
        started = begin()
    except Exception:
        failure = traceback.format_exc()
        raise JobStartError(
            {
                "ok": False,
                "error": _traceback_error(failure),
                "hint": "The background tool's begin() raised before starting. Fix the arguments and retry; "
                "no job was started.",
                "traceback": failure,
            }
        ) from None
    if isinstance(started, dict) and started.get("ok") is False:
        raise JobStartError(dict(started))

    registry = _registry()
    with _LOCK:
        job_id = f"job-{registry['next_id']}"
        registry["next_id"] += 1
        job = {
            "id": job_id,
            "kind": str(kind or "job"),
            "status": "running",
            "progress": None,
            "result": None,
            "error": None,
            "hint": None,
            "traceback": None,
            "cancel_error": None,
            "started_at": started_at,
            "finished_at": None,
            "_started_mono": started_mono,
            "_begin": begin,
            "_poll": poll,
            "_cancel": cancel,
            "_timeout": job_timeout,
            "_timer": None,
            "_polling": False,
            "_event": threading.Event(),
            "_owner": _TOKEN,
        }
        registry["jobs"][job_id] = job
        registry["order"].append(job_id)
        _prune(registry)

    try:
        timer = _make_timer(job)
    except Exception:
        _finish(
            job,
            "failed",
            traceback_text=traceback.format_exc(),
            hint="The job timer could not be created; the job was not started.",
        )
        return job_id
    with _LOCK:
        if job["status"] == "running":
            job["_timer"] = timer
        else:
            try:
                timer.stop()
                timer.deleteLater()
            except Exception:
                pass
    return job_id


def _tick(job: dict) -> None:
    """QTimer callback: ask the job for progress and finish it once poll says done."""
    with _LOCK:
        if job["status"] != "running":
            _stop_timer(job)
            return
        if job.get("_owner") is not _TOKEN:
            _mark_lost(job, "A restart replaced the code that owned this job; its result is not available.")
            _stop_timer(job)
            return
        timeout = job.get("_timeout")
        elapsed = time.monotonic() - job["_started_mono"]
        cancel = job.get("_cancel")
        poll = job.get("_poll")
        expired = timeout is not None and elapsed > timeout
        if job.get("_polling") and not expired:
            # A previous poll is still inside a nested event loop; do not overlap it. A timeout
            # must still fire, so it is checked again below even while poll is re-entrant.
            return
    if expired:
        _finish(
            job,
            "failed",
            error=f"Job exceeded its {timeout:g}s timeout.",
            hint="The job was stopped after job_timeout expired.",
        )
        _run_cancel(job, cancel)
        return
    if poll is None:
        _finish(job, "failed", error="The job has no poll callback.")
        return
    with _LOCK:
        if job["status"] != "running":
            return
        job["_polling"] = True
    try:
        update = poll()
    except Exception:
        with _LOCK:
            job["_polling"] = False
        _finish(
            job,
            "failed",
            traceback_text=traceback.format_exc(),
            hint="poll() raised; check get_log for details.",
        )
        return
    with _LOCK:
        job["_polling"] = False
    if not isinstance(update, dict):
        _finish(
            job,
            "failed",
            error="poll() must return a dict like {'done': bool, 'progress': 0..1, 'result': ...}.",
            hint="Return a dict from poll().",
        )
        return
    if update.get("ok") is False:
        failure_traceback = update.get("traceback")
        _finish(
            job,
            "failed",
            error=update.get("error") or "poll() returned ok: false.",
            hint=update.get("hint"),
            traceback_text=str(failure_traceback) if failure_traceback else None,
        )
        return
    if "progress" in update:
        with _LOCK:
            if job["status"] == "running":
                job["progress"] = _progress(update.get("progress"))
    if update.get("done"):
        _finish(job, "done", result=update.get("result"))


def _run_cancel(job: dict, cancel) -> None:
    if cancel is None:
        return
    try:
        cancel()
    except Exception:
        with _LOCK:
            job["cancel_error"] = traceback.format_exc()


def _finish(job: dict, status: str, *, result=None, error=None, hint=None, traceback_text=None) -> None:
    _stop_timer(job)
    if status == "done" and result is not None:
        # JSON conversion can touch Cascadeur objects, so do it once here on the GUI thread
        # instead of on every get_job/list_jobs call in the HTTP thread.
        try:
            result = jsonable(result)
        except Exception:
            status = "failed"
            result = None
            error = "The job result could not be converted to JSON."
            hint = "Return JSON-safe values from poll(), or convert them with common.jsonable."
            traceback_text = traceback.format_exc()
    with _LOCK:
        if job["status"] in _TERMINAL:
            return
        if error is None and traceback_text:
            # The traceback's last line is the exception itself; keep it in "error" so an agent
            # does not have to parse the traceback to find out what went wrong.
            error = _traceback_error(traceback_text)
        job["status"] = status
        job["result"] = result
        job["error"] = str(error) if error else None
        job["hint"] = str(hint) if hint else None
        job["traceback"] = str(traceback_text) if traceback_text else None
        job["finished_at"] = time.time()
        job["_begin"] = None
        job["_poll"] = None
        job["_cancel"] = None
        job["_event"].set()


def _mark_lost(job: dict, reason: str) -> None:
    """Mark a job from another package instance lost without touching its QTimer.

    Called under _LOCK (the timer's own next tick stops it on the GUI thread).
    """
    if job["status"] != "running":
        return
    job["status"] = "lost"
    job["error"] = reason
    job["finished_at"] = time.time()
    job["_event"].set()


def shutdown() -> None:
    """Mark running jobs lost and stop their timers; called when the server stops."""
    registry = getattr(sys, REGISTRY_ATTR, None)
    if not isinstance(registry, dict):
        return
    with _LOCK:
        running = [job for job in registry["jobs"].values() if job["status"] == "running"]
    for job in running:
        _finish(job, "lost", error="The server restarted while the job was running; its result is not available.")


# ---------------------------------------------------------------------------
# Queries (safe from the HTTP thread)
# ---------------------------------------------------------------------------


def _lookup(job_id) -> dict:
    if not isinstance(job_id, str) or not job_id.strip():
        raise ToolError("job_id must be a non-empty string; list_jobs shows the recent job ids.")
    registry = _registry()
    with _LOCK:
        job = registry["jobs"].get(job_id)
    if job is None:
        raise ToolError(f"Unknown job id {job_id!r}; list_jobs shows the recent job ids.")
    _check_owner(job)
    return job


def _check_owner(job: dict) -> None:
    if job["status"] == "running" and job.get("_owner") is not _TOKEN:
        with _LOCK:
            _mark_lost(job, "A restart replaced the code that owned this job; its result is not available.")


def snapshot(job: dict) -> dict:
    """Return one job as a JSON-safe dict.

    The result was already converted once on the GUI thread by _finish(), so this only reads
    plain Python values and is safe to call from the HTTP thread.
    """
    _check_owner(job)
    with _LOCK:
        status = job["status"]
        finished = job["finished_at"]
        data = {
            "job_id": job["id"],
            "kind": job["kind"],
            "status": status,
            "done": status in _TERMINAL,
            "progress": job["progress"],
            "elapsed_s": round((finished or time.time()) - job["started_at"], 3),
            "started_at": round(job["started_at"], 3),
            "finished_at": round(finished, 3) if finished else None,
            "result": job.get("result"),
            "error": job["error"],
            "hint": job.get("hint"),
            "traceback": job["traceback"],
            "cancel_error": job["cancel_error"],
        }
    return data


def get(job_id, wait_s=0) -> dict:
    """Return a job snapshot, optionally waiting (on the caller's thread) for completion."""
    job = _lookup(job_id)
    try:
        wait = float(wait_s or 0)
    except (TypeError, ValueError):
        raise ToolError(f"wait_s must be a number of seconds (got {wait_s!r}).") from None
    wait = max(0.0, min(wait, MAX_WAIT_S))
    if wait and job["status"] == "running":
        job["_event"].wait(wait)
    return snapshot(job)


def recent(limit=20) -> list[dict]:
    """Return up to limit jobs, newest first."""
    try:
        count = int(limit)
    except (TypeError, ValueError):
        raise ToolError(f"limit must be an integer (got {limit!r}).") from None
    count = max(1, min(count, MAX_JOBS))
    registry = _registry()
    with _LOCK:
        # Capture the job objects while _prune() cannot run; snapshotting them afterwards is safe
        # even if another thread prunes them, because the objects are already referenced here.
        job_ids = list(registry["order"])[-count:]
        jobs_list = [registry["jobs"][job_id] for job_id in reversed(job_ids) if job_id in registry["jobs"]]
    return [snapshot(job) for job in jobs_list]


def cancel(job_id) -> dict:
    """Cancel a running job on the GUI thread and return its snapshot."""
    _require_gui_thread("cancel_job")
    job = _lookup(job_id)
    with _LOCK:
        if job["status"] != "running":
            raise ToolError(f"Job {job_id} is already {job['status']}; only a running job can be cancelled.")
        job["status"] = "cancelled"
        job["finished_at"] = time.time()
        job["_event"].set()
        cancel_fn = job.get("_cancel")
        job["_begin"] = None
        job["_poll"] = None
        job["_cancel"] = None
        _stop_timer(job)
    _run_cancel(job, cancel_fn)
    return snapshot(job)


# ---------------------------------------------------------------------------
# Tool integration
# ---------------------------------------------------------------------------


def start_tool_job(tool: dict, arguments: dict):
    """Start the background job declared by a tool dict; called by server._call_tool.

    A background tool keeps its normal fn (the begin callable) and adds poll and optionally cancel
    (and job_timeout) to the dict. poll and cancel receive the same keyword arguments as fn.
    Returns a job id, or the error result dict when begin() refused synchronously.
    """
    _require_gui_thread("A background tool")
    name = tool.get("name") or "job"
    if "handler" in tool:
        raise ToolError(f"Background tool {name!r} must use fn (the begin callable), not handler.")
    fn = tool.get("fn")
    poll = tool.get("poll")
    cancel_fn = tool.get("cancel")
    if not callable(fn):
        raise ToolError(f"Background tool {name!r} needs fn as its begin callable.")
    if not callable(poll):
        raise ToolError(f"Background tool {name!r} needs a poll callable that returns {{done, progress, result}}.")
    if cancel_fn is not None and not callable(cancel_fn):
        raise ToolError(f"Background tool {name!r} has a cancel entry that is not callable.")
    args = dict(arguments or {})
    try:
        return start_job(
            name,
            lambda: fn(**args),
            lambda: poll(**args),
            (lambda: cancel_fn(**args)) if callable(cancel_fn) else None,
            tool.get("job_timeout"),
        )
    except JobStartError as error:
        return error.result


def started_result(started):
    """Format start_tool_job's return value as the MCP tool result (used by server._call_tool)."""
    if isinstance(started, dict):
        return started
    if isinstance(started, str) and started:
        return {"ok": True, "job_id": started, "hint": "call get_job"}
    raise ToolError("The background tool did not return a job id.")
