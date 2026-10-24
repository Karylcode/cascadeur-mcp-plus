"""Background jobs against a real Cascadeur: jobs, progress, UI responsiveness, cancel, errors, restart.

The probe background tool is installed at run time through run_python (into server._TOOLS_BY_NAME),
so no test-only tool ships in the package. It uses the same tool-dict fields a real background tool
uses: fn (begin), poll, cancel and background: True.

Run with: python tests/live/run.py test_jobs.py   (after taking the live-test lock)
"""

from __future__ import annotations

import json
import os
import time
import unittest

try:
    from . import client
    from .fixture import LiveTestCase
except ImportError:
    import client  # type: ignore[no-redef]
    from fixture import LiveTestCase  # type: ignore[no-redef]


# The checkout this test file belongs to; the live server must run the same one, whatever it is
# called (main, T03-fix2, T53, ...).
CHECKOUT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


PROBE_CODE = """
import json
import sys

import cascadeur_mcp_plus.server as server

reg = sys.modules["_cascadeur_mcp_plus_live"]
state = getattr(reg, "t03_state", None)
if state is None:
    state = {}
    reg.t03_state = state
state.update({"polls": 0, "cancelled": False, "last_mode": None})


def begin(mode="normal", ticks=6, fail=False, refuse=False, fail_begin=False, poll_fail=False):
    if fail_begin:
        raise TypeError("T03 begin boom")
    if refuse:
        return {"ok": False, "error": "T03 begin refused", "hint": "use mode=normal"}
    state["polls"] = 0
    state["last_mode"] = mode


def poll(mode="normal", ticks=6, fail=False, refuse=False, fail_begin=False, poll_fail=False):
    if fail:
        raise RuntimeError("T03 probe boom")
    if poll_fail:
        return {"ok": False, "error": "T03 poll refused", "hint": "retry with a valid scene"}
    # A real GUI-thread step on the test scene: this is the "advance the timeline frame by frame"
    # example from the ticket, not just a counter. "quiet" leaves the scene untouched so the
    # official script server's scene-idle event can fire while this job is still running; the
    # restart check uses that mode.
    if mode != "quiet":
        csc.app.get_application().get_action_manager().call_action("Timeline.Next frame")
    state["polls"] += 1
    if mode in ("forever", "quiet"):
        return {"done": False, "progress": min(0.1 + state["polls"] * 0.05, 0.9)}
    if state["polls"] < int(ticks):
        return {"done": False, "progress": state["polls"] / float(ticks)}
    return {
        "done": True,
        "progress": 1.0,
        "result": {"mode": mode, "polls": state["polls"], "frame": scene.get_current_frame()},
    }


def cancel(mode="normal", ticks=6, fail=False, refuse=False, fail_begin=False, poll_fail=False):
    state["cancelled"] = True
    state["cancelled_mode"] = mode


server._TOOLS_BY_NAME["t03_probe"] = {
    "name": "t03_probe",
    "description": "T03 live-test probe background job.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "mode": {"type": "string"},
            "ticks": {"type": "integer"},
            "fail": {"type": "boolean"},
            "refuse": {"type": "boolean"},
            "fail_begin": {"type": "boolean"},
            "poll_fail": {"type": "boolean"},
        },
    },
    "fn": begin,
    "poll": poll,
    "cancel": cancel,
    "background": True,
}

print("__RESULT__" + json.dumps({"installed": "t03_probe" in server._TOOLS_BY_NAME}))
"""

STATE_CODE = """
import json
import sys

reg = sys.modules["_cascadeur_mcp_plus_live"]
state = getattr(reg, "t03_state", {})
print("__RESULT__" + json.dumps({
    "polls": state.get("polls"),
    "cancelled": state.get("cancelled"),
    "cancelled_mode": state.get("cancelled_mode"),
    "last_mode": state.get("last_mode"),
}))
"""

PACKAGE_CODE = """
import json
import os

import cascadeur_mcp_plus

print("__RESULT__" + json.dumps({
    "root": os.path.dirname(os.path.dirname(os.path.abspath(cascadeur_mcp_plus.__file__))),
    "file": cascadeur_mcp_plus.__file__,
}))
"""


