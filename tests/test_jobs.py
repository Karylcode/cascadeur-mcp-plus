"""Unit tests for the background-job core (no Cascadeur needed).

Loads cascadeur_mcp_plus/jobs.py through a synthetic package, like tests/test_api.py, and replaces
the QTimer with a fake timer whose tick() the test drives. The live behaviour (a real QTimer on the
GUI thread, ui_tree/get_scene_info still answering, a restart) is covered by tests/live/test_jobs.py.

Run: python -m unittest discover -s tests -v
"""

from __future__ import annotations

import importlib.util
import os
import sys
import threading
import time
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_jobs():
    """Import cascadeur_mcp_plus/jobs.py without the Cascadeur-only package __init__."""
    package = "cascadeur_mcp_plus"
    if package not in sys.modules:
        module = types.ModuleType(package)
        module.__path__ = [os.path.join(ROOT, package)]
        sys.modules[package] = module
    name = package + ".jobs"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, package, "jobs.py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


jobs = load_jobs()


class FakeTimer:
    """Stand-in for the QTimer that would poll the job on Cascadeur's GUI thread."""

    def __init__(self, callback):
        self.callback = callback
        self.running = False
        self.stopped = False
        self.deleted = False

    def start(self):
        self.running = True

    def stop(self):
        self.stopped = True
        self.running = False

    def deleteLater(self):
        self.deleted = True

    def tick(self):
        if self.running:
            self.callback()


