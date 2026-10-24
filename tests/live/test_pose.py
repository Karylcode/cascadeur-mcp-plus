r"""Live tests for the pose tools (T16): get_pose and set_pose.

Every test opens Cascy.casc in its own tab; the fixture discards it in tearDown and checks that
the user's scenes, current scene and frame are unchanged. Positions and rotations are also read
straight from Cascadeur's data viewer (run_python), so the checks do not depend on the tools' own
answers.

The visual check renders "before" and "after" pictures into
%TEMP%\cascadeur-mcp-plus-tests\T16\ with render_image (T30): the reviewer looks at them.

Run with: python tests/live/run.py test_pose.py
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest

try:  # imported as live.test_pose by unittest discovery
    from . import client
    from .fixture import LiveTestCase
except ImportError:  # executed by run.py
    import client  # type: ignore[no-redef]
    from fixture import LiveTestCase  # type: ignore[no-redef]

FRAME = 10
HAND_OFFSET = [0.0, 40.0, 0.0]
BOX = "hand_Box_l"
TOLERANCE_CM = 1.0
TOLERANCE_DEG = 0.5

_POSITIONS_CODE = """
import json
from cascadeur_mcp_plus import rig

frame = %(frame)d
names = %(names)s
mv = scene.model_viewer()
bv = mv.behaviour_viewer()
dv = mv.data_viewer()
out = {}
for name in names:
    object_id = rig.resolve([name])[0]
    transform = bv.get_behaviour_by_name(object_id, "Transform")
    pos_id = bv.get_behaviour_data(transform, "global_position")
    out[name] = {"position": [round(float(value), 3) for value in dv.get_data_value(pos_id, frame)]}
print("__RESULT__" + json.dumps(out))
"""

_MAIN_POINTS_CODE = """
import json
from cascadeur_mcp_plus import rig

frame = %(frame)d
mv = scene.model_viewer()
bv = mv.behaviour_viewer()
dv = mv.data_viewer()
out = {}
for entry in rig.controllers():
    if entry["kind"] != "main_point":
        continue
    transform = bv.get_behaviour_by_name(entry["id"], "Transform")
    data = bv.get_behaviour_data(transform, "global_position")
    out[entry["name"]] = [round(float(value), 3) for value in dv.get_data_value(data, frame)]
print("__RESULT__" + json.dumps(out))
"""

_ROTATION_CODE = """
import json
import math
import csc
from cascadeur_mcp_plus import rig

