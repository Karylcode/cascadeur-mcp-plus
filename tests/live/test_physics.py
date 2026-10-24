"""Live tests for the AutoPhysics tools (T18) against Cascadeur 2026.2.3.

Backflip_animation.casc is the test scene: it has a real animation (unlike the one-frame Cascy
scenes other tickets must not run physics on), so the Physics Assistant has something to solve.
The test covers the ticket's completion conditions:

* the Physics Assistant state is readable and agrees with the QML toolbar / toolManager;
* physics_enable and physics_freeze are idempotent and the state stays consistent;
* physics_snap is a background job that returns immediately, keeps get_scene_info and ui_tree
  answering while it runs, and returns a changed frame range;
* the pelvis trajectory actually changed at airborne frames (compared before/after with
  run_python, because T16's get_pose is not merged yet);
* one Cascadeur undo (or the number reported in the assertion message) reverts the snap.

Run with: python tests/live/run.py test_physics.py   (after taking the live-test lock)
"""

from __future__ import annotations

import json
import math
import time
import unittest

try:
    from . import client
    from .fixture import LiveTestCase
except ImportError:  # executed from tests/live
    import client  # type: ignore[no-redef]
    from fixture import LiveTestCase  # type: ignore[no-redef]


PELVIS_TRACK = """
import json
import cascadeur_mcp_plus.rig as rig
from cascadeur_mcp_plus import common

_app, view, scene = common.scenes()
ids = rig.resolve(["pelvis"])
model_viewer = scene.model_viewer()
behaviour_viewer = model_viewer.behaviour_viewer()
data_viewer = model_viewer.data_viewer()
behaviour = behaviour_viewer.get_behaviour_by_name(ids[0], "Transform")
data = behaviour_viewer.get_behaviour_data(behaviour, "global_position")
boundary = view.animation_boundary()
first, last = int(boundary.first_frame), int(boundary.last_frame)
positions = {}
for frame in range(first, last + 1):
    positions[frame] = [round(float(value), 6) for value in data_viewer.get_data_value(data, frame)]
print("__RESULT__" + json.dumps({"first": first, "last": last, "positions": positions}))
"""

QML_STATE = """
import json
import cascadeur_mcp_plus.tools.physics as physics
from cascadeur_mcp_plus import ui

manager = physics._auto_physics_manager()
properties = {}
if manager is not None:
    for name in ("isAutoPhysicsEnabled", "isFrozenAutoPhysics", "isAllFulcrumPoints", "isOnlyKeyFulcrumPoints"):
        try:
            properties[name] = manager.property(name)
        except Exception as exc:
            properties[name] = "error: " + repr(exc)
print("__RESULT__" + json.dumps({
    "manager": manager is not None,
    "properties": properties,
    "buttons": physics._action_states(),
}))
"""


STALE_SNAP_STATE = """
import json
import sys
import time

import cascadeur_mcp_plus.tools.physics as physics

setattr(sys, physics.SNAP_STATE_KEY, {
    "started": physics._now(),
    "started_at": time.time(),
    "job_id": "job-does-not-exist",
})
print("__RESULT__" + json.dumps({"set": True}))
"""


