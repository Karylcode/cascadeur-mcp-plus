"""Geometry tests for cascadeur_mcp_plus/camera.py (T31).

The default-view maths must be testable without Cascadeur: these tests load camera.py from its
file (the package __init__ imports Cascadeur's server and PySide6) and only call the pure
functions: character axes, preset directions and cameras, framing distances and projection.

Run: python -m unittest discover -s tests -v
"""

from __future__ import annotations

import importlib.util
import math
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CAMERA_PATH = os.path.join(ROOT, "cascadeur_mcp_plus", "camera.py")

_SPEC = importlib.util.spec_from_file_location("cascadeur_mcp_plus_camera_for_tests", CAMERA_PATH)
camera = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(camera)


def sub(first, second):
    return tuple(a - b for a, b in zip(first, second, strict=True))


def add(first, second):
    return tuple(a + b for a, b in zip(first, second, strict=True))


def mul(vector, factor):
    return tuple(item * factor for item in vector)


def dot(first, second):
    return sum(a * b for a, b in zip(first, second, strict=True))


def cross(first, second):
    return (
        first[1] * second[2] - first[2] * second[1],
        first[2] * second[0] - first[0] * second[2],
        first[0] * second[1] - first[1] * second[0],
    )


def norm(vector):
    return math.sqrt(dot(vector, vector))


def unit(vector):
    size = norm(vector)
    return tuple(item / size for item in vector)


def yaw_90(point):
    """Rotate a point 90 degrees about the world Y axis (x, y, z) -> (z, y, -x)."""
    return (point[2], point[1], -point[0])


# A T-pose facing -Z: the character's right hand is at +X, its left at -X.
STANDARD_POINTS = {
    "pelvis": (0.0, 95.0, 0.0),
    "chest": (0.0, 145.0, 0.0),
    "left_shoulder": (-20.0, 140.0, 0.0),
    "right_shoulder": (20.0, 140.0, 0.0),
    "left_hand": (-70.0, 140.0, 0.0),
    "right_hand": (70.0, 140.0, 0.0),
    "left_foot": (-10.0, 5.0, 0.0),
    "right_foot": (10.0, 5.0, 0.0),
    "left_toe": (-10.0, 5.0, -15.0),
    "right_toe": (10.0, 5.0, -15.0),
}


class VectorTests(unittest.TestCase):
    def test_vec_accepts_lists_and_numpy_like_values(self):
        self.assertEqual(camera.vec([1, 2, 3]), (1.0, 2.0, 3.0))
        self.assertEqual(camera.vec((1.0, 2.0, 3.0)), (1.0, 2.0, 3.0))

    def test_vec_rejects_bad_values(self):
        for value in ("abc", [1, 2], [1, 2, 3, 4], [float("nan"), 0, 0], [float("inf"), 0, 0]):
            with self.subTest(value=value), self.assertRaises(ValueError):
                camera.vec(value)

    def test_unit_normalizes_and_refuses_zero(self):
        self.assertAlmostEqual(norm(camera.unit((0, 5, 0))), 1.0)
        self.assertEqual(camera.unit((0, 0, 0), fallback=(1, 0, 0)), (1, 0, 0))
        with self.assertRaises(ValueError):
            camera.unit((0, 0, 0))


class LookAtBasisTests(unittest.TestCase):
    def test_basis_is_orthonormal(self):
        forward, right, up = camera.look_at_basis((0.3, -0.4, -1.0))
        for vector in (forward, right, up):
            self.assertAlmostEqual(norm(vector), 1.0, places=9)
        self.assertAlmostEqual(dot(forward, right), 0.0, places=9)
        self.assertAlmostEqual(dot(forward, up), 0.0, places=9)
        self.assertAlmostEqual(dot(right, up), 0.0, places=9)

    def test_screen_right_and_up_for_a_z_view(self):
        forward, right, up = camera.look_at_basis((0, 0, -1))
        self.assertEqual(tuple(round(item, 9) for item in forward), (0.0, 0.0, -1.0))
        # Looking along -Z, screen right is +X and screen up is +Y, like ui.project_points.
        self.assertEqual(tuple(round(item, 9) for item in right), (1.0, 0.0, 0.0))
        self.assertEqual(tuple(round(item, 9) for item in up), (0.0, 1.0, 0.0))

    def test_vertical_forward_stays_finite(self):
        forward, right, up = camera.look_at_basis((0, -1, 0), up_hint=(0, 0, -1))
        self.assertAlmostEqual(norm(forward), 1.0)
        self.assertAlmostEqual(norm(right), 1.0)
        self.assertAlmostEqual(norm(up), 1.0)
        self.assertAlmostEqual(dot(forward, right), 0.0, places=9)


