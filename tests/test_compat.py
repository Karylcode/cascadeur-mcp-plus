"""Unit tests for the version compatibility check (T54).

compat.py is standard library only, so these tests load it without Cascadeur, PySide6 or csc:
the package's __init__ (which imports server.py) is skipped by registering a bare package stub.
Cascadeur-facing functions (csc attributes, the QML tree, the installed strings) are replaced
with fakes. The live behaviour (2026.2.3 reports nothing unavailable; a monkeypatched API is
refused) is covered by tests/live/test_compat.py.

Also checks statically, with ast and without importing the tool modules, that every tool declares
a valid ``requires`` list (the registry enforces the same at load time).

Run: python -m unittest discover -s tests -v
"""

from __future__ import annotations

import ast
import importlib
import os
import pkgutil
import sys
import tempfile
import types
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGE = "cascadeur_mcp_plus"
PACKAGE_DIR = os.path.join(ROOT, PACKAGE)
TOOLS_DIR = os.path.join(PACKAGE_DIR, "tools")


def _package(name: str, path: str) -> types.ModuleType:
    module = sys.modules.get(name)
    if module is None:
        module = types.ModuleType(name)
        module.__path__ = [path]
        sys.modules[name] = module
    return module


def _real_tools_package() -> types.ModuleType:
    """The cascadeur_mcp_plus.tools package with load_tools, run from its __init__.py.

    test_server.py leaves a bare package stub under this name when it loads server.py with
    stand-ins, so the real package is executed again when the stub has no load_tools.
    """
    module = sys.modules.get(f"{PACKAGE}.tools")
    if module is not None and hasattr(module, "load_tools"):
        return module
    module = types.ModuleType(f"{PACKAGE}.tools")
    module.__path__ = [TOOLS_DIR]
    module.__package__ = f"{PACKAGE}.tools"
    module.__file__ = os.path.join(TOOLS_DIR, "__init__.py")
    sys.modules[f"{PACKAGE}.tools"] = module
    with open(module.__file__, encoding="utf-8") as handle:
        exec(compile(handle.read(), module.__file__, "exec"), module.__dict__)
    return module


_package(PACKAGE, PACKAGE_DIR)
compat = importlib.import_module(f"{PACKAGE}.compat")
_real_tools_package()
status_tool = importlib.import_module(f"{PACKAGE}.tools.status")


class _SavedReport:
    """Keeps compat's module-level report out of the other tests."""

    def setUp(self):
        self._saved = compat._report
        compat._report = None

    def tearDown(self):
        compat._report = self._saved


class ValidateRequiresTests(unittest.TestCase):
    def test_accepts_api_action_and_module_entries(self):
        entries = [
            "csc.Guid",
            "csc.tools.RenderToFile.take_image",
            "action:AutoPhysicsTool.Snap to Auto Physics",
            "module:rig_mode.on",
            "module:rig_mode.on:run_raw",
        ]
        self.assertEqual(compat.validate_requires(entries), entries)
        self.assertEqual(compat.validate_requires([]), [])

    def test_strips_surrounding_spaces(self):
        self.assertEqual(compat.validate_requires(["  csc.glb.process_import "]), ["csc.glb.process_import"])

    def test_rejects_malformed_entries(self):
        bad = [
            "csc",  # no name after the root
            "csc..x",
            "glb.process_import",  # not rooted at csc
            "action:",
            "action:   ",
            "module:",
            "module:rig mode.on",
            "module:rig_mode.on:",
            "module:rig_mode.on:run:extra",
            "module:rig_mode.on:not a name",
            "",
            "   ",
            42,
        ]
        for entry in bad:
            with self.subTest(entry=entry):
                with self.assertRaises(ValueError):
                    compat.validate_requires([entry])

    def test_rejects_non_list_values(self):
        for value in ("csc.Guid", {"csc.Guid": 1}, None):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    compat.validate_requires(value)

    def test_split_separates_api_paths_and_action_keys_and_skips_modules(self):
        apis, actions = compat._split(["csc.Guid", "action:View.Surface", "module:rig_mode.on:run_raw"])
        self.assertEqual(apis, ["csc.Guid"])
        self.assertEqual(actions, ["View.Surface"])