class PhysicsLiveTests(LiveTestCase):
    def _run_json(self, code: str, timeout: float = 90.0) -> dict:
        result = client.call("run_python", code=code, timeout=timeout, http_timeout=timeout + 30.0)
        self.assertTrue(isinstance(result, dict) and result.get("ok"), f"run_python failed: {result!r}")
        for line in reversed(str(result.get("stdout", "")).splitlines()):
            if line.startswith("__RESULT__"):
                return json.loads(line[len("__RESULT__") :])
        self.fail(f"run_python produced no __RESULT__ line: {result!r}")

    def _track(self) -> dict:
        data = self._run_json(PELVIS_TRACK)
        # JSON object keys are strings; the comparisons below index frames as integers.
        data["positions"] = {int(frame): position for frame, position in data["positions"].items()}
        return data

    def _qml_state(self) -> dict:
        return self._run_json(QML_STATE)

    def _wait_job(self, job_id: str, timeout: float = 90.0) -> tuple[dict, list]:
        """Wait for a job, returning its last snapshot and the snapshots seen while running."""
        running = []
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            snapshot = client.call("get_job", job_id=job_id, wait_s=5)
            if snapshot.get("status") == "running":
                running.append(snapshot)
            if snapshot.get("done"):
                return snapshot, running
            time.sleep(0.2)
        self.fail(f"job {job_id} did not finish in {timeout}s")

    def assertToolFails(self, tool: str, message: str, **arguments) -> None:
        with self.assertRaises(client.LiveError) as caught:
            client.call(tool, **arguments)
        self.assertIn(message, str(caught.exception))

    def test_autophysics_state_toggles_priority_and_snap(self) -> None:
        self.open_sample("Backflip_animation.casc")

        tools = {tool["name"] for tool in client.tools()}
        for name in (
            "get_physics_state",
            "physics_enable",
            "physics_snap",
            "physics_freeze",
            "physics_set_priority_frame",
            "physics_show_fulcrums",
        ):
            self.assertIn(name, tools, f"{name} is missing from tools/list")

        # 1. The state is readable from the same QML manager the toolbar uses.
        state = client.call("get_physics_state")
        self.assertTrue(state.get("ok"), state)
        self.assertIn(state["source"], ("toolManager", "toolbar buttons"), state)
        self.assertIsNotNone(state["enabled"], state)
        self.assertIsInstance(state["only_key_fulcrums"], bool, state)
        self.assertGreaterEqual(state["last_frame"], state["first_frame"], state)
        self.assertIsInstance(state["priority_frames"], list, state)
        self.assertEqual(state["auto_physics_behaviours"] >= 1, True, state)
        self.assertGreater(state["fps"], 0, state)
        self.assertAlmostEqual(state["gravity_cm_per_frame2"], 980.0 / (state["fps"] ** 2), places=4)

        qml = self._qml_state()
        self.assertTrue(qml["manager"], f"the AutoPhysics toolbar's toolManager is not reachable: {qml}")
        self.assertEqual(
            bool(qml["properties"]["isAutoPhysicsEnabled"]),
            state["enabled"],
            f"get_physics_state and the QML button disagree: {state} / {qml}",
        )

        # 2. The fulcrum visualizer is a separate tool that Cascadeur refuses to show while the
        #    Physics Assistant is on, so test it before enabling physics.
        shown = client.call("physics_show_fulcrums", enabled=True)
        self.assertTrue(shown.get("ok"), shown)
        self.assertTrue(shown["show_fulcrums"], shown)
        shown_again = client.call("physics_show_fulcrums", enabled=True)
        self.assertFalse(shown_again["changed"], shown_again)
        qml = self._qml_state()
        self.assertTrue(qml["properties"]["isAllFulcrumPoints"], qml)
        self.assertTrue(qml["buttons"].get("AutoPhysicsTool.Show all fucrum points"), qml)
        hidden = client.call("physics_show_fulcrums", enabled=False)
        self.assertTrue(hidden.get("ok"), hidden)
        self.assertFalse(hidden["show_fulcrums"], hidden)

        # 3. physics_enable is idempotent and the interface follows it.
        off = client.call("physics_enable", enabled=False)
        self.assertTrue(off.get("ok"), off)
        self.assertFalse(off["enabled"], off)
        off_again = client.call("physics_enable", enabled=False)
        self.assertFalse(off_again["changed"], off_again)
        on = client.call("physics_enable", enabled=True)
        self.assertTrue(on.get("ok"), on)
        self.assertTrue(on["enabled"], on)
        on_again = client.call("physics_enable", enabled=True)
        self.assertFalse(on_again["changed"], on_again)
        qml = self._qml_state()
        self.assertTrue(qml["properties"]["isAutoPhysicsEnabled"], qml)
        self.assertTrue(qml["buttons"].get("AutoPhysicsTool.Switch Auto Physics"), qml)
        self.assertToolFails("physics_show_fulcrums", "simultaneously", enabled=True)

        # 4. freeze is idempotent and visible in both state sources.
        frozen = client.call("physics_freeze", enabled=True)
        self.assertTrue(frozen.get("ok"), frozen)
        self.assertTrue(frozen["frozen"], frozen)
        frozen_again = client.call("physics_freeze", enabled=True)
        self.assertFalse(frozen_again["changed"], frozen_again)
        qml = self._qml_state()
        self.assertTrue(qml["properties"]["isFrozenAutoPhysics"], qml)
        self.assertTrue(qml["buttons"].get("AutoPhysicsTool.Switch Frozen Auto Physics"), qml)
        unfrozen = client.call("physics_freeze", enabled=False)
        self.assertTrue(unfrozen.get("ok"), unfrozen)
        self.assertFalse(unfrozen["frozen"], unfrozen)

        # 5. A keyed frame can become a priority frame, twice in a row without toggling it off.
        keys = client.call("list_keys", start=state["first_frame"], end=state["last_frame"])
        self.assertTrue(keys.get("ok"), keys)
        self.assertTrue(keys["key_frames"], f"Backflip has no keyframes to prioritise: {keys}")
        key_frame = keys["key_frames"][min(1, len(keys["key_frames"]) - 1)]
        playhead_before = client.call("get_timeline")["frame"]
        priority_before = client.call("get_physics_state")["priority_frames"]
        # An out-of-range frame used to clamp the playhead and toggle frame 123; it must now be
        # rejected before the action, with the playhead and priority frames unchanged.
        self.assertToolFails("physics_set_priority_frame", "500", frame=500)
        self.assertEqual(client.call("get_timeline")["frame"], playhead_before)
        self.assertEqual(client.call("get_physics_state")["priority_frames"], priority_before)
        priority = client.call("physics_set_priority_frame", frame=key_frame)
        self.assertTrue(priority.get("ok"), priority)
        self.assertTrue(priority["priority"], priority)
        self.assertIn(key_frame, priority["priority_frames"], priority)
        self.assertEqual(client.call("get_timeline")["frame"], playhead_before, "the playhead was not restored")
        priority_again = client.call("physics_set_priority_frame", frame=key_frame)
        self.assertTrue(priority_again.get("ok"), priority_again)
        self.assertFalse(priority_again["changed"], priority_again)
        state = client.call("get_physics_state")
        self.assertIn(key_frame, state["priority_frames"], state)

        # 6. Snap as a background job: it returns immediately, the UI keeps answering, and the
        #    pelvis trajectory changes. A stale busy flag from a job that no longer exists must
        #    not block the new snap (item 1 of the code review).
        self._run_json(STALE_SNAP_STATE)
        before = self._track()
        started_at = time.monotonic()
        started = client.call("physics_snap")
        elapsed = time.monotonic() - started_at
        self.assertTrue(started.get("ok"), started)
        self.assertEqual(started.get("hint"), "call get_job", started)
        self.assertLess(elapsed, 2.0, f"physics_snap took {elapsed:.2f}s to return")
        job_id = started["job_id"]

        def cancel_snap():
            # Never let tearDown close the scene under a running poll.
            try:
                snapshot = client.call("get_job", job_id=job_id)
                if not snapshot.get("done"):
                    client.call("cancel_job", job_id=job_id)
            except Exception:
                pass

        self.addCleanup(cancel_snap)

        # During the job the HTTP thread must keep serving; each call is bounded by the client.
        probe_times = []
        saw_running = False
        for _ in range(6):
            probe_started = time.monotonic()
            scene = client.call("get_scene_info")
            probe_times.append(time.monotonic() - probe_started)
            self.assertEqual(scene["scene"]["name"], "Backflip_animation.casc")
            self.assertGreater(len(scene["objects"]["by_type"]), 0, scene)
            timeline = client.call("get_timeline")
            self.assertTrue(timeline.get("ok"), timeline)
            tree = client.call("ui_tree", query="Filter by name")
            self.assertTrue(tree.get("windows"), tree)
            snapshot = client.call("get_job", job_id=job_id)
            saw_running = saw_running or snapshot.get("status") == "running"
            if snapshot.get("done"):
                break
            time.sleep(0.2)
        self.assertLess(max(probe_times), 5.0, f"get_scene_info took too long during the job: {probe_times}")

        finished, running_snapshots = self._wait_job(job_id)
        self.assertEqual(finished["status"], "done", finished)
        self.assertTrue(finished["done"], finished)
        result = finished["result"]
        self.assertIsInstance(result, dict, finished)
        self.assertTrue(result.get("ok"), finished)
        self.assertIsNone(finished["error"], finished)
        self.assertTrue(
            saw_running or running_snapshots or result.get("duration_s", 0.0) < 2.0,
            "the snap job lasted more than 2 s but no call ever saw it running",
        )
        self.assertEqual(result["frames"], {"first": before["first"], "last": before["last"]}, result)
        self.assertGreater(result["changed_frame_count"], 0, f"snap changed nothing: {finished}")
        self.assertIsNotNone(result["changed_first"], result)
        self.assertIsNotNone(result["changed_last"], result)
        self.assertGreater(result["max_delta_cm"], 0.01, f"snap moved nothing measurable: {result}")

        # 7. Compare the pelvis trajectory frame by frame and require an airborne change.
        after = self._track()
        self.assertEqual(after["first"], before["first"], after)
        self.assertEqual(after["last"], before["last"], after)
        deltas = {}
        for frame, position in before["positions"].items():
            current = after["positions"][frame]
            deltas[int(frame)] = math.dist(position, current)
        changed = [frame for frame, delta in deltas.items() if delta > 0.01]
        self.assertTrue(changed, f"no pelvis frame changed: before={before['positions']} after={after['positions']}")
        ys = [position[1] for position in before["positions"].values()]
        lowest, highest = min(ys), max(ys)
        airborne = [
            frame for frame, position in before["positions"].items() if position[1] > lowest + (highest - lowest) * 0.25
        ]
        self.assertTrue(airborne, f"could not identify airborne frames from pelvis y {ys}")
        airborne_changed = sorted(set(changed) & set(airborne))
        self.assertTrue(
            airborne_changed,
            f"the pelvis moved ({changed}) but not on an airborne frame ({airborne}); before/after y: "
            f"{[(frame, before['positions'][frame][1], after['positions'][frame][1]) for frame in sorted(changed)[:10]]}",
        )
        largest = max(changed, key=lambda frame: deltas[frame])
        self.assertGreater(deltas[largest], 0.01, deltas)

        # 8. Undo. The snap is Cascadeur's own command (not an MCP: step), so try the real undo
        #    action and report how many steps it takes. The ticket allows documenting the actual
        #    behaviour, but it must not take more than five steps to restore the pelvis.
        target = largest
        before_position = before["positions"][target]
        snapped_position = after["positions"][target]
        undos = 0
        restored = False
        history = client.call("get_undo_history", limit=10)
        for _ in range(5):
            undo = client.call("undo", steps=1)
            if not undo.get("ok"):
                break
            undos += 1
            current = self._track()["positions"][target]
            if math.dist(current, before_position) <= 0.01:
                restored = True
                break
        self.assertGreaterEqual(undos, 1, f"undo did nothing; history={history}")
        self.assertTrue(
            restored,
            f"the snap did not revert within 5 undo steps (target frame {target}, before={before_position}, "
            f"snapped={snapped_position}, now={self._track()['positions'][target]}, history={history})",
        )
        self.assertNotEqual(before_position, snapped_position, "the chosen frame did not change at all")


if __name__ == "__main__":
    unittest.main()
