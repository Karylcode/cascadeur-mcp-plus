"""Live tests for select_objects, get_selection and clear_selection (T13).

Each test opens Cascy.casc in its own tab; the fixture discards it in tearDown and checks the
user's scenes, current scene and frame are unchanged. The selection is read back through the tool
and through Cascadeur's own domain scene, and the two-hand selection is screenshotted so the
viewport highlight can be checked by eye. The action test selects the two hands, runs
View.Center camera (an action that acts on the current selection) and checks through the viewport
camera that the camera moved onto the midpoint of the two selected hands and away from the
unselected head. AutoPosingTool.SwitchLock is the ticket's other example, but Cascadeur 2026.2.3
has no Python query for the lock state (T12), so T15 covers the lock's effect behaviourally.

Run with: python tests/live/run.py test_selection.py
"""

from __future__ import annotations

import base64
import json
import os
import time
import unittest

try:  # imported as live.test_selection by unittest discovery
    from . import client
    from .fixture import LiveTestCase
except ImportError:  # executed by run.py
    import client  # type: ignore[no-redef]
    from fixture import LiveTestCase  # type: ignore[no-redef]


# Cascadeur's own view of the selection, used to prove the tools are not echoing arguments.
_SELECTION_CODE = """
import json

mv = scene.model_viewer()
selected = scene.selector().selected()
names = []
for object_id in selected.ids:
    try:
        names.append(mv.get_object_name(object_id))
    except TypeError:
        names.append("<%s %s>" % (type(object_id).__name__, object_id))
print("__RESULT__" + json.dumps({"count": len(names), "names": names}))
"""

# The viewport camera, so a camera action can be checked against the selected control points.
_CAMERA_CODE = """
import json

camera = view_scene.active_viewport().domain_viewport().camera_struct()
print("__RESULT__" + json.dumps({
    "target": [float(value) for value in camera.target],
    "position": [float(value) for value in camera.position],
}))
"""