frame = %(frame)d
name = %(name)s
data_name = %(data_name)s
mv = scene.model_viewer()
bv = mv.behaviour_viewer()
dv = mv.data_viewer()
object_id = rig.resolve([name])[0]
transform = bv.get_behaviour_by_name(object_id, "Transform")
data = bv.get_behaviour_data(transform, data_name)
rotation = dv.get_data_value(data, frame)
print("__RESULT__" + json.dumps({
    "rotation": [round(math.degrees(float(value)), 4)
                 for value in csc.math.quaternion_to_euler_angles_x_y_z(rotation.to_quaternion())],
    "data": str(data),
}))
"""


def distance(first, second) -> float:
    return sum((a - b) ** 2 for a, b in zip(first, second, strict=True)) ** 0.5


def angle_difference(after, requested) -> float:
    return abs((float(after) - float(requested) + 180.0) % 360.0 - 180.0)


class PoseLiveTests(LiveTestCase):
    def setUp(self) -> None:
        self.info = self.open_sample("Cascy.casc")
        client.call("set_frame", frame=FRAME)

    # -- helpers ---------------------------------------------------------

    def run_json(self, code: str, timeout: float = 90.0):
        result = client.call("run_python", code=code, timeout=timeout, http_timeout=timeout + 30.0)
        self.assertTrue(isinstance(result, dict) and result.get("ok"), f"run_python failed: {result!r}")
        for line in reversed(str(result.get("stdout", "")).splitlines()):
            if line.startswith("__RESULT__"):
                return json.loads(line[len("__RESULT__") :])
        self.fail(f"run_python produced no __RESULT__ line: {result!r}")

    def positions(self, names, frame: int = FRAME) -> dict:
        return self.run_json(_POSITIONS_CODE % {"frame": frame, "names": json.dumps(list(names))})

    def main_positions(self, frame: int = FRAME) -> dict:
        return self.run_json(_MAIN_POINTS_CODE % {"frame": frame})

    def rotation(self, name: str, space: str = "world", frame: int = FRAME) -> list:
        data_name = "global_rotation" if space == "world" else "local_rotation"
        return self.run_json(
            _ROTATION_CODE % {"frame": frame, "name": json.dumps(name), "data_name": json.dumps(data_name)}
        )["rotation"]

    def pose(self, **arguments):
        return client.call("set_pose", **arguments)

    def expect_failure(self, **arguments) -> dict:
        """client.call raises LiveError for ok:false; parse the result back out of the message."""
        try:
            result = client.call("set_pose", **arguments)
        except client.LiveError as exc:
            message = str(exc)
            start = message.find("{")
            self.assertGreaterEqual(start, 0, f"no JSON in the error message: {message}")
            return json.loads(message[start:])
        self.fail(f"the call unexpectedly succeeded: {result!r}")

    def dialogs(self) -> list:
        return client.call("dialogs").get("dialogs", [])

    def out_dir(self) -> str:
        path = os.path.join(tempfile.gettempdir(), "cascadeur-mcp-plus-tests", "T16")
        os.makedirs(path, exist_ok=True)
        return path

    def render(self, name: str, camera_preset: str) -> str:
        path = os.path.join(self.out_dir(), name)
        result = client.call(
            "render_image",
            path=path,
            overwrite=True,
            return_image=False,
            camera_preset=camera_preset,
            width=960,
            height=720,
        )
        self.assertTrue(result.get("ok"), result)
        self.assertTrue(os.path.isfile(path), f"render_image did not write {path}")
        self.assertGreater(os.path.getsize(path), 0, f"{path} is empty")
        return path

    # -- tests -----------------------------------------------------------

    def test_offset_moves_the_hand_keys_it_and_the_body_follows(self) -> None:
        client.call("autoposing_enable", enabled=True)
        # Cascy's channels only reach frame 0 before the frame is keyed, so the baseline pose is
        # read there; set_pose's own "before" snapshot reports the frame it had to use.
        before = self.positions(["left_hand", "left_elbow", "arm_MainPoint_l", "right_foot"], frame=0)

        result = self.pose(values={"left_hand": {"offset": HAND_OFFSET}}, frame=FRAME)
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(result["frame"], FRAME)
        self.assertEqual(result["space"], "world")
        self.assertTrue(result["keyed"])
        self.assertEqual(result["undo"], "MCP: set_pose")
        self.assertEqual(result["undo_steps"], ["MCP: set_pose", "AutoPosing.Autolock"], result)
        self.assertEqual(result["undo_count"], 2)
        self.assertIn("two undos", result["undo_note"])
        self.assertTrue(result["autoposing"]["enabled"], result["autoposing"])
        self.assertTrue(result["autoposing"]["locked"], result["autoposing"])
        self.assertIsNone(result["dialog"], result)
        self.assertEqual(result["controllers"][0]["before"]["frame"], 0, result["controllers"][0])
        self.assertEqual(result["extended_to"], 101, result)

        # The tool's read-back and Cascadeur's own data viewer agree.
        hand = result["controllers"][0]
        self.assertEqual(hand["name"], "hand_MainPoint_l")
        self.assertEqual(hand["alias"], "left_hand")
        self.assertLess(hand["position_error_cm"], 0.5, hand)
        after = self.positions(["left_hand", "left_elbow", "arm_MainPoint_l", "right_foot"])
        raised = after["left_hand"]["position"][1] - before["left_hand"]["position"][1]
        self.assertAlmostEqual(raised, HAND_OFFSET[1], delta=TOLERANCE_CM)
        self.assertAlmostEqual(hand["after"]["position"][1], after["left_hand"]["position"][1], delta=0.01)

        # The chain follows: Cascadeur drags the forearm through the rig hierarchy as soon as the
        # hand's global position is written (measured in T15 as well), and the tool reports it.
        elbow_moved = distance(after["left_elbow"]["position"], before["left_elbow"]["position"])
        foot_moved = distance(after["right_foot"]["position"], before["right_foot"]["position"])
        self.assertGreater(elbow_moved, 1.0, f"the left elbow did not follow: {elbow_moved}")
        self.assertLess(foot_moved, TOLERANCE_CM, f"the right foot moved: {foot_moved}")
        adjusted = {item["alias"]: item["moved_cm"] for item in result["adjusted"]}
        self.assertIn("left_elbow", adjusted, result["adjusted"])
        self.assertAlmostEqual(adjusted["left_elbow"], elbow_moved, delta=0.1)

        # get_pose reads the same numbers without moving the playhead, and frame 10 is keyed.
        read = client.call("get_pose", controllers=["left_hand"], frame=FRAME)
        self.assertTrue(read["ok"], read)
        self.assertAlmostEqual(read["controllers"][0]["position"][1], after["left_hand"]["position"][1], delta=0.01)
        timeline = client.call("get_timeline")
        self.assertEqual(timeline["frame"], FRAME)
        keys = client.call("list_keys", controllers=["left_hand"], start=FRAME, end=FRAME)
        self.assertIn(FRAME, keys["key_frames"], keys)
        print(
            json.dumps(
                {
                    "measure": "offset +40 cm on left_hand (autoposing)",
                    "raised_cm": round(raised, 3),
                    "tool_readback_y": hand["after"]["position"][1],
                    "raw_readback_y": after["left_hand"]["position"][1],
                    "position_error_cm": round(hand["position_error_cm"], 4),
                    "elbow_moved_cm": round(elbow_moved, 3),
                    "shoulder_joint_moved_cm": round(
                        distance(after["arm_MainPoint_l"]["position"], before["arm_MainPoint_l"]["position"]), 3
                    ),
                    "right_foot_moved_cm": round(foot_moved, 4),
                    "extended_to": result["extended_to"],
                    "undo_steps": result["undo_steps"],
                }
            )
        )
        self.assertEqual(self.dialogs(), [])

    def test_autoposing_false_moves_only_the_given_controller(self) -> None:
        # A controller that sits in the rig's chain drags its neighbours through the hierarchy as
        # soon as its global position is written (T15 measured the same for the pelvis and the
        # feet; hand -> forearm drags the elbow, a box drags its hand). The head is the one main
        # point measured to move on its own, so it is the "only the given point changes" case.
        # The rig also pulls the head back (requested +10 cm, reached about +1.3 cm), so the call must
        # say so: ok false with the miss, and no fallback, because autoposing=false is already the plain write.
        before = self.main_positions(frame=0)
        result = self.expect_failure(values={"head": {"offset": [0.0, 10.0, 0.0]}}, frame=FRAME, autoposing=False)
        self.assertFalse(result.get("ok"), result)
        self.assertIn("These controllers are off target", result["error"])
        self.assertFalse(result["autoposing"]["locked"])
        self.assertFalse(result["autoposing_applied"])
        self.assertIsNone(result["fallback"])
        self.assertNotIn("warning", result)
        self.assertEqual(result["undo_steps"], ["MCP: set_pose"])
        self.assertEqual(result["undo_count"], 1)
        self.assertEqual(result["missed"][0]["name"], "head_MainPoint")
        self.assertGreater(result["missed"][0]["position_error_cm"], 5.0, result)
        self.assertEqual(result["adjusted"], [])
        self.assertIsNone(result["autoposing_update"])
        after = self.main_positions()
        moved = {
            name: round(distance(after[name], before[name]), 4)
            for name in before
            if distance(after[name], before[name]) > 0.0001
        }
        self.assertEqual(list(moved), ["head_MainPoint"], f"other main points moved: {moved}")
        head = result["controllers"][0]
        self.assertEqual(head["name"], "head_MainPoint")
        self.assertEqual(head["before"]["frame"], 0)
        print(
            json.dumps(
                {
                    "measure": "offset +10 cm on head (autoposing=false)",
                    "main_points_checked": len(before),
                    "moved_main_points": moved,
                    "requested": head["requested"]["position"],
                    "after": head["after"]["position"],
                    "position_error_cm": round(head["position_error_cm"], 4),
                    "undo_steps": result["undo_steps"],
                }
            )
        )
        self.assertEqual(self.dialogs(), [])

    def test_pose_after_posed_frames_keeps_the_targets(self) -> None:
        # The scenario that broke examples/wave.py: keys at frames 0 and 6, then set_pose on frame 12
        # with AutoPosing on. Whether AutoPosing holds the hand or the call falls back, the hand must end
        # on the requested position, and the result must say which path ran.
        client.call("set_key", frames=[0])
        client.call("set_key", frames=[6])
        start = client.call("get_pose", controllers=["right_hand"], frame=12)["controllers"][0]["position"]
        result = self.pose(values={"right_hand": {"offset": [0.0, 30.0, 0.0]}}, frame=12)
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(result["autoposing_applied"], result["fallback"] is None, result)
        if result["fallback"] is None:
            self.assertEqual(result["undo_count"], 2, result)
            self.assertNotIn("warning", result)
        else:
            self.assertEqual(result["undo_count"], 3, result)
            self.assertIn("reverted the requested pose", result["warning"])
        target = [start[0], start[1] + 30.0, start[2]]
        end = client.call("get_pose", controllers=["right_hand"], frame=12)["controllers"][0]["position"]
        self.assertLess(distance(end, target), 1.0, f"right_hand ended at {end}, requested {target}: {result}")
        self.assertLess(result["controllers"][0]["position_error_cm"], 1.0, result)
        print(
            json.dumps(
                {
                    "measure": "keys at 0 and 6, set_pose right_hand +30 cm on frame 12 (autoposing on)",
                    "autoposing_applied": result["autoposing_applied"],
                    "before_fallback_cm": result["controllers"][0].get("position_error_cm_before_fallback"),
                    "after_cm": round(distance(end, target), 4),
                    "undo_steps": result["undo_steps"],
                }
            )
        )
        self.assertEqual(self.dialogs(), [])

    def test_refused_target_fails_after_the_fallback(self) -> None:
        # The rig pulls the head back and no path reaches the target: AutoPosing misses, the fallback
        # misses too, so the call returns ok false with both errors and every undo step it made.
        result = self.expect_failure(values={"head": {"offset": [0.0, 10.0, 0.0]}}, frame=FRAME)
        self.assertFalse(result["ok"], result)
        self.assertIn("AutoPosing and the fallback", result["error"])
        self.assertEqual(result["missed"][0]["name"], "head_MainPoint")
        self.assertEqual(
            result["undo_steps"],
            ["MCP: set_pose (without AutoPosing)", "MCP: set_pose", "AutoPosing.Autolock"],
        )
        self.assertEqual(self.dialogs(), [])

    def test_rotation_reads_back_within_half_a_degree(self) -> None:
        # Main control points have no rotation data in Cascadeur 2026.2.3; boxes and joints do.
        # A box's global_rotation is recomputed from its local_rotation (a global write is
        # overwritten), so the reliable rotation write is local space.
        start = client.call("get_pose", controllers=[BOX], frame=FRAME, space="local")
        self.assertTrue(start["ok"], start)
        original = start["controllers"][0]["rotation"]
        self.assertIsNotNone(original, start["controllers"][0])
        wanted = [original[0], original[1] + 30.0, original[2]]
        result = self.pose(values={BOX: {"rotation": wanted}}, frame=FRAME, autoposing=False, space="local")
        self.assertTrue(result.get("ok"), result)
        item = result["controllers"][0]
        self.assertLess(item["rotation_error_deg"], TOLERANCE_DEG, item)
        raw = self.rotation(BOX, space="local")
        for actual, expected in zip(raw, wanted, strict=True):
            self.assertLess(angle_difference(actual, expected), TOLERANCE_DEG, f"{actual} vs {expected}")
        read = client.call("get_pose", controllers=[BOX], frame=FRAME, space="local")
        for actual, expected in zip(read["controllers"][0]["rotation"], wanted, strict=True):
            self.assertLess(angle_difference(actual, expected), TOLERANCE_DEG)
        quaternion = read["controllers"][0]["quaternion"]
        self.assertEqual(set(quaternion), {"w", "x", "y", "z"})
        # The world-space read-back follows the local write through the parent's rotation.
        world = client.call("get_pose", controllers=[BOX], frame=FRAME, space="world")["controllers"][0]
        self.assertIsNotNone(world["rotation"])

        # A main control point has no rotation data, so the tool refuses it with a reason.
        refused = self.expect_failure(
            values={"left_hand": {"rotation": [0.0, 30.0, 0.0]}}, frame=FRAME, autoposing=False
        )
        self.assertIn("no rotation data", refused["error"])
        self.assertIn("hand_Box_l", refused["hint"])
        main = client.call("get_pose", controllers=["left_hand"], frame=FRAME)["controllers"][0]
        self.assertIsNone(main["rotation"])
        self.assertFalse(main["rotation_available"])
        print(
            json.dumps(
                {
                    "measure": "local rotation on " + BOX,
                    "requested": [round(value, 4) for value in wanted],
                    "tool_readback": [round(value, 4) for value in item["after"]["rotation"]],
                    "raw_readback": raw,
                    "error_deg": round(item["rotation_error_deg"], 4),
                    "world_readback": [round(value, 4) for value in world["rotation"]],
                }
            )
        )
        self.assertEqual(self.dialogs(), [])

    def test_two_axis_rotation_round_trip(self) -> None:
        # Review follow-up: writes went through Rotation.from_euler while reads used
        # to_euler_angles_x_y_z, two conventions that only agree on single-axis rotations. Both
        # sides now use Cascadeur's euler_angles_to_quaternion_x_y_z / quaternion_to_euler_angles_x_y_z
        # pair, so a two-axis rotation must read back and stay stable when written again.
        wanted = [30.0, 45.0, 0.0]
        result = self.pose(values={BOX: {"rotation": wanted}}, frame=0, autoposing=False, space="local")
        self.assertTrue(result.get("ok"), result)
        item = result["controllers"][0]
        self.assertLess(item["rotation_error_deg"], TOLERANCE_DEG, item)
        # The read-back Euler triple may be the equivalent branch (x+180, 180-y, z+180) once two
        # axes are involved; the orientation is what has to match, so the check is on the
        # quaternion (the per-axis Euler values are only reported, not compared).
        raw = self.rotation(BOX, space="local", frame=0)
        first = client.call("get_pose", controllers=[BOX], frame=0, space="local")["controllers"][0]
        again = self.pose(values={BOX: {"rotation": first["rotation"]}}, frame=0, autoposing=False, space="local")
        self.assertTrue(again.get("ok"), again)
        second = client.call("get_pose", controllers=[BOX], frame=0, space="local")["controllers"][0]
        drift = sum((first["quaternion"][key] - second["quaternion"][key]) ** 2 for key in ("w", "x", "y", "z")) ** 0.5
        self.assertLess(drift, 1e-4, f"writing the read-back angles moved the orientation: {first['quaternion']}")
        print(
            json.dumps(
                {
                    "measure": "two-axis local rotation on " + BOX,
                    "requested": wanted,
                    "tool_readback": [round(value, 4) for value in item["after"]["rotation"]],
                    "raw_readback": raw,
                    "readback_quaternion": first["quaternion"],
                    "rotation_error_deg": round(item["rotation_error_deg"], 4),
                    "second_write_quaternion_drift": round(drift, 8),
                }
            )
        )
        self.assertEqual(self.dialogs(), [])

    def test_playhead_not_on_the_target_frame(self) -> None:
        client.call("autoposing_enable", enabled=True)
        client.call("set_frame", frame=0)
        before_at_0 = self.positions(["left_hand"], frame=0)

        # Target frame 10 while the playhead stays on 0: the write must land on frame 10.
        result = self.pose(values={"left_hand": {"offset": [0, 40, 0]}}, frame=FRAME)
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(result["frame"], FRAME, result)
        self.assertEqual(client.call("get_timeline")["frame"], 0, "set_pose moved the playhead")
        written = self.positions(["left_hand"])
        raised = written["left_hand"]["position"][1] - before_at_0["left_hand"]["position"][1]
        self.assertAlmostEqual(raised, 40.0, delta=TOLERANCE_CM)
        self.assertAlmostEqual(
            self.positions(["left_hand"], frame=0)["left_hand"]["position"][1],
            before_at_0["left_hand"]["position"][1],
            delta=0.01,
        )

        # The AutoPosing solve must act on the target frame, not on the playhead: frame 0 has
        # data and a lock state, and the playhead sits on 5 while the call targets 0.
        client.call("set_frame", frame=5)
        before_at_0 = self.positions(["left_hand", "arm_MainPoint_l"], frame=0)
        result_zero = self.pose(values={"left_hand": {"offset": [0, 30, 0]}}, frame=0)
        self.assertTrue(result_zero.get("ok"), result_zero)
        self.assertEqual(client.call("get_timeline")["frame"], 5, "set_pose moved the playhead")
        after_at_0 = self.positions(["left_hand", "arm_MainPoint_l"], frame=0)
        arm_moved = distance(after_at_0["arm_MainPoint_l"]["position"], before_at_0["arm_MainPoint_l"]["position"])
        hand_moved = distance(after_at_0["left_hand"]["position"], before_at_0["left_hand"]["position"])
        self.assertAlmostEqual(hand_moved, 30.0, delta=TOLERANCE_CM)
        self.assertGreater(arm_moved, 1.0, f"AutoPosing did not solve frame 0: arm moved {arm_moved} cm")
        self.assertLess(result_zero["controllers"][0]["position_error_cm"], 0.5)
        print(
            json.dumps(
                {
                    "measure": "playhead not on the target frame",
                    "frame10_raised_cm": round(raised, 3),
                    "playhead_after_frame10_call": 0,
                    "frame0_hand_moved_cm": round(hand_moved, 3),
                    "frame0_arm_moved_cm": round(arm_moved, 4),
                    "playhead_after_frame0_call": 5,
                }
            )
        )
        self.assertEqual(self.dialogs(), [])

    def test_undo_twice_restores_the_original_pose(self) -> None:
        client.call("autoposing_enable", enabled=True)
        before = self.main_positions(frame=0)
        result = self.pose(values={"left_hand": {"offset": HAND_OFFSET}}, frame=FRAME)
        self.assertTrue(result.get("ok"), result)
        written = self.main_positions()
        self.assertGreater(
            distance(written["hand_MainPoint_l"], before["hand_MainPoint_l"]),
            HAND_OFFSET[1] - TOLERANCE_CM,
        )

        first = client.call("undo")
        self.assertTrue(first.get("ok"), first)
        self.assertEqual(first["steps"], ["MCP: set_pose"], first)
        # Undoing the write removes the key at frame 10 too, so read through get_pose: it falls
        # back to the closest frame with data and says which one it used.
        between = client.call("get_pose", controllers=["left_hand"], frame=FRAME)["controllers"][0]
        self.assertLess(
            abs(between["position"][1] - before["hand_MainPoint_l"][1]),
            TOLERANCE_CM,
            f"the MCP: set_pose undo did not remove the written pose: {between}",
        )

        second = client.call("undo")
        self.assertTrue(second.get("ok"), second)
        self.assertEqual(second["steps"], ["AutoPosing.Autolock"], second)
        restored = self.main_positions(frame=0)
        self.assertLess(
            distance(restored["hand_MainPoint_l"], before["hand_MainPoint_l"]),
            TOLERANCE_CM,
            f"two undos did not restore the hand: {before['hand_MainPoint_l']} -> {restored['hand_MainPoint_l']}",
        )
        self.assertEqual(self.dialogs(), [])

    def test_get_pose_does_not_move_the_playhead(self) -> None:
        client.call("set_frame", frame=7)
        result = client.call("get_pose", frame=FRAME)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["frame"], FRAME)
        self.assertEqual(result["current_frame"], 7)
        self.assertEqual(client.call("get_timeline")["frame"], 7)
        names = [item["name"] for item in result["controllers"]]
        self.assertIn("hand_MainPoint_l", names)
        self.assertGreater(len(names), 10, names)
        for item in result["controllers"]:
            self.assertIn("position", item)
            self.assertIn("rotation", item)
            self.assertIn("quaternion", item)
            self.assertFalse(item["rotation_available"], item)
            self.assertEqual(item["frame_used"], 0, item)

    def test_locked_layer_refuses_set_pose(self) -> None:
        created = client.call("create_layer", name="T16 locked")
        self.assertTrue(created["ok"], created)
        moved = client.call("move_to_layer", controllers=["left_hand"], layer="T16 locked")
        self.assertTrue(moved["ok"], moved)
        locked = client.call("set_layer_locked", name="T16 locked", locked=True)
        self.assertTrue(locked["ok"], locked)
        before = self.positions(["left_hand"], frame=0)
        failed_step = client.call("get_undo_history", limit=1)
        try:
            failure = self.expect_failure(values={"left_hand": {"offset": [0, 5, 0]}}, frame=FRAME)
            self.assertFalse(failure["ok"], failure)
            self.assertIn("locked layer", failure["error"])
            self.assertIn("set_layer_locked", failure["hint"])
            self.assertEqual(failure["undo_steps"], [])
            after = self.positions(["left_hand"], frame=0)
            self.assertAlmostEqual(
                distance(before["left_hand"]["position"], after["left_hand"]["position"]),
                0.0,
                delta=0.001,
            )
            self.assertEqual(
                client.call("get_undo_history", limit=1)["steps"],
                failed_step["steps"],
                "the refused call created an undo step",
            )
        finally:
            client.call("set_layer_locked", name="T16 locked", locked=False)
        self.assertEqual(self.dialogs(), [])

    def test_render_before_and_after_poses(self) -> None:
        """The pictures the reviewer looks at: before, left hand up and hands on hips."""
        client.call("autoposing_enable", enabled=True)
        paths = [self.render("before_front.png", "front"), self.render("before_three_quarter.png", "three_quarter")]

        raised = self.pose(values={"left_hand": {"offset": HAND_OFFSET}}, frame=FRAME)
        self.assertTrue(raised.get("ok"), raised)
        paths.append(self.render("after_hand_up_front.png", "front"))
        paths.append(self.render("after_hand_up_three_quarter.png", "three_quarter"))

        # A second pose on the same frame: the left arm out to the side (a T-pose-like pose).
        out = self.pose(values={"left_hand": {"offset": [20.0, 0.0, 0.0]}}, frame=FRAME)
        self.assertTrue(out.get("ok"), out)
        for item in out["controllers"]:
            self.assertLess(item["position_error_cm"], 2.0, item)
        paths.append(self.render("after_left_arm_out_front.png", "front"))

        for path in paths:
            self.assertGreater(os.path.getsize(path), 1000, path)
        self.assertEqual(self.dialogs(), [])


if __name__ == "__main__":
    unittest.main()
