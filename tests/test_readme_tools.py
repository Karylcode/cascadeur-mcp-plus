"""Unit tests for tools/check_readme_tools.py: the README tool tables against the registry (T56).

The registry is read in a fresh interpreter with Cascadeur's packages stubbed out, so these tests
need neither Cascadeur nor a running MCP Plus. The live tools/list check is the script's own job.
"""

from __future__ import annotations

import importlib.util
import os
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "tools", "check_readme_tools.py")


def _load_checker():
    spec = importlib.util.spec_from_file_location("check_readme_tools", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ReadmeToolTablesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.checker = _load_checker()
        cls.registry = cls.checker.registry_names(ROOT)

    def test_registry_names_are_unique_and_not_empty(self):
        self.assertTrue(self.registry)
        self.assertEqual(len(self.registry), len(set(self.registry)))

    def test_each_readme_lists_every_tool_exactly_once(self):
        for name in self.checker.READMES:
            found = self.checker.readme_names(os.path.join(ROOT, name))
            self.assertEqual(self.checker.differences(self.registry, found, name), [], name)

    def test_both_readmes_list_the_same_tools_in_the_same_order(self):
        english, chinese = (self.checker.readme_names(os.path.join(ROOT, name)) for name in self.checker.READMES)
        self.assertEqual(english, chinese)

    def test_differences_report_duplicates_missing_and_extra_names(self):
        problems = self.checker.differences(["a", "b"], ["a", "a", "c"], "label")
        self.assertIn("label: a appears more than once", problems)
        self.assertIn("label: missing b", problems)
        self.assertIn("label: not a tool of the server: c", problems)
        self.assertEqual(self.checker.differences(["a"], ["a"], "label"), [])

    def test_only_rows_in_the_tools_section_count(self):
        text = (
            "# Title\n\n## Tools\n\n### Scene\n\n| Tool | What |\n|---|---|\n| `alpha` | one |\n\n"
            "## Configuration\n\n| Setting | Value |\n|---|---|\n| `beta` | not a tool |\n"
        )
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "README.md")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(text)
            self.assertEqual(self.checker.readme_names(path), ["alpha"])


if __name__ == "__main__":
    unittest.main()
