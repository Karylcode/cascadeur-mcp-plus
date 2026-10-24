"""Live tests for the AutoPosing tools and autoposing helpers (T15).

Each test opens Cascy.casc in its own tab; the fixture discards it in tearDown and checks the
user's scenes, current scene and frame are unchanged. The tests use Cascadeur's own data viewer
to move control points and read them back, so the results do not come from the tools' own
answers.

Verified flow on Cascadeur 2026.2.3 (2026-10-24): lock the frame with AutoPosing.Autolock
(autoposing.lock_pose / lock_controllers), write the pose, then run AutoPosingTool.update
(autoposing.solve). The locked hand keeps the scripted position while the free elbow and upper
arm follow; the clavicle and the right foot stay. Locking after the write locks the moved chain
too, so the body cannot follow.

Run with: python tests/live/run.py test_autoposing.py
"""

from __future__ import annotations

import json
import unittest

try:  # imported as live.test_autoposing by unittest discovery
    from . import client
    from .fixture import LiveTestCase
except ImportError:  # executed by run.py
    import client  # type: ignore[no-redef]
    from fixture import LiveTestCase  # type: ignore[no-redef]

FRAME = 0
HAND_OFFSET = [0.0, 30.0, 0.0]
PELVIS_OFFSET = [0.0, 20.0, 0.0]
TOLERANCE_CM = 1.0
ARM = ["left_hand", "left_elbow", "arm_MainPoint_l", "clavicle_MainPoint_l", "right_foot"]

_POSITIONS_CODE = """
import json
from cascadeur_mcp_plus import rig

mv = scene.model_viewer()
bv = mv.behaviour_viewer()
dv = mv.data_viewer()
frame = %(frame)d
out = {}
for alias in %(names)s:
    object_id = rig.resolve([alias])[0]
    transform = bv.get_behaviour_by_name(object_id, "Transform")
    data = bv.get_behaviour_data(transform, "global_position")
    out[alias] = [round(float(value), 3) for value in dv.get_data_value(data, frame)]
print("__RESULT__" + json.dumps(out))
"""

_LOCK_POSE_CODE = """
import json
from cascadeur_mcp_plus import autoposing, common, rig

result = autoposing.lock_pose(%(frame)d, rig.resolve(%(names)s))
print("__RESULT__" + json.dumps(common.jsonable(result)))
"""

_MOVE_CODE = """
import json
import numpy as np
from cascadeur_mcp_plus import common, rig

frame = %(frame)d
alias = %(alias)s
offset = np.array(%(offset)s, dtype=float)
object_id = rig.resolve([alias])[0]
mv = scene.model_viewer()
bv = mv.behaviour_viewer()
dv = mv.data_viewer()
transform = bv.get_behaviour_by_name(object_id, "Transform")
data = bv.get_behaviour_data(transform, "global_position")
before = [float(value) for value in dv.get_data_value(data, frame)]

def mod(editor, update, updater):
    value = np.asarray(dv.get_data_value(data, frame), dtype=float).reshape(-1)
    editor.data_editor().set_data_value(data, frame, value + offset)
    updater.run_update({data}, frame)

result = common.edit("probe_move_" + alias, mod)
after = [float(value) for value in dv.get_data_value(data, frame)]
print("__RESULT__" + json.dumps({"result": result, "before": before, "after": after}))
"""

_SOLVE_CODE = """
import json
from cascadeur_mcp_plus import autoposing, common, rig

result = autoposing.solve(scene, %(frame)d, rig.resolve(%(names)s), answer=%(answer)r)
print("__RESULT__" + json.dumps(common.jsonable(result)))
"""


def distance(first, second) -> float:
    return sum((a - b) ** 2 for a, b in zip(first, second, strict=True)) ** 0.5


