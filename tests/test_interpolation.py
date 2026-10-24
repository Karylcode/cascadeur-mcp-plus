"""Unit tests for the interpolation and IK/FK tools (T17); no Cascadeur needed.

cascadeur_mcp_plus/tools/interpolation.py imports csc, common, rig and timeline. The tests load
it behind a throw-away package with a fake csc module (the three enum classes), a fake common
that runs the edit callback against a fake scene, a fake rig and the real timeline.py. The pure
helpers (kind/mode names, the section range rule) are tested for real, and the tools are
exercised against fake layers so the argument errors, the readback and the one-edit-per-call
rule are covered without Cascadeur. tests/live/test_interpolation.py checks the behavior on the
running application.

Run: python -m unittest discover -s tests -v
"""

from __future__ import annotations

import importlib.util
import os
import sys
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGE_DIR = os.path.join(ROOT, "cascadeur_mcp_plus")
TOOLS_DIR = os.path.join(PACKAGE_DIR, "tools")
PKG = "t17_interpolation_test_pkg"


# ---------------------------------------------------------------------------
# Fake csc enums
# ---------------------------------------------------------------------------


class EnumValue:
    def __init__(self, name):
        self.name = name

    def __repr__(self):
        return f"EnumValue({self.name!r})"


class FakeLayerEnum:
    IK = EnumValue("IK")
    FK = EnumValue("FK")
    GR = EnumValue("GR")


class FakeInterpolationEnum:
    BEZIER = EnumValue("BEZIER")
    LOW_AMPLITUDE_BEZIER = EnumValue("LOW_AMPLITUDE_BEZIER")
    LINEAR = EnumValue("LINEAR")
    STEP = EnumValue("STEP")
    FIXED = EnumValue("FIXED")
    CLAMPED_BEZIER = EnumValue("CLAMPED_BEZIER")


class FakeTangentsEnum:
    Continuous = EnumValue("Continuous")
    UserDefined = EnumValue("UserDefined")


# ---------------------------------------------------------------------------
# Fake scene, layers and editors
# ---------------------------------------------------------------------------


class FakeSection:
    def __init__(
        self,
        interpolation="STEP",
        ik_fk="IK",
        key_ik_fk="IK",
        tangents="Continuous",
        left=1.0,
        right=1.0,
    ):
        self.interval = types.SimpleNamespace(
            interpolation=EnumValue(interpolation),
            common=types.SimpleNamespace(ik_fk=EnumValue(ik_fk)),
        )
        self.key = types.SimpleNamespace(
            common=types.SimpleNamespace(ik_fk=EnumValue(key_ik_fk)),
            tangents=EnumValue(tangents),
            left_weight_velocity=left,
            right_weight_velocity=right,
        )


class FakeLayer:
    def __init__(self, name, sections):
        self.header = types.SimpleNamespace(name=name)
        self.sections = sections


class FakeViewer:
    def __init__(self, owners):
        self.owners = dict(owners)
        self.layers = {}

    def add(self, layer_id, name, sections):
        self.layers[layer_id] = FakeLayer(name, sections)

    def layer(self, layer_id):
        return self.layers[layer_id]

    def layer_id_by_obj_id_or_null(self, object_id):
        return self.owners.get(object_id)


class FakeModelViewer:
    def __init__(self, names, objects):
        self.names = names
        self.objects = objects

    def get_objects(self, name=None):
        return list(self.objects) if name is None else []

    def get_object_name(self, object_id):
        return self.names[object_id]


class FakeModelEditor:
    def __init__(self, viewer):
        self.viewer = viewer
        self.changes = []

    def layers_editor(self):
        return self

    def change_section(self, position, layer_id, callback):
        self.changes.append((layer_id, position))
        callback(self.viewer.layer(layer_id).sections[position])


class FakeObjectSelector:
    def __init__(self, ids=()):
        self.ids = list(ids)
        self.pivot = None
        self.calls = []

    def selected(self):
        return types.SimpleNamespace(ordered_ids=list(self.ids), ids=list(self.ids))

    def select(self, ids, pivot=None):
        self.calls.append((list(ids), pivot))
        self.ids = list(ids)
        self.pivot = pivot


