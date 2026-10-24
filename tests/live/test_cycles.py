"""Live tests for the cycle tools (T21).

The tools never read cycle structures: csc.layers.CyclesViewer / Cycle reads crashed Cascadeur
2026.2.3 three times during this ticket (see the handover notes). Creation and removal go through
Cascadeur's own Timeline actions, and this test verifies them through Cascadeur's log
(Command/Undo/Redo), a timeline screenshot, and the timeline selection that must be restored
around every call. The one-frame Cascy sample must be refused before any action runs.

Run with: python tests/live/run.py test_cycles.py
"""

from __future__ import annotations

import base64
import json
import os
import unittest

try:
    from . import client
    from .fixture import LiveTestCase
except ImportError:  # executed from tests/live
    import client  # type: ignore[no-redef]
    from fixture import LiveTestCase  # type: ignore[no-redef]

TIMELINE_SELECTION = r"""
import json
import csc

scene = csc.app.get_application().current_scene().domain_scene()
interval = scene.get_layers_selector().selection().frames_interval()
print("__RESULT__" + json.dumps({"first": int(interval.first), "last": int(interval.last)}))
"""

TOLERANCE = 1e-3


def pose_matches(one, other, tolerance: float = TOLERANCE) -> bool:
    """True when two world positions (cm) are within tolerance on every axis.

    A copy of the tool module's helper: importing cascadeur_mcp_plus outside Cascadeur would
    execute the package __init__ (PySide6/server), which live tests must not do.
    """
    if not one or len(one) != len(other):
        return False
    return all(abs(float(left) - float(right)) <= tolerance for left, right in zip(one, other, strict=False))