class CharacterAxesTests(unittest.TestCase):
    def test_standard_character_faces_minus_z(self):
        axes = camera.character_axes(STANDARD_POINTS)
        self.assertEqual(tuple(round(item, 6) for item in axes["right"]), (1.0, 0.0, 0.0))
        self.assertEqual(tuple(round(item, 6) for item in axes["up"]), (0.0, 1.0, 0.0))
        self.assertEqual(tuple(round(item, 6) for item in axes["forward"]), (0.0, 0.0, -1.0))
        self.assertEqual(axes["sources"]["up"], "pelvis to chest")
        self.assertEqual(axes["sources"]["right"], "right_shoulder to left_shoulder")

    def test_quarter_turn_turns_the_axes_with_the_character(self):
        turned = {name: yaw_90(point) for name, point in STANDARD_POINTS.items()}
        axes = camera.character_axes(turned)
        self.assertEqual(tuple(round(item, 6) for item in axes["forward"]), (-1.0, 0.0, 0.0))
        self.assertEqual(tuple(round(item, 6) for item in axes["right"]), (0.0, 0.0, -1.0))
        self.assertEqual(tuple(round(item, 6) for item in axes["up"]), (0.0, 1.0, 0.0))

    def test_half_turn_points_the_other_way(self):
        # 180 degrees about Y: (x, y, z) -> (-x, y, -z).
        turned = {name: (-point[0], point[1], -point[2]) for name, point in STANDARD_POINTS.items()}
        axes = camera.character_axes(turned)
        self.assertAlmostEqual(axes["forward"][2], 1.0, places=6)

    def test_hands_are_the_fallback_for_the_side_axis(self):
        points = {"pelvis": (0, 95, 0), "chest": (0, 145, 0), "left_hand": (-60, 140, 0), "right_hand": (60, 140, 0)}
        axes = camera.character_axes(points)
        self.assertEqual(axes["sources"]["right"], "right_hand to left_hand")
        self.assertAlmostEqual(axes["forward"][2], -1.0, places=6)

    def test_feet_are_a_further_fallback(self):
        points = {"pelvis": (0, 95, 0), "chest": (0, 145, 0), "left_foot": (-10, 5, 0), "right_foot": (10, 5, 0)}
        axes = camera.character_axes(points)
        self.assertEqual(axes["sources"]["right"], "right_foot to left_foot")
        # right_foot (10) - left_foot (-10) is +X, the character's right; up x right is -Z.
        self.assertAlmostEqual(axes["forward"][2], -1.0, places=6)

    def test_toes_give_forward_when_no_side_line_exists(self):
        points = {
            "pelvis": (0, 95, 0),
            "chest": (0, 145, 0),
            "right_foot": (5, 5, 10),
            "right_toe": (5, 5, 0),
        }
        axes = camera.character_axes(points)
        self.assertEqual(axes["sources"]["forward"], "right_foot to right_toe")
        self.assertEqual(tuple(round(item, 6) for item in axes["forward"]), (0.0, 0.0, -1.0))
        self.assertEqual(tuple(round(item, 6) for item in axes["right"]), (1.0, 0.0, 0.0))

    def test_world_axes_are_the_last_resort(self):
        axes = camera.character_axes({"head": (0, 160, 0)})
        self.assertIn("world", axes["sources"]["right"])
        self.assertEqual(axes["sources"]["up"], "world Y (no pelvis/chest pair)")
        self.assertEqual(tuple(round(item, 6) for item in axes["forward"]), (0.0, 0.0, -1.0))

    def test_a_leaning_body_keeps_its_own_up(self):
        points = dict(STANDARD_POINTS)
        points["chest"] = (0.0, 145.0, -20.0)
        axes = camera.character_axes(points)
        body_up = unit(sub(points["chest"], points["pelvis"]))
        for actual, expected in zip(axes["up"], body_up, strict=True):
            self.assertAlmostEqual(actual, expected, places=6)
        # forward stays perpendicular to that up axis and the shoulder line
        self.assertAlmostEqual(norm(axes["forward"]), 1.0, places=9)