class _FakeCsc:
    def __init__(self):
        self.glb = types.SimpleNamespace(process_import=lambda: None)
        self.Guid = type("Guid", (), {})


class ApiExistsTests(unittest.TestCase):
    def test_walks_attributes_of_the_module(self):
        fake = _FakeCsc()
        self.assertTrue(compat.api_exists("csc.glb.process_import", module=fake))
        self.assertTrue(compat.api_exists("csc.Guid", module=fake))
        self.assertFalse(compat.api_exists("csc.glb.process_export", module=fake))
        self.assertFalse(compat.api_exists("csc.missing.thing", module=fake))

    def test_only_csc_paths_count(self):
        self.assertFalse(compat.api_exists("os.path", module=_FakeCsc()))


class ScriptModuleTests(unittest.TestCase):
    """module: entries are looked up on disk, so a fake official script package is written to a temp dir."""

    PACKAGE_NAME = "t54_fake_scripts_pkg"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = os.path.join(self._tmp.name, self.PACKAGE_NAME)
        os.makedirs(os.path.join(root, "sub"))
        with open(os.path.join(root, "__init__.py"), "w", encoding="utf-8") as handle:
            handle.write("raise RuntimeError('must not be imported')\n")
        with open(os.path.join(root, "on.py"), "w", encoding="utf-8") as handle:
            handle.write("def run_raw(scene):\n    return scene\n\ndef helper():\n    pass\n")
        with open(os.path.join(root, "sub", "__init__.py"), "w", encoding="utf-8") as handle:
            handle.write("")
        with open(os.path.join(root, "sub", "leaf.py"), "w", encoding="utf-8") as handle:
            handle.write("def run(scene):\n    return scene\n")
        self._saved_path = list(sys.path)
        sys.path.insert(0, self._tmp.name)

    def tearDown(self):
        sys.path[:] = self._saved_path
        sys.modules.pop(self.PACKAGE_NAME, None)
        self._tmp.cleanup()

    def test_finds_modules_and_functions_without_importing(self):
        name = self.PACKAGE_NAME
        self.assertTrue(compat.script_module_provides(f"module:{name}.on"))
        self.assertTrue(compat.script_module_provides(f"module:{name}.on:run_raw"))
        self.assertTrue(compat.script_module_provides(f"module:{name}.on:helper"))
        self.assertTrue(compat.script_module_provides(f"module:{name}.sub.leaf:run"))
        self.assertTrue(compat.script_module_provides(f"module:{name}"))
        self.assertFalse(compat.script_module_provides(f"module:{name}.on:renamed_away"))
        self.assertFalse(compat.script_module_provides(f"module:{name}.absent"))
        self.assertFalse(compat.script_module_provides(f"module:{name}.on.deeper"))
        self.assertFalse(compat.script_module_provides("module:t54_no_such_package.on"))
        self.assertNotIn(name, sys.modules, "the package __init__ must not run")