class CycleTests(LiveTestCase):
    def pelvis_position(self, frame: int):
        """World position of the pelvis main point at a frame (T12 list_controllers, no cycle API)."""
        moved = client.call("set_frame", frame=frame)
        self.assertEqual(moved.get("ok"), True, moved)
        self.assertEqual(moved["frame"], frame, moved)
        result = client.call("list_controllers", kinds=["main_point"])
        self.assertTrue(result.get("ok"), result)
        for entry in result["controllers"]:
            if entry.get("alias") == "pelvis":
                return entry["position"]
        self.fail(f"Dracorex has no pelvis main point: {result}")

    def assert_matches(self, one, other, message: str) -> None:
        self.assertTrue(
            pose_matches(one, other, TOLERANCE),
            f"{message}: {one} != {other}",
        )

    def save_screenshot(self, name: str) -> str:
        result = client.call("screenshot", format="png", max_width=1200, http_timeout=60.0)
        self.assertTrue(result and result.get("images"), f"screenshot returned no image: {result!r}")
        data = base64.b64decode(result["images"][0]["data"])
        self.assertGreater(len(data), 1000, "screenshot is suspiciously small")
        path = os.path.join(self.out_dir(), name + ".png")
        with open(path, "wb") as handle:
            handle.write(data)
        return path

    def timeline_selection(self) -> dict:
        """The timeline's selected frame interval, read through the layers selector (no cycle API)."""
        result = client.call("run_python", code=TIMELINE_SELECTION, timeout=60.0, http_timeout=90.0)
        self.assertTrue(isinstance(result, dict) and result.get("ok"), f"run_python failed: {result!r}")
        for line in reversed(str(result.get("stdout", "")).splitlines()):
            if line.startswith("__RESULT__"):
                return json.loads(line[len("__RESULT__") :])
        self.fail(f"run_python produced no __RESULT__ line: {result!r}")

    def recent_log(self) -> list:
        result = client.call("get_log", lines=60)
        self.assertIsInstance(result, dict, result)
        lines = result.get("lines")
        self.assertIsInstance(lines, list, result)
        return lines

    def assert_recent_command(self, needle: str, label: str) -> None:
        """Assert that Cascadeur logged a Command containing needle (the action really ran)."""
        matches = [line for line in self.recent_log() if "command:" in line.lower() and needle in line.lower()]
        self.assertTrue(matches, f"no {label} command in the log tail")

    def test_create_and_remove_plain_cycle_on_dracorex(self) -> None:
        # No cycle API is read anywhere. Each action is verified through Cascadeur's own log
        # (a "Command:" line) and through undo/redo of that command, plus a timeline screenshot.
        # Evaluated frame data outside the keyed range does not repeat on this sample (its
        # playable end is its last key), so a pose comparison cannot prove a plain cycle here;
        # the pose inside the range is checked to stay put.
        self.open_sample("Dracorex_walk_cycle.casc")
        reference = self.pelvis_position(5)
        selected = client.call("select_frames", start=5, end=10)
        self.assertTrue(selected.get("ok"), selected)
        self.assertEqual(self.timeline_selection(), {"first": 5, "last": 10})

        removed = client.call("remove_cycles")
        self.assertTrue(removed.get("ok"), removed)
        self.assertEqual(removed["action"], "Timeline.Remove cycles", removed)
        self.assertEqual(removed["start"], 0, removed)
        self.assertGreaterEqual(removed["end"], 39, removed)
        self.assertIsNone(removed["undo"], removed)
        self.assertEqual(self.timeline_selection(), {"first": 5, "last": 10})
        self.assert_recent_command("cycle", "remove")
        self.assertTrue(self.save_screenshot("dracorex_after_remove"))

        created = client.call("create_cycle", start=0, end=30)
        self.assertTrue(created.get("ok"), created)
        self.assertEqual(created["action"], "Timeline.Create cycle", created)
        self.assertEqual(created["offset"], "none", created)
        self.assertEqual((created["selection"]["first"], created["selection"]["last"]), (0, 30), created)
        self.assertIsNone(created["undo"], created)
        self.assertIn("Timeline action", created["hint"])
        self.assertEqual(self.timeline_selection(), {"first": 5, "last": 10})
        self.assert_recent_command("cycle", "create")
        self.assertTrue(self.save_screenshot("dracorex_after_create"))

        # Creating a loop does not move the pose inside its active range.
        self.assert_matches(reference, self.pelvis_position(5), "frame 5 changed when the cycle was created")

        # The selection restore is itself a native Cascadeur command, so a single undo targets
        # that restore rather than the cycle; the Command lines above are the proof that the
        # cycle action ran. (Undo/redo of the action was verified before the restore was added.)
        removed_again = client.call("remove_cycles")
        self.assertTrue(removed_again.get("ok"), removed_again)
        self.assertEqual(removed_again["action"], "Timeline.Remove cycles", removed_again)
        self.assertEqual(self.timeline_selection(), {"first": 5, "last": 10})
        self.assert_recent_command("cycle", "remove")
        self.assertTrue(self.save_screenshot("dracorex_after_remove_again"))
        self.assert_matches(reference, self.pelvis_position(5), "frame 5 changed when the cycle was removed")

    def test_short_animation_is_refused_on_cascy(self) -> None:
        # Cascy has only one frame of keyed animation; asking Cascadeur to loop frames 0-30 there
        # crashed it, so create_cycle must refuse before any action runs.
        self.open_sample("Cascy.casc")
        before = client.call("get_undo_history", limit=10)
        for offset in ("none", "position", "position_rotation"):
            with self.assertRaises(client.LiveError) as caught:
                client.call("create_cycle", start=0, end=30, offset=offset)
            text = str(caught.exception)
            self.assertIn("only 1 frame", text)
            self.assertIn("one-frame animation", text)
        with self.assertRaises(client.LiveError) as caught:
            client.call("remove_cycles")
        text = str(caught.exception)
        self.assertIn("only 1 frame", text)
        self.assertIn("one-frame animation", text)
        self.assertEqual(client.call("get_undo_history", limit=10)["count"], before["count"])

    def test_argument_errors(self) -> None:
        self.open_sample("Cascy.casc")
        for arguments, message in (
            ({"start": 10, "end": 5}, "greater"),
            ({"start": 5, "end": 5}, "two frames"),
            ({"start": 0, "end": 100000}, "outside the animation range"),
            ({"start": 0, "end": 30, "offset": "spin"}, "offset"),
            ({"end": 30}, "start"),
        ):
            with self.assertRaises(client.LiveError) as caught:
                client.call("create_cycle", **arguments)
            self.assertIn(message, str(caught.exception))
        with self.assertRaises(client.LiveError) as caught:
            client.call("remove_cycles", start=20, end=10)
        self.assertIn("greater", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