class PresetDirectionTests(unittest.TestCase):
    def setUp(self):
        self.axes = camera.character_axes(STANDARD_POINTS)

    def test_all_six_views(self):
        directions = {view: camera.preset_direction(view, self.axes) for view in camera.VIEWS}
        self.assertEqual(tuple(round(item, 6) for item in directions["front"]), (0.0, 0.0, -1.0))
        self.assertEqual(tuple(round(item, 6) for item in directions["back"]), (0.0, 0.0, 1.0))
        self.assertEqual(tuple(round(item, 6) for item in directions["left"]), (-1.0, 0.0, 0.0))
        self.assertEqual(tuple(round(item, 6) for item in directions["right"]), (1.0, 0.0, 0.0))
        self.assertEqual(tuple(round(item, 6) for item in directions["top"]), (0.0, 1.0, 0.0))
        three_quarter = directions["three_quarter"]
        self.assertGreater(three_quarter[0], 0.0)  # in front of the character, to its right
        self.assertGreater(three_quarter[1], 0.0)  # and above it
        self.assertLess(three_quarter[2], 0.0)
        self.assertAlmostEqual(norm(three_quarter), 1.0, places=9)

    def test_unknown_view_is_rejected(self):
        with self.assertRaises(ValueError) as caught:
            camera.preset_direction("diagonal", self.axes)
        self.assertIn("front", str(caught.exception))


class FitDistanceTests(unittest.TestCase):
    def test_vertical_extent_sets_the_distance(self):
        points = [(0, 0, 0), (0, 180, 0)]
        distance = camera.fit_distance(points, (0, 90, 0), (0, 0, -1), (1, 0, 0), (0, 1, 0), 30, 16 / 9, margin=1.0)
        expected = 90.0 / math.tan(math.radians(15))
        self.assertAlmostEqual(distance, expected, places=6)

    def test_horizontal_extent_sets_the_distance_for_a_wide_character(self):
        points = [(-200, 90, 0), (200, 90, 0)]
        distance = camera.fit_distance(points, (0, 90, 0), (0, 0, -1), (1, 0, 0), (0, 1, 0), 30, 2.0, margin=1.0)
        expected = 200.0 / (math.tan(math.radians(15)) * 2.0)
        self.assertAlmostEqual(distance, expected, places=6)

    def test_margin_and_minimum_distance(self):
        points = [(-1, 90, 0), (1, 90, 0)]
        plain = camera.fit_distance(points, (0, 90, 0), (0, 0, -1), (1, 0, 0), (0, 1, 0), 30, 1.0, margin=1.0)
        padded = camera.fit_distance(points, (0, 90, 0), (0, 0, -1), (1, 0, 0), (0, 1, 0), 30, 1.0, margin=1.2)
        self.assertGreaterEqual(plain, camera.MIN_DISTANCE)
        self.assertAlmostEqual(padded, plain * 1.2, places=6)

    def test_everything_is_inside_the_frame_with_the_margin(self):
        points = STANDARD_POINTS.values()
        target = (0, 100, 0)
        forward, right, up = camera.look_at_basis((0, 0, 1))
        distance = camera.fit_distance(points, target, forward, right, up, 30, 16 / 9)
        position = sub(target, mul(forward, distance))
        for point in points:
            projected = camera.project(point, position, target, 30, 16 / 9)
            self.assertTrue(projected["on_screen"], (point, projected))
            self.assertLessEqual(abs(projected["x"]), 0.9, point)
            self.assertLessEqual(abs(projected["y"]), 0.9, point)