class SelectionLiveTests(LiveTestCase):
    def setUp(self) -> None:
        self.info = self.open_sample("Cascy.casc")

    # -- helpers ---------------------------------------------------------

    def _run_json(self, code: str, timeout: float = 90.0) -> dict:
        result = client.call("run_python", code=code, timeout=timeout, http_timeout=timeout + 30.0)
        self.assertTrue(isinstance(result, dict) and result.get("ok"), f"run_python failed: {result!r}")
        for line in reversed(str(result.get("stdout", "")).splitlines()):
            if line.startswith("__RESULT__"):
                return json.loads(line[len("__RESULT__") :])
        self.fail(f"run_python produced no __RESULT__ line: {result!r}")

    def select(self, names, mode="replace") -> dict:
        result = client.call("select_objects", names=list(names), mode=mode)
        self.assertTrue(result.get("ok"), result)
        return result

    def selection(self) -> dict:
        result = client.call("get_selection")
        self.assertTrue(result.get("ok"), result)
        return result

    def raw_selection(self) -> dict:
        return self._run_json(_SELECTION_CODE)

    def camera(self) -> dict:
        return self._run_json(_CAMERA_CODE)

    def hand_positions(self) -> dict:
        listed = client.call("list_controllers", kinds=["main_point"])
        self.assertTrue(listed.get("ok"), listed)
        return {entry["alias"]: entry["position"] for entry in listed["controllers"] if entry["alias"]}

    def expect_failure(self, tool: str, **arguments) -> dict:
        """Call a tool that must fail and return its {"ok": false, ...} result.

        client.call raises LiveError for a tool result with "ok": false; the server puts the
        whole result dict in that message, so parse it back out.
        """
        try:
            result = client.call(tool, **arguments)
        except client.LiveError as exc:
            message = str(exc)
            start = message.find("{")
            self.assertGreaterEqual(start, 0, f"no JSON in the error message: {message}")
            return json.loads(message[start:])
        self.fail(f"{tool} unexpectedly succeeded: {result!r}")

    def save_screenshot(self, name: str) -> tuple[str, bytes]:
        result = client.call("screenshot", format="png", max_width=1000, http_timeout=60.0)
        self.assertTrue(result and result.get("images"), f"screenshot returned no image: {result!r}")
        data = base64.b64decode(result["images"][0]["data"])
        self.assertGreater(len(data), 1000, "screenshot is suspiciously small")
        path = os.path.join(self.out_dir(), name)
        with open(path, "wb") as handle:
            handle.write(data)
        return path, data

    # -- tests -----------------------------------------------------------

    def test_select_both_hands_and_read_them_back(self) -> None:
        self.assertTrue(client.call("clear_selection")["ok"])
        clear_path, clear_data = self.save_screenshot("selection_cleared.png")
        self.assertTrue(os.path.isfile(clear_path))

        result = self.select(["left_hand", "right_hand"])
        self.assertEqual(result["mode"], "replace")
        self.assertEqual(result["count"], 2, result)
        self.assertEqual(sorted(result["names"]), ["hand_MainPoint_l", "hand_MainPoint_r"])
        by_alias = {entry["alias"]: entry for entry in result["selection"]}
        self.assertEqual(sorted(by_alias), ["left_hand", "right_hand"])
        for alias in ("left_hand", "right_hand"):
            entry = by_alias[alias]
            self.assertEqual(entry["kind"], "main_point", entry)
            self.assertEqual(entry["type"], "Point", entry)
            self.assertTrue(entry["character"], entry)
            self.assertEqual(entry["name"], f"hand_MainPoint_{'l' if alias == 'left_hand' else 'r'}")

        # get_selection reads the same domain selection, and does not reuse select_objects' reply.
        listing = self.selection()
        self.assertEqual(listing["count"], 2, listing)
        self.assertEqual(sorted(listing["names"]), ["hand_MainPoint_l", "hand_MainPoint_r"])
        raw = self.raw_selection()
        self.assertEqual(raw["count"], 2, raw)
        self.assertEqual(sorted(raw["names"]), ["hand_MainPoint_l", "hand_MainPoint_r"])
        scene = client.call("get_scene_info")
        self.assertEqual(scene["selection"]["count"], 2, scene["selection"])

        # The viewport/UI must show the selection: the screenshot changes once the two control
        # points are selected. The PNG is kept for a visual check of the highlight.
        selected_path, selected_data = self.save_screenshot("selection_two_hands.png")
        self.assertTrue(os.path.isfile(selected_path))
        self.assertNotEqual(
            clear_data,
            selected_data,
            "the interface looks identical with and without a selection; the viewport is not showing it",
        )

    def test_add_remove_and_clear(self) -> None:
        self.select(["left_hand"], mode="replace")
        self.assertEqual(self.selection()["names"], ["hand_MainPoint_l"])

        added = self.select(["right_hand"], mode="add")
        self.assertEqual(sorted(added["names"]), ["hand_MainPoint_l", "hand_MainPoint_r"])
        again = self.select(["left_hand"], mode="add")
        self.assertEqual(again["count"], 2, again)  # adding the same object twice does not duplicate it

        removed = self.select(["left_hand"], mode="remove")
        self.assertEqual(removed["names"], ["hand_MainPoint_r"], removed)
        unchanged = self.select(["left_hand"], mode="remove")
        self.assertEqual(unchanged["names"], ["hand_MainPoint_r"], unchanged)

        cleared = client.call("clear_selection")
        self.assertTrue(cleared["ok"], cleared)
        self.assertEqual(cleared["count"], 0, cleared)
        self.assertEqual(self.selection()["count"], 0)
        self.assertEqual(self.raw_selection()["count"], 0)

    def test_action_on_selection_uses_the_selected_hands(self) -> None:
        def distance(point, other):
            return sum((a - b) ** 2 for a, b in zip(point, other, strict=True)) ** 0.5

        def center(timeout: float = 2.0) -> list:
            """Run the action and wait for the camera target to settle on the new value."""
            previous = self.camera()["target"]
            call = client.call("call_action", key="View.Center camera")
            self.assertEqual(call.get("called"), "View.Center camera", call)
            deadline = time.monotonic() + timeout
            target = previous
            while time.monotonic() < deadline:
                time.sleep(0.25)
                target = self.camera()["target"]
                if distance(target, previous) > 0.5:
                    return target
            return target

        self.assertTrue(client.call("clear_selection")["ok"])
        self.select(["left_hand", "right_hand"])
        positions = self.hand_positions()
        left, right, head = positions["left_hand"], positions["right_hand"], positions["head"]

        # View.Center camera acts on the current selection: with the two hands selected it must
        # land on a selected hand, not on the whole character (which would be the head/torso).
        # Cascadeur centres on the selection pivot (the first selected object), so check both
        # hands as a pair and each hand on its own.
        before = self.camera()["target"]
        target = center()
        self.assertGreater(distance(before, target), 1.0, f"View.Center camera did not move the camera: {before}")
        self.assertLess(
            min(distance(target, left), distance(target, right)),
            5.0,
            f"camera target {target} is on neither selected hand (left={left}, right={right})",
        )
        self.assertGreater(
            distance(target, head),
            30.0,
            f"camera target {target} is too close to the unselected head {head}; the action ignored the selection",
        )

        for alias, position in (("left_hand", left), ("right_hand", right)):
            self.select([alias])
            solo_target = center()
            self.assertLess(
                distance(solo_target, position),
                5.0,
                f"camera target {solo_target} did not move to the selected {alias} at {position}",
            )
            self.assertGreater(
                distance(solo_target, head), 30.0, f"{alias}: camera target {solo_target} is on the head"
            )

        # The action operates on the selection; with both hands selected again it stays as it was.
        listing = self.select(["left_hand", "right_hand"])
        self.assertEqual(sorted(listing["names"]), ["hand_MainPoint_l", "hand_MainPoint_r"], listing)
        self.assertEqual(sorted(self.selection()["names"]), ["hand_MainPoint_l", "hand_MainPoint_r"])

    def test_bad_names_and_modes_leave_the_selection_alone(self) -> None:
        self.select(["left_hand"])

        ambiguous = self.expect_failure("select_objects", names=["hand"], mode="replace")
        self.assertIn("hand_MainPoint_l", [item["name"] for item in ambiguous.get("candidates", [])])
        self.assertEqual(self.selection()["names"], ["hand_MainPoint_l"])

        unknown = self.expect_failure("select_objects", names=["not_a_bone"], mode="replace")
        self.assertIn("hint", unknown)
        self.assertEqual(self.selection()["names"], ["hand_MainPoint_l"])

        bad_mode = self.expect_failure("select_objects", names=["right_hand"], mode="toggle")
        self.assertIn("mode must be one of", bad_mode["error"])
        self.assertEqual(self.selection()["names"], ["hand_MainPoint_l"])

        self.expect_failure("select_objects", names=[], mode="replace")
        self.assertEqual(self.selection()["names"], ["hand_MainPoint_l"])


if __name__ == "__main__":
    unittest.main()