class FakeIndices:
    """Stand-in for csc.layers.index.IndicesContainer, with a value-copying state."""

    def __init__(self, state):
        self.state = dict(state)

    def frames_interval(self):
        return types.SimpleNamespace(first=self.state["first"], last=self.state["last"])


class FakeLayersSelector:
    def __init__(self):
        self.state = {"ids": [], "first": 0, "last": 0}
        self.calls = []

    def selection(self):
        return FakeIndices(self.state)

    def set_full_selection_by_parts(self, items=None, first=None, last=None):
        self.calls.append((items, first, last))
        if first is None:
            self.state = dict(items.state if isinstance(items, FakeIndices) else items)
        else:
            self.state = {"ids": list(items), "first": int(first), "last": int(last)}


class FakeActionManager:
    """Applies the IK/FK menu action to the current fake timeline selection."""

    def __init__(self):
        self.calls = []

    def call_action(self, key):
        self.calls.append(key)
        mode = key.split(".")[2].split(" ")[0].lower()
        state = WORLD.scene.get_layers_selector().state
        for layer_id in state["ids"]:
            for position, section in WORLD.viewer.layer(layer_id).sections.items():
                last = max(state["last"], state["first"])
                if state["first"] <= position <= last:
                    section.interval.common.ik_fk = EnumValue(mode.upper())


class FakeScene:
    def __init__(self, viewer, model_viewer):
        self.viewer = viewer
        self._model_viewer = model_viewer
        self._selector = FakeObjectSelector(["left_hand", "pelvis"])
        self._layers_selector = FakeLayersSelector()

    def layers_viewer(self):
        return self.viewer

    def model_viewer(self):
        return self._model_viewer

    def selector(self):
        return self._selector

    def get_layers_selector(self):
        return self._layers_selector


class FakeWorld:
    def __init__(self):
        self.reset()

    def reset(self):
        self.objects = {"left_hand": "Hand_L", "pelvis": "Torso"}
        self.names = {"left_hand": "hand_MainPoint_l", "pelvis": "pelvis_MainPoint"}
        self.viewer = FakeViewer(self.objects)
        self.viewer.add("Hand_L", "Hand_L", {0: FakeSection(), 20: FakeSection()})
        self.viewer.add("Torso", "Torso", {0: FakeSection(), 20: FakeSection()})
        self.model_viewer = FakeModelViewer(self.names, list(self.objects))
        self.scene = FakeScene(self.viewer, self.model_viewer)
        self.edits = []
        self.updates = []
        self.model_editor = FakeModelEditor(self.viewer)
        self.action_manager = FakeActionManager()

    def scenes(self):
        return None, None, self.scene

    def edit(self, undo_name, fn, *, update=True):
        self.edits.append(undo_name)
        self.updates.append(update)
        fn(self.model_editor, None, None)
        return {"ok": True, "undo": "MCP: " + undo_name}


WORLD = FakeWorld()


# ---------------------------------------------------------------------------
# Fake common/rig and module loading
# ---------------------------------------------------------------------------


class FakeRigError(Exception):
    def __init__(self, message="ambiguous", result=None):
        super().__init__(message)
        self._result = result or {"ok": False, "error": message, "hint": "fake rig hint"}

    def result(self):
        return self._result


