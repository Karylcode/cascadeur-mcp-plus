"""Unit tests for the tool descriptions and parameter docs (T42).

The server exposes TOOLS from every module in cascadeur_mcp_plus/tools/; this test reads those
module sources with ast (no Cascadeur, PySide6 or numpy needed) and checks the text an agent sees
in tools/list:

* every tool has a unique snake_case name and a non-empty English description;
* the first sentence starts with an imperative verb (or with "Last resort:" for interface tools);
* the description says when to use the tool and roughly what it returns;
* no description leaks internal ticket numbers;
* the interface tools (ui_*, screenshot, project_point) are marked "Last resort:" and point at the
  dedicated tool an agent should use instead;
* every top-level inputSchema property has a non-empty description.

The checks are deliberately about wording and metadata, not behaviour; live tests cover behaviour.
"""

from __future__ import annotations

import ast
import os
import re
import unittest
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS_DIR = os.path.join(ROOT, "cascadeur_mcp_plus", "tools")
SCHEMA_PATH = os.path.join(TOOLS_DIR, "_schema.py")

# Interface tools are kept for troubleshooting, but an agent should reach for a dedicated tool.
LAST_RESORT = {
    "ui_tree": ("get_scene_info", "list_controllers"),
    "ui_click": ("set_pose", "select_objects", "call_action"),
    "ui_drag": ("set_pose",),
    "ui_hover": ("get_scene_info", "list_controllers"),
    "ui_key": ("set_frame", "call_action", "undo"),
    "ui_type": ("call_action",),
    "ui_set_property": ("set_view_mode", "set_ghosts"),
    "ui_invoke": ("call_action",),
    "screenshot": ("render_image", "render_contact_sheet"),
    "project_point": ("set_pose", "select_objects"),
}

# Base forms used by the descriptions. Keep this list descriptive, not a grammar checker: the
# point is to reject noun-first summaries such as "Tool for posing", not to grade vocabulary.
FIRST_WORDS = {
    "add",
    "apply",
    "bake",
    "capture",
    "change",
    "clear",
    "click",
    "close",
    "copy",
    "create",
    "delete",
    "describe",
    "deselect",
    "drag",
    "enter",
    "export",
    "fill",
    "freeze",
    "generate",
    "get",
    "hover",
    "import",
    "invoke",
    "list",
    "lock",
    "make",
    "mark",
    "mirror",
    "move",
    "open",
    "paste",
    "prepare",
    "press",
    "project",
    "push",
    "put",
    "read",
    "recompute",
    "redo",
    "remove",
    "rename",
    "render",
    "report",
    "reset",
    "retarget",
    "return",
    "run",
    "save",
    "search",
    "select",
    "set",
    "show",
    "start",
    "stop",
    "summarize",
    "switch",
    "trigger",
    "turn",
    "type",
    "undo",
}

NON_ASCII = re.compile(r"[^\x09\x0a\x0d\x20-\x7e]")
TICKET_NUMBER = re.compile(r"\bT\d{2}\b")
WHEN_WORDS = re.compile(r"\b(use|when|before|after|instead)\b", re.IGNORECASE)
RETURN_WORDS = re.compile(r"\b(returns?|reports?|lists?|reads?|describes?|shows?|gets?)\b", re.IGNORECASE)


def _assignments(tree):
    found = {}
    for statement in tree.body:
        targets = []
        value = None
        if isinstance(statement, ast.Assign):
            targets = statement.targets
            value = statement.value
        elif isinstance(statement, ast.AnnAssign) and statement.value is not None:
            targets = [statement.target]
            value = statement.value
        for target in targets:
            if isinstance(target, ast.Name):
                found[target.id] = value
    return found


def _schema_constants():
    """The shared property dicts from _schema.py, keyed by their module-level name."""
    tree = ast.parse(Path(SCHEMA_PATH).read_text(encoding="utf-8"), filename=SCHEMA_PATH)
    constants = {}
    for name, value in _assignments(tree).items():
        if isinstance(value, ast.Dict):
            constants[name] = value
    return constants


