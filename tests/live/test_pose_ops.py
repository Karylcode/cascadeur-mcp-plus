r"""Live tests for the pose operations (T22): mirror, copy/paste and reset.

Every test opens Cascy.casc in its own tab; the fixture discards it in tearDown and checks that
the user's scenes, current scene and frame are unchanged. Positions are read straight from
Cascadeur's data viewer (run_python) so the checks do not depend on the tools' own answers.

The visual check renders "before" and "after" pictures into
%TEMP%\cascadeur-mcp-plus-tests\T22\ with render_image (T30): the reviewer looks at them.

Run with: python tests/live/run.py test_pose_ops.py
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest

try:  # imported as live.test_pose_ops by unittest discovery
    from . import client
    from .fixture import LiveTestCase
except ImportError:  # executed by run.py
    import client  # type: ignore[no-redef]
    from fixture import LiveTestCase  # type: ignore[no-redef]

FRAME = 0
PASTE_FRAME = 20
HAND_OFFSET = [0.0, 40.0, 0.0]
TOLERANCE_CM = 1.0

_MAIN_POINTS_CODE = """
import json
from cascadeur_mcp_plus import rig

frame = %(frame)d
mv = scene.model_viewer()
bv = mv.behaviour_viewer()
dv = mv.data_viewer()
out = {}
used = {}
for entry in rig.controllers():
    if entry["kind"] != "main_point":
        continue
    transform = bv.get_behaviour_by_name(entry["id"], "Transform")
    data = bv.get_behaviour_data(transform, "global_position")
    try:
        out[entry["name"]] = [round(float(value), 4) for value in dv.get_data_value(data, frame)]
    except Exception as exc:
        used[entry["name"]] = repr(exc)
print("__RESULT__" + json.dumps({"positions": out, "unreadable": used}))
"""

_TPOSE_CODE = """
import json
from cascadeur_mcp_plus import rig

mv = scene.model_viewer()
bv = mv.behaviour_viewer()
dv = mv.data_viewer()
out = {}
for entry in rig.controllers():
    if entry["kind"] not in ("main_point", "box"):
        continue
    behaviour = bv.get_behaviour_by_name(entry["id"], "TPose")
    if behaviour is None or behaviour.is_null():
        continue
    data = bv.get_behaviour_data(behaviour, "position")
    if data is None or data.is_null():
        continue
    try:
        out[entry["name"]] = [round(float(value), 4) for value in dv.get_data_value(data)]
    except Exception as exc:
        out[entry["name"]] = repr(exc)
print("__RESULT__" + json.dumps(out))
"""

_HANDS_CODE = """
import json
from cascadeur_mcp_plus import rig

frame = %(frame)d
mv = scene.model_viewer()
bv = mv.behaviour_viewer()
dv = mv.data_viewer()
out = {}
for name in %(names)s:
    object_id = mv.get_objects(name)[0]
    transform = bv.get_behaviour_by_name(object_id, "Transform")
    data = bv.get_behaviour_data(transform, "global_position")
    out[name] = [round(float(value), 4) for value in dv.get_data_value(data, frame)]
