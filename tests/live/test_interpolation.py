"""Live tests for the interpolation and IK/FK tools (T17).

Each test opens Cascadeur's Cascy sample in its own tab; the fixture discards it in tearDown and
checks that the user's scenes, current scene and current frame are unchanged. The tests cover the
ticket's completion conditions:

* every interpolation kind is applied to 0-20 and list_keys reports it;
* step holds frame 0 at frame 10 and linear puts frame 10 at the midpoint of frames 0 and 20;
* set_ik_fk switches ik, fk and gr and the section data reads back;
* the object selection, timeline selection and playhead are unchanged by the calls;
* reset_tangents and reset_easing clear custom values;
* one call is one undo step named "MCP: ...".

Run with: python tests/live/run.py test_interpolation.py
"""

from __future__ import annotations

import json
import math
import unittest

try:  # imported as live.test_interpolation by unittest discovery
    from . import client
    from .fixture import LiveTestCase
except ImportError:  # executed by run.py
    import client  # type: ignore[no-redef]
    from fixture import LiveTestCase  # type: ignore[no-redef]


KINDS = ("bezier", "bezier_clamped", "bezier_viscous", "linear", "step", "fixed")

# list_keys reports Cascadeur's enum names, which differ for the clamped/viscous variants.
LIST_NAMES = {"bezier_clamped": "clamped_bezier", "bezier_viscous": "low_amplitude_bezier"}

# Read every section of the layer that holds hand_MainPoint_l. Reading only; no scene change.
_SECTION_CODE = """
import json

scene = csc.app.get_application().current_scene().domain_scene()
viewer = scene.layers_viewer()
model_viewer = scene.model_viewer()
object_id = list(model_viewer.get_objects("hand_MainPoint_l"))[0]
layer = viewer.layer(viewer.layer_id_by_obj_id_or_null(object_id))
sections = []
for position, section in sorted(layer.sections.items()):
    sections.append({
        "frame": int(position),
        "interpolation": section.interval.interpolation.name,
        "interval_ik": section.interval.common.ik_fk.name,
        "key_ik": section.key.common.ik_fk.name,
        "tangents": section.key.tangents.name,
        "left": float(section.key.left_weight_velocity),
        "right": float(section.key.right_weight_velocity),
    })
print("__RESULT__" + json.dumps(sections))
"""

# Make the key at frame 0 a non-default tangent/easing value so the reset tools have work to do.
_CUSTOM_KEY_CODE = """
import json

import csc

scene = csc.app.get_application().current_scene().domain_scene()
viewer = scene.layers_viewer()
model_viewer = scene.model_viewer()

def mod(model_editor, update_editor, scene_updater):
    editor = model_editor.layers_editor()
    object_id = list(model_viewer.get_objects("hand_MainPoint_l"))[0]
    layer_id = viewer.layer_id_by_obj_id_or_null(object_id)
    def change(section):
        section.interval.interpolation = csc.layers.layer.Interpolation.BEZIER
        section.key.tangents = csc.layers.layer.Tangents.UserDefined
        section.key.left_weight_velocity = 0.25
        section.key.right_weight_velocity = 0.75
    editor.change_section(0, layer_id, change)

changed = scene.modify_update("live test: custom tangents", mod)
print("__RESULT__" + json.dumps({"done": bool(changed)}))
"""

_SELECTION_CODE = """
import json

scene = csc.app.get_application().current_scene().domain_scene()
model_viewer = scene.model_viewer()
names = []
for object_id in scene.selector().selected().ordered_ids:
    try:
        names.append(model_viewer.get_object_name(object_id))
    except TypeError:
        names.append("<tool object>")
selector = scene.get_layers_selector()
interval = selector.selection().frames_interval()
print("__RESULT__" + json.dumps({
    "objects": names,
    "timeline_first": int(interval.first),
    "timeline_last": int(interval.last),
    "timeline_layers": len(selector.all_included_layer_ids()),
}))
"""