class SlowContainsDict(dict):
    """Dict whose membership test can let another thread prune the same key.

    recent() must capture the job objects while holding the registry lock; otherwise this hook
    can delete the key between the "in" test and the indexing (the race the fix removes).
    """

    def __init__(self, *args, on_contains=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.on_contains = on_contains

    def __contains__(self, key):
        present = super().__contains__(key)
        if present and self.on_contains is not None:
            self.on_contains(key)
        return present


class JobTests(unittest.TestCase):
    def setUp(self):
        if hasattr(sys, jobs.REGISTRY_ATTR):
            delattr(sys, jobs.REGISTRY_ATTR)
        self.timers = []

        def make_timer(job):
            timer = FakeTimer(lambda: jobs._tick(job))
            self.timers.append(timer)
            timer.start()
            return timer

        self.saved = {
            "_make_timer": jobs._make_timer,
            "_on_gui_thread": jobs._on_gui_thread,
            "jsonable": jobs.jsonable,
        }
        jobs._make_timer = make_timer
        jobs._on_gui_thread = lambda: True
        jobs.jsonable = lambda value: value
        self.registry = jobs._registry()

    def tearDown(self):
        for job in self.registry["jobs"].values():
            if job.get("_timer") is not None:
                job["_timer"].stop()
        jobs._make_timer = self.saved["_make_timer"]
        jobs._on_gui_thread = self.saved["_on_gui_thread"]
        jobs.jsonable = self.saved["jsonable"]
        if hasattr(sys, jobs.REGISTRY_ATTR):
            delattr(sys, jobs.REGISTRY_ATTR)

    def job(self, job_id):
        return self.registry["jobs"][job_id]

    def timer(self, job_id):
        return self.job(job_id)["_timer"]

    # -- lifecycle ----------------------------------------------------------

    def test_poll_reports_progress_and_result(self):
        calls = []
        polls = {"count": 0}

        def begin():
            calls.append("begin")

        def poll():
            polls["count"] += 1
            if polls["count"] < 3:
                return {"done": False, "progress": polls["count"] / 3}
            return {"done": True, "result": {"value": 7}}

        job_id = jobs.start_job("probe", begin, poll)
        self.assertTrue(job_id.startswith("job-"))
        self.assertEqual(calls, ["begin"])
        timer = self.timer(job_id)

        first = jobs.get(job_id)
        self.assertEqual(first["status"], "running")
        self.assertFalse(first["done"])
        self.assertIsNone(first["progress"])
        self.assertIsNone(first["result"])

        timer.tick()
        second = jobs.get(job_id)
        self.assertEqual(second["status"], "running")
        self.assertAlmostEqual(second["progress"], 1 / 3, places=4)

        timer.tick()
        third = jobs.get(job_id)
        self.assertAlmostEqual(third["progress"], 2 / 3, places=4)
        self.assertFalse(third["done"])

        timer.tick()
        done = jobs.get(job_id)
        self.assertEqual(done["status"], "done")
        self.assertTrue(done["done"])
        self.assertEqual(done["result"], {"value": 7})
        self.assertIsNotNone(done["finished_at"])
        self.assertIsNone(done["error"])
        self.assertIsNone(done["traceback"])
        self.assertTrue(timer.stopped)
        self.assertIsNone(self.job(job_id)["_timer"])

    def test_poll_exception_fails_job_with_traceback(self):
        def poll():
            raise RuntimeError("T03 probe boom")

        job_id = jobs.start_job("probe", lambda: None, poll)
        self.timer(job_id).tick()
        snap = jobs.get(job_id)
        self.assertEqual(snap["status"], "failed")
        self.assertTrue(snap["done"])
        self.assertIn("T03 probe boom", snap["error"])
        self.assertIn("RuntimeError", snap["traceback"])
        self.assertIn("poll", snap["traceback"])
        self.assertIsNone(self.job(job_id)["_timer"])

        # The registry keeps working after a failed job.
        other = jobs.start_job("after-failure", lambda: None, lambda: {"done": True, "result": 1})
        self.timer(other).tick()
        self.assertEqual(jobs.get(other)["status"], "done")
        self.assertEqual(jobs.get(other)["result"], 1)

    def test_begin_exception_is_returned_synchronously(self):
        def begin():
            raise TypeError("bad argument")

        tool = {"name": "probe_tool", "fn": begin, "poll": lambda: {"done": True}, "background": True}
        result = jobs.start_tool_job(tool, {})
        self.assertEqual(result["ok"], False)
        self.assertIn("TypeError", result["error"])
        self.assertIn("bad argument", result["error"])
        self.assertIn("Traceback", result["traceback"])
        self.assertIn("no job was started", result["hint"])
        self.assertEqual(self.registry["jobs"], {})
        self.assertEqual(self.registry["next_id"], 1)
        self.assertEqual(self.timers, [])

    def test_begin_ok_false_is_returned_synchronously(self):
        def begin():
            return {"ok": False, "error": "No object named X", "hint": "get_scene_info lists objects"}

        tool = {"name": "probe_tool", "fn": begin, "poll": lambda: {"done": True}, "background": True}
        result = jobs.start_tool_job(tool, {})
        self.assertEqual(result["ok"], False)
        self.assertEqual(result["error"], "No object named X")
        self.assertEqual(result["hint"], "get_scene_info lists objects")
        self.assertEqual(self.registry["jobs"], {})
        self.assertEqual(self.registry["next_id"], 1)
        self.assertEqual(self.timers, [])

    def test_start_job_raises_job_start_error_and_keeps_no_job(self):
        with self.assertRaises(jobs.JobStartError) as caught:
            jobs.start_job("probe", lambda: {"ok": False, "error": "nope"}, lambda: {"done": True})
        self.assertEqual(caught.exception.result["error"], "nope")
        self.assertEqual(self.registry["jobs"], {})

    def test_poll_ok_false_fails_job_with_error_and_hint(self):
        calls = []

        def poll():
            calls.append(True)
            return {"ok": False, "error": "object was deleted", "hint": "re-read the scene"}

        job_id = jobs.start_job("probe", lambda: None, poll)
        timer = self.timer(job_id)
        timer.tick()
        snap = jobs.get(job_id)
        self.assertEqual(snap["status"], "failed")
        self.assertEqual(snap["error"], "object was deleted")
        self.assertEqual(snap["hint"], "re-read the scene")
        self.assertTrue(timer.stopped)
        timer.tick()
        self.assertEqual(len(calls), 1)

    def test_poll_must_return_a_dict(self):
        job_id = jobs.start_job("probe", lambda: None, lambda: "not a dict")
        self.timer(job_id).tick()
        snap = jobs.get(job_id)
        self.assertEqual(snap["status"], "failed")
        self.assertIn("dict", snap["error"])

    def test_progress_is_clamped_and_none_is_kept(self):
        updates = iter([{"done": False, "progress": 4.5}, {"done": False, "progress": None}, {"done": False}])
        job_id = jobs.start_job("probe", lambda: None, lambda: next(updates))
        timer = self.timer(job_id)
        timer.tick()
        self.assertEqual(jobs.get(job_id)["progress"], 1.0)
        timer.tick()
        self.assertIsNone(jobs.get(job_id)["progress"])
        timer.tick()
        self.assertIsNone(jobs.get(job_id)["progress"])

    def test_result_is_converted_once_when_job_finishes(self):
        sentinel = object()
        calls = []
        saved = jobs.jsonable

        def convert(value):
            calls.append(value)
            return "converted"

        jobs.jsonable = convert
        try:
            job_id = jobs.start_job("probe", lambda: None, lambda: {"done": True, "result": sentinel})
            self.timer(job_id).tick()
            self.assertEqual(calls, [sentinel])
            snap = jobs.get(job_id)
            self.assertEqual(snap["status"], "done")
            self.assertEqual(snap["result"], "converted")
            jobs.recent(5)
            self.assertEqual(len(calls), 1)
        finally:
            jobs.jsonable = saved

    def test_result_conversion_failure_marks_job_failed(self):
        saved = jobs.jsonable

        def convert(value):
            raise TypeError("not JSON")

        jobs.jsonable = convert
        try:
            job_id = jobs.start_job("probe", lambda: None, lambda: {"done": True, "result": {"bad": 1}})
            self.timer(job_id).tick()
            snap = jobs.get(job_id)
            self.assertEqual(snap["status"], "failed")
            self.assertIn("could not be converted", snap["error"])
            self.assertIn("TypeError", snap["traceback"])
            self.assertIsNone(snap["result"])
        finally:
            jobs.jsonable = saved

    # -- cancel, timeout, shutdown -----------------------------------------

    def test_cancel_calls_cancel_and_marks_cancelled(self):
        cancelled = []
        job_id = jobs.start_job(
            "probe",
            lambda: None,
            lambda: {"done": False, "progress": 0.1},
            lambda: cancelled.append(True),
        )
        timer = self.timer(job_id)
        timer.tick()
        snap = jobs.cancel(job_id)
        self.assertEqual(snap["status"], "cancelled")
        self.assertTrue(snap["done"])
        self.assertEqual(cancelled, [True])
        self.assertTrue(timer.stopped)
        self.assertIsNone(self.job(job_id)["_timer"])
        with self.assertRaises(jobs.ToolError):
            jobs.cancel(job_id)

    def test_cancel_error_is_reported(self):
        def cancel():
            raise RuntimeError("cancel boom")

        job_id = jobs.start_job("probe", lambda: None, lambda: {"done": False}, cancel)
        snap = jobs.cancel(job_id)
        self.assertEqual(snap["status"], "cancelled")
        self.assertIn("cancel boom", snap["cancel_error"])

    def test_timeout_fails_and_cancels(self):
        cancelled = []
        job_id = jobs.start_job(
            "probe",
            lambda: None,
            lambda: {"done": False, "progress": 0.5},
            lambda: cancelled.append(True),
            timeout=0.01,
        )
        time.sleep(0.02)
        self.timer(job_id).tick()
        snap = jobs.get(job_id)
        self.assertEqual(snap["status"], "failed")
        self.assertIn("timeout", snap["error"])
        self.assertEqual(cancelled, [True])
        self.assertIsNone(self.job(job_id)["_timer"])

    def test_timeout_fires_while_poll_is_reentrant(self):
        polls = []
        cancelled = []
        job_id = jobs.start_job(
            "probe",
            lambda: None,
            lambda: polls.append(True) or {"done": False},
            lambda: cancelled.append(True),
            timeout=0.01,
        )
        timer = self.timer(job_id)
        self.job(job_id)["_polling"] = True
        time.sleep(0.02)
        timer.tick()
        snap = jobs.get(job_id)
        self.assertEqual(snap["status"], "failed")
        self.assertIn("timeout", snap["error"])
        self.assertEqual(cancelled, [True])
        self.assertEqual(polls, [])
        self.assertTrue(timer.stopped)

    def test_poll_guard_skips_only_until_poll_returns(self):
        polls = []

        def poll():
            polls.append(True)
            return {"done": False}

        job_id = jobs.start_job("probe", lambda: None, poll)
        timer = self.timer(job_id)
        self.job(job_id)["_polling"] = True
        timer.tick()
        self.assertEqual(polls, [])
        self.job(job_id)["_polling"] = False
        timer.tick()
        self.assertEqual(polls, [True])

    def test_shutdown_marks_running_jobs_lost(self):
        job_id = jobs.start_job("probe", lambda: None, lambda: {"done": True})
        timer = self.timer(job_id)
        jobs.shutdown()
        snap = jobs.get(job_id)
        self.assertEqual(snap["status"], "lost")
        self.assertTrue(snap["done"])
        self.assertIn("restarted", snap["error"])
        self.assertTrue(timer.stopped)
        self.assertIsNone(self.job(job_id)["_timer"])

    def test_job_from_old_package_instance_is_lost(self):
        job_id = jobs.start_job("probe", lambda: None, lambda: {"done": True})
        self.job(job_id)["_owner"] = object()
        snap = jobs.get(job_id)
        self.assertEqual(snap["status"], "lost")
        self.assertIn("restart", snap["error"])

    # -- queries ------------------------------------------------------------

    def test_get_waits_for_completion(self):
        job_id = jobs.start_job("probe", lambda: None, lambda: {"done": True, "result": "ok"})
        job = self.job(job_id)

        def finish():
            time.sleep(0.05)
            jobs._tick(job)

        thread = threading.Thread(target=finish)
        thread.start()
        started = time.monotonic()
        snap = jobs.get(job_id, wait_s=5)
        elapsed = time.monotonic() - started
        thread.join()
        self.assertEqual(snap["status"], "done")
        self.assertEqual(snap["result"], "ok")
        self.assertGreaterEqual(elapsed, 0.04)
        self.assertLess(elapsed, 2.0)

    def test_get_caps_the_wait(self):
        saved = jobs.MAX_WAIT_S
        jobs.MAX_WAIT_S = 0.05
        try:
            job_id = jobs.start_job("probe", lambda: None, lambda: {"done": False})
            started = time.monotonic()
            snap = jobs.get(job_id, wait_s=999)
            self.assertEqual(snap["status"], "running")
            self.assertLess(time.monotonic() - started, 1.0)
        finally:
            jobs.MAX_WAIT_S = saved

    def test_unknown_job_ids(self):
        for bad in ("", "   ", "job-999", None):
            with self.assertRaises(jobs.ToolError):
                jobs.get(bad)
        with self.assertRaises(jobs.ToolError):
            jobs.cancel("job-999")

    def test_recent_jobs_are_newest_first(self):
        ids = [jobs.start_job(f"probe-{index}", lambda: None, lambda: {"done": False}) for index in range(3)]
        items = jobs.recent(2)
        self.assertEqual([item["job_id"] for item in items], [ids[-1], ids[-2]])
        self.assertEqual(items[0]["kind"], "probe-2")
        self.assertEqual(len(jobs.recent(99)), 3)

    def test_registry_creation_is_shared_by_concurrent_callers(self):
        if hasattr(sys, jobs.REGISTRY_ATTR):
            delattr(sys, jobs.REGISTRY_ATTR)
        barrier = threading.Barrier(8)
        registries = []

        def worker():
            barrier.wait()
            registries.append(jobs._registry())

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(len(registries), 8)
        self.assertEqual(len({id(registry) for registry in registries}), 1)
        self.assertIs(registries[0], getattr(sys, jobs.REGISTRY_ATTR))

    def test_recent_keeps_prune_out_of_its_indexing(self):
        ids = [jobs.start_job(f"probe-{index}", lambda: None, lambda: {"done": False}) for index in range(3)]
        victim = ids[-1]
        pruned = threading.Event()
        started_pruner = []

        def on_contains(key):
            if key != victim or started_pruner:
                return
            started_pruner.append(key)

            def prune():
                with jobs._LOCK:
                    self.registry["jobs"].pop(key, None)
                    try:
                        self.registry["order"].remove(key)
                    except ValueError:
                        pass
                pruned.set()

            threading.Thread(target=prune).start()
            pruned.wait(0.5)

        slow = SlowContainsDict(self.registry["jobs"], on_contains=on_contains)
        self.registry["jobs"] = slow
        try:
            items = jobs.recent(3)
        finally:
            slow.on_contains = None
        self.assertTrue(pruned.wait(1.0))
        self.assertIn(victim, [item["job_id"] for item in items])

    def test_prune_keeps_running_jobs(self):
        saved = jobs.MAX_JOBS
        jobs.MAX_JOBS = 2
        try:
            running = jobs.start_job("running", lambda: None, lambda: {"done": False})
            finished = jobs.start_job("finished", lambda: None, lambda: {"done": True})
            self.timer(finished).tick()
            third = jobs.start_job("third", lambda: None, lambda: {"done": True})
            self.timer(third).tick()
            self.assertIn(running, self.registry["jobs"])
            self.assertNotIn(finished, self.registry["jobs"])
            self.assertIn(third, self.registry["jobs"])
        finally:
            jobs.MAX_JOBS = saved

    # -- tool integration ---------------------------------------------------

    def test_started_result_formats_job_id_and_passes_error_dict(self):
        self.assertEqual(
            jobs.started_result("job-7"),
            {"ok": True, "job_id": "job-7", "hint": "call get_job"},
        )
        error = {"ok": False, "error": "bad argument", "hint": "fix it"}
        self.assertIs(jobs.started_result(error), error)
        with self.assertRaises(jobs.ToolError):
            jobs.started_result(None)

    def test_start_tool_job_binds_arguments_and_timeout(self):
        seen = []

        def begin(mode, count=2):
            seen.append(("begin", mode, count))

        def poll(mode, count=2):
            seen.append(("poll", mode, count))
            return {"done": len(seen) >= 4, "result": [mode, count]}

        def cancel(mode, count=2):
            seen.append(("cancel", mode, count))

        tool = {
            "name": "probe_tool",
            "fn": begin,
            "poll": poll,
            "cancel": cancel,
            "background": True,
            "job_timeout": 30,
        }
        job_id = jobs.start_tool_job(tool, {"mode": "fast", "count": 5})
        self.assertEqual(self.job(job_id)["kind"], "probe_tool")
        self.assertEqual(self.job(job_id)["_timeout"], 30.0)
        self.assertEqual(seen, [("begin", "fast", 5)])
        timer = self.timer(job_id)
        timer.tick()
        timer.tick()
        timer.tick()
        self.assertEqual(jobs.get(job_id)["status"], "done")
        self.assertEqual(jobs.get(job_id)["result"], ["fast", 5])
        self.assertEqual(seen[-1], ("poll", "fast", 5))

    def test_start_tool_job_rejects_bad_definitions(self):
        def poll():
            return {"done": True}

        with self.assertRaises(jobs.ToolError):
            jobs.start_tool_job({"name": "no_poll", "fn": lambda: None, "background": True}, {})
        with self.assertRaises(jobs.ToolError):
            jobs.start_tool_job({"name": "no_fn", "poll": poll, "background": True}, {})
        with self.assertRaises(jobs.ToolError):
            jobs.start_tool_job({"name": "handler", "handler": lambda: None, "poll": poll, "background": True}, {})
        with self.assertRaises(jobs.ToolError):
            jobs.start_tool_job(
                {"name": "bad_cancel", "fn": lambda: None, "poll": poll, "cancel": 3, "background": True}, {}
            )


if __name__ == "__main__":
    unittest.main()