class AutoPosingLiveTests(LiveTestCase):
    def setUp(self) -> None:
        self.info = self.open_sample("Cascy.casc")

    # -- helpers ---------------------------------------------------------

    def run_json(self, code: str, timeout: float = 90.0) -> dict:
        result = client.call("run_python", code=code, timeout=timeout, http_timeout=timeout + 30.0)
        self.assertTrue(isinstance(result, dict) and result.get("ok"), f"run_python failed: {result!r}")
        for line in reversed(str(result.get("stdout", "")).splitlines()):
            if line.startswith("__RESULT__"):
                return json.loads(line[len("__RESULT__") :])
        self.fail(f"run_python produced no __RESULT__ line: {result!r}")

    def state(self) -> dict:
        result = client.call("get_autoposing_state")
        self.assertTrue(result.get("ok"), result)
        return result

    def enable(self, enabled: bool, answer: str = "yes") -> dict:
        return client.call("autoposing_enable", enabled=enabled, answer=answer)

    def dialogs(self) -> list:
        return client.call("dialogs").get("dialogs", [])

    def positions(self, names, frame: int = FRAME) -> dict:
        return self.run_json(_POSITIONS_CODE % {"frame": frame, "names": json.dumps(list(names))})

    def lock_pose(self, names, frame: int = FRAME) -> dict:
        return self.run_json(_LOCK_POSE_CODE % {"frame": frame, "names": json.dumps(list(names))})

    def move(self, alias: str, offset, frame: int = FRAME) -> dict:
        return self.run_json(
            _MOVE_CODE % {"frame": frame, "alias": json.dumps(alias), "offset": json.dumps(list(offset))}
        )

    def solve(self, names, frame: int = FRAME, answer: str = "yes") -> dict:
        return self.run_json(_SOLVE_CODE % {"frame": frame, "names": json.dumps(list(names)), "answer": answer})

    def interface_elements(self) -> list:
        tree = client.call("ui_tree", query="AutoPosingTool.AutoPosing", include_hidden=True)
        return [entry for entry in tree.get("elements", []) if entry.get("actionId") == "AutoPosingTool.AutoPosing"]

    # -- tests -----------------------------------------------------------

    def test_enable_is_idempotent_and_state_follows_the_interface(self) -> None:
        initial = self.state()
        self.assertIn(initial["enabled"], (True, False), initial)
        self.assertEqual(initial["action"], "AutoPosingTool.AutoPosing", initial)
        self.assertIsNone(initial["locked_controllers"], initial)
        self.assertFalse(initial["locked_known"], initial)

        off = self.enable(False)
        self.assertTrue(off["ok"], off)
        self.assertFalse(off["enabled"], off)
        off_again = self.enable(False)
        self.assertTrue(off_again["ok"], off_again)
        self.assertFalse(off_again["enabled"], off_again)
        self.assertFalse(off_again["changed"], off_again)
        self.assertFalse(self.state()["enabled"])
        self.assertFalse(any(entry.get("active") for entry in self.interface_elements()), self.interface_elements())

        on = self.enable(True)
        self.assertTrue(on["ok"], on)
        self.assertTrue(on["enabled"], on)
        on_again = self.enable(True)
        self.assertTrue(on_again["ok"], on_again)
        self.assertTrue(on_again["enabled"], on_again)
        self.assertFalse(on_again["changed"], on_again)
        self.assertTrue(self.state()["enabled"])
        self.assertTrue(any(entry.get("active") for entry in self.interface_elements()), self.interface_elements())

        off_final = self.enable(False)
        self.assertTrue(off_final["ok"], off_final)
        self.assertFalse(off_final["enabled"], off_final)
        self.assertFalse(any(entry.get("active") for entry in self.interface_elements()), self.interface_elements())
        self.assertEqual(self.dialogs(), [])

    def test_locked_pose_survives_update_and_moves_the_body(self) -> None:
        self.assertTrue(self.enable(True)["ok"])
        client.call("set_frame", frame=FRAME)
        self.lock_pose(["left_hand"])
        before = self.positions(ARM)

        move = self.move("left_hand", HAND_OFFSET)
        self.assertTrue(move["result"].get("ok"), move)
        written = self.positions(ARM)
        self.assertGreater(
            distance(written["left_hand"], before["left_hand"]),
            HAND_OFFSET[1] - 5.0,
            f"the scripted move did not reach the data: {before['left_hand']} -> {written['left_hand']}",
        )

        solved = self.solve(["left_hand"])
        self.assertTrue(solved.get("ok"), solved)
        self.assertEqual(solved.get("frame"), FRAME, solved)
        self.assertNotIn("restored", solved, solved)
        after = self.positions(ARM)
        hand = distance(after["left_hand"], written["left_hand"])
        elbow = distance(after["left_elbow"], written["left_elbow"])
        arm = distance(after["arm_MainPoint_l"], written["arm_MainPoint_l"])
        foot = distance(after["right_foot"], written["right_foot"])
        self.assertLess(
            hand, TOLERANCE_CM, f"AutoPosing restored the locked hand: {written['left_hand']} -> {after['left_hand']}"
        )
        self.assertGreater(
            elbow, TOLERANCE_CM, f"the left elbow did not follow: {written['left_elbow']} -> {after['left_elbow']}"
        )
        self.assertGreater(
            arm,
            TOLERANCE_CM,
            f"the left shoulder joint (arm_MainPoint_l) did not follow: "
            f"{written['arm_MainPoint_l']} -> {after['arm_MainPoint_l']}",
        )
        self.assertLess(
            foot,
            TOLERANCE_CM,
            f"the right foot moved although only the left hand was posed: "
            f"{written['right_foot']} -> {after['right_foot']}",
        )
        self.assertEqual(self.dialogs(), [], "the AutoPosing prompt was left open")

    def test_lock_controllers_locks_the_frame(self) -> None:
        self.assertTrue(self.enable(True)["ok"])
        client.call("set_frame", frame=FRAME)
        names = ["pelvis", "left_foot", "right_foot"]
        before = self.positions(names)

        lock = client.call("lock_controllers", names=["left_foot"], locked=True)
        self.assertTrue(lock["ok"], lock)
        self.assertEqual(lock["action"], "AutoPosingTool.AutoUnlock", lock)
        self.assertEqual(lock["undo"], "AutoPosing.Autolock", lock)
        self.assertTrue(lock["undoable"], lock)
        self.assertEqual(lock["count"], 1, lock)

        move = self.move("pelvis", PELVIS_OFFSET)
        self.assertTrue(move["result"].get("ok"), move)
        written = self.positions(names)
        self.assertGreater(
            distance(written["pelvis"], before["pelvis"]),
            10.0,
            f"the pelvis did not move: {before['pelvis']} -> {written['pelvis']}",
        )
        # A raw data edit of the pelvis also drags the feet through the rig hierarchy before
        # AutoPosing sees the frame (measured 2026-10-24: the left foot went from y=11.7 to
        # y=33.7 together with the pelvis). The lock is about the solver; the check below is
        # that the update keeps the locked pelvis and foot where the write left them, instead
        # of restoring them or moving them further.
        dragged = distance(written["left_foot"], before["left_foot"])
        solved = self.solve(["pelvis"])
        self.assertTrue(solved.get("ok"), solved)
        after = self.positions(names)
        self.assertLess(
            distance(after["pelvis"], written["pelvis"]),
            TOLERANCE_CM,
            f"the locked pelvis changed during the update: {written['pelvis']} -> {after['pelvis']}",
        )
        self.assertLess(
            distance(after["left_foot"], written["left_foot"]),
            TOLERANCE_CM,
            f"the locked left foot moved during the update (the data write had dragged it "
            f"{dragged:.1f} cm): {written['left_foot']} -> {after['left_foot']}",
        )
        self.assertEqual(self.dialogs(), [], "the AutoPosing prompt was left open")

        # client.call raises LiveError for an {"ok": false} result; the server puts the whole
        # result dict in the message, so parse it back out (same pattern as T13/T14).
        try:
            unlock = client.call("lock_controllers", names=["left_foot"], locked=False)
        except client.LiveError as exc:
            message = str(exc)
            start = message.find("{")
            self.assertGreaterEqual(start, 0, f"no JSON in the error message: {message}")
            unlock = json.loads(message[start:])
        else:
            self.fail(f"unlocking unexpectedly succeeded: {unlock!r}")
        self.assertFalse(unlock["ok"], unlock)
        self.assertIn("no unlock action", unlock["error"])

    def test_update_without_lock_restores_the_prediction_and_leaves_no_dialog(self) -> None:
        self.assertTrue(self.enable(True)["ok"])
        client.call("set_frame", frame=FRAME)
        names = ["left_hand", "left_elbow"]
        before = self.positions(names)
        move = self.move("left_hand", [0.0, 10.0, 0.0])
        self.assertTrue(move["result"].get("ok"), move)

        solved = self.solve(["left_hand"])
        self.assertTrue(solved.get("ok"), solved)
        self.assertIn("restored", solved, solved)
        self.assertIn("lock_pose", solved.get("hint", ""), solved)
        after = self.positions(names)
        self.assertLess(
            distance(after["left_hand"], before["left_hand"]),
            TOLERANCE_CM,
            f"the unlocked hand was expected to be restored: {before['left_hand']} -> {after['left_hand']}",
        )
        self.assertEqual(self.dialogs(), [], "the AutoPosing prompt was left open")

    def test_update_refuses_locked_layers(self) -> None:
        """The update rewrites the whole frame, so a locked character layer must refuse it."""
        self.assertTrue(self.enable(True)["ok"])
        client.call("set_frame", frame=FRAME)
        locked = client.call("set_layer_locked", name="Hand_L", locked=True)
        self.assertTrue(locked["ok"], locked)
        try:
            try:
                result = client.call("autoposing_update")
            except client.LiveError as exc:
                message = str(exc)
                start = message.find("{")
                self.assertGreaterEqual(start, 0, f"no JSON in the error message: {message}")
                result = json.loads(message[start:])
            else:
                self.fail(f"autoposing_update unexpectedly succeeded: {result!r}")
            self.assertFalse(result["ok"], result)
            self.assertIn("locked layer", result["error"])
            self.assertIn("Hand_L", result["error"])
            self.assertEqual(self.dialogs(), [], "the AutoPosing prompt was left open")
        finally:
            unlocked = client.call("set_layer_locked", name="Hand_L", locked=False)
            self.assertTrue(unlocked["ok"], unlocked)


if __name__ == "__main__":
    unittest.main()
