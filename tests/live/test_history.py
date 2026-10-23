"""Live checks for undo, redo and get_undo_history (T24).

Each test opens Cascy.casc in its own tab, records the value of one joint's
Transform.global_position, makes undoable changes through the same common.edit wrapper every
tool uses, and checks every undo/redo against the stored data values.

Run with: python tests/live/run.py test_history.py
"""

from __future__ import annotations

import json
import time
import unittest
from string import Template

try:
    from . import client
    from .fixture import LiveTestCase
except ImportError:  # executed from tests/live
    import client  # type: ignore[no-redef]
    from fixture import LiveTestCase  # type: ignore[no-redef]


JOINT = "toe_r"
FIRST = "T24 first edit"  # common.edit adds the "MCP: " prefix
SECOND = "T24 second edit"
FIRST_STEP = f"MCP: {FIRST}"
SECOND_STEP = f"MCP: {SECOND}"

_READ_CODE = Template(
    """
import json

mv = scene.model_viewer()
bv = mv.behaviour_viewer()
joints = mv.get_objects($name)
if not joints:
    raise RuntimeError("object not found: " + $name)
transform = bv.get_behaviour_by_name(joints[0], "Transform")
data_id = bv.get_behaviour_data(transform, "global_position")
value = [float(v) for v in mv.data_viewer().get_data_value(data_id, 0)]
print("__RESULT__" + json.dumps({"value": value}))
"""
)

_EDIT_CODE = Template(
    """
import json

import numpy as np

from cascadeur_mcp_plus import common

mv = scene.model_viewer()
bv = mv.behaviour_viewer()
joints = mv.get_objects($name)
if not joints:
    raise RuntimeError("object not found: " + $name)
transform = bv.get_behaviour_by_name(joints[0], "Transform")
data_id = bv.get_behaviour_data(transform, "global_position")
before = mv.data_viewer().get_data_value(data_id, 0)
target = before + np.array([$dx, $dy, $dz], dtype=before.dtype)


def change(model_editor, update_editor, scene_updater):
    model_editor.data_editor().set_data_value(data_id, 0, target)
    scene_updater.run_update({data_id}, 0)


result = common.edit($undo_name, change)
after = [float(v) for v in mv.data_viewer().get_data_value(data_id, 0)]
print("__RESULT__" + json.dumps({"edit": result, "before": [float(v) for v in before], "after": after}))
"""
)

_FOCUS_CODE = """
import json

from PySide6.QtGui import QGuiApplication

window = None
for candidate in QGuiApplication.topLevelWindows():
    title = candidate.title()
    if candidate.isVisible() and candidate.inherits("QQuickWindow"):
        if title == "Cascadeur" or title.endswith(" - Cascadeur"):
            window = candidate
            break
if window is None:
    window = QGuiApplication.focusWindow()
item = window.activeFocusItem() if window is not None else None
print(
    "__RESULT__"
    + json.dumps(
        {
            "type": item.metaObject().className() if item is not None else None,
            "text": item.property("text") if item is not None else None,
        }
    )
)
"""

INPUT_TYPES = ("TextInput", "TextField", "TextEdit")


def _is_text_input(focus: dict) -> bool:
    kind = focus.get("type") or ""
    return any(name in kind for name in INPUT_TYPES)


def _run_json(code: str, timeout: float = 120.0) -> dict:
    result = client.call("run_python", code=code, timeout=timeout, http_timeout=timeout + 30.0)
    if not isinstance(result, dict) or not result.get("ok"):
        raise AssertionError(f"run_python failed: {result!r}")
    for line in reversed(str(result.get("stdout", "")).splitlines()):
        if line.startswith("__RESULT__"):
            return json.loads(line[len("__RESULT__") :])
    raise AssertionError(f"run_python produced no __RESULT__ line: {result!r}")


