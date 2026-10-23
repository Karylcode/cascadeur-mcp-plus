r"""Tests for cascadeur_mcp_plus/tools/history.py (no Cascadeur needed).

The module under test imports only the standard library at import time, so it is loaded
straight from its file. Cascadeur's log is replaced by a temp file and the action runner by a
fake that appends the log lines Cascadeur would write.

The fragments in REAL_* were copied from this machine's Cascadeur log
(%LOCALAPPDATA%\Nekki Limited\Cascadeur\logs\cascadeur_log.log, Cascadeur 2026.2.3) without
editing their timestamps or wording, so the tests exercise the real line formats, real startup
sequences and the real memory-manager message.

Run: python -m unittest discover -s tests -v
"""

from __future__ import annotations

import importlib.util
import os
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STAMP = "2026-10-24 01:52:15,189"


def load_history():
    spec = importlib.util.spec_from_file_location(
        "history_under_test", os.path.join(ROOT, "cascadeur_mcp_plus", "tools", "history.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


history = load_history()


def line(text):
    return f"{STAMP} {text}\n"


# A real launch block: log lines 7830-7850 (2026-10-24 04:44). The two transient duplicate
# instances at 01:40 and 01:45 did not write "auto deleting logs"; a real start always does.
REAL_LAUNCH = r"""2026-10-24 04:44:25,764 Application executable path: C:/Program Files/Cascadeur/cascadeur.exe
2026-10-24 04:44:25,783 Operating system: Microsoft Windows (Undetected)
2026-10-24 04:44:25,783 Window manager: Windows Explorer
2026-10-24 04:44:25,783 RAM - capacity: 0 MB
2026-10-24 04:44:25,783 Program version: 2026.2.3 6f839ab373704c701854a9b4ab3bb788d6f47112
2026-10-24 04:44:25,791 ScriptsDir: "C:/Program Files/Cascadeur/resources/scripts"
2026-10-24 04:44:25,834 License flags: perpetual
2026-10-24 04:44:26,688 GL_VERSION: "4.1.0 NVIDIA 621.01"
2026-10-24 04:44:27,507 auto deleting logs: start
2026-10-24 04:44:27,508 auto deleting logs: finish
2026-10-24 04:44:27,512 Command: Select default layer
2026-10-24 04:44:27,513 Command: default constants initialization
2026-10-24 04:44:41,461 Launch time 00:00:15.715
2026-10-24 04:44:41,848 [Cascadeur MCP] start command executed
"""

# A real memory-manager clear: log lines 3169-3178 (2026-10-24 03:10). Four scenes are open, so
# the marker appears once per scene, each followed by the reason line.
REAL_CLEAR = r"""2026-10-24 03:10:02,662 WARNING [C:\csc\src\presenter\memory\memory_manager.cpp:118] Memory usage of current process = 6208 Megabytes (limit = 6000 Megabytes)
2026-10-24 03:10:02,662 WARNING [C:\csc\src\presenter\memory\memory_manager.cpp:124] History will be cleared in every scene!
2026-10-24 03:10:02,663 History was cleared because of:
2026-10-24 03:10:02,663 * Memory usage of current process = 6208 Megabytes (limit = 6000 Megabytes)
2026-10-24 03:10:02,668 History was cleared because of:
2026-10-24 03:10:02,668 * Memory usage of current process = 6208 Megabytes (limit = 6000 Megabytes)
2026-10-24 03:10:02,669 History was cleared because of:
2026-10-24 03:10:02,669 * Memory usage of current process = 6208 Megabytes (limit = 6000 Megabytes)
2026-10-24 03:10:02,669 History was cleared because of:
2026-10-24 03:10:02,669 * Memory usage of current process = 6208 Megabytes (limit = 6000 Megabytes)
"""

# A real sample load: log lines 7792-7797 (2026-10-24 04:43).
REAL_LOAD = r"""2026-10-24 04:43:07,675 Command: Select default layer
2026-10-24 04:43:07,675 Command: default constants initialization
2026-10-24 04:43:08,910 Start loading of C:/Program Files/Cascadeur/samples/Cascy.casc
2026-10-24 04:43:09,648 Command: load scene
2026-10-24 04:43:09,666 Command: create autoposing
2026-10-24 04:43:09,666 Command: Trajectory: change trajectory interval
"""

# A real new tab created for an import: log lines 1456-1460 (2026-10-24 02:53).
REAL_NEW_SCENE = r"""2026-10-24 02:53:46,486 Command: Select default layer
2026-10-24 02:53:46,486 Command: default constants initialization
2026-10-24 02:53:47,736 __NEWSCENE__untitled (1).casc
2026-10-24 02:53:48,451 Using 'cm' unit
2026-10-24 02:53:48,611 Command: Import FBX Model
"""

REAL_CLEAR_REASON = "Memory usage of current process = 6208 Megabytes (limit = 6000 Megabytes)"


class HistoryTestCase(unittest.TestCase):
    """A temp log file, a fresh tracker, and a fast log poll."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.log = os.path.join(self.temp.name, "cascadeur_log.log")
        open(self.log, "w", encoding="utf-8").close()
        history.reset(self.log)
        self.addCleanup(history.reset)
        old_timeout = history.EVENT_TIMEOUT
        history.EVENT_TIMEOUT = 0.05
        self.addCleanup(setattr, history, "EVENT_TIMEOUT", old_timeout)
        self.keys = []
        self.queue = []  # (undo|redo, name) lines the fake Cascadeur writes per action
        history._run_action = self.fake_action
        self.addCleanup(setattr, history, "_run_action", history._run_action)

    def append(self, *lines):
        with open(self.log, "a", encoding="utf-8") as handle:
            for text in lines:
                handle.write(line(text))

    def write_raw(self, text):
        with open(self.log, "a", encoding="utf-8") as handle:
            handle.write(text if text.endswith("\n") else text + "\n")

    def fake_action(self, key):
        self.keys.append(key)
        if self.queue:
            kind, name = self.queue.pop(0)
            self.append(f"'{kind.capitalize()}' of command '{name}'")

    def tracker(self):
        return history._tracker()


class ParseLineTests(unittest.TestCase):
    def test_command_line(self):
        self.assertEqual(history._parse_line(line("Command: MCP: probe")), ("command", "MCP: probe", STAMP))
        self.assertEqual(history._parse_line(line("Command: load scene")), ("command", "load scene", STAMP))

    def test_undo_and_redo_lines(self):
        self.assertEqual(history._parse_line(line("'Undo' of command 'MCP: probe'")), ("undo", "MCP: probe", STAMP))
        self.assertEqual(history._parse_line(line("'Redo' of command 'MCP: probe'")), ("redo", "MCP: probe", STAMP))

    def test_reset_markers(self):
        self.assertEqual(
            history._parse_line(line("Application executable path: C:/Program Files/Cascadeur/cascadeur.exe")),
            ("reset", "Cascadeur started", STAMP),
        )
        self.assertEqual(history._parse_line(line("auto deleting logs: finish")), ("reset", "Cascadeur started", STAMP))
        warning = line(
            r"WARNING [C:\csc\src\presenter\memory\memory_manager.cpp:124] History will be cleared in every scene!"
        )
        self.assertEqual(history._parse_line(warning), ("reset", "undo history is being cleared", STAMP))
        self.assertEqual(
            history._parse_line(line("History was cleared because of:")),
            ("reset", "undo history cleared", STAMP),
        )

    def test_clear_reason_line(self):
        reason = line(f"* {REAL_CLEAR_REASON}")
        self.assertEqual(history._clear_reason(reason), REAL_CLEAR_REASON)
        self.assertIsNone(history._clear_reason(line("Command: MCP: probe")))
        self.assertIsNone(history._clear_reason("no timestamp"))

    def test_other_lines_are_ignored(self):
        self.assertIsNone(history._parse_line(line("Start loading of C:/Cascadeur/samples/Cascy.casc")))
        self.assertIsNone(history._parse_line("Traceback (most recent call last):"))
        self.assertIsNone(history._parse_line("Command: no timestamp"))
        self.assertIsNone(history._parse_line(""))


class TrackerTests(HistoryTestCase):
    def test_commands_are_collected_in_order(self):
        self.append("Command: MCP: first", "Command: MCP: second")
        self.tracker().sync()
        snapshot = self.tracker().snapshot(10)
        self.assertEqual([step["name"] for step in snapshot["steps"]], ["MCP: first", "MCP: second"])
        self.assertEqual(snapshot["position"], 2)
        self.assertEqual(snapshot["next_undo"], "MCP: second")
        self.assertIsNone(snapshot["next_redo"])

    def test_non_mcp_commands_are_not_listed(self):
        self.append("Command: Select default layer", "Command: MCP: first", "Command: Match pose")
        self.tracker().sync()
        snapshot = self.tracker().snapshot(10)
        self.assertEqual([step["name"] for step in snapshot["steps"]], ["MCP: first"])
        self.assertEqual(snapshot["position"], 1)

    def test_undo_and_redo_move_the_position(self):
        self.append(
            "Command: MCP: first",
            "Command: MCP: second",
            "'Undo' of command 'MCP: second'",
            "'Undo' of command 'MCP: first'",
        )
        self.tracker().sync()
        snapshot = self.tracker().snapshot(10)
        self.assertEqual(snapshot["position"], 0)
        self.assertEqual([step["state"] for step in snapshot["steps"]], ["undone", "undone"])

        self.append("'Redo' of command 'MCP: first'", "'Redo' of command 'MCP: second'")
        self.tracker().sync()
        self.assertEqual(self.tracker().snapshot(10)["position"], 2)

    def test_undo_of_a_command_outside_the_record_is_ignored(self):
        self.append("Command: MCP: first", "'Undo' of command 'Move joints'")
        self.tracker().sync()
        self.assertEqual(self.tracker().snapshot(10)["position"], 1)

    def test_new_command_discards_the_redo_branch(self):
        self.append(
            "Command: MCP: first",
            "Command: MCP: second",
            "'Undo' of command 'MCP: second'",
            "Command: MCP: third",
        )
        self.tracker().sync()
        snapshot = self.tracker().snapshot(10)
        self.assertEqual([step["name"] for step in snapshot["steps"]], ["MCP: first", "MCP: third"])
        self.assertEqual(snapshot["position"], 2)

    def test_duplicate_names_undo_one_at_a_time(self):
        self.append("Command: MCP: same", "Command: MCP: same", "'Undo' of command 'MCP: same'")
        self.tracker().sync()
        self.assertEqual(self.tracker().snapshot(10)["position"], 1)
        self.append("'Undo' of command 'MCP: same'", "'Redo' of command 'MCP: same'")
        self.tracker().sync()
        self.assertEqual(self.tracker().snapshot(10)["position"], 1)

    def test_load_scene_starts_a_new_record(self):
        self.append("Command: MCP: old", "Command: load scene", "Command: create autoposing")
        self.tracker().sync()
        snapshot = self.tracker().snapshot(10)
        self.assertEqual(snapshot["steps"], [])
        self.assertEqual(snapshot["position"], 0)
        self.assertIsNone(snapshot["next_undo"])
        self.assertEqual(snapshot["reset_reason"], "scene loaded")

    def test_new_scene_command_starts_a_new_record(self):
        self.append("Command: MCP: old", "Command: Select default layer", "Command: default constants initialization")
        self.tracker().sync()
        snapshot = self.tracker().snapshot(10)
        self.assertEqual(snapshot["steps"], [])
        self.assertEqual(snapshot["reset_reason"], "new scene created")

    def test_partial_line_waits_for_the_newline(self):
        with open(self.log, "a", encoding="utf-8") as handle:
            handle.write(f"{STAMP} Command: MCP: half")
        self.assertEqual(self.tracker().sync(), [])
        self.assertEqual(self.tracker().steps, [])
        with open(self.log, "a", encoding="utf-8") as handle:
            handle.write("-written\n")
        self.tracker().sync()
        self.assertEqual(self.tracker().steps, ["MCP: half-written"])

    def test_replaced_log_is_read_from_the_start(self):
        for index in range(5):
            self.append(f"Command: MCP: old {index}")
        self.tracker().sync()
        self.assertEqual(self.tracker().snapshot(10)["count"], 5)
        # A smaller log means Cascadeur replaced it; the tracker must not skip the new lines.
        with open(self.log, "w", encoding="utf-8") as handle:
            handle.write(line("Command: load scene"))
            handle.write(line("Command: MCP: second"))
        self.tracker().sync()
        self.assertEqual(self.tracker().steps, ["MCP: second"])

    def test_missing_log_is_ignored(self):
        os.remove(self.log)
        self.assertEqual(self.tracker().sync(), [])
        self.assertFalse(history._log_available(self.tracker()))

    def test_limit_returns_the_newest_steps(self):
        for index in range(5):
            self.append(f"Command: MCP: step {index}")
        snapshot = self.tracker().snapshot(2)
        self.assertEqual([step["name"] for step in snapshot["steps"]], ["MCP: step 3", "MCP: step 4"])
        self.assertEqual([step["index"] for step in snapshot["steps"]], [3, 4])
        self.assertTrue(snapshot["truncated"])
        self.assertEqual(snapshot["count"], 5)

    def test_record_start_survives_more_log_lines(self):
        self.write_raw("2026-10-24 04:43:09,648 Command: load scene\n")
        self.write_raw("2026-10-24 04:43:20,000 Command: MCP: first\n")
        self.write_raw("2026-10-24 04:43:21,000 Command: MCP: second\n")
        snapshot = self.tracker().snapshot(10)
        self.assertEqual([step["name"] for step in snapshot["steps"]], ["MCP: first", "MCP: second"])
        self.assertEqual(snapshot["recorded_since"], "2026-10-24 04:43:09,648")
        self.assertEqual(snapshot["reset_reason"], "scene loaded")
        self.assertEqual(snapshot["log"], self.log)


class RealLogFragmentTests(HistoryTestCase):
    """Replays real log slices: a restart and a memory clear must not leave stale steps."""

    def test_cross_launch_replay_keeps_only_the_new_session(self):
        self.write_raw("2026-10-24 03:00:00,000 Command: MCP: before restart\n")
        self.write_raw("2026-10-24 03:00:01,000 'Undo' of command 'MCP: before restart'\n")
        self.write_raw("2026-10-24 03:00:02,000 Command: MCP: still before restart\n")
        self.write_raw(REAL_LAUNCH)
        self.write_raw("2026-10-24 04:45:00,000 Command: MCP: after restart\n")

        snapshot = self.tracker().snapshot(10)
        self.assertEqual([step["name"] for step in snapshot["steps"]], ["MCP: after restart"])
        self.assertEqual(snapshot["position"], 1)
        self.assertEqual(snapshot["next_undo"], "MCP: after restart")
        self.assertEqual(snapshot["recorded_since"], "2026-10-24 04:44:27,512")
        self.assertEqual(snapshot["reset_reason"], "new scene created")

    def test_memory_clear_drops_every_recorded_step(self):
        self.write_raw("2026-10-24 03:09:00,000 Command: MCP: before clear\n")
        self.write_raw("2026-10-24 03:09:30,000 Command: MCP: also before clear\n")
        self.write_raw(REAL_CLEAR)
        self.write_raw("2026-10-24 03:11:00,000 Command: MCP: after clear\n")

        snapshot = self.tracker().snapshot(10)
        self.assertEqual([step["name"] for step in snapshot["steps"]], ["MCP: after clear"])
        self.assertEqual(snapshot["recorded_since"], "2026-10-24 03:10:02,669")
        self.assertEqual(snapshot["reset_reason"], f"undo history cleared: {REAL_CLEAR_REASON}")

    def test_scene_load_resets_the_record(self):
        self.write_raw("2026-10-24 04:43:00,000 Command: MCP: before load\n")
        self.write_raw(REAL_LOAD)

        snapshot = self.tracker().snapshot(10)
        self.assertEqual(snapshot["steps"], [])
        self.assertEqual(snapshot["recorded_since"], "2026-10-24 04:43:09,648")
        self.assertEqual(snapshot["reset_reason"], "scene loaded")

    def test_new_tab_resets_the_record(self):
        self.write_raw("2026-10-24 02:53:00,000 Command: MCP: before tab\n")
        self.write_raw(REAL_NEW_SCENE)

        snapshot = self.tracker().snapshot(10)
        self.assertEqual(snapshot["steps"], [])
        self.assertEqual(snapshot["recorded_since"], "2026-10-24 02:53:46,486")
        self.assertEqual(snapshot["reset_reason"], "new scene created")

    def test_history_reports_where_the_record_starts(self):
        self.write_raw("2026-10-24 03:09:00,000 Command: MCP: gone\n")
        self.write_raw(REAL_CLEAR)

        result = history.get_undo_history()
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["steps"], [])
        self.assertEqual(result["position"], 0)
        self.assertEqual(result["recorded_since"], "2026-10-24 03:10:02,669")
        self.assertIn("undo history cleared", result["reset_reason"])
        self.assertIn("Memory usage", result["reset_reason"])
        self.assertIn("The record starts at 2026-10-24 03:10:02,669", result["note"])
        self.assertIsNone(result["next_undo"])
        self.assertIsNone(result["next_redo"])

    def test_undo_after_restart_reports_nothing_to_undo(self):
        # The scenario from the review: a step is recorded, Cascadeur restarts, and undo() would
        # find an empty stack. The tracker must not report the old step as applied.
        self.write_raw("2026-10-24 03:00:00,000 Command: MCP: early edit\n")
        self.write_raw(REAL_LAUNCH)

        result = history.undo()
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "Nothing to undo.")
        self.assertIn("2026-10-24 04:44:27,512", result["hint"])
        self.assertEqual(self.tracker().snapshot(10)["steps"], [])


class UndoRedoTests(HistoryTestCase):
    def test_undo_returns_the_names_cascadeur_undid(self):
        self.append("Command: MCP: first", "Command: MCP: second")
        self.queue = [("undo", "MCP: second"), ("undo", "MCP: first")]
        result = history.undo(2)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["action"], "undo")
        self.assertEqual(result["requested"], 2)
        self.assertEqual(result["count"], 2)
        self.assertEqual(result["steps"], ["MCP: second", "MCP: first"])
        self.assertEqual(self.keys, ["Scene.Undo", "Scene.Undo"])
        self.assertEqual(self.tracker().snapshot(10)["position"], 0)

    def test_redo_returns_the_names_cascadeur_redid(self):
        self.append("Command: MCP: first", "Command: MCP: second", "'Undo' of command 'MCP: second'")
        self.queue = [("redo", "MCP: second")]
        result = history.redo()
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["action"], "redo")
        self.assertEqual(result["steps"], ["MCP: second"])
        self.assertEqual(self.keys, ["Scene.Redo"])
        self.assertEqual(self.tracker().snapshot(10)["position"], 2)

    def test_undo_stops_when_the_stack_runs_out(self):
        self.append("Command: MCP: only")
        self.queue = [("undo", "MCP: only")]
        result = history.undo(3)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["requested"], 3)
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["steps"], ["MCP: only"])
        self.assertIn("Only 1 of 3", result["hint"])

    def test_undo_without_history_fails(self):
        result = history.undo()
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "Nothing to undo.")
        self.assertIn("undo stack is empty", result["hint"])
        self.assertEqual(self.keys, ["Scene.Undo"])

    def test_redo_without_undone_steps_fails(self):
        self.append("Command: MCP: first")
        result = history.redo()
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "Nothing to redo.")
        self.assertIn("no undone steps", result["hint"])

    def test_invalid_steps_are_rejected_without_running_the_action(self):
        for value in (0, -2, "nope", None, True):
            result = history.undo(value)
            self.assertFalse(result["ok"], value)
            self.assertIn("steps must be a positive integer", result["error"])
        self.assertEqual(self.keys, [])

    def test_steps_are_capped(self):
        old = history.MAX_STEPS
        history.MAX_STEPS = 2
        self.addCleanup(setattr, history, "MAX_STEPS", old)
        self.append("Command: MCP: a", "Command: MCP: b", "Command: MCP: c")
        self.queue = [("undo", "MCP: c"), ("undo", "MCP: b")]
        result = history.undo(5)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["requested"], 2)
        self.assertEqual(result["count"], 2)
        self.assertIn("capped at 2", result["hint"])

    def test_action_failure_is_reported(self):
        def boom(key):
            raise RuntimeError("no scene")

        history._run_action = boom
        result = history.undo()
        self.assertFalse(result["ok"])
        self.assertIn("Scene.Undo", result["error"])
        self.assertIn("no scene", result["detail"])
        self.assertIn("hint", result)

    def test_falls_back_to_the_mcp_record_when_the_log_is_missing(self):
        self.append("Command: MCP: first", "Command: MCP: second")
        self.tracker().sync()
        os.remove(self.log)
        history._run_action = lambda key: self.keys.append(key)
        result = history.undo()
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["steps"], ["MCP: second"])
        self.assertEqual(result["source"], "MCP Plus's own record")
        self.assertIn("log is unreadable", result["hint"])
        self.assertEqual(self.tracker().snapshot(10)["position"], 1)


class HistoryToolTests(HistoryTestCase):
    def test_position_marks_applied_and_undone_steps(self):
        self.append("Command: MCP: first", "Command: MCP: second", "'Undo' of command 'MCP: second'")
        result = history.get_undo_history()
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["position"], 1)
        self.assertEqual(result["count"], 2)
        self.assertFalse(result["truncated"])
        self.assertEqual([step["name"] for step in result["steps"]], ["MCP: first", "MCP: second"])
        self.assertEqual([step["state"] for step in result["steps"]], ["applied", "undone"])
        self.assertEqual(result["next_undo"], "MCP: first")
        self.assertEqual(result["next_redo"], "MCP: second")
        self.assertIn("MCP steps only", result["source"])

    def test_limit_is_applied_and_reported(self):
        for index in range(4):
            self.append(f"Command: MCP: step {index}")
        result = history.get_undo_history(2)
        self.assertEqual([step["name"] for step in result["steps"]], ["MCP: step 2", "MCP: step 3"])
        self.assertTrue(result["truncated"])

    def test_invalid_limit_is_rejected(self):
        for value in (0, -1, "many", None, True):
            self.assertFalse(history.get_undo_history(value)["ok"], value)

    def test_missing_log_warns(self):
        os.remove(self.log)
        result = history.get_undo_history()
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["steps"], [])
        self.assertIn("log is not readable", result["warning"])

    def test_empty_history_is_ok(self):
        result = history.get_undo_history()
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["steps"], [])
        self.assertEqual(result["position"], 0)
        self.assertIsNone(result["next_undo"])
        self.assertIsNone(result["recorded_since"])


class ToolDefinitionTests(unittest.TestCase):
    def test_tools_follow_the_registry_contract(self):
        self.assertEqual([tool["name"] for tool in history.TOOLS], ["undo", "redo", "get_undo_history"])
        for tool in history.TOOLS:
            self.assertIn("description", tool)
            self.assertEqual(tool["inputSchema"]["type"], "object")
            self.assertNotEqual("fn" in tool, "handler" in tool, tool["name"])
        by_name = {tool["name"]: tool for tool in history.TOOLS}
        self.assertIn("handler", by_name["undo"], "undo must poll the log off the GUI thread")
        self.assertIn("handler", by_name["redo"], "redo must poll the log off the GUI thread")
        self.assertFalse(by_name["get_undo_history"].get("gui", True))


if __name__ == "__main__":
    unittest.main()