SCHEMA_CONSTANTS = _schema_constants()


def _literal_string(node, where):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    raise AssertionError(f"{where} must be a string literal, got {ast.dump(node)[:120]}")


def _dict_key(node, key):
    if not isinstance(node, ast.Dict):
        return None
    for key_node, value_node in zip(node.keys, node.values, strict=False):
        if isinstance(key_node, ast.Constant) and key_node.value == key:
            return value_node
    return None


def _property_description(node):
    """The description of one inputSchema property, following shared _schema constants."""
    if isinstance(node, ast.Name) and node.id in SCHEMA_CONSTANTS:
        node = SCHEMA_CONSTANTS[node.id]
    if not isinstance(node, ast.Dict):
        return None
    value = _dict_key(node, "description")
    if value is None:
        return None
    return _literal_string(value, "a schema property description")


def _schema_properties(tool):
    """The top-level properties dict of a tool's inputSchema, or {} when it has none."""
    schema = _dict_key(tool, "inputSchema")
    if isinstance(schema, ast.Call) and isinstance(schema.func, ast.Name) and schema.func.id == "schema":
        schema = schema.args[0] if schema.args else None
    if not isinstance(schema, ast.Dict):
        return {}
    properties = _dict_key(schema, "properties")
    return properties if isinstance(properties, ast.Dict) else {}


def _tool_dicts(path):
    tree = ast.parse(Path(path).read_text(encoding="utf-8"), filename=path)
    value = _assignments(tree).get("TOOLS")
    if value is None:
        return []
    if not isinstance(value, ast.List):
        raise AssertionError(f"{path}: TOOLS must be a list literal")
    tools = []
    for element in value.elts:
        if not isinstance(element, ast.Dict):
            raise AssertionError(f"{path}: every TOOLS entry must be a dict literal")
        name = _dict_key(element, "name")
        if name is None:
            raise AssertionError(f"{path}: a tool has no name")
        tools.append((_literal_string(name, f"{path} tool name"), element))
    return tools


def _all_tools():
    tools = []
    for filename in sorted(os.listdir(TOOLS_DIR)):
        if not filename.endswith(".py") or filename.startswith("_"):
            continue
        path = os.path.join(TOOLS_DIR, filename)
        tools.extend((filename, name, node) for name, node in _tool_dicts(path))
    return tools


class ToolDescriptionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tools = _all_tools()

    def test_every_tool_module_contributes_tools(self):
        modules = {module for module, _name, _node in self.tools}
        expected = {
            "api.py",
            "autoposing.py",
            "batch.py",
            "camera.py",
            "cleanup.py",
            "contact_sheet.py",
            "cycles.py",
            "display.py",
            "history.py",
            "import_export.py",
            "inbetweening.py",
            "interpolation.py",
            "jobs.py",
            "layers.py",
            "mocap.py",
            "physics.py",
            "pose.py",
            "pose_ops.py",
            "python.py",
            "render.py",
            "retarget.py",
            "rig.py",
            "rigging.py",
            "scene.py",
            "scene_files.py",
            "selection.py",
            "timeline.py",
            "ui_input.py",
            "video.py",
            "viewport.py",
        }
        self.assertTrue(expected.issubset(modules), sorted(expected - modules))
        self.assertGreaterEqual(len(self.tools), 100)

    def test_names_are_unique_snake_case(self):
        names = [name for _module, name, _node in self.tools]
        self.assertEqual(len(names), len(set(names)), "duplicate tool names")
        for name in names:
            self.assertRegex(name, r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$", name)

    def test_descriptions_are_english_and_start_with_a_verb(self):
        for module, name, node in self.tools:
            with self.subTest(tool=name, module=module):
                description = _literal_string(_dict_key(node, "description"), f"{name}.description")
                self.assertTrue(description.strip(), f"{name}: empty description")
                self.assertIsNone(NON_ASCII.search(description), f"{name}: description is not ASCII English")
                self.assertIsNone(TICKET_NUMBER.search(description), f"{name}: internal ticket number")

                body = description.strip()
                if body.startswith("Last resort:"):
                    self.assertIn(name, LAST_RESORT, f"{name}: only interface tools are last resorts")
                    body = body[len("Last resort:") :].strip()
                    self.assertTrue(
                        any(tool in body for tool in LAST_RESORT[name]),
                        f"{name}: last-resort description must name a dedicated tool",
                    )
                else:
                    self.assertNotIn(name, LAST_RESORT, f"{name}: interface tool is missing 'Last resort:'")

                match = re.match(r"[A-Za-z]+", body)
                self.assertIsNotNone(match, f"{name}: no first word")
                self.assertIn(match.group(0).lower(), FIRST_WORDS, f"{name}: first sentence is not verb-first")

                sentence = re.match(r".*?[.!?](?:\s|$)", body, re.DOTALL)
                self.assertIsNotNone(sentence, f"{name}: first sentence has no end punctuation")
                self.assertLessEqual(len(sentence.group(0).strip()), 350, f"{name}: first sentence is too long")
                self.assertRegex(body, WHEN_WORDS, f"{name}: description does not say when to use it")
                self.assertRegex(body, RETURN_WORDS, f"{name}: description does not say what it returns")

    def test_last_resort_tools_point_at_their_replacements(self):
        expected = {
            "ui_tree": "get_scene_info",
            "ui_click": "set_pose",
            "ui_drag": "set_pose",
            "ui_hover": "get_scene_info",
            "ui_key": "set_frame",
            "ui_type": "call_action",
            "ui_set_property": "set_view_mode",
            "ui_invoke": "call_action",
            "screenshot": "render_image",
            "project_point": "set_pose",
        }
        for name, replacement in expected.items():
            with self.subTest(tool=name):
                _module, _name, node = next(item for item in self.tools if item[1] == name)
                description = _literal_string(_dict_key(node, "description"), f"{name}.description")
                self.assertTrue(description.startswith("Last resort:"), name)
                self.assertIn(replacement, description, f"{name}: mention {replacement}")
        self.assertIn("render_contact_sheet", self._description("screenshot"))
        self.assertIn("render_image", self._description("screenshot"))

    def test_parameter_descriptions_are_present(self):
        for module, name, node in self.tools:
            properties = _schema_properties(node)
            if not isinstance(properties, ast.Dict):
                continue
            for parameter, value in zip(properties.keys, properties.values, strict=False):
                parameter_name = _literal_string(parameter, f"{name} parameter name")
                with self.subTest(tool=name, parameter=parameter_name, module=module):
                    description = _property_description(value)
                    self.assertIsNotNone(description, f"{name}.{parameter_name}: missing description")
                    self.assertTrue(description.strip(), f"{name}.{parameter_name}: empty description")
                    self.assertIsNone(NON_ASCII.search(description), f"{name}.{parameter_name}: not ASCII")

    def test_all_nested_description_strings_are_non_empty(self):
        for module, name, node in self.tools:
            for sub_node in ast.walk(node):
                if not isinstance(sub_node, ast.Dict):
                    continue
                description_node = _dict_key(sub_node, "description")
                if description_node is None:
                    continue
                description = _literal_string(description_node, f"{name} nested description")
                with self.subTest(tool=name, module=module):
                    self.assertTrue(description.strip())
                    self.assertIsNone(NON_ASCII.search(description))

    def _description(self, name):
        _module, _name, node = next(item for item in self.tools if item[1] == name)
        return _literal_string(_dict_key(node, "description"), f"{name}.description")


if __name__ == "__main__":
    unittest.main()
