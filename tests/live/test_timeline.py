"""Live tests for the frame, timeline and keyframe tools (T14).

Each test opens Cascadeur's Cascy sample in its own tab; the fixture discards it in tearDown
and checks that the user's scenes and current frame are unchanged. The tests cover the ticket's
completion conditions:

* set_key on frames 0/10/20 for left_hand, list_keys sees them, delete_key(10) leaves 0/20;
* set_frame(15) makes get_timeline and scene.get_current_frame() report 15;
* select_frames(0, 20) + "Timeline.Linear.Linear on selected interval" turns the interval's
  interpolation to linear, as list_keys reports;
* one set_key call is one undo step (get_undo_history/undo/redo, not Ctrl+Z);
* set_animation_range widens the timeline and the whole-character default keys every controller.

Run with: python tests/live/run.py test_timeline.py
"""

from __future__ import annotations

import json
import unittest

try:
    from . import client
    from .fixture import LiveTestCase
except ImportError:  # executed from tests/live
    import client  # type: ignore[no-redef]
    from fixture import LiveTestCase  # type: ignore[no-redef]


LINEAR_ACTION = "Timeline.Linear.Linear on selected interval"

CURRENT_FRAME = """
import json

print("__RESULT__" + json.dumps({"frame": scene.get_current_frame()}))
"""