class AuditTests(_SavedReport, unittest.TestCase):
    def _run(self, tools, *, apis=(), interface=None, build=(), scanned=("build.dll",), modules=(), complete=True):
        known_apis = set(apis)
        known_modules = set(modules)

        def fake_api(path, module=None):
            return path in known_apis

        def fake_search(keys):
            found = {key for key in keys if key in build}
            return found, (list(scanned) if scanned else []), complete

        with (
            mock.patch.object(compat, "api_exists", side_effect=fake_api),
            mock.patch.object(compat, "interface_actions", return_value=interface),
            mock.patch.object(compat, "_search_build", side_effect=fake_search),
            mock.patch.object(compat, "script_module_provides", side_effect=lambda entry: entry in known_modules),
        ):
            return compat.audit(tools)

    def test_reports_missing_api_paths_per_tool(self):
        tools = [
            {"name": "good", "requires": ["csc.Guid"]},
            {"name": "bad", "requires": ["csc.Guid", "csc.tools.Gone.method"]},
            {"name": "none", "requires": []},
        ]
        report = self._run(tools, apis={"csc.Guid"})
        self.assertEqual(report["unavailable"], {"bad": ["csc.tools.Gone.method"]})
        self.assertEqual(report["tools"], 3)
        self.assertEqual(report["requirements"], 3)

    def test_actions_verified_by_the_interface_or_the_build_strings(self):
        tools = [{"name": "a", "requires": ["action:View.Surface", "action:Scene.Undo"]}]
        report = self._run(tools, interface={"View.Surface"}, build={"Scene.Undo"})
        self.assertEqual(report["unavailable"], {})
        self.assertEqual(report["actions"]["verified_in_interface"], 1)
        self.assertEqual(report["actions"]["verified_in_installation"], ["Scene.Undo"])

    def test_action_found_nowhere_is_missing_when_the_build_was_searched(self):
        tools = [{"name": "a", "requires": ["action:Gone.Action"]}]
        report = self._run(tools, interface={"View.Surface"}, build=set())
        self.assertEqual(report["unavailable"], {"a": ["action:Gone.Action"]})

    def test_action_stays_unverified_without_any_search_source(self):
        tools = [{"name": "a", "requires": ["action:Maybe.Action"]}]
        report = self._run(tools, interface=None, build=set(), scanned=())
        self.assertEqual(report["unavailable"], {})
        self.assertEqual(report["actions"]["unverified"], ["Maybe.Action"])

    def test_action_not_found_in_an_incomplete_scan_is_unverified(self):
        # The byte budget cut the search short, so an absent key proves nothing.
        tools = [{"name": "a", "requires": ["action:Maybe.Elsewhere"]}]
        report = self._run(tools, interface=set(), build=set(), scanned=("tools.dll",), complete=False)
        self.assertEqual(report["unavailable"], {})
        self.assertEqual(report["actions"]["unverified"], ["Maybe.Elsewhere"])
        self.assertFalse(report["actions"]["scan_complete"])

    def test_module_entries_are_checked_on_disk(self):
        tools = [{"name": "enter", "requires": ["module:rig_mode.on:run_raw", "module:rig_mode.on:gone"]}]
        report = self._run(tools, modules={"module:rig_mode.on:run_raw"})
        self.assertEqual(report["unavailable"], {"enter": ["module:rig_mode.on:gone"]})


class BuildSearchTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.small = os.path.join(self._tmp.name, "small.bin")
        self.big = os.path.join(self._tmp.name, "big.bin")
        with open(self.small, "wb") as handle:
            handle.write("xx\x00Alpha.Key\x00".encode("utf-16-le") + b"tail")
        with open(self.big, "wb") as handle:
            handle.write(b"\x00" * 1000 + b"Beta.Key")

    def tearDown(self):
        self._tmp.cleanup()

    def test_found_keys_come_from_utf16_or_utf8_strings(self):
        with mock.patch.object(compat, "_candidate_files", return_value=[self.small]):
            found, scanned, complete = compat._search_build(["Alpha.Key", "Gone.Key"])
        self.assertEqual(found, {"Alpha.Key"})
        self.assertEqual(scanned, [self.small])
        self.assertTrue(complete)

    def test_a_file_over_the_budget_is_skipped_and_marks_the_search_incomplete(self):
        with (
            mock.patch.object(compat, "_candidate_files", return_value=[self.big, self.small]),
            mock.patch.object(compat, "MAX_SCAN_BYTES", 100),
        ):
            found, scanned, complete = compat._search_build(["Beta.Key", "Alpha.Key"])
        self.assertEqual(found, {"Alpha.Key"})
        self.assertEqual(scanned, [self.small])
        self.assertFalse(complete)

    def test_a_complete_search_stops_early_and_is_complete(self):
        with mock.patch.object(compat, "_candidate_files", return_value=[self.small, self.big]):
            found, scanned, complete = compat._search_build(["Alpha.Key"])
        self.assertEqual(scanned, [self.small])
        self.assertTrue(complete)

    def test_tool_libraries_come_after_the_program_smallest_first(self):
        root = os.path.join(self._tmp.name, "install")
        os.makedirs(os.path.join(root, "resources", "translations"))
        for name, size in (("app_ja.qm", 4), ("tools.dll", 4), ("tool_big.dll", 300), ("tool_small.dll", 8)):
            folder = os.path.join(root, "resources", "translations") if name.endswith(".qm") else root
            with open(os.path.join(folder, name), "wb") as handle:
                handle.write(b"\x00" * size)
        with mock.patch("cascadeur_mcp_plus.paths.install_dirs", return_value=[root]):
            ordered = [os.path.basename(path) for path in compat._candidate_files()]
        self.assertLess(ordered.index("app_ja.qm"), ordered.index("tools.dll"))
        self.assertLess(ordered.index("tools.dll"), ordered.index("tool_small.dll"))
        self.assertLess(ordered.index("tool_small.dll"), ordered.index("tool_big.dll"))


