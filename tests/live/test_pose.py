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
print("__RESULT__" + json.dumps({
    "rotation": [round(math.degrees(float(value)), 4)
                 for value in dv.get_data_value(data, frame).to_euler_angles_x_y_z()],
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
        before = self.main_positions(frame=0)
        result = self.pose(values={"head": {"offset": [0.0, 10.0, 0.0]}}, frame=FRAME, autoposing=False)
        self.assertTrue(result.get("ok"), result)
        self.assertFalse(result["autoposing"]["locked"])
        self.assertEqual(result["undo_steps"], ["MCP: set_pose"])
        self.assertEqual(result["undo_count"], 1)
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
