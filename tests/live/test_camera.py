"""Live tests for the camera tools (T31).

Each test opens Cascy in its own tab; the fixture discards it in tearDown. Every test also puts
the user's viewport camera back: the camera is view state shared by the active tab, not part of
the scene, so the fixture cannot check it for us.

The preset renders are written to %TEMP%/cascadeur-mcp-plus-tests/CameraTests/ so a human (or an
agent with image input) can look at the six views.

Run with the live-test lock held and MCP Plus restarted from this worktree:

    python docs/tickets/bin/live.py lock T31
    python docs/tickets/bin/live.py restart C:/Users/User/Tools/cascadeur-mcp-plus-wt/T31
    python tests/live/run.py test_camera.py
"""

from __future__ import annotations

import ast
import math
import os
import unittest

try:
    from . import client
    from .fixture import LiveTestCase
    from .test_render import difference_ratio, distinct_colors, edge_ratio, image_of, mean_brightness
except ImportError:  # executed from tests/live
    import client  # type: ignore[no-redef]
    from fixture import LiveTestCase  # type: ignore[no-redef]
    from test_render import (  # type: ignore[no-redef]
        difference_ratio,
        distinct_colors,
        edge_ratio,
        image_of,
        mean_brightness,
    )

VIEWS = ("front", "back", "left", "right", "top", "three_quarter")
HTTP_TIMEOUT = 300.0

# Turn the character's control points and joints 90 degrees about the world Y axis, around the
# pelvis. Cascadeur's dependency graph does not accept a second write that moves the controllers
# against the joints ("Cycle in data dependency graph"), so the mesh keeps its own orientation;
# what this test checks is that camera_preset follows the character's control-point facing.
ROTATE_CODE = r"""
import math

import numpy as np

import cascadeur_mcp_plus.rig as rig

mv = scene.model_viewer()
bv = mv.behaviour_viewer()
dv = mv.data_viewer()
character = rig._characters(scene, mv, bv)[0]
frame = scene.get_current_frame(False)
angle = math.radians(90.0)
plus = np.array(
    [[math.cos(angle), 0.0, math.sin(angle)], [0.0, 1.0, 0.0], [-math.sin(angle), 0.0, math.cos(angle)]],
    dtype=float,
)
turn = csc.math.Rotation.from_quaternion(
    csc.math.euler_angles_to_quaternion_x_y_z(np.array([0.0, angle, 0.0], dtype=np.float32))
)
pelvis = bv.get_behaviour_by_name(rig.resolve(["pelvis"])[0], "Transform")
pivot_id = bv.get_behaviour_data(pelvis, "global_position")
pivot = np.asarray(dv.get_data_value(pivot_id, frame), dtype=float).reshape(3)
object_ids = list(character["objects"])


def rotate(model_editor, update_editor, scene_updater):
    data_editor = model_editor.data_editor()
    actuals = set()
    for object_id in object_ids:
        transform = bv.get_behaviour_by_name(object_id, "Transform")
        if transform.is_null():
            continue
        position_id = bv.get_behaviour_data(transform, "global_position")
        if not position_id.is_null():
            current = np.asarray(dv.get_data_value(position_id, frame), dtype=float).reshape(3)
            data_editor.set_data_value(position_id, frame, (plus @ (current - pivot) + pivot).astype(np.float32))
            actuals.add(position_id)
        rotation_id = bv.get_behaviour_data(transform, "global_rotation")
        if not rotation_id.is_null():
            data_editor.set_data_value(rotation_id, frame, turn * dv.get_data_value(rotation_id, frame))
            actuals.add(rotation_id)
    scene_updater.run_update(actuals, frame)


changed = scene.modify_update("Rotate the character 90 degrees", rotate)
result = {
    "changed": bool(changed),
    "objects": len(object_ids),
    "pivot": [float(value) for value in pivot],
    "frame": frame,
}
"""


def run_json(code: str, timeout: float = 120.0):
    """Run code inside Cascadeur that sets result and return that value."""
    answer = client.call("run_python", code=code, timeout=timeout, http_timeout=timeout + 30.0)
    if not answer.get("ok"):
        raise AssertionError(f"run_python failed: {answer!r}")
    return ast.literal_eval(answer["value"])


# ---------------------------------------------------------------------------
# Geometry helpers mirrored in the test, so the tools are not trusted blindly
# ---------------------------------------------------------------------------


def _sub(first, second):
    return tuple(a - b for a, b in zip(first, second, strict=True))


def _dot(first, second):
    return sum(a * b for a, b in zip(first, second, strict=True))


def _cross(first, second):
    return (
        first[1] * second[2] - first[2] * second[1],
        first[2] * second[0] - first[0] * second[2],
        first[0] * second[1] - first[1] * second[0],
    )


