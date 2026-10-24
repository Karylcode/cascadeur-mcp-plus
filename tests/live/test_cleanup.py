"""Live tests for the cleanup tools (T19) against Cascadeur 2026.2.3.

Dracorex_walk_cycle.casc is used for the foot-slide test: its hind feet stay on the ground
during a walk cycle, and the Fulcrum Motion Cleaning action reduces the horizontal movement of
the contact points. Cascy.casc is used for the collision test with a deliberately lowered
character, so the feet penetrate the floor before Collision Penetration Cleaning.

Both are background jobs (T03), so the tests check that the job returns a job_id, finishes,
measures before/after values and leaves no dialog open. prepare_keys_by_fulcrums runs first:
Cascadeur only fills its fulcrum/collision cache after Auto keys has analysed the scene.

Run with: python tests/live/run.py test_cleanup.py   (after taking the live-test lock)
"""

from __future__ import annotations

import json
import time
import unittest

try:
    from . import client
    from .fixture import LiveTestCase
except ImportError:  # executed from tests/live
    import client  # type: ignore[no-redef]
    from fixture import LiveTestCase  # type: ignore[no-redef]


LOWER_CASCY = """
import json
from cascadeur_mcp_plus import common, rig

_app, view, scene = common.scenes()
model_viewer = scene.model_viewer()
behaviour_viewer = model_viewer.behaviour_viewer()
data_viewer = model_viewer.data_viewer()
ids = []
for alias in ("pelvis", "left_foot", "right_foot"):
    try:
        ids.extend(rig.resolve([alias]))
    except Exception:
        pass
changed = []
for object_id in ids:
    try:
        behaviour = behaviour_viewer.get_behaviour_by_name(object_id, "Transform")
        data = behaviour_viewer.get_behaviour_data(behaviour, "global_position")
    except Exception:
        continue
    for frame in (0, 5, 10, 15, 20):
        try:
            value = [float(x) for x in data_viewer.get_data_value(data, frame)]
        except Exception:
            continue
        target = [value[0], value[1] - 15.0, value[2]]

        def change(model_editor, _update_editor, scene_updater, data=data, target=target, frame=frame):
            model_editor.data_editor().set_data_value(data, frame, target)
            scene_updater.run_update({data}, frame)

        if scene.modify_update("live test: lower character", change):
            changed.append([str(object_id), frame])
print("__RESULT__" + json.dumps({"changed": len(changed)}))
"""