class ReportTextTests(_SavedReport, unittest.TestCase):
    def _report(self, **overrides):
        report = {
            "cascadeur_version": "2026.2.3",
            "version_matches_tested": True,
            "unavailable": {},
            "tools": 1,
            "requirements": 1,
            "checked_at": "2026-10-24T00:00:00+0000",
            "actions": {},
        }
        report.update(overrides)
        return report

    def test_describe_prefixes_unavailable_tools_only(self):
        tool = {"name": "t", "description": "Do the thing.", "requires": ["csc.Gone"]}
        self.assertEqual(compat.describe(tool), "Do the thing.")
        compat._report = self._report(unavailable={"t": ["csc.Gone"]})
        text = compat.describe(tool)
        self.assertTrue(text.startswith(compat.UNAVAILABLE_PREFIX), text)
        self.assertIn("csc.Gone", text)
        self.assertTrue(text.endswith("Do the thing."))

    def test_refusal_names_the_missing_entries(self):
        result = compat.refusal("t", ["csc.Gone", "module:rig_mode.on:run_raw"])
        self.assertFalse(result["ok"])
        self.assertIn(compat.UNAVAILABLE_PREFIX, result["error"])
        self.assertIn("csc.Gone", result["error"])
        self.assertEqual(result["missing"], ["csc.Gone", "module:rig_mode.on:run_raw"])
        self.assertIn("get_status", result["hint"])

    def test_instructions_gain_a_reminder_only_on_another_version(self):
        base = "Inspect the UI."
        self.assertEqual(compat.instructions(base), base)
        compat._report = self._report()
        self.assertEqual(compat.instructions(base), base)
        compat._report = self._report(cascadeur_version="2026.3.0", version_matches_tested=False)
        text = compat.instructions(base)
        self.assertTrue(text.startswith(base))
        self.assertIn("2026.3.0", text)
        self.assertIn(compat.TESTED_CASCADEUR_VERSION, text)

    def test_version_note_mentions_the_tested_version_and_unknown_versions(self):
        self.assertIn("could not be read", compat.version_note())
        compat._report = self._report()
        self.assertIn("tested with", compat.version_note())
        compat._report = self._report(cascadeur_version="2027.1.0", version_matches_tested=False)
        self.assertIn("not the tested version", compat.version_note())

    def test_status_payload_before_and_after_an_audit(self):
        empty = compat.status()
        self.assertTrue(empty["ok"])
        self.assertIsNone(empty["cascadeur_version"])
        self.assertEqual(empty["unavailable"], [])
        self.assertEqual(empty["tested_cascadeur_version"], compat.TESTED_CASCADEUR_VERSION)
        self.assertTrue(empty["version_dependent"])
        compat._report = self._report(unavailable={"t": ["csc.Gone"]})
        payload = compat.status()
        self.assertEqual(payload["unavailable_count"], 1)
        self.assertEqual(payload["unavailable"][0]["tool"], "t")
        self.assertEqual(payload["unavailable"][0]["missing"], ["csc.Gone"])


class RefreshTests(_SavedReport, unittest.TestCase):
    def test_refresh_records_version_and_match_with_the_tested_build(self):
        with (
            mock.patch.object(compat, "api_exists", return_value=True),
            mock.patch.object(compat, "interface_actions", return_value=set()),
            mock.patch.object(compat, "_search_build", return_value=(set(), [], True)),
        ):
            report = compat.refresh([{"name": "t", "requires": ["csc.Guid"]}], version=compat.TESTED_CASCADEUR_VERSION)
        self.assertTrue(report["version_matches_tested"])
        self.assertIs(compat.report(), report)
        self.assertEqual(compat.unavailable("t"), [])

    def test_unavailable_reads_the_cached_report(self):
        with (
            mock.patch.object(compat, "api_exists", return_value=False),
            mock.patch.object(compat, "interface_actions", return_value=set()),
            mock.patch.object(compat, "_search_build", return_value=(set(), [], True)),
        ):
            compat.refresh([{"name": "t", "requires": ["csc.Gone"]}], version="2026.3.0")
        self.assertEqual(compat.unavailable("t"), ["csc.Gone"])
        self.assertFalse(compat.report()["version_matches_tested"])


