"""Unit tests for the batch tool (no Cascadeur needed).

batch.py imports common.py at module level, and common.py imports csc and numpy. Outside
Cascadeur those are missing, so these tests register a small stand-in for common (ok/fail)
while batch.py is loaded, then put sys.modules back. Every sub-call is replaced by a fake
_call_tool that returns MCP-style content results, so nothing reaches Cascadeur.

Run: python -m unittest discover -s tests -v
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGE = "cascadeur_mcp_plus"
TOOLS_PACKAGE = f"{PACKAGE}.tools"


def _package(name: str, path: str) -> types.ModuleType:
    module = sys.modules.get(name)
    if module is None:
        module = types.ModuleType(name)
        module.__path__ = [path]
        sys.modules[name] = module
    return module


def _load_real(name: str, filename: str):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _stub_common() -> types.ModuleType:
    """The ok/fail half of common.py, without its csc and numpy imports."""
    module = types.ModuleType(f"{PACKAGE}.common")

    def fail(error, hint=None, **data):
        result = {"ok": False, "error": str(error)}
        if hint:
            result["hint"] = str(hint)
        result.update(data)
        return result

    module.fail = fail
    module.ok = lambda **data: {"ok": True, **data}
    return module


def _load_batch():
    _package(PACKAGE, os.path.join(ROOT, PACKAGE))
    _package(TOOLS_PACKAGE, os.path.join(ROOT, PACKAGE, "tools"))
    _load_real(f"{TOOLS_PACKAGE}._schema", os.path.join(ROOT, PACKAGE, "tools", "_schema.py"))
    common_name = f"{PACKAGE}.common"
    saved = sys.modules.get(common_name)
    sys.modules[common_name] = _stub_common()
    try:
        return _load_real(f"{TOOLS_PACKAGE}.batch", os.path.join(ROOT, PACKAGE, "tools", "batch.py"))
    finally:
        if saved is None:
            sys.modules.pop(common_name, None)
        else:
            sys.modules[common_name] = saved


batch_tool = _load_batch()


def _result(value, *, is_error: bool = False) -> dict:
    return {"content": [{"type": "text", "text": json.dumps(value)}], "isError": is_error}


class BatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.handler = None
        self._original = batch_tool._call_tool
        batch_tool._call_tool = self._fake

    def tearDown(self) -> None:
        batch_tool._call_tool = self._original

    def _fake(self, name: str, arguments: dict) -> dict:
        self.calls.append((name, arguments))
        if self.handler is None:
            return _result({"ok": True, "tool": name})
        return self.handler(name, arguments)

    # -- happy path ---------------------------------------------------------

    def test_runs_calls_in_order_and_returns_each_result(self):
        replies = {
            "get_scene_info": {"scene": {"name": "Cascy.casc"}, "frames": {"current": 0}},
            "run_python": {"ok": True, "value": "3"},
        }
        self.handler = lambda name, arguments: _result(replies[name])
        result = batch_tool.batch(
            [
                {"tool": "get_scene_info", "arguments": {}},
                {"tool": "run_python", "arguments": {"code": "1", "timeout": 30}},
                {"tool": "get_scene_info", "arguments": {"max_objects": 5}},
            ]
        )
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["count"], 3)
        self.assertEqual([item["index"] for item in result["results"]], [0, 1, 2])
        self.assertEqual(
            [item["tool"] for item in result["results"]],
            ["get_scene_info", "run_python", "get_scene_info"],
        )
        self.assertEqual([item["ok"] for item in result["results"]], [True, True, True])
        self.assertEqual(result["results"][0]["result"]["scene"]["name"], "Cascy.casc")
        self.assertEqual(result["results"][1]["result"]["value"], "3")
        self.assertEqual(
            self.calls,
            [
                ("get_scene_info", {}),
                ("run_python", {"code": "1", "timeout": 30}),
                ("get_scene_info", {"max_objects": 5}),
            ],
        )

    def test_missing_arguments_default_to_empty_object(self):
        result = batch_tool.batch([{"tool": "get_scene_info"}])
        self.assertTrue(result["ok"], result)
        self.assertEqual(self.calls, [("get_scene_info", {})])

    def test_empty_calls_returns_an_empty_batch(self):
        result = batch_tool.batch([])
        self.assertEqual(result, {"ok": True, "count": 0, "results": []})
        self.assertEqual(self.calls, [])

    def test_background_job_id_passes_through(self):
        self.handler = lambda name, arguments: _result({"ok": True, "job_id": "job-1"})
        result = batch_tool.batch([{"tool": "some_background_tool"}])
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["results"][0]["result"]["job_id"], "job-1")

    def test_image_result_keeps_count_and_metadata(self):
        self.handler = lambda name, arguments: {
            "content": [
                {"type": "image", "data": "AAAA", "mimeType": "image/jpeg"},
                {"type": "text", "text": json.dumps({"window_size": [10, 20]})},
            ]
        }
        result = batch_tool.batch([{"tool": "screenshot"}])
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["results"][0]["result"], {"images": 1, "meta": {"window_size": [10, 20]}})

    def test_plain_text_result_is_returned_as_text(self):
        self.handler = lambda name, arguments: {"content": [{"type": "text", "text": "plain words"}]}
        result = batch_tool.batch([{"tool": "odd_tool"}])
        self.assertEqual(result["results"][0]["result"], "plain words")

    # -- failures -----------------------------------------------------------

    def test_stop_on_error_true_stops_at_the_first_failure(self):
        def handler(name, arguments):
            if name == "broken":
                return _result({"ok": False, "error": "broken call", "hint": "fix it"}, is_error=True)
            return _result({"ok": True})

        self.handler = handler
        result = batch_tool.batch([{"tool": "first"}, {"tool": "broken"}, {"tool": "last"}])
        self.assertFalse(result["ok"])
        self.assertEqual(result["stopped_at"], 1)
        self.assertEqual(len(result["results"]), 2)
        self.assertFalse(result["results"][1]["ok"])
        self.assertEqual(result["results"][1]["result"]["error"], "broken call")
        self.assertEqual(result["results"][1]["result"]["hint"], "fix it")
        self.assertIn("call 1", result["error"])
        self.assertIn("broken", result["error"])
        self.assertIn("results[1]", result["hint"])
        self.assertEqual([name for name, _ in self.calls], ["first", "broken"])

    def test_stop_on_error_false_runs_the_remaining_calls(self):
        def handler(name, arguments):
            if name == "broken":
                return _result({"ok": False, "error": "broken call"}, is_error=True)
            return _result({"ok": True})

        self.handler = handler
        result = batch_tool.batch([{"tool": "first"}, {"tool": "broken"}, {"tool": "last"}], stop_on_error=False)
        self.assertFalse(result["ok"])
        self.assertEqual(result["failed"], [1])
        self.assertEqual(len(result["results"]), 3)
        self.assertEqual([item["ok"] for item in result["results"]], [True, False, True])
        self.assertIn("1 of 3", result["error"])
        self.assertEqual([name for name, _ in self.calls], ["first", "broken", "last"])

    def test_tool_error_text_is_wrapped(self):
        self.handler = lambda name, arguments: {
            "content": [{"type": "text", "text": "Bad argument: nope"}],
            "isError": True,
        }
        result = batch_tool.batch([{"tool": "bad_tool"}])
        self.assertFalse(result["ok"])
        self.assertEqual(result["results"][0]["result"], {"error": "Bad argument: nope"})
        self.assertIn("Bad argument: nope", result["error"])

    def test_unknown_tool_is_a_failed_call(self):
        def handler(name, arguments):
            if name == "nope":
                raise KeyError(name)
            return _result({"ok": True})

        self.handler = handler
        result = batch_tool.batch([{"tool": "nope"}, {"tool": "ok"}], stop_on_error=False)
        self.assertFalse(result["ok"])
        self.assertEqual(result["failed"], [0])
        self.assertIn("Unknown tool: nope", result["results"][0]["result"]["error"])
        self.assertIn("tools/list", result["results"][0]["result"]["hint"])
        self.assertTrue(result["results"][1]["ok"])

    def test_unexpected_exception_becomes_a_failed_call(self):
        def handler(name, arguments):
            raise RuntimeError("kaboom")

        self.handler = handler
        result = batch_tool.batch([{"tool": "explodes"}])
        self.assertFalse(result["ok"])
        self.assertIn("RuntimeError: kaboom", result["results"][0]["result"]["error"])
        self.assertIn("RuntimeError: kaboom", result["error"])

    # -- validation ---------------------------------------------------------

    def test_nested_batch_is_rejected_before_any_call_runs(self):
        result = batch_tool.batch([{"tool": "get_scene_info"}, {"tool": "batch", "arguments": {"calls": []}}])
        self.assertFalse(result["ok"])
        self.assertIn("calls[1]", result["error"])
        self.assertIn("nested", result["error"])
        self.assertNotIn("results", result)
        self.assertEqual(self.calls, [])

    def test_bad_shapes_are_rejected_without_running(self):
        cases = [
            ("nope", "must be a list"),
            ([1], "calls[0] must be an object"),
            ([{}], "needs a non-empty string 'tool'"),
            ([{"tool": ""}], "needs a non-empty string 'tool'"),
            ([{"tool": 3}], "needs a non-empty string 'tool'"),
            ([{"tool": "get_scene_info", "arguments": []}], "arguments must be an object"),
        ]
        for calls, expected in cases:
            with self.subTest(calls=calls):
                result = batch_tool.batch(calls)
                self.assertFalse(result["ok"], result)
                self.assertIn(expected, result["error"])
        self.assertEqual(self.calls, [])

    def test_stop_on_error_must_be_a_bool(self):
        result = batch_tool.batch([{"tool": "get_scene_info"}], stop_on_error="false")
        self.assertFalse(result["ok"])
        self.assertIn("stop_on_error", result["error"])
        self.assertEqual(self.calls, [])

    # -- registry -----------------------------------------------------------

    def test_registry(self):
        self.assertEqual([tool["name"] for tool in batch_tool.TOOLS], ["batch"])
        tool = batch_tool.TOOLS[0]
        self.assertIs(tool["handler"], batch_tool.batch)
        self.assertNotIn("fn", tool)
        self.assertIn("undo step", tool["description"])
        self.assertIn("Nested batch calls are rejected", tool["description"])
        schema = tool["inputSchema"]
        self.assertEqual(schema["required"], ["calls"])
        self.assertEqual(schema["properties"]["calls"]["type"], "array")
        self.assertEqual(schema["properties"]["calls"]["items"]["required"], ["tool"])
        self.assertEqual(schema["properties"]["stop_on_error"]["type"], "boolean")


if __name__ == "__main__":
    unittest.main()
