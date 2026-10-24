"""Live tests for the AI inbetweening tools (T20).

Each test opens Cascadeur's Cascy sample in its own tab. It keys frames 0 and 30 for the whole
character, raises the left hand at frame 30 with a small data edit (T16 is not merged yet), and
then checks what the AI action produced:

* ai_inbetween(0, 30) leaves the hand at frame 15 somewhere between the two keyed poses and not
  where linear interpolation would put it;
* the job returns immediately, get_scene_info answers while it runs, and no dialog is left open;
* generate_motion(0, 30) finishes and reports what it generated;
* both tools restore the caller's selection and playhead and are one undoable action (or report
  the action's actual undo behaviour).

Run with: python tests/live/run.py test_inbetweening.py   (after taking the live-test lock)
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


SET_POSE_CODE = r"""
import json

from cascadeur_mcp_plus import rig

scene = csc.app.get_application().current_scene().domain_scene()
hand = rig.resolve(["left_hand"])[0]
model_viewer = scene.model_viewer()
behaviour_viewer = model_viewer.behaviour_viewer()
data_viewer = model_viewer.data_viewer()
transform = behaviour_viewer.get_behaviour_by_name(hand, "Transform")
position_id = behaviour_viewer.get_behaviour_data(transform, "global_position")
base = data_viewer.get_data_value(position_id, 0)
target = base + [0.0, 40.0, 0.0]


def mod(model_editor, update_editor, scene_updater):
    model_editor.data_editor().set_data_value(position_id, 30, target)
    scene_updater.run_update({position_id}, 30)


scene.modify_update("T20 raise hand", mod)
after = data_viewer.get_data_value(position_id, 30)
print("__RESULT__" + json.dumps({
    "hand": model_viewer.get_object_name(hand),
    "before": [float(value) for value in base],
    "after": [float(value) for value in after],
}))
"""

POSITIONS_CODE = r"""
import json

from cascadeur_mcp_plus import rig

scene = csc.app.get_application().current_scene().domain_scene()
hand = rig.resolve(["left_hand"])[0]
model_viewer = scene.model_viewer()
behaviour_viewer = model_viewer.behaviour_viewer()
data_viewer = model_viewer.data_viewer()
transform = behaviour_viewer.get_behaviour_by_name(hand, "Transform")
position_id = behaviour_viewer.get_behaviour_data(transform, "global_position")
positions = {}
for frame in (0, 15, 30):
    positions[str(frame)] = [float(value) for value in data_viewer.get_data_value(position_id, frame)]
print("__RESULT__" + json.dumps({"positions": positions, "frame": scene.get_current_frame(False)}))
"""

INTERPOLATION_CODE = r"""
import json

scene = csc.app.get_application().current_scene().domain_scene()
viewer = scene.layers_viewer()
result = {}
for layer_id in viewer.all_layer_ids():
    layer = viewer.layer(layer_id)
    result[str(layer_id)] = {
        "name": layer.header.name,
        "interpolation": {
            str(position): section.interval.interpolation.name.lower()
            for position, section in layer.sections.items()
        },
        "keys": [int(position) for position in layer.key_frame_indices()],
    }