def _unit(vector):
    size = math.sqrt(_dot(vector, vector))
    if size <= 1e-9:
        raise AssertionError(f"zero-length vector {vector!r}")
    return tuple(item / size for item in vector)


def _perp(vector, axis):
    factor = _dot(vector, axis)
    return tuple(item - axis[index] * factor for index, item in enumerate(vector))


def character_axes(by_alias):
    """right/up/forward from the controller positions, computed independently of camera.py."""
    pelvis = by_alias.get("pelvis")
    chest = by_alias.get("chest")
    if pelvis is None or chest is None:
        raise AssertionError(f"missing pelvis/chest: {sorted(by_alias)}")
    up = _unit(_sub(chest, pelvis))
    for right_alias, left_alias in (
        ("right_shoulder", "left_shoulder"),
        ("right_hand", "left_hand"),
        ("right_knee", "left_knee"),
        ("right_foot", "left_foot"),
    ):
        if right_alias in by_alias and left_alias in by_alias:
            side = _perp(_sub(by_alias[right_alias], by_alias[left_alias]), up)
            if math.sqrt(_dot(side, side)) >= 1.0:
                right = _unit(side)
                return {"right": right, "up": up, "forward": _unit(_cross(up, right))}
    raise AssertionError("no usable left/right landmarks in the sample")


