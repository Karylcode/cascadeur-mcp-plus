"""Unit tests for server tool dispatch (no Cascadeur needed).

server.py normally imports PySide6, the tool registry, the resource registry and the live
dispatcher. These tests load the real server.py behind small stand-ins for those modules.
They cover the explicit tool-name check in _handle (a KeyError raised inside a tool must not
be reported as an unknown tool), server.call_tool's GUI-thread guard, and has_tool.

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
SERVER_NAME = f"{PACKAGE}.server"

CALLS: list[tuple[str, dict]] = []


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


def _probe(value=0):
    CALLS.append(("probe", {"value": value}))
    return {"ok": True, "value": value}


def _explode():
    CALLS.append(("explode", {}))
    raise KeyError("missing-key")


def _load_tools():
    return [
        {"name": "probe", "description": "probe tool", "inputSchema": {}, "handler": _probe},
        {"name": "explode", "description": "raises KeyError", "inputSchema": {}, "handler": _explode},
    ]


class FakeApplication:
    def __init__(self):
        self.main = object()

    def thread(self):
        return self.main


class FakeQtCore:
    """Stands in for PySide6.QtCore: tests choose the current thread."""

    def __init__(self):
        self.app = None
        self.current = None

    def instance(self):
        return self.app

    def current_thread(self):
        return self.current

    def module(self) -> types.ModuleType:
        module = types.ModuleType("PySide6.QtCore")
        module.QCoreApplication = types.SimpleNamespace(instance=self.instance)
        module.QThread = types.SimpleNamespace(currentThread=self.current_thread)
        return module


def _no_gui(*args, **kwargs):
    raise AssertionError("the fake dispatcher must not run a GUI job in these tests")


def _load_server():
    _package(PACKAGE, os.path.join(ROOT, PACKAGE))
    _package(TOOLS_PACKAGE, os.path.join(ROOT, PACKAGE, "tools"))
    _load_real(f"{PACKAGE}.errors", os.path.join(ROOT, PACKAGE, "errors.py"))

    qt = FakeQtCore()
    pyside = types.ModuleType("PySide6")
    pyside.QtCore = qt.module()

    dispatch = types.ModuleType(f"{PACKAGE}.dispatch")
    dispatch.STATE_KEY = "_test_state"
    dispatch.state = lambda: None
    dispatch.call_on_gui = _no_gui

    jobs = types.ModuleType(f"{PACKAGE}.jobs")
    jobs.start_tool_job = _no_gui
    jobs.shutdown = lambda: None

    resources = types.ModuleType(f"{PACKAGE}.resources")
    resources.load_resources = lambda: []

    tools = types.ModuleType(TOOLS_PACKAGE)
    tools.load_tools = _load_tools

    version = types.ModuleType(f"{PACKAGE}.version")
    version.DEFAULT_PORT = 48766
    version.VERSION = "test"

    fakes = {
        "PySide6": pyside,
        "PySide6.QtCore": pyside.QtCore,
        f"{PACKAGE}.dispatch": dispatch,
        f"{PACKAGE}.jobs": jobs,
        f"{PACKAGE}.resources": resources,
        TOOLS_PACKAGE: tools,
        f"{PACKAGE}.version": version,
    }
    saved = {name: sys.modules.get(name) for name in fakes}
    saved_server = sys.modules.pop(SERVER_NAME, None)
    sys.modules.update(fakes)
    try:
        spec = importlib.util.spec_from_file_location(SERVER_NAME, os.path.join(ROOT, PACKAGE, "server.py"))
        server = importlib.util.module_from_spec(spec)
        sys.modules[SERVER_NAME] = server
        spec.loader.exec_module(server)
    finally:
        for name, module in saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
        if saved_server is None:
            sys.modules.pop(SERVER_NAME, None)
        else:
            sys.modules[SERVER_NAME] = saved_server
    return server, qt


server, qt = _load_server()


class ServerToolDispatchTests(unittest.TestCase):
    def setUp(self) -> None:
        CALLS.clear()
        qt.app = None
        qt.current = None

    def _call(self, tool: str, arguments: dict | None = None) -> dict:
        return server._handle(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": tool, "arguments": arguments or {}},
            }
        )

    def test_unknown_tool_is_a_jsonrpc_error(self):
        response = self._call("nope")
        self.assertIn("error", response)
        self.assertEqual(response["error"]["code"], -32602)
        self.assertIn("Unknown tool: nope", response["error"]["message"])
        self.assertEqual(CALLS, [])

    def test_tool_keyerror_is_a_tool_failure_not_an_unknown_tool(self):
        response = self._call("explode")
        self.assertNotIn("error", response)
        result = response["result"]
        self.assertTrue(result["isError"])
        text = result["content"][0]["text"]
        self.assertIn("KeyError: 'missing-key'", text)
        self.assertNotIn("Unknown tool", text)
        self.assertEqual(CALLS, [("explode", {})])

    def test_registered_tool_runs(self):
        response = self._call("probe", {"value": 7})
        result = response["result"]
        self.assertFalse(result["isError"])
        self.assertEqual(json.loads(result["content"][0]["text"]), {"ok": True, "value": 7})
        self.assertEqual(CALLS, [("probe", {"value": 7})])

    def test_call_tool_refuses_the_gui_thread(self):
        app = FakeApplication()
        qt.app = app
        qt.current = app.thread()
        with self.assertRaises(RuntimeError) as caught:
            server.call_tool("probe", {"value": 1})
        message = str(caught.exception)
        self.assertIn("GUI thread", message)
        self.assertIn("HTTP thread", message)
        self.assertEqual(CALLS, [])

    def test_call_tool_runs_off_the_gui_thread(self):
        app = FakeApplication()
        qt.app = app
        qt.current = object()
        raw = server.call_tool("probe", {"value": 4})
        self.assertEqual(json.loads(raw["content"][0]["text"]), {"ok": True, "value": 4})
        self.assertEqual(CALLS, [("probe", {"value": 4})])

    def test_call_tool_without_an_application_runs(self):
        raw = server.call_tool("probe", {"value": 2})
        self.assertEqual(json.loads(raw["content"][0]["text"]), {"ok": True, "value": 2})
        self.assertEqual(CALLS, [("probe", {"value": 2})])

    def test_has_tool(self):
        self.assertTrue(server.has_tool("probe"))
        self.assertFalse(server.has_tool("nope"))


if __name__ == "__main__":
    unittest.main()