class TimelineTests(LiveTestCase):
    def _run_json(self, code: str, timeout: float = 90.0) -> dict:
        result = client.call("run_python", code=code, timeout=timeout, http_timeout=timeout + 30.0)
        self.assertTrue(isinstance(result, dict) and result.get("ok"), f"run_python failed: {result!r}")
        for line in reversed(str(result.get("stdout", "")).splitlines()):
            if line.startswith("__RESULT__"):
                return json.loads(line[len("__RESULT__") :])
        self.fail(f"run_python produced no __RESULT__ line: {result!r}")

    def _scene_frame(self) -> int:
        return self._run_json(CURRENT_FRAME)["frame"]

    def _keys(self, result: dict) -> dict:
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(result["count"], 1, result)
        entry = result["controllers"][0]
        self.assertIn("hand_MainPoint_l", entry["names"], entry)
        return entry

    def test_key_cycle_delete_and_one_undo_step(self) -> None:
        self.open_sample("Cascy.casc")
        before = client.call("get_undo_history", limit=10)

        # Cascy ships with a step key on frame 0, so only 10 and 20 are new.
        created = client.call("set_key", frames=[0, 10, 20], controllers=["left_hand"])
        self.assertTrue(created.get("ok"), created)
        self.assertEqual(created["keys_added"] + created["keys_already"], 3, created)
        self.assertEqual(created["keys_added"], 2, created)
        self.assertEqual(created["layers"], 1, created)
        self.assertEqual(created["frames"], [0, 10, 20], created)

        listed = self._keys(client.call("list_keys", controllers=["left_hand"], start=0, end=20))
        self.assertEqual(listed["keys"], [0, 10, 20], listed)
        self.assertIn("interpolation", listed)

        # One set_key call is exactly one undo step; undo removes all three keys at once.
        history = client.call("get_undo_history", limit=10)
        self.assertTrue(history.get("ok"), history)
        self.assertEqual(history["count"], before["count"] + 1, history)
        self.assertEqual(history["steps"][-1]["name"], "MCP: set_key", history)

        undone = client.call("undo")
        self.assertTrue(undone.get("ok"), undone)
        self.assertEqual(undone["steps"], ["MCP: set_key"], undone)
        after_undo = client.call("list_keys", controllers=["left_hand"], start=0, end=20)
        self.assertTrue(after_undo.get("ok"), after_undo)
        # Undo goes back to the sample's own key at frame 0, not to an empty timeline.
        self.assertEqual(after_undo["key_frames"], [0], after_undo)

        redone = client.call("redo")
        self.assertTrue(redone.get("ok"), redone)
        self.assertEqual(redone["steps"], ["MCP: set_key"], redone)
        self.assertEqual(
            client.call("list_keys", controllers=["left_hand"], start=0, end=20)["key_frames"], [0, 10, 20]
        )

        # delete_key removes only frame 10 and is a single undo step too.
        removed = client.call("delete_key", frames=[10], controllers=["left_hand"])
        self.assertTrue(removed.get("ok"), removed)
        self.assertEqual(removed["keys_removed"], 1, removed)
        self.assertEqual(
            self._keys(client.call("list_keys", controllers=["left_hand"], start=0, end=20))["keys"],
            [0, 20],
        )
        self.assertEqual(client.call("list_keys", controllers=["left_hand"], start=0, end=5)["key_frames"], [0])

        # set_frame moves the playhead; both the tool and Cascadeur's scene report frame 15.
        moved = client.call("set_frame", frame=15)
        self.assertTrue(moved.get("ok"), moved)
        self.assertEqual(moved["frame"], 15, moved)
        self.assertEqual(client.call("get_timeline")["frame"], 15)
        self.assertEqual(self._scene_frame(), 15)

    def test_select_frames_then_linear_interpolation(self) -> None:
        self.open_sample("Cascy.casc")
        self.assertTrue(client.call("set_key", frames=[0, 20], controllers=["left_hand"]).get("ok"))

        before = self._keys(client.call("list_keys", controllers=["left_hand"], start=0, end=20))
        self.assertEqual(before["interpolation"].get("0"), "step", before)

        selected = client.call("select_frames", start=0, end=20)
        self.assertTrue(selected.get("ok"), selected)
        self.assertEqual(selected["first"], 0, selected)
        self.assertEqual(selected["last"], 20, selected)
        self.assertEqual(selected["layers"], 13, selected)

        action = client.call("call_action", key=LINEAR_ACTION)
        self.assertEqual(action.get("called"), LINEAR_ACTION)

        entry = self._keys(client.call("list_keys", controllers=["left_hand"], start=0, end=20))
        # The selected interval 0..20 covers the intervals that start at 0; key 20's own
        # outgoing interval is outside it, so it keeps the sample's step interpolation.
        self.assertEqual(entry["interpolation"].get("0"), "linear", entry)
        self.assertEqual(entry["interpolation"].get("20"), "step", entry)
        first_segment = entry["segments"][0]
        self.assertEqual(first_segment["first"], 0, entry)
        self.assertEqual(first_segment["interpolation"], "linear", entry)
        middle = next(segment for segment in entry["segments"] if segment["first"] <= 10 <= segment["last"])
        self.assertEqual(middle["interpolation"], "linear", entry)

    def test_animation_range_and_whole_character_keys(self) -> None:
        opened = self.open_sample("Cascy.casc")
        timeline = client.call("get_timeline")
        self.assertTrue(timeline.get("ok"), timeline)
        self.assertEqual(timeline["first_frame"], 0, timeline)
        self.assertGreater(timeline["last_frame"], 0, timeline)
        self.assertEqual(timeline["length"], timeline["last_frame"] - timeline["first_frame"] + 1, timeline)
        self.assertEqual(timeline["frame"], opened["frame"], timeline)
        self.assertGreater(timeline["fps"], 0, timeline)
        self.assertGreater(timeline["seconds"], 0, timeline)

        new_last = timeline["last_frame"] + 10
        widened = client.call("set_animation_range", last=new_last)
        self.assertTrue(widened.get("ok"), widened)
        self.assertEqual(widened["last_frame"], new_last, widened)
        self.assertEqual(client.call("get_timeline")["last_frame"], new_last)
        boundary = self._run_json(
            """
import json

boundary = csc.app.get_application().current_scene().animation_boundary()
print("__RESULT__" + json.dumps({"first": boundary.first_frame, "last": boundary.last_frame}))
"""
        )
        self.assertEqual(boundary["last"], new_last, boundary)

        outside = client.call("set_key", frames=[new_last], controllers=["left_hand"])
        self.assertTrue(outside.get("ok"), outside)
        self.assertNotIn("warning", outside)
        self.assertEqual(outside["keys_added"], 1, outside)

        # controllers omitted = the whole character: every controller layer gets a key. Cascy
        # groups its controls into 13 timeline layers, and frame 0 is already keyed on all of
        # them, so frame 1 is the one that proves the whole-character default.
        whole = client.call("set_key", frames=[1])
        self.assertTrue(whole.get("ok"), whole)
        self.assertEqual(whole["layers"], 13, whole)
        self.assertEqual(whole["keys_added"], whole["layers"], whole)
        self.assertEqual(whole["keys_already"], 0, whole)
        every = client.call("list_keys", start=1, end=1)
        self.assertTrue(every.get("ok"), every)
        self.assertEqual(every["key_frames"], [1], every)
        self.assertEqual(every["count"], whole["layers"], every)

    def assertToolFails(self, tool: str, message: str, **arguments) -> None:
        # The server turns {"ok": false, ...} into an isError result, which client.call raises.
        with self.assertRaises(client.LiveError) as caught:
            client.call(tool, **arguments)
        self.assertIn(message, str(caught.exception))
        self.assertIn("hint", str(caught.exception))

    def test_argument_errors(self) -> None:
        self.open_sample("Cascy.casc")

        self.assertToolFails("set_frame", "must be 0 or more", frame=-1)
        self.assertToolFails("select_frames", "greater than", start=10, end=5)
        self.assertToolFails("set_animation_range", "greater than", first=30, last=10)
        self.assertToolFails("list_keys", "not_a_bone", controllers=["not_a_bone"], start=0, end=10)


if __name__ == "__main__":
    unittest.main()