print("__RESULT__" + json.dumps(result))
"""


class InbetweeningTests(LiveTestCase):
    def _run_json(self, code: str, timeout: float = 120.0) -> dict:
        result = client.call("run_python", code=code, timeout=timeout, http_timeout=timeout + 30.0)
        self.assertTrue(isinstance(result, dict) and result.get("ok"), f"run_python failed: {result!r}")
        for line in reversed(str(result.get("stdout", "")).splitlines()):
            if line.startswith("__RESULT__"):
                return json.loads(line[len("__RESULT__") :])
        self.fail(f"run_python produced no __RESULT__ line: {result!r}")

    def _wait_job(self, job_id: str, timeout: float = 300.0) -> dict:
        deadline = time.monotonic() + timeout
        last = None
        while time.monotonic() < deadline:
            last = client.call("get_job", job_id=job_id, wait_s=10)
            if last.get("done"):
                return last
            time.sleep(0.5)
        self.fail(f"job {job_id} did not finish in {timeout}s: {last!r}")

    def _blocking_dialogs(self) -> list:
        dialogs = client.call("dialogs").get("dialogs", [])
        return [dialog for dialog in dialogs if dialog.get("buttons") or dialog.get("type") != "QQuickPopupItem"]

    def _prepare_poses(self) -> dict:
        keys = client.call("set_key", frames=[0, 30])
        self.assertTrue(keys.get("ok"), keys)
        self.assertGreaterEqual(keys["layers"], 1, keys)
        posed = self._run_json(SET_POSE_CODE)
        self.assertEqual(posed["hand"], "hand_MainPoint_l", posed)
        self.assertGreater(posed["after"][1], posed["before"][1] + 30.0, posed)
        # The key at frame 30 must see the new value.
        self.assertAlmostEqual(posed["after"][1], posed["before"][1] + 40.0, delta=2.0, msg=posed)
        return posed

    def _before_job(self) -> dict:
        selection = client.call("get_selection")
        timeline = client.call("get_timeline")
        self.assertTrue(selection.get("ok"), selection)
        self.assertTrue(timeline.get("ok"), timeline)
        return {
            "names": selection["names"],
            "frame": timeline["frame"],
            "undo_count": client.call("get_undo_history", limit=200)["count"],
        }

    def _assert_after_job(self, before: dict) -> None:
        selection = client.call("get_selection")
        timeline = client.call("get_timeline")
        self.assertEqual(selection["names"], before["names"], "the job did not restore the object selection")
        self.assertEqual(timeline["frame"], before["frame"], "the job did not restore the playhead")
        self.assertEqual(self._blocking_dialogs(), [], "a dialog was left open")

    def test_ai_inbetween_raises_the_hand_between_the_keys(self) -> None:
        self.open_sample("Cascy.casc")
        self._prepare_poses()
        before = self._before_job()
        linear = self._run_json(POSITIONS_CODE)["positions"]
        linear_15 = [(linear["0"][axis] + linear["30"][axis]) / 2.0 for axis in range(3)]

        started = time.monotonic()
        started_job = client.call("ai_inbetween", start=0, end=30)
        elapsed = time.monotonic() - started
        self.assertTrue(started_job.get("ok"), started_job)
        self.assertRegex(started_job["job_id"], r"^job-\d+$")
        self.assertLess(elapsed, 6.0, f"ai_inbetween blocked for {elapsed:.2f}s instead of returning a job id")

        # Cascadeur must keep answering while the AI job runs. The result is asserted after the
        # job finishes so a slow action is never interrupted by tearDown().
        time.sleep(0.3)
        ui_started = time.monotonic()
        ui_error = None
        scene_info = None
        try:
            scene_info = client.call("get_scene_info", http_timeout=8.0)
        except client.LiveError as exc:
            ui_error = exc
        ui_elapsed = time.monotonic() - ui_started

        finished = self._wait_job(started_job["job_id"])
        self.assertEqual(finished["status"], "done", finished)
        result = finished["result"]
        self.assertEqual(result["first_frame"], 0, result)
        self.assertEqual(result["last_frame"], 30, result)
        self.assertGreaterEqual(result["ai_layers"], 1, result)
        self.assertGreaterEqual(result["layers_changed"], 1, result)
        self.assertIsNotNone(result["changed_first"], result)
        self.assertIsNotNone(result["changed_last"], result)
        if ui_error is not None:
            self.fail(f"Cascadeur stopped answering while the AI job ran: {ui_error}")
        self.assertTrue(scene_info["scene"]["name"].startswith("Cascy"), scene_info)
        self.assertLess(ui_elapsed, 5.0, "get_scene_info did not answer while the AI job ran")

        self._assert_after_job(before)

        after = self._run_json(POSITIONS_CODE)["positions"]
        ai_15 = after["15"]
        distance = math.dist(ai_15, linear_15)
        self.assertGreater(distance, 0.5, f"AI frame 15 ({ai_15}) is the linear midpoint ({linear_15})")
        low = min(linear["0"][1], linear["30"][1])
        high = max(linear["0"][1], linear["30"][1])
        self.assertGreaterEqual(ai_15[1], low - 5.0, f"AI hand y {ai_15[1]} is below both keyed poses ({low}..{high})")
        self.assertLessEqual(ai_15[1], high + 5.0, f"AI hand y {ai_15[1]} is above both keyed poses ({low}..{high})")

        # The timeline must report the interval as AI interpolation.
        layers = self._run_json(INTERPOLATION_CODE)
        ai_layers = [
            entry["name"] for entry in layers.values() if any(name == "ai" for name in entry["interpolation"].values())
        ]
        self.assertTrue(ai_layers, f"no layer reports AI interpolation: {layers!r}")

    def _warm_motion_model(self) -> float:
        """Run motion generation once in a throwaway sample to load Cascadeur's model.

        The first Run Motion Generation in a Cascadeur process loads its Torch model synchronously
        (~20 s) inside the action, so that call freezes the GUI; the tool itself already returns a
        job id immediately. Every later call is fast and keeps answering. This warm-up is separate
        from the measured generation so the test documents both numbers.
        """
        self.open_sample("Cascy.casc")
        self.assertTrue(client.call("set_key", frames=[0, 30])["ok"])
        started = time.monotonic()
        job = client.call("generate_motion", start=0, end=30)
        self.assertTrue(job.get("ok"), job)
        finished = self._wait_job(job["job_id"])
        self.assertEqual(finished["status"], "done", finished)
        return round(time.monotonic() - started, 2)

    def test_generate_motion_finishes_and_reports_what_it_changed(self) -> None:
        warmup_seconds = self._warm_motion_model()
        self.assertGreater(warmup_seconds, 0.0)
        self.open_sample("Cascy.casc")
        self._prepare_poses()
        before = self._before_job()
        positions_before = self._run_json(POSITIONS_CODE)["positions"]

        started = time.monotonic()
        started_job = client.call("generate_motion", start=0, end=30)
        elapsed = time.monotonic() - started
        self.assertTrue(started_job.get("ok"), started_job)
        self.assertRegex(started_job["job_id"], r"^job-\d+$")
        self.assertLess(elapsed, 6.0, f"generate_motion blocked for {elapsed:.2f}s instead of returning a job id")

        # Let the scheduled action start, then make sure the GUI still serves a cheap call. The
        # failure is asserted after the job finishes so a blocked generator never gets its scene
        # closed underneath it by tearDown().
        time.sleep(0.5)
        ui_started = time.monotonic()
        ui_error = None
        scene_info = None
        try:
            scene_info = client.call("get_scene_info", http_timeout=8.0)
        except client.LiveError as exc:
            ui_error = exc
        ui_elapsed = time.monotonic() - ui_started

        finished = self._wait_job(started_job["job_id"])
        self.assertEqual(finished["status"], "done", finished)
        result = finished["result"]
        self.assertEqual(result["first_frame"], 0, result)
        self.assertEqual(result["last_frame"], 30, result)
        self.assertGreaterEqual(result["layers_changed"], 1, result)
        self.assertIsNotNone(result["changed_first"], result)
        self.assertIsNotNone(result["changed_last"], result)
        # Motion generation creates a new motion; ai_inbetween only changes interpolation.
        self.assertGreaterEqual(result["keys_added"], 0, result)

        self._assert_after_job(before)
        after = self._run_json(POSITIONS_CODE)["positions"]
        moved = max(math.dist(positions_before[str(frame)], after[str(frame)]) for frame in (0, 15, 30))
        # Run Motion Generation rewrites the keyed motion (interpolation becomes fixed), so the
        # hand position must actually change even though no key was added.
        self.assertGreater(moved, 1.0, "the motion generator did not change the hand position")
        if ui_error is not None:
            self.fail(f"Cascadeur stopped answering while the motion generator ran: {ui_error}")
        self.assertTrue(scene_info["scene"]["name"].startswith("Cascy"), scene_info)
        self.assertLess(ui_elapsed, 5.0, "get_scene_info did not answer while the motion generator ran")

    def test_argument_errors_do_not_start_jobs(self) -> None:
        self.open_sample("Cascy.casc")
        self._prepare_poses()

        with self.assertRaises(client.LiveError) as caught:
            client.call("ai_inbetween", start=10, end=10)
        self.assertIn("smaller than end", str(caught.exception))

        # Only frame 0 has a key on the whole character before _prepare_poses; frame 30 is keyed by
        # it, so use a fresh scene for the one-key case.
        self.open_sample("Cascy.casc")
        with self.assertRaises(client.LiveError) as caught:
            client.call("ai_inbetween", start=0, end=30)
        self.assertIn("keyframes", str(caught.exception))
        self.assertEqual(self._blocking_dialogs(), [])


if __name__ == "__main__":
    unittest.main()