class CleanupLiveTests(LiveTestCase):
    def _run_json(self, code: str, timeout: float = 90.0) -> dict:
        result = client.call("run_python", code=code, timeout=timeout, http_timeout=timeout + 30.0)
        self.assertTrue(isinstance(result, dict) and result.get("ok"), f"run_python failed: {result!r}")
        for line in reversed(str(result.get("stdout", "")).splitlines()):
            if line.startswith("__RESULT__"):
                return json.loads(line[len("__RESULT__") :])
        self.fail(f"run_python produced no __RESULT__ line: {result!r}")

    def _wait_job(self, job_id: str, timeout: float = 180.0) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            snapshot = client.call("get_job", job_id=job_id, wait_s=5)
            if snapshot.get("done"):
                return snapshot
            time.sleep(0.2)
        self.fail(f"job {job_id} did not finish in {timeout}s")

    def _run_cleanup(self, tool: str, **arguments) -> dict:
        started_at = time.monotonic()
        started = client.call(tool, **arguments)
        elapsed = time.monotonic() - started_at
        self.assertTrue(started.get("ok"), started)
        self.assertEqual(started.get("hint"), "call get_job", started)
        self.assertLess(elapsed, 20.0, f"{tool} blocked for {elapsed:.2f}s instead of returning a job_id")
        finished = self._wait_job(started["job_id"])
        self.assertEqual(finished["status"], "done", finished)
        self.assertIsNone(finished["error"], finished)
        result = finished["result"]
        self.assertIsInstance(result, dict, finished)
        self.assertTrue(result.get("ok"), finished)
        self.assertNotIn("open_dialogs", result, result)
        return result

    def test_foot_sliding_and_prepare_keys(self) -> None:
        self.open_sample("Dracorex_walk_cycle.casc")
        tools = {tool["name"] for tool in client.tools()}
        for name in ("fix_foot_sliding", "fix_collisions", "prepare_keys_by_fulcrums"):
            self.assertIn(name, tools, f"{name} is missing from tools/list")

        # Auto keys fills Cascadeur's fulcrum cache and normalises the walk's keys.
        prepared = self._run_cleanup("prepare_keys_by_fulcrums", start=0, end=39)
        self.assertGreater(prepared["after"]["key_count"], 0, prepared)

        sliding = self._run_cleanup("fix_foot_sliding", start=0, end=39)
        before = sliding["before"]
        after = sliding["same_point_frames"]
        self.assertTrue(before.get("pairs"), f"no fulcrum contact pairs were measured: {sliding}")
        self.assertIsNotNone(after, f"the after measurement did not compare the same points: {sliding}")
        self.assertGreater(after["matched_pairs"], 0, sliding)
        self.assertGreater(before["slide_total_cm"], 0.0, sliding)
        self.assertLess(
            after["slide_total_cm"],
            before["slide_total_cm"],
            f"the contact slide did not decrease: before={before} after={after}",
        )
        self.assertGreater(sliding["change"]["slide_reduction_cm"], 0.0, sliding)
        self.assertEqual(sliding["change"]["basis"], "same_point_frames", sliding)
        self.assertGreater(len(sliding.get("commands") or []), 0, sliding)
        print(
            f"T19 foot: before={before['slide_total_cm']} cm after(same pairs)={after['slide_total_cm']} cm "
            f"reduction={sliding['change']['slide_reduction_cm']} cm "
            f"({sliding['change']['slide_reduction_percent']}%) pairs={after['matched_pairs']}"
        )

    def test_collision_penetration_decreases(self) -> None:
        self.open_sample("Cascy.casc")
        client.call("set_animation_range", first=0, last=20)
        keys = client.call("set_key", frames=[0, 5, 10, 15, 20], controllers=["pelvis", "left_foot", "right_foot"])
        self.assertTrue(keys.get("ok"), keys)
        lowered = self._run_json(LOWER_CASCY, timeout=120.0)
        self.assertGreater(lowered["changed"], 0, lowered)
        client.call("select_frames", start=0, end=20)

        prepared = self._run_cleanup("prepare_keys_by_fulcrums", start=0, end=20)
        self.assertGreater(prepared["after"]["key_count"], 0, prepared)

        cleaned = self._run_cleanup("fix_collisions", start=0, end=20)
        before = cleaned["before"]
        after = cleaned["same_point_frames"]
        self.assertTrue(before.get("point_depths"), f"no collision points were measured: {cleaned}")
        self.assertIsNotNone(after, f"the after measurement did not compare the same points: {cleaned}")
        self.assertGreater(after["matched_pairs"], 0, cleaned)
        self.assertGreater(before["penetration_total_cm"], 0.0, cleaned)
        self.assertLess(
            after["penetration_total_cm"],
            before["penetration_total_cm"],
            f"the penetration did not decrease: before={before} after={after}",
        )
        self.assertGreater(cleaned["change"]["penetration_reduction_cm"], 0.0, cleaned)
        self.assertEqual(cleaned["change"]["basis"], "same_point_frames", cleaned)
        print(
            f"T19 collision: before={before['penetration_total_cm']} cm "
            f"after(same pairs)={after['penetration_total_cm']} cm "
            f"reduction={cleaned['change']['penetration_reduction_cm']} cm "
            f"({cleaned['change']['penetration_reduction_percent']}%) pairs={after['matched_pairs']}"
        )


if __name__ == "__main__":
    unittest.main()