class InterpolationLiveTests(LiveTestCase):
    def setUp(self) -> None:
        self.open_sample("Cascy.casc")
        self.assertTrue(client.call("set_key", frames=[0, 20], controllers=["left_hand"]).get("ok"))

    # -- helpers ---------------------------------------------------------

    def _run_json(self, code: str, timeout: float = 90.0) -> dict | list:
        result = client.call("run_python", code=code, timeout=timeout, http_timeout=timeout + 30.0)
        self.assertTrue(isinstance(result, dict) and result.get("ok"), f"run_python failed: {result!r}")
        for line in reversed(str(result.get("stdout", "")).splitlines()):
            if line.startswith("__RESULT__"):
                return json.loads(line[len("__RESULT__") :])
        self.fail(f"run_python produced no __RESULT__ line: {result!r}")

    def sections(self) -> list[dict]:
        return self._run_json(_SECTION_CODE)

    def section(self, frame: int) -> dict:
        for entry in self.sections():
            if entry["frame"] == frame:
                return entry
        self.fail(f"no section at frame {frame}: {self.sections()!r}")

    def selection(self) -> dict:
        return self._run_json(_SELECTION_CODE)

    def hand_position(self) -> list:
        listed = client.call("list_controllers", kinds=["main_point"])
        self.assertTrue(listed.get("ok"), listed)
        for entry in listed["controllers"]:
            if entry.get("alias") == "left_hand":
                return entry["position"]
        self.fail(f"left_hand is not in list_controllers: {listed!r}")

    def keys(self) -> dict:
        result = client.call("list_keys", controllers=["left_hand"], start=0, end=20)
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(result["count"], 1, result)
        return result["controllers"][0]

    def expect_failure(self, tool: str, **arguments) -> dict:
        try:
            result = client.call(tool, **arguments)
        except client.LiveError as exc:
            message = str(exc)
            start = message.find("{")
            self.assertGreaterEqual(start, 0, f"no JSON in the error message: {message}")
            return json.loads(message[start:])
        self.fail(f"{tool} unexpectedly succeeded: {result!r}")

    @staticmethod
    def distance(left, right) -> float:
        return math.sqrt(sum((a - b) ** 2 for a, b in zip(left, right, strict=True)))

    # -- tests -----------------------------------------------------------

    def test_every_kind_and_the_step_linear_poses(self) -> None:
        # Pin an arbitrary state, then prove the tools leave it alone.
        self.assertTrue(client.call("select_objects", names=["left_hand", "right_hand"]).get("ok"))
        self.assertTrue(client.call("select_frames", start=5, end=10).get("ok"))
        self.assertTrue(client.call("set_frame", frame=7).get("ok"))
        before_selection = self.selection()
        self.assertEqual(len(before_selection["objects"]), 2, before_selection)
        self.assertEqual((before_selection["timeline_first"], before_selection["timeline_last"]), (5, 10))
        self.assertEqual(client.call("get_timeline")["frame"], 7)

        for kind in KINDS:
            with self.subTest(kind=kind):
                result = client.call("set_interpolation", kind=kind, start=0, end=20, controllers=["left_hand"])
                self.assertTrue(result.get("ok"), result)
                self.assertEqual(result["kind"], kind, result)
                self.assertGreaterEqual(result["sections_changed"], 1, result)
                expected = LIST_NAMES.get(kind, kind)
                self.assertIn(expected, result["readback"], result)

                listed = self.keys()
                self.assertEqual(listed["interpolation"].get("0"), expected, listed)
                # The interval that starts on frame 20 continues past the selected range.
                self.assertEqual(listed["interpolation"].get("20"), "step", listed)

                after_selection = self.selection()
                self.assertEqual(after_selection, before_selection, "a tool changed the selection")
                self.assertEqual(client.call("get_timeline")["frame"], 7, "a tool moved the playhead")

        # Step holds the first key's pose at frame 10.
        self.assertTrue(client.call("set_frame", frame=0).get("ok"))
        step_start = self.hand_position()
        self.assertTrue(client.call("set_frame", frame=10).get("ok"))
        step_middle = self.hand_position()
        self.assertLess(self.distance(step_start, step_middle), 0.01, "step does not hold the pose")

        # Linear puts frame 10 halfway between frames 0 and 20 (1 cm tolerance).
        result = client.call("set_interpolation", kind="linear", start=0, end=20, controllers=["left_hand"])
        self.assertTrue(result.get("ok"), result)
        self.assertTrue(client.call("set_frame", frame=0).get("ok"))
        linear_start = self.hand_position()
        self.assertTrue(client.call("set_frame", frame=20).get("ok"))
        linear_end = self.hand_position()
        self.assertTrue(client.call("set_frame", frame=10).get("ok"))
        linear_middle = self.hand_position()
        midpoint = [(a + b) / 2.0 for a, b in zip(linear_start, linear_end, strict=True)]
        self.assertLess(self.distance(midpoint, linear_middle), 1.0, (linear_start, linear_middle, linear_end))

    def test_ik_fk_modes_switch_and_read_back(self) -> None:
        before_0 = self.section(0)
        before_20 = self.section(20)
        self.assertTrue(client.call("select_objects", names=["left_hand"]).get("ok"))
        self.assertTrue(client.call("set_frame", frame=3).get("ok"))
        selection = self.selection()
        frame = client.call("get_timeline")["frame"]

        # Start with a mode that differs from Cascy's IK default so every switch changes data.
        for mode in ("fk", "gr", "ik"):
            with self.subTest(mode=mode):
                result = client.call("set_ik_fk", mode=mode, start=0, end=20, controllers=["left_hand"])
                self.assertTrue(result.get("ok"), result)
                self.assertEqual(result["mode"], mode, result)
                self.assertEqual(result["action"], f"Timeline.IK FK type.{mode.upper()} type on selected interval")
                self.assertEqual(result["sections"], 2, result)
                self.assertEqual(result["sections_changed"], 2, result)
                self.assertEqual(result["readback"], {mode: 2}, result)
                self.assertEqual(result["undo_source"], "Cascadeur action")

                raw = self.section(0)
                self.assertEqual(raw["interval_ik"].lower(), mode, raw)
                # The key's own type is the object's interpolation root; the tool leaves it alone.
                self.assertEqual(raw["key_ik"], before_0["key_ik"], raw)
                # The IK/FK action also changes the interval that starts on the last selected frame.
                raw_20 = self.section(20)
                self.assertEqual(raw_20["interval_ik"].lower(), mode, raw_20)
                self.assertEqual(raw_20["key_ik"], before_20["key_ik"], raw_20)

                self.assertEqual(self.selection(), selection, "set_ik_fk changed the selection")
                self.assertEqual(client.call("get_timeline")["frame"], frame, "set_ik_fk moved the playhead")

    def test_reset_tangents_and_easing(self) -> None:
        # The referenced values are what Cascadeur's own actions leave on a pristine key: run
        # them first, then make the key custom and check that the tools restore the same state.
        self.assertTrue(client.call("select_frames", start=0, end=20).get("ok"))
        self.assertEqual(
            client.call("call_action", key="Timeline.Clear custom tangents on selected interval")["called"],
            "Timeline.Clear custom tangents on selected interval",
        )
        reference_tangents = self.section(0)["tangents"]
        self.assertEqual(
            client.call("call_action", key="Timeline.Reset easing weights on selected interval")["called"],
            "Timeline.Reset easing weights on selected interval",
        )
        reference = self.section(0)

        custom = self._run_json(_CUSTOM_KEY_CODE)
        self.assertTrue(custom["done"], custom)
        key = self.section(0)
        self.assertEqual(key["tangents"], "UserDefined", key)
        self.assertAlmostEqual(key["left"], 0.25, places=6)
        self.assertAlmostEqual(key["right"], 0.75, places=6)

        tangents = client.call("reset_tangents", start=0, end=20, controllers=["left_hand"])
        self.assertTrue(tangents.get("ok"), tangents)
        self.assertEqual(tangents["readback"], {str(reference_tangents).lower(): 1}, tangents)
        self.assertEqual(tangents["sections_changed"], 1, tangents)
        self.assertEqual(self.section(0)["tangents"], reference_tangents)

        easing = client.call("reset_easing", start=0, end=20, controllers=["left_hand"])
        self.assertTrue(easing.get("ok"), easing)
        self.assertEqual(len(easing["readback"]), 1, easing)
        self.assertAlmostEqual(float(next(iter(easing["readback"]))), reference["left"], places=6)
        self.assertEqual(easing["sections_changed"], 1, easing)
        key = self.section(0)
        self.assertAlmostEqual(key["left"], reference["left"], places=6)
        self.assertAlmostEqual(key["right"], reference["right"], places=6)
        self.assertEqual(len(self.sections()), 2, self.sections())

    def test_one_undo_step(self) -> None:
        before = client.call("get_undo_history", limit=10)
        self.assertTrue(before.get("ok"), before)
        self.assertEqual(self.keys()["interpolation"].get("0"), "step")

        result = client.call("set_interpolation", kind="linear", start=0, end=20, controllers=["left_hand"])
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(result["undo"], "MCP: set_interpolation", result)
        history = client.call("get_undo_history", limit=10)
        self.assertEqual(history["count"], before["count"] + 1, history)
        self.assertEqual(history["steps"][-1]["name"], "MCP: set_interpolation", history)

        undone = client.call("undo")
        self.assertTrue(undone.get("ok"), undone)
        self.assertEqual(undone["steps"], ["MCP: set_interpolation"], undone)
        self.assertEqual(self.keys()["interpolation"].get("0"), "step", self.keys())

    def test_argument_errors(self) -> None:
        for tool, arguments in (
            ("set_interpolation", {"kind": "smooth", "start": 0, "end": 20}),
            ("set_ik_fk", {"mode": "inverse", "start": 0, "end": 20}),
            ("set_interpolation", {"kind": "linear", "start": 20, "end": 0}),
            ("reset_tangents", {"start": 20, "end": 0}),
            ("reset_easing", {"start": 0, "end": 20, "controllers": ["not_a_bone"]}),
        ):
            with self.subTest(tool=tool, arguments=arguments):
                failure = self.expect_failure(tool, **arguments)
                self.assertFalse(failure.get("ok"), failure)
                self.assertIn("hint", failure, failure)


if __name__ == "__main__":
    unittest.main()