class PresetCameraTests(unittest.TestCase):
    def setUp(self):
        self.axes = camera.character_axes(STANDARD_POINTS)
        self.target = (0.0, 100.0, 0.0)

    def camera_for(self, view, distance=100.0):
        return camera.preset_camera(view, self.target, self.axes, STANDARD_POINTS.values(), distance=distance)

    def test_front_is_on_the_side_the_character_faces(self):
        result = self.camera_for("front")
        self.assertEqual(tuple(round(item, 6) for item in result["position"]), (0.0, 100.0, -100.0))
        self.assertEqual(tuple(round(item, 6) for item in result["target"]), self.target)

    def test_back_left_right_and_top(self):
        self.assertEqual(tuple(round(item, 6) for item in self.camera_for("back")["position"]), (0.0, 100.0, 100.0))
        self.assertEqual(tuple(round(item, 6) for item in self.camera_for("left")["position"]), (-100.0, 100.0, 0.0))
        self.assertEqual(tuple(round(item, 6) for item in self.camera_for("right")["position"]), (100.0, 100.0, 0.0))
        self.assertEqual(tuple(round(item, 6) for item in self.camera_for("top")["position"]), (0.0, 200.0, 0.0))

    def test_three_quarter_is_in_front_right_and_above(self):
        result = self.camera_for("three_quarter")
        self.assertLess(result["position"][2], self.target[2])
        self.assertGreater(result["position"][0], self.target[0])
        self.assertGreater(result["position"][1], self.target[1])

    def test_distance_is_overridable_and_validated(self):
        result = self.camera_for("front", distance=250.0)
        self.assertAlmostEqual(result["distance"], 250.0)
        self.assertAlmostEqual(norm(sub(result["position"], result["target"])), 250.0, places=6)
        for bad in (0, -5, float("nan")):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.camera_for("front", distance=bad)

    def test_automatic_distance_frames_the_whole_character(self):
        result = camera.preset_camera(
            "front", self.target, self.axes, STANDARD_POINTS.values(), fov_deg=30, aspect=16 / 9
        )
        self.assertGreater(result["distance"], 100.0)
        for point in STANDARD_POINTS.values():
            projected = camera.project(point, result["position"], result["target"], 30, 16 / 9)
            self.assertTrue(projected["on_screen"], (point, projected))

    def test_top_view_puts_the_character_front_at_the_top_of_the_picture(self):
        result = self.camera_for("top")
        self.assertEqual(tuple(round(item, 6) for item in result["camera_up"]), (0.0, 0.0, -1.0))


class ProjectTests(unittest.TestCase):
    def test_the_target_projects_to_the_center(self):
        result = camera.project((0, 100, 0), (0, 100, 300), (0, 100, 0), 30, 16 / 9)
        self.assertAlmostEqual(result["x"], 0.0, places=9)
        self.assertAlmostEqual(result["y"], 0.0, places=9)
        self.assertAlmostEqual(result["depth"], 300.0, places=6)
        self.assertTrue(result["on_screen"])

    def test_screen_directions(self):
        camera_position = (0, 100, 300)
        target = (0, 100, 0)
        right = camera.project((50, 100, 0), camera_position, target, 30, 16 / 9)
        above = camera.project((0, 150, 0), camera_position, target, 30, 16 / 9)
        self.assertGreater(right["x"], 0.0)
        self.assertAlmostEqual(right["y"], 0.0, places=9)
        self.assertGreater(above["y"], 0.0)
        self.assertAlmostEqual(above["x"], 0.0, places=9)

    def test_points_behind_the_camera_are_flagged(self):
        result = camera.project((0, 100, 400), (0, 100, 300), (0, 100, 0), 30, 16 / 9)
        self.assertFalse(result["on_screen"])
        self.assertIsNone(result["x"])

    def test_projection_matches_the_camera_ray(self):
        # An independent check: the projected NDC, turned back into a direction through the
        # camera basis, must point at the world point.
        camera_position = (120.0, 250.0, 340.0)
        target = (10.0, 80.0, -20.0)
        point = (60.0, 120.0, 30.0)
        fov, aspect = 42.0, 1.5
        ndc = camera.project(point, camera_position, target, fov, aspect)
        forward, right, up = camera.look_at_basis(sub(target, camera_position))
        tan_vertical = math.tan(math.radians(fov) / 2)
        ray = add(add(forward, mul(right, ndc["x"] * tan_vertical * aspect)), mul(up, ndc["y"] * tan_vertical))
        direction = unit(sub(point, camera_position))
        cross_product = cross(ray, direction)
        self.assertLess(norm(cross_product), 1e-9)


class BBoxTests(unittest.TestCase):
    def test_center_of_the_bounding_box(self):
        self.assertEqual(camera.bbox_center([(0, 0, 0), (10, 20, 30)]), (5.0, 10.0, 15.0))
        self.assertEqual(camera.bbox([(0, 0, 0), (10, 20, 30)]), ((0.0, 0.0, 0.0), (10.0, 20.0, 30.0)))

    def test_empty_points_are_rejected(self):
        with self.assertRaises(ValueError):
            camera.bbox([])


if __name__ == "__main__":
    unittest.main()
