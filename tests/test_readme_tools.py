"""Unit tests for tools/check_readme_tools.py: the tool reference pages against the registry.

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


class ToolPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.checker = _load_checker()
        cls.registry = cls.checker.registry_names(ROOT)

    def test_registry_names_are_unique_and_not_empty(self):
        self.assertTrue(self.registry)
        self.assertEqual(len(self.registry), len(set(self.registry)))

    def test_each_page_lists_every_tool_exactly_once(self):
        for page in self.checker.TOOL_PAGES:
            found = self.checker.page_names(os.path.join(ROOT, page))
            self.assertEqual(self.checker.differences(self.registry, found, page), [], page)

    def test_both_pages_list_the_same_tools_in_the_same_order(self):
        english, chinese = (self.checker.page_names(os.path.join(ROOT, page)) for page in self.checker.TOOL_PAGES)
        self.assertEqual(english, chinese)

    def test_differences_report_duplicates_missing_and_extra_names(self):
        problems = self.checker.differences(["a", "b"], ["a", "a", "c"], "label")
        self.assertIn("label: a appears more than once", problems)
        self.assertIn("label: missing b", problems)
        self.assertIn("label: not a tool of the server: c", problems)
        self.assertEqual(self.checker.differences(["a"], ["a"], "label"), [])

    def test_only_tool_headings_outside_code_blocks_count(self):
        text = (
            "# Tools\n\n## Scene and files\n\n### `alpha`\n\nText.\n\n### Scene\n\n"
            "```text\n### `gamma`\n```\n\n### `beta`\n\n## Common parameters\n\n| Parameter | Meaning |\n"
        )
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "tools.md")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(text)
            self.assertEqual(self.checker.page_names(path), ["alpha", "beta"])


if __name__ == "__main__":
    unittest.main()