def _load(name: str, path: str):
    full = f"{PKG}.{name}"
    spec = importlib.util.spec_from_file_location(full, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[full] = module
    spec.loader.exec_module(module)
    return module


def _module(name, **attributes):
    module = types.ModuleType(f"{PKG}.{name}")
    for key, value in attributes.items():
        setattr(module, key, value)
    sys.modules[f"{PKG}.{name}"] = module
    return module


def _load_tool():
    if PKG in sys.modules:
        return sys.modules[f"{PKG}.tools.interpolation"]

    package = types.ModuleType(PKG)
    package.__path__ = []
    sys.modules[PKG] = package

    errors = _load("errors", os.path.join(PACKAGE_DIR, "errors.py"))
    package.errors = errors

    timeline = _load("timeline", os.path.join(PACKAGE_DIR, "timeline.py"))
    package.timeline = timeline

    common = _module(
        "common",
        ok=lambda **data: {"ok": True, **data},
        fail=lambda error, hint=None, **data: {
            "ok": False,
            "error": str(error),
            **({"hint": str(hint)} if hint else {}),
            **data,
        },
        jsonable=lambda value: value,
        scenes=WORLD.scenes,
        edit=WORLD.edit,
        ToolError=errors.ToolError,
    )
    package.common = common

    def resolve(names, character=None):
        wanted = [names] if isinstance(names, str) else list(names)
        return [name for name in wanted if name in WORLD.objects]

    rig = _module(
        "rig",
        RigError=FakeRigError,
        controllers=lambda character=None: [{"id": object_id} for object_id in WORLD.objects],
        resolve=resolve,
    )
    package.rig = rig

    tools_package = types.ModuleType(f"{PKG}.tools")
    tools_package.__path__ = [TOOLS_DIR]
    sys.modules[f"{PKG}.tools"] = tools_package
    package.tools = tools_package
    _load("tools._schema", os.path.join(TOOLS_DIR, "_schema.py"))

    csc = types.ModuleType("csc")
    layers = types.ModuleType("csc.layers")
    layer = types.ModuleType("csc.layers.layer")
    layer.Interpolation = FakeInterpolationEnum
    layer.IkFk = FakeLayerEnum
    layer.Tangents = FakeTangentsEnum
    layers.layer = layer
    csc.layers = layers

    class FakeObjectId:
        @staticmethod
        def null():
            return "null"

    class FakeApplication:
        def get_action_manager(self):
            return WORLD.action_manager

    csc.app = types.SimpleNamespace(get_application=lambda: FakeApplication())
    csc.model = types.SimpleNamespace(ObjectId=FakeObjectId)
    previous = sys.modules.get("csc")
    sys.modules["csc"] = csc
    try:
        tool = _load("tools.interpolation", os.path.join(TOOLS_DIR, "interpolation.py"))
    finally:
        if previous is None:
            sys.modules.pop("csc", None)
        else:
            sys.modules["csc"] = previous
    package.tools.interpolation = tool
    return tool


tool = _load_tool()


# ---------------------------------------------------------------------------
# Argument helpers and the section range rule
# ---------------------------------------------------------------------------


class ArgumentTests(unittest.TestCase):
    def test_interpolation_kind_names(self):
        for value in ("bezier", "Bezier", " BEZIER "):
            self.assertEqual(tool.interpolation_kind(value), "bezier")
        self.assertEqual(tool.interpolation_kind("clamped_bezier"), "bezier_clamped")
        self.assertEqual(tool.interpolation_kind("viscous"), "bezier_viscous")
        self.assertEqual(tool.interpolation_kind("low_amplitude_bezier"), "bezier_viscous")

    def test_bad_interpolation_kind_is_rejected(self):
        for value in (None, "smooth", 3, ["linear"], True):
            with self.subTest(value=value), self.assertRaises(tool._BadArgument) as caught:
                tool.interpolation_kind(value)
            self.assertIn("kind", str(caught.exception))
            self.assertTrue(caught.exception.hint)

    def test_ik_fk_mode_names(self):
        self.assertEqual(tool.ik_fk_mode("IK"), "ik")
        self.assertEqual(tool.ik_fk_mode(" fk "), "fk")
        self.assertEqual(tool.ik_fk_mode("gr"), "gr")
        self.assertEqual(tool.ik_fk_mode("global_fk"), "gr")

    def test_bad_ik_fk_mode_is_rejected(self):
        for value in (None, "inverse", 1, []):
            with self.subTest(value=value), self.assertRaises(tool._BadArgument):
                tool.ik_fk_mode(value)

    def test_frame_range(self):
        self.assertEqual(tool.frame_range(0, 20), (0, 20))
        self.assertEqual(tool.frame_range("10", 10), (10, 10))

    def test_frame_range_rejects_bad_and_reversed_values(self):
        for start, end in ((-1, 20), (10, 5), ("x", 20), (0, None)):
            with self.subTest(start=start, end=end), self.assertRaises(tool.ToolError):
                tool.frame_range(start, end)


class SectionPositionTests(unittest.TestCase):
    def test_interval_starting_on_the_last_frame_is_outside(self):
        self.assertEqual(tool.section_positions({0: None, 20: None}, 0, 20), [0])

    def test_intervals_inside_the_range_are_selected(self):
        self.assertEqual(tool.section_positions({0: None, 10: None, 20: None, 30: None}, 0, 20), [0, 10])

    def test_interval_that_covers_the_start_is_selected(self):
        # The key is before the range, but its interval runs into the range.
        self.assertEqual(tool.section_positions({0: None, 30: None}, 10, 20), [0])

    def test_interval_that_ends_before_the_range_is_not_selected(self):
        self.assertEqual(tool.section_positions({0: None, 5: None, 30: None}, 10, 20), [5])

    def test_single_frame_range_targets_the_key_on_that_frame(self):
        self.assertEqual(tool.section_positions({0: None, 10: None, 20: None}, 10, 10), [10])

    def test_include_end_selects_the_last_frames_interval_too(self):
        self.assertEqual(tool.section_positions({0: None, 20: None}, 0, 20, include_end=True), [0, 20])
        self.assertEqual(tool.section_positions({0: None, 10: None, 20: None}, 0, 20, include_end=True), [0, 10, 20])
        self.assertEqual(tool.section_positions({0: None, 20: None}, 20, 20, include_end=True), [20])

    def test_the_last_interval_is_open_ended(self):
        # The last key's interval runs to the end of the animation, so a later range still
        # belongs to it (this is why a layer with keys 0 and 20 changes the section at 20 for
        # a range 30-40).
        self.assertEqual(tool.section_positions({0: None, 20: None}, 30, 40), [20])


# ---------------------------------------------------------------------------
# Tools against a fake scene
# ---------------------------------------------------------------------------


class ToolTests(unittest.TestCase):
    def setUp(self):
        WORLD.reset()
        self._target = tool.timeline.target_object_ids
        self._resolve = tool.rig.resolve
        tool.timeline.target_object_ids = lambda: list(WORLD.objects)
        tool.rig.resolve = lambda names, character=None: [
            name for name in ([names] if isinstance(names, str) else list(names)) if name in WORLD.objects
        ]

    def tearDown(self):
        tool.timeline.target_object_ids = self._target
        tool.rig.resolve = self._resolve

    def test_set_interpolation_changes_one_section_per_layer_and_one_edit(self):
        result = tool.set_interpolation("linear", 0, 20)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["kind"], "linear")
        self.assertEqual(result["start"], 0)
        self.assertEqual(result["end"], 20)
        self.assertEqual(result["layer_count"], 2, result)
        self.assertEqual(result["sections"], 2, result)
        self.assertEqual(result["requested_changes"], 2, result)
        self.assertEqual(result["sections_changed"], 2, result)
        self.assertEqual(result["readback"], {"linear": 2}, result)
        self.assertEqual(WORLD.edits, ["set_interpolation"])
        # Section edits use scene.modify, not modify_update: the dependency-graph update rejects
        # an interval change as an interpolation-root change and aborts the undo step.
        self.assertEqual(WORLD.updates, [False])
        self.assertEqual(result["undo"], "MCP: set_interpolation")
        # Frame 20 starts the next interval and is outside the selected range.
        for layer in result["layers"]:
            self.assertEqual([section["frame"] for section in layer["sections"]], [0], layer)
            self.assertEqual(layer["sections"][0]["interpolation"], "linear", layer)
        for name in ("Hand_L", "Torso"):
            self.assertEqual(WORLD.viewer.layer(name).sections[0].interval.interpolation.name, "LINEAR")

    def test_bezier_variant_names_match_cascadeurs_enum(self):
        # The tool kind names (bezier_clamped, bezier_viscous) differ from the enum names that
        # list_keys and the readback use (clamped_bezier, low_amplitude_bezier); the plan must
        # compare with the enum name or every call reports zero changed sections.
        for kind, enum_name in (("bezier_clamped", "clamped_bezier"), ("bezier_viscous", "low_amplitude_bezier")):
            with self.subTest(kind=kind):
                WORLD.reset()
                result = tool.set_interpolation(kind, 0, 20)
                self.assertTrue(result["ok"], result)
                self.assertEqual(result["sections_changed"], 2, result)
                self.assertEqual(result["requested_changes"], 2, result)
                self.assertEqual(result["readback"], {enum_name: 2}, result)
                again = tool.set_interpolation(kind, 0, 20)
                self.assertEqual(again["requested_changes"], 0, "the name mapping broke the no-op check")

    def test_set_interpolation_uses_the_requested_controllers(self):
        result = tool.set_interpolation("step", 0, 20, controllers=["left_hand"])
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["layer_count"], 1, result)
        self.assertEqual(result["controllers"], ["hand_MainPoint_l"], result)
        self.assertEqual(WORLD.viewer.layer("Hand_L").sections[0].interval.interpolation.name, "STEP")

    def test_set_ik_fk_runs_the_action_and_restores_the_selection(self):
        section = WORLD.viewer.layer("Hand_L").sections[0]
        self.assertEqual(section.key.common.ik_fk.name, "IK")
        world_scene = WORLD.scene
        world_scene.selector().ids = ["pelvis"]
        world_scene.get_layers_selector().state = {"ids": ["Torso"], "first": 5, "last": 10}

        result = tool.set_ik_fk("gr", 0, 20, controllers=["left_hand"])
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["mode"], "gr", result)
        self.assertEqual(result["readback"], {"gr": 2}, result)
        self.assertEqual(result["sections"], 2, result)
        self.assertEqual(result["sections_changed"], 2, result)
        self.assertEqual(result["action"], "Timeline.IK FK type.GR type on selected interval")
        self.assertEqual(result["undo_source"], "Cascadeur action")
        self.assertEqual(WORLD.action_manager.calls, ["Timeline.IK FK type.GR type on selected interval"])
        self.assertEqual(section.interval.common.ik_fk.name, "GR")
        # The key's own type is the object's interpolation root; the action is the supported path.
        self.assertEqual(section.key.common.ik_fk.name, "IK")
        self.assertEqual(
            result["layers"][0]["sections"],
            [
                {"frame": 0, "interval": "gr", "key": "ik"},
                {"frame": 20, "interval": "gr", "key": "ik"},
            ],
        )
        # The IK/FK action includes the interval that starts on the last selected frame.
        self.assertEqual(WORLD.viewer.layer("Hand_L").sections[20].interval.common.ik_fk.name, "GR")
        # Both selections are back exactly as they were.
        self.assertEqual(list(world_scene.selector().ids), ["pelvis"])
        self.assertEqual(world_scene.get_layers_selector().state, {"ids": ["Torso"], "first": 5, "last": 10})
        self.assertEqual(WORLD.edits, [], "the action must not add a second common.edit step")

        again = tool.set_ik_fk("gr", 0, 20, controllers=["left_hand"])
        self.assertTrue(again["ok"], again)
        self.assertEqual(again["sections_changed"], 0, again)
        self.assertIn("already", again["hint"])

    def test_reset_tangents_clears_custom_tangents(self):
        section = WORLD.viewer.layer("Hand_L").sections[0]
        section.key.tangents = EnumValue("UserDefined")
        result = tool.reset_tangents(0, 20, controllers=["left_hand"])
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["readback"], {"continuous": 1}, result)
        self.assertEqual(section.key.tangents.name, "Continuous")
        self.assertEqual(WORLD.edits, ["reset_tangents"])

    def test_reset_easing_returns_the_neutral_weights(self):
        WORLD.viewer.layer("Hand_L").sections[0].key.left_weight_velocity = 0.5
        WORLD.viewer.layer("Torso").sections[0].key.right_weight_velocity = 0.25
        result = tool.reset_easing(0, 20)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["requested_changes"], 2, result)
        self.assertEqual(result["readback"], {str(tool.EASING_WEIGHT): 2}, result)
        self.assertAlmostEqual(result["weight"], 1.0 / 3.0, places=6)
        for layer in ("Hand_L", "Torso"):
            section = WORLD.viewer.layer(layer).sections[0]
            self.assertAlmostEqual(section.key.left_weight_velocity, tool.EASING_WEIGHT, places=6)
            self.assertAlmostEqual(section.key.right_weight_velocity, tool.EASING_WEIGHT, places=6)

    def test_reset_easing_tolerates_cascadeurs_float32_weight(self):
        # The live value is float32(1/3) = 0.3333333134651184, not the exact Python double.
        section = WORLD.viewer.layer("Hand_L").sections[0]
        section.key.left_weight_velocity = 0.3333333134651184
        section.key.right_weight_velocity = 0.3333333134651184
        result = tool.reset_easing(0, 20, controllers=["left_hand"])
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["requested_changes"], 0, result)
        self.assertIn("already", result["hint"])
        self.assertEqual(WORLD.edits, [])

    def test_a_range_without_keys_is_ok_and_creates_no_edit(self):
        WORLD.viewer.layer("Hand_L").sections = {}
        WORLD.viewer.layer("Torso").sections = {}
        result = tool.set_interpolation("linear", 30, 40)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["sections"], 0, result)
        self.assertIn("No timeline keys overlap", result["hint"])
        self.assertEqual(WORLD.edits, [])

    def test_already_matching_values_create_no_edit(self):
        tool.set_interpolation("step", 0, 20, controllers=["left_hand"])
        WORLD.edits.clear()
        result = tool.set_interpolation("step", 0, 20, controllers=["left_hand"])
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["requested_changes"], 0, result)
        self.assertIn("already", result["hint"])
        self.assertEqual(WORLD.edits, [])

    def test_bad_arguments_do_not_edit(self):
        for call in (
            lambda: tool.set_interpolation("nope", 0, 20),
            lambda: tool.set_ik_fk("nope", 0, 20),
            lambda: tool.set_interpolation("linear", 20, 0),
            lambda: tool.reset_tangents(20, 0),
            lambda: tool.reset_easing(None, 20),
        ):
            with self.subTest(call=call):
                self.assertFalse(call()["ok"])
        self.assertEqual(WORLD.edits, [])

    def test_rig_errors_are_passed_through(self):
        def ambiguous(names, character=None):
            raise FakeRigError("ambiguous", {"ok": False, "error": "ambiguous", "hint": "pick", "candidates": [1]})

        tool.rig.resolve = ambiguous
        result = tool.set_interpolation("linear", 0, 20, controllers=["hand"])
        self.assertFalse(result["ok"])
        self.assertEqual(result["candidates"], [1])
        self.assertEqual(WORLD.edits, [])