class HistoryLiveTests(LiveTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.assertEqual(self.open_sample("Cascy.casc")["name"], "Cascy.casc")
        self.original = self.value()

    # -- helpers -----------------------------------------------------------

    def value(self) -> list[float]:
        """The joint's stored global_position at frame 0, after any pending update."""
        time.sleep(0.2)
        return _run_json(_READ_CODE.substitute(name=repr(JOINT)))["value"]

    def edit(self, undo_name: str, dx: float = 0.0, dy: float = 0.0, dz: float = 0.0) -> dict:
        """Run one common.edit change; undo_name is the part after common.edit's "MCP: " prefix."""
        result = _run_json(_EDIT_CODE.substitute(name=repr(JOINT), undo_name=repr(undo_name), dx=dx, dy=dy, dz=dz))
        self.assertTrue(result["edit"]["ok"], result)
        self.assertEqual(result["edit"]["undo"], f"MCP: {undo_name}")
        return result

    @staticmethod
    def moved(before: list[float], after: list[float]) -> float:
        return max(abs(now - then) for now, then in zip(before, after, strict=False))

    def assert_close(self, actual, expected, tolerance: float = 1e-3) -> None:
        self.assertEqual(len(actual), 3, actual)
        for now, then in zip(actual, expected, strict=False):
            self.assertAlmostEqual(now, then, delta=tolerance, msg=f"{actual!r} != {expected!r}")

    # -- tests -------------------------------------------------------------

    def test_undo_redo_and_history(self) -> None:
        first = self.edit(FIRST, dx=2.0)
        self.assertGreater(self.moved(first["before"], first["after"]), 0.5, "the first edit did not move the joint")
        value_after_first = self.value()
        self.assert_close(first["after"], value_after_first)

        second = self.edit(SECOND, dy=3.0)
        self.assertGreater(self.moved(second["before"], second["after"]), 0.5, "the second edit did not move the joint")
        value_after_second = self.value()
        self.assert_close(second["after"], value_after_second)

        history = client.call("get_undo_history", limit=10)
        names = [step["name"] for step in history["steps"]]
        self.assertEqual(names[-2:], [FIRST_STEP, SECOND_STEP], history)
        self.assertEqual(history["next_undo"], SECOND_STEP, history)
        self.assertEqual(history["position"], len(names), history)
        self.assertEqual([step["state"] for step in history["steps"][-2:]], ["applied", "applied"], history)
        # The record must say where it starts: the fixture loaded Cascy.casc, so the last reset is
        # that load (this is what keeps a previous Cascadeur session's steps out of the list).
        self.assertTrue(history["recorded_since"], history)
        self.assertEqual(history["reset_reason"], "scene loaded", history)

        undone = client.call("undo")
        self.assertEqual(undone["action"], "undo", undone)
        self.assertEqual(undone["steps"], [SECOND_STEP], undone)
        self.assert_close(self.value(), value_after_first)

        redone = client.call("redo")
        self.assertEqual(redone["action"], "redo", redone)
        self.assertEqual(redone["steps"], [SECOND_STEP], redone)
        self.assert_close(self.value(), value_after_second)

        both = client.call("undo", steps=2)
        self.assertEqual(both["steps"], [SECOND_STEP, FIRST_STEP], both)
        self.assert_close(self.value(), self.original)

        history = client.call("get_undo_history", limit=10)
        self.assertEqual([step["state"] for step in history["steps"][-2:]], ["undone", "undone"], history)
        self.assertEqual(history["next_redo"], FIRST_STEP, history)
        self.assertIsNone(history["next_undo"], history)
        self.assertEqual(history["reset_reason"], "scene loaded", history)

    def test_undo_ignores_a_focused_text_field(self) -> None:
        edit = self.edit("T24 focus edit", dx=2.0)
        self.assertGreater(self.moved(edit["before"], edit["after"]), 0.5, "the edit did not move the joint")

        typed = client.call("ui_type", query="Filter by name", text="pelvis", replace=True, release_focus=False)
        self.assertEqual(typed["typed"], "pelvis")
        focus = _run_json(_FOCUS_CODE, timeout=30.0)
        self.assertTrue(_is_text_input(focus), f"the Outliner filter did not keep keyboard focus: {focus!r}")
        self.assertEqual(focus["text"], "pelvis")

        result = client.call("undo")
        self.assertEqual(result["steps"], ["MCP: T24 focus edit"], result)
        self.assert_close(self.value(), self.original)

        # The undo went to the scene, not to the text field: its text is still there.
        focus = _run_json(_FOCUS_CODE, timeout=30.0)
        self.assertTrue(_is_text_input(focus), f"the undo moved focus away from the text field: {focus!r}")
        self.assertEqual(focus["text"], "pelvis")
        client.call("ui_type", query="Filter by name", text="", replace=True, release_focus=True)


if __name__ == "__main__":
    unittest.main()