class GetStatusToolTests(_SavedReport, unittest.TestCase):
    def test_returns_the_cached_report_without_auditing_again(self):
        compat._report = {"cascadeur_version": "2026.2.3", "version_matches_tested": True, "unavailable": {}}
        with mock.patch.object(compat, "refresh", side_effect=AssertionError("no audit expected")):
            result = status_tool.get_status()
        self.assertTrue(result["ok"])
        self.assertEqual(result["cascadeur_version"], "2026.2.3")

    def test_force_runs_the_audit_again(self):
        compat._report = {"cascadeur_version": "2026.2.3", "version_matches_tested": True, "unavailable": {}}
        calls = []
        with mock.patch.object(compat, "refresh", side_effect=lambda *a, **k: calls.append(k) or {}):
            status_tool.get_status(force=True)
        self.assertEqual(len(calls), 1)

    def test_a_failing_audit_is_reported_not_raised(self):
        compat._report = None
        with mock.patch.object(compat, "refresh", side_effect=RuntimeError("boom")):
            result = status_tool.get_status()
        self.assertFalse(result["ok"])
        self.assertIn("boom", result["error"])


class RegistryRequiresTests(unittest.TestCase):
    """Every tool dict in cascadeur_mcp_plus/tools declares requires, checked statically."""

    def _tool_dicts(self):
        for filename in sorted(os.listdir(TOOLS_DIR)):
            if not filename.endswith(".py") or filename.startswith("_"):
                continue
            with open(os.path.join(TOOLS_DIR, filename), encoding="utf-8") as handle:
                tree = ast.parse(handle.read(), filename=filename)
            for node in tree.body:
                if not (isinstance(node, ast.Assign) and any(getattr(t, "id", None) == "TOOLS" for t in node.targets)):
                    continue
                self.assertIsInstance(node.value, ast.List, filename)
                for entry in node.value.elts:
                    self.assertIsInstance(entry, ast.Dict, filename)
                    yield filename, entry

    def test_every_tool_declares_valid_requires(self):
        seen = 0
        for filename, entry in self._tool_dicts():
            values = {
                key.value: value
                for key, value in zip(entry.keys, entry.values, strict=True)
                if isinstance(key, ast.Constant)
            }
            name = values.get("name")
            label = f"{filename}:{getattr(name, 'value', '?')}"
            self.assertIn("requires", values, f"{label} has no requires")
            requires = values["requires"]
            self.assertIsInstance(requires, ast.List, label)
            entries = [item.value for item in requires.elts if isinstance(item, ast.Constant)]
            self.assertEqual(len(entries), len(requires.elts), f"{label}: requires entries must be string literals")
            compat.validate_requires(entries)
            seen += 1
        self.assertGreater(seen, 100)

    def test_load_tools_rejects_a_tool_without_requires(self):
        tools_pkg = _real_tools_package()
        fake = types.ModuleType("fake_tool_module")
        fake.TOOLS = [{"name": "bare", "description": "x", "inputSchema": {}, "fn": lambda: None}]
        with (
            mock.patch.object(pkgutil, "iter_modules", return_value=[types.SimpleNamespace(name="fake")]),
            mock.patch.object(importlib, "import_module", return_value=fake),
        ):
            with self.assertRaises(RuntimeError) as raised:
                tools_pkg.load_tools()
        self.assertIn("no requires", str(raised.exception))

    def test_load_tools_rejects_a_malformed_requires(self):
        tools_pkg = _real_tools_package()
        fake = types.ModuleType("fake_tool_module")
        fake.TOOLS = [{"name": "bad", "description": "x", "inputSchema": {}, "fn": lambda: None, "requires": ["Guid"]}]
        with (
            mock.patch.object(pkgutil, "iter_modules", return_value=[types.SimpleNamespace(name="fake")]),
            mock.patch.object(importlib, "import_module", return_value=fake),
        ):
            with self.assertRaises(RuntimeError) as raised:
                tools_pkg.load_tools()
        self.assertIn("bad requires field", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