class CameraTests(LiveTestCase):
    def setUp(self) -> None:
        self.original_camera = client.call("get_camera")["camera"]
        self.addCleanup(self.restore_user_camera)

    def restore_user_camera(self) -> None:
        camera = self.original_camera
        if not camera:
            return
        client.call("set_camera", position=camera["position"], target=camera["target"])
        if camera.get("type") and camera["type"] != "PERSPECTIVE":
            client.call(
                "run_python",
                code="import cascadeur_mcp_plus.camera as camera_module\n"
                f"result = camera_module.write_camera(type_name={camera['type']!r})",
            )

    # -- helpers ------------------------------------------------------------

    def controllers_by_alias(self) -> dict:
        listed = client.call("list_controllers", kinds=["main_point"])
        self.assertTrue(listed["ok"], listed)
        return {item["alias"]: item["position"] for item in listed["controllers"] if item["alias"]}

    def axes(self) -> dict:
        return character_axes(self.controllers_by_alias())

    def render(self, **arguments):
        return client.call("render_image", http_timeout=HTTP_TIMEOUT, **arguments)

    def visible(self, image, where: str) -> None:
        self.assertGreater(mean_brightness(image), 4.0, f"{where}: the render is (almost) black")
        self.assertGreater(distinct_colors(image), 64, f"{where}: the render has almost no colours")
        edges = edge_ratio(image)
        self.assertGreater(edges, 0.003, f"{where}: the render has no visible character (edge ratio {edges:.4f})")

    def projection_inside(self, names, margin: float = 2.0) -> dict:
        projected = client.call("project_point", names=names)
        rectangle = projected["viewport_rect"]
        left, top, width, height = rectangle
        for name in names:
            entry = projected["points"][name]
            self.assertTrue(entry["on_screen"], f"{name} is outside the viewport: {entry}")
            x, y = entry["window_xy"]
            self.assertGreaterEqual(x, left + margin, f"{name} touches the viewport edge: {entry}")
            self.assertLessEqual(x, left + width - margin, f"{name} touches the viewport edge: {entry}")
            self.assertGreaterEqual(y, top + margin, f"{name} touches the viewport edge: {entry}")
            self.assertLessEqual(y, top + height - margin, f"{name} touches the viewport edge: {entry}")
        return projected

    # -- tests --------------------------------------------------------------

    def test_six_presets_render_the_whole_character(self):
        self.open_sample("Cascy.casc")
        mains = [item["name"] for item in client.call("list_controllers", kinds=["main_point"])["controllers"]]
        self.assertEqual(len(mains), 21)
        images = {}
        for view in VIEWS:
            with self.subTest(view=view):
                preset = client.call("camera_preset", view=view, target="character")
                self.assertTrue(preset["ok"], preset)
                self.assertEqual(preset["preset"], view)
                self.assertEqual(preset["target"]["kind"], "character")
                self.assertGreater(preset["distance"], 10.0, preset)
                self.assertEqual(preset["camera"]["type"], "PERSPECTIVE", preset)

                self.projection_inside(mains)

                path = os.path.join(self.out_dir(), f"preset-{view}.png")
                rendered = self.render(width=640, height=360, path=path, overwrite=True, return_image=True)
                image = image_of(rendered)
                self.assertEqual((image[0], image[1]), (640, 360))
                self.visible(image, view)
                images[view] = image
                self.assertTrue(os.path.isfile(path), path)

        # The views must actually show different sides of the character.
        for first, second in (("front", "back"), ("left", "right"), ("front", "three_quarter")):
            difference = difference_ratio(images[first], images[second])
            self.assertGreater(difference, 0.01, f"{first} and {second} render almost the same picture")

    def test_front_follows_a_quarter_turned_character(self):
        self.open_sample("Cascy.casc")
        before = self.axes()
        first = client.call("camera_preset", view="front")
        self.assertTrue(first["ok"], first)
        direction = _unit(_sub(first["camera"]["position"], first["camera"]["target"]))
        self.assertGreater(_dot(direction, before["forward"]), 0.9, f"front camera is not in front: {first}")
        first_image = image_of(
            self.render(
                width=480,
                height=270,
                return_image=True,
                path=os.path.join(self.out_dir(), "rotation-before.png"),
                overwrite=True,
            )
        )

        rotated = run_json(ROTATE_CODE)
        self.assertTrue(rotated["changed"], rotated)
        after = self.axes()
        # A quarter turn about Y either way; Cascadeur applies the stored rotations in the
        # opposite sense, so both signs are valid as long as the facing really changed by 90
        # degrees. rotation-before.png / rotation-after.png show what the two views looked like.
        expected = (before["forward"][2], before["forward"][1], -before["forward"][0])
        turned = _dot(after["forward"], expected)
        self.assertGreater(abs(turned), 0.99, f"the character did not turn 90 degrees: {before} -> {after}")

        second = client.call("camera_preset", view="front")
        self.assertTrue(second["ok"], second)
        direction = _unit(_sub(second["camera"]["position"], second["camera"]["target"]))
        self.assertGreater(_dot(direction, after["forward"]), 0.9, f"front camera did not follow the turn: {second}")
        old_direction = _unit(_sub(first["camera"]["position"], first["camera"]["target"]))
        self.assertLess(
            _dot(direction, old_direction), 0.2, "the front camera did not move when the character turned 90 degrees"
        )

        second_image = image_of(
            self.render(
                width=480,
                height=270,
                return_image=True,
                path=os.path.join(self.out_dir(), "rotation-after.png"),
                overwrite=True,
            )
        )
        self.assertGreater(difference_ratio(first_image, second_image), 0.01, "the turned character rendered the same")

    def test_set_camera_round_trip_and_projection_stays_accurate(self):
        self.open_sample("Cascy.casc")
        controllers = {
            item["alias"]: item for item in client.call("list_controllers", kinds=["main_point"])["controllers"]
        }
        head = controllers["head"]
        target = head["position"]
        position = [target[0] + 150.0, target[1] + 80.0, target[2] + 150.0]
        written = client.call("set_camera", position=position, target=target)
        self.assertTrue(written["ok"], written)
        camera = written["camera"]
        for index in range(3):
            self.assertAlmostEqual(camera["position"][index], position[index], delta=0.02)
            self.assertAlmostEqual(camera["target"][index], target[index], delta=0.02)
        read = client.call("get_camera")["camera"]
        for key in ("position", "target"):
            for index in range(3):
                self.assertAlmostEqual(read[key][index], camera[key][index], delta=0.001)
        self.assertEqual(read["type"], "PERSPECTIVE")

        # The aim point is the target, so it must project to the viewport centre within 2 px.
        projected = self.projection_inside([head["name"], controllers["pelvis"]["name"]])
        rectangle = projected["viewport_rect"]
        centre = (rectangle[0] + rectangle[2] / 2.0, rectangle[1] + rectangle[3] / 2.0)
        x, y = projected["points"][head["name"]]["window_xy"]
        self.assertLessEqual(abs(x - centre[0]), 2.0, f"head projects {x - centre[0]:.1f}px off centre")
        self.assertLessEqual(abs(y - centre[1]), 2.0, f"head projects {y - centre[1]:.1f}px off centre")

        # A second point: the projection must match the camera get_camera reports.
        pelvic = controllers["pelvis"]["position"]
        forward = _unit(_sub(camera["target"], camera["position"]))
        right = _unit(_cross(forward, (0.0, 1.0, 0.0)))
        up = _unit(_cross(right, forward))
        relative = _sub(pelvic, camera["position"])
        depth = _dot(relative, forward)
        self.assertGreater(depth, 0.0)
        tan_half = math.tan(math.radians(read["fov_deg"]) / 2.0)
        aspect = rectangle[2] / rectangle[3]
        expected_x = rectangle[0] + (_dot(relative, right) / (depth * tan_half * aspect) + 1.0) / 2.0 * rectangle[2]
        expected_y = rectangle[1] + (1.0 - _dot(relative, up) / (depth * tan_half)) / 2.0 * rectangle[3]
        actual = projected["points"][controllers["pelvis"]["name"]]["window_xy"]
        self.assertLessEqual(abs(actual[0] - expected_x), 2.0, f"projection x is {actual[0] - expected_x:.2f}px off")
        self.assertLessEqual(abs(actual[1] - expected_y), 2.0, f"projection y is {actual[1] - expected_y:.2f}px off")

    def test_render_image_camera_preset_restores_the_camera(self):
        self.open_sample("Cascy.casc")
        controllers = self.controllers_by_alias()
        target = controllers["head"]
        position = [target[0] + 200.0, target[1] + 50.0, target[2] + 200.0]
        client.call("set_camera", position=position, target=target)
        plain = self.render(width=320, height=180, return_image=True)
        self.assertNotIn("camera_preset", plain["meta"])

        preset = self.render(
            width=320,
            height=180,
            return_image=True,
            path=os.path.join(self.out_dir(), "render-preset.png"),
            overwrite=True,
            camera_preset="three_quarter",
        )
        self.assertEqual(preset["meta"]["camera_preset"], "three_quarter")
        self.assertTrue(preset["meta"]["camera_restored"], preset["meta"])
        self.visible(image_of(preset), "three_quarter render")
        self.assertGreater(
            difference_ratio(image_of(plain), image_of(preset)),
            0.005,
            "the preset render looked exactly like the current camera's render",
        )

        restored = client.call("get_camera")["camera"]
        for key, expected in (("position", position), ("target", target)):
            for index in range(3):
                self.assertAlmostEqual(restored[key][index], expected[index], delta=0.02)

        with self.assertRaises(client.LiveError):
            self.render(width=160, height=90, camera_preset="diagonal")

    def test_frame_objects_puts_the_named_objects_in_view(self):
        self.open_sample("Cascy.casc")
        controllers = {
            item["alias"]: item for item in client.call("list_controllers", kinds=["main_point"])["controllers"]
        }
        result = client.call("frame_objects", names=["left_hand", "right_hand"])
        self.assertTrue(result["ok"], result)
        left = controllers["left_hand"]["position"]
        right = controllers["right_hand"]["position"]
        midpoint = [(left[index] + right[index]) / 2.0 for index in range(3)]
        for index in range(3):
            self.assertAlmostEqual(result["target"][index], midpoint[index], delta=0.5)
        for name in ("left_hand", "right_hand"):
            entry = result["projected"][controllers[name]["name"]]
            self.assertTrue(entry["on_screen"], entry)
            self.assertLessEqual(abs(entry["ndc"][0]), 0.95, entry)
            self.assertLessEqual(abs(entry["ndc"][1]), 0.95, entry)

        # Aliases resolve the same way as elsewhere; a made-up name is an error with a hint.
        again = client.call("frame_objects", names=["head", "pelvis"])
        self.assertTrue(again["ok"], again)
        self.assertEqual(set(again["projected"]), {"head_MainPoint", "pelvis_MainPoint"})
        with self.assertRaises(client.LiveError):
            client.call("frame_objects", names=["not_a_bone"])

    def test_track_object_toggles_and_restores_the_selection(self):
        self.open_sample("Cascy.casc")
        before = client.call("get_scene_info")["selection"]
        try:
            started = client.call("track_object", name="head")
            self.assertTrue(started["ok"], started)
            self.assertTrue(started["tracking"], started)
            self.assertTrue(started["changed"], started)
            selection = client.call("get_scene_info")["selection"]["names"]
            self.assertIn("head_MainPoint", selection, "the tracked control point was not selected")

            again = client.call("track_object", name="head")
            self.assertFalse(again["changed"], again)
        finally:
            stopped = client.call("track_object")
        self.assertTrue(stopped["ok"], stopped)
        self.assertFalse(stopped["tracking"], stopped)
        self.assertTrue(stopped["changed"], stopped)
        selection = client.call("get_scene_info")["selection"]["names"]
        self.assertEqual(sorted(selection), sorted(before["names"]) if before["count"] else [], selection)

    def test_argument_errors_are_clear(self):
        self.open_sample("Cascy.casc")
        with self.assertRaises(client.LiveError) as caught:
            client.call("camera_preset", view="diagonal")
        self.assertIn("front", str(caught.exception))
        with self.assertRaises(client.LiveError) as caught:
            client.call("camera_preset", view="front", target="not_a_bone")
        self.assertIn("not_a_bone", str(caught.exception))
        head = self.controllers_by_alias()["head"]
        with self.assertRaises(client.LiveError):
            client.call("set_camera", position=head, target=head)
        with self.assertRaises(client.LiveError):
            client.call("frame_objects", names=[])


if __name__ == "__main__":
    unittest.main()