class ToolDeclarationTests(unittest.TestCase):
    def test_tool_table(self):
        self.assertEqual(
            [entry["name"] for entry in tool.TOOLS],
            ["set_interpolation", "set_ik_fk", "reset_tangents", "reset_easing"],
        )
        for entry in tool.TOOLS:
            with self.subTest(tool=entry["name"]):
                self.assertTrue(entry["description"].strip().endswith("."))
                self.assertEqual(entry["inputSchema"]["type"], "object")
                self.assertTrue(callable(entry["fn"]))
                self.assertNotIn("handler", entry)

    def test_schemas(self):
        by_name = {entry["name"]: entry["inputSchema"] for entry in tool.TOOLS}
        self.assertEqual(by_name["set_interpolation"]["required"], ["kind", "start", "end"])
        self.assertEqual(by_name["set_ik_fk"]["required"], ["mode", "start", "end"])
        self.assertEqual(by_name["reset_tangents"]["required"], ["start", "end"])
        self.assertEqual(by_name["reset_easing"]["required"], ["start", "end"])
        self.assertEqual(by_name["set_interpolation"]["properties"]["kind"]["enum"], list(tool.INTERPOLATION_KINDS))
        self.assertEqual(by_name["set_ik_fk"]["properties"]["mode"]["enum"], ["ik", "fk", "gr"])


if __name__ == "__main__":
    unittest.main()