def run_json(code: str) -> dict:
    """Run code in Cascadeur and return its __RESULT__ JSON line."""
    result = client.call("run_python", code=code)
    if not isinstance(result, dict) or not result.get("ok"):
        raise AssertionError(f"run_python failed: {result!r}")
    for line in reversed(str(result.get("stdout", "")).splitlines()):
        if line.startswith("__RESULT__"):
            return json.loads(line[len("__RESULT__") :])
    raise AssertionError(f"run_python produced no __RESULT__ line: {result!r}")


class BackgroundJobTests(LiveTestCase):
    def _wait_job(self, job_id: str, predicate, timeout: float = 10.0) -> dict:
        deadline = time.monotonic() + timeout
        last = None
        while time.monotonic() < deadline:
            last = client.call("get_job", job_id=job_id)
            if predicate(last):
                return last
            time.sleep(0.1)
        self.fail(f"job {job_id} did not reach the expected state in {timeout}s: {last!r}")

    def test_background_jobs(self) -> None:
        opened = self.open_sample("Cascy.casc")
        self.assertEqual(opened["name"], "Cascy.casc")
        self.assertGreater(opened["objects"], 0)

        installed = run_json(PROBE_CODE)
        self.assertTrue(installed["installed"])
        tools = {tool["name"] for tool in client.tools()}
        for name in ("get_job", "list_jobs", "cancel_job"):
            self.assertIn(name, tools, f"{name} is missing from tools/list")

        # 1. A begin() that rejects the arguments returns an error synchronously (no job id,
        #    no job record) and an exception from begin() behaves the same way.
        jobs_before = len(client.call("list_jobs", limit=100)["jobs"])
        with self.assertRaises(client.LiveError) as raised:
            client.call("t03_probe", fail_begin=True)
        message = str(raised.exception)
        self.assertIn("T03 begin boom", message)
        self.assertIn("TypeError", message)
        with self.assertRaises(client.LiveError) as raised:
            client.call("t03_probe", refuse=True)
        message = str(raised.exception)
        self.assertIn("T03 begin refused", message)
        self.assertIn("use mode=normal", message)
        self.assertEqual(
            len(client.call("list_jobs", limit=100)["jobs"]),
            jobs_before,
            "a rejected begin() created a job record",
        )

        # 2. A background tool returns its job id immediately.
        started = time.monotonic()
        first = client.call("t03_probe", ticks=20)
        elapsed = time.monotonic() - started
        self.assertTrue(first["ok"], first)
        self.assertEqual(first["hint"], "call get_job")
        job_id = first["job_id"]
        self.assertRegex(job_id, r"^job-\d+$")
        self.assertLess(elapsed, 1.5, f"background tool took {elapsed:.2f}s to return")

        # 3. While it runs, get_job shows progress and the UI tools still answer.
        running = self._wait_job(
            job_id, lambda snap: snap["status"] == "running" and snap["progress"] not in (None, 0.0)
        )
        self.assertGreaterEqual(running["progress"], 0.0)
        self.assertLessEqual(running["progress"], 1.0)
        ui_started = time.monotonic()
        tree = client.call("ui_tree", query="Filter by name")
        scene = client.call("get_scene_info")
        ui_elapsed = time.monotonic() - ui_started
        self.assertTrue(tree["windows"], "ui_tree returned no windows during the job")
        self.assertEqual(scene["scene"]["name"], "Cascy.casc")
        self.assertLess(ui_elapsed, 5.0, "Cascadeur stopped answering ui_tree/get_scene_info during the job")

        finished = client.call("get_job", job_id=job_id, wait_s=20)
        self.assertEqual(finished["status"], "done")
        self.assertTrue(finished["done"])
        self.assertEqual(finished["progress"], 1.0)
        self.assertEqual(finished["result"]["mode"], "normal")
        self.assertEqual(finished["result"]["polls"], 20)
        self.assertIsInstance(finished["result"]["frame"], int)
        self.assertIsNone(finished["error"])
        self.assertLess(finished["elapsed_s"], 30)

        # 4. poll() returning ok: false fails the job with its error and hint, and stops polling.
        poll_failed_id = client.call("t03_probe", poll_fail=True)["job_id"]
        poll_failed = client.call("get_job", job_id=poll_failed_id, wait_s=5)
        self.assertEqual(poll_failed["status"], "failed")
        self.assertEqual(poll_failed["error"], "T03 poll refused")
        self.assertEqual(poll_failed["hint"], "retry with a valid scene")

        # 5. cancel_job stops a running job and calls the tool's cancel hook.
        cancel_id = client.call("t03_probe", mode="forever")["job_id"]
        self._wait_job(cancel_id, lambda snap: snap["status"] == "running" and snap["progress"] not in (None, 0.0))
        cancelled = client.call("cancel_job", job_id=cancel_id)
        self.assertTrue(cancelled["ok"], cancelled)
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertTrue(cancelled["cancelled"])
        self.assertTrue(cancelled["done"])
        state = run_json(STATE_CODE)
        self.assertTrue(state["cancelled"], "the tool's cancel hook was not called")
        self.assertEqual(state["cancelled_mode"], "forever")
        after_cancel = client.call("get_job", job_id=cancel_id)
        self.assertEqual(after_cancel["status"], "cancelled")

        # 6. An exception in poll fails the job with error + traceback, not the server.
        failed_id = client.call("t03_probe", fail=True)["job_id"]
        failed = client.call("get_job", job_id=failed_id, wait_s=5)
        self.assertEqual(failed["status"], "failed")
        self.assertTrue(failed["done"])
        self.assertIn("T03 probe boom", failed["error"])
        self.assertIn("RuntimeError", failed["traceback"])
        self.assertIn("poll", failed["traceback"])
        self.assertEqual(client.call("get_scene_info")["scene"]["name"], "Cascy.casc")

        # 7. list_jobs sees the recent jobs, newest first.
        listing = client.call("list_jobs", limit=20)
        self.assertTrue(listing["ok"], listing)
        listed_ids = [item["job_id"] for item in listing["jobs"]]
        for expected in (job_id, poll_failed_id, cancel_id, failed_id):
            self.assertIn(expected, listed_ids)
        self.assertEqual(listed_ids[0], failed_id)

        # 8. After a restart, a job that was running is reported as lost, a finished job survives,
        #    and the new server works. The running job polls in "quiet" mode (no scene edits), because
        #    start_mcp_plus.py is executed by Cascadeur's official script server on a scene-idle event;
        #    a poll that keeps stepping the timeline would starve that event and time the restart out.
        lost_id = client.call("t03_probe", mode="quiet")["job_id"]
        self._wait_job(lost_id, lambda snap: snap["status"] == "running" and snap["progress"] not in (None, 0.0))
        package = run_json(PACKAGE_CODE)
        root_before = package["root"]
        self.assertEqual(
            os.path.normcase(os.path.abspath(root_before)),
            os.path.normcase(CHECKOUT_ROOT),
            "the live server is not running this checkout",
        )
        info = client.restart(root_before)
        self.assertEqual(info.get("server"), "cascadeur-mcp-plus")
        root_after = run_json(PACKAGE_CODE)["root"]
        self.assertEqual(
            os.path.normcase(os.path.abspath(root_after)),
            os.path.normcase(os.path.abspath(root_before)),
            "restart loaded a different checkout",
        )

        lost = client.call("get_job", job_id=lost_id)
        self.assertEqual(lost["status"], "lost")
        self.assertTrue(lost["done"])
        self.assertIn("restart", lost["error"])
        survived = client.call("get_job", job_id=job_id)
        self.assertEqual(survived["status"], "done")
        self.assertEqual(survived["result"]["mode"], "normal")
        self.assertEqual(survived["result"]["polls"], 20)
        self.assertEqual(client.call("get_scene_info")["scene"]["name"], "Cascy.casc")


if __name__ == "__main__":
    unittest.main()