print("__RESULT__" + json.dumps(out))
"""


def distance(first, second) -> float:
    return sum((a - b) ** 2 for a, b in zip(first, second, strict=True)) ** 0.5


def reflect(position, plane) -> list[float]:
    normal = plane["normal"]
    offset = float(plane["offset"])
    distance_to_plane = sum(float(position[index]) * normal[index] for index in range(3)) - offset
    return [float(position[index]) - 2.0 * distance_to_plane * normal[index] for index in range(3)]


class PoseOpsLiveTests(LiveTestCase):
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

    def main_positions(self, frame: int = FRAME) -> dict:
        return self.run_json(_MAIN_POINTS_CODE % {"frame": frame})["positions"]

    def tpose_positions(self) -> dict:
        return self.run_json(_TPOSE_CODE)

    def positions(self, names, frame: int = FRAME) -> dict:
        return self.run_json(_HANDS_CODE % {"frame": frame, "names": json.dumps(list(names))})

    def raise_left_hand(self, autoposing: bool = True) -> dict:
        result = client.call(
            "set_pose", values={"left_hand": {"offset": HAND_OFFSET}}, frame=FRAME, autoposing=autoposing
        )
        self.assertTrue(result.get("ok"), result)
        return result

    def expect_failure(self, tool: str, **arguments) -> dict:
        try:
            result = client.call(tool, **arguments)
        except client.LiveError as exc:
            message = str(exc)
            start = message.find("{")
            self.assertGreaterEqual(start, 0, f"no JSON in the error message: {message}")
            return json.loads(message[start:])
        self.fail(f"the call unexpectedly succeeded: {result!r}")

    def dialogs(self) -> list:
        return client.call("dialogs").get("dialogs", [])

    def out_dir(self) -> str:
        path = os.path.join(tempfile.gettempdir(), "cascadeur-mcp-plus-tests", "T22")
        os.makedirs(path, exist_ok=True)
        return path

    def render(self, name: str) -> str:
        path = os.path.join(self.out_dir(), name)
        result = client.call(
            "render_image",
            path=path,
            overwrite=True,
            return_image=False,
            camera_preset="front",
            width=960,
            height=720,
        )
        self.assertTrue(result.get("ok"), result)
        self.assertTrue(os.path.isfile(path), f"render_image did not write {path}")
        self.assertGreater(os.path.getsize(path), 0, f"{path} is empty")
        return path

    # -- tests -----------------------------------------------------------

    def test_mirror_swaps_the_raised_hand_and_one_undo_restores_it(self) -> None:
        self.raise_left_hand(autoposing=False)
        before = self.main_positions()
        before_hands = self.positions(["hand_MainPoint_l", "hand_MainPoint_r", "hand_Box_l", "hand_Box_r"])
        self.assertGreater(before["hand_MainPoint_l"][1] - before["hand_MainPoint_r"][1], 30.0, before)

        result = client.call("mirror_pose", frame=FRAME)
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(result["frame"], FRAME)
        self.assertEqual(result["scope"], "whole character")
        self.assertEqual(result["plane"]["axis"], "x")
        self.assertLess(abs(result["plane"]["offset"]), 0.05, result["plane"])
        self.assertEqual(result["undo"], "Mirror")
        self.assertEqual(result["undo_steps"], ["Mirror"])
        self.assertEqual(result["undo_count"], 1)
        self.assertLess(result["max_symmetry_error_cm"], TOLERANCE_CM, result)
        self.assertIsNone(result["dialog"] if "dialog" in result else None)

        after = self.main_positions()
        plane = result["plane"]
        expected_right = reflect(before["hand_MainPoint_l"], plane)
        expected_left = reflect(before["hand_MainPoint_r"], plane)
        right_error = distance(after["hand_MainPoint_r"], expected_right)
        left_error = distance(after["hand_MainPoint_l"], expected_left)
        self.assertLess(right_error, TOLERANCE_CM, f"right hand {after['hand_MainPoint_r']} vs {expected_right}")
        self.assertLess(left_error, TOLERANCE_CM, f"left hand {after['hand_MainPoint_l']} vs {expected_left}")
        self.assertGreater(after["hand_MainPoint_r"][1], 130.0, after["hand_MainPoint_r"])
        self.assertLess(after["hand_MainPoint_l"][1], 100.0, after["hand_MainPoint_l"])

        # The whole rig is mirrored: the right hand box took the left hand box's raised position.
        after_hands = self.positions(["hand_MainPoint_l", "hand_MainPoint_r", "hand_Box_l", "hand_Box_r"])
        self.assertLess(
            distance(after_hands["hand_Box_r"], reflect(before_hands["hand_Box_l"], plane)),
            TOLERANCE_CM,
        )

        # One Ctrl+Z restores the pose that was mirrored (Cascadeur's native Mirror step).
        undone = client.call("undo")
        self.assertTrue(undone.get("ok"), undone)
        self.assertEqual(undone["steps"], ["Mirror"], undone)
        restored = self.main_positions()
        self.assertLess(distance(restored["hand_MainPoint_l"], before["hand_MainPoint_l"]), 0.05)
        self.assertLess(distance(restored["hand_MainPoint_r"], before["hand_MainPoint_r"]), 0.05)
        print(
            json.dumps(
                {
                    "measure": "mirror_pose after raising left_hand 40 cm",
                    "plane": plane,
                    "right_hand_error_cm": round(right_error, 4),
                    "left_hand_error_cm": round(left_error, 4),
                    "tool_max_symmetry_error_cm": result["max_symmetry_error_cm"],
                    "undo_steps": result["undo_steps"],
                    "reported_controllers": len(result["controllers"]),
                    "unpaired": result["unpaired"],
                }
            )
        )
        self.assertEqual(self.dialogs(), [])

    def test_copy_paste_reproduces_and_mirrors_the_pose(self) -> None:
        self.raise_left_hand(autoposing=False)
        copied = client.call("copy_pose", frame=FRAME)
        self.assertTrue(copied.get("ok"), copied)
        self.assertEqual(copied["name"], "pose_1")
        self.assertEqual(copied["frame"], FRAME)
        self.assertEqual(copied["count"], 21, copied)
        stored = {item["name"]: item["position"] for item in copied["controllers"]}
        source = self.main_positions(FRAME)

        pasted = client.call("paste_pose", frame=PASTE_FRAME)
        self.assertTrue(pasted.get("ok"), pasted)
        self.assertEqual(pasted["name"], "pose_1")
        self.assertEqual(pasted["source_frame"], FRAME)
        self.assertTrue(pasted["keyed"])
        self.assertEqual(pasted["undo"], "MCP: paste_pose")
        self.assertEqual(pasted["undo_steps"], ["MCP: paste_pose"])
        self.assertEqual(pasted["undo_count"], 1)
        self.assertEqual(pasted["extended_to"], 101, pasted)
        after = self.main_positions(PASTE_FRAME)
        deviations = {
            name: round(distance(position, source[name]), 4) for name, position in after.items() if name in source
        }
        worst = max(deviations.items(), key=lambda item: item[1])
        self.assertLess(worst[1], 0.05, f"pasted pose differs at {worst}")

        undone = client.call("undo")
        self.assertEqual(undone["steps"], ["MCP: paste_pose"], undone)
        read = client.call("get_pose", frame=PASTE_FRAME)
        self.assertTrue(read["ok"], read)
        self.assertEqual(read["controllers"][0].get("frame_used"), FRAME, read["controllers"][0])

        # mirrored=true mirrors the stored positions across the character's mirror plane.
        mirrored = client.call("paste_pose", frame=PASTE_FRAME, mirrored=True)
        self.assertTrue(mirrored.get("ok"), mirrored)
        self.assertTrue(mirrored["mirrored"])
        plane = mirrored.get("plane") or {"axis": "x", "normal": [1.0, 0.0, 0.0], "offset": 0.0}
        after_mirrored = self.main_positions(PASTE_FRAME)
        right_expected = reflect(stored["hand_MainPoint_l"], plane)
        left_expected = reflect(stored["hand_MainPoint_r"], plane)
        self.assertLess(distance(after_mirrored["hand_MainPoint_r"], right_expected), TOLERANCE_CM)
        self.assertLess(distance(after_mirrored["hand_MainPoint_l"], left_expected), TOLERANCE_CM)
        self.assertLess(distance(after_mirrored["head_MainPoint"], stored["head_MainPoint"]), TOLERANCE_CM)
        print(
            json.dumps(
                {
                    "measure": "copy_pose(0) -> paste_pose(20), then mirrored",
                    "worst_paste_deviation_cm": worst,
                    "mirror_plane": plane,
                    "right_hand_error_cm": round(distance(after_mirrored["hand_MainPoint_r"], right_expected), 4),
                    "left_hand_error_cm": round(distance(after_mirrored["hand_MainPoint_l"], left_expected), 4),
                    "undo_steps": pasted["undo_steps"],
                    "extended_to": pasted["extended_to"],
                }
            )
        )
        self.assertEqual(self.dialogs(), [])

    def test_reset_default_returns_to_the_tpose_and_one_undo_restores(self) -> None:
        self.raise_left_hand(autoposing=True)
        before = self.main_positions()
        tpose = self.tpose_positions()
        self.assertGreater(len(tpose), 10, tpose)

        result = client.call("reset_pose", kind="default", frame=FRAME)
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(result["kind"], "default")
        self.assertEqual(result["undo"], "MCP: reset_pose")
        self.assertEqual(result["undo_steps"], ["MCP: reset_pose"])
        self.assertEqual(result["undo_count"], 1)
        after = self.main_positions()
        deviations = {
            name: round(distance(position, tpose[name]), 4)
            for name, position in after.items()
            if name in tpose and isinstance(tpose[name], list)
        }
        # Boxes are part of the pose too: reset writes their global positions as well as the
        # main control points (writing only local transforms left them near the hand).
        box_names = ["hand_Box_l", "forearm_Box_l", "f_middle3_Box_l"]
        boxes = self.positions(box_names)
        for name in box_names:
            if name in tpose and isinstance(tpose[name], list):
                deviations[name] = round(distance(boxes[name], tpose[name]), 4)
        worst = max(deviations.items(), key=lambda item: item[1])
        self.assertLess(worst[1], TOLERANCE_CM, f"reset left {worst} away from the TPose")
        self.assertGreater(after["hand_MainPoint_l"][0], 40.0, after["hand_MainPoint_l"])

        undone = client.call("undo")
        self.assertEqual(undone["steps"], ["MCP: reset_pose"], undone)
        restored = self.main_positions()
        self.assertLess(distance(restored["hand_MainPoint_l"], before["hand_MainPoint_l"]), 0.05)
        print(
            json.dumps(
                {
                    "measure": "reset_pose(default) after raising left_hand 40 cm",
                    "tpose_controllers": len(tpose),
                    "written_controllers": result["count"],
                    "skipped": result["skipped"][:8],
                    "worst_tpose_deviation_cm": worst,
                    "undo_steps": result["undo_steps"],
                }
            )
        )
        self.assertEqual(self.dialogs(), [])

    def test_render_mirror_and_reset_before_and_after(self) -> None:
        """The pictures the reviewer looks at: before/after the mirror and the default reset."""
        self.raise_left_hand(autoposing=False)
        paths = [self.render("mirror_before_front.png")]

        mirrored = client.call("mirror_pose", frame=FRAME)
        self.assertTrue(mirrored.get("ok"), mirrored)
        paths.append(self.render("mirror_after_front.png"))

        reset = client.call("reset_pose", kind="default", frame=FRAME)
        self.assertTrue(reset.get("ok"), reset)
        paths.append(self.render("reset_after_front.png"))
        for path in paths:
            self.assertGreater(os.path.getsize(path), 1000, path)
        print(json.dumps({"rendered": paths}))
        self.assertEqual(self.dialogs(), [])


if __name__ == "__main__":
    unittest.main()
