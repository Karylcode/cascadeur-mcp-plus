"""Unit tests for the AutoPhysics tools (T18, no Cascadeur needed).

cascadeur_mcp_plus/tools/physics.py is loaded through a synthetic package with fake common/rig/
dispatch/ui modules, the same technique tests/test_timeline.py uses. The QML manager, the pose
data, the snap dialog and the background job's poll are exercised with small fakes; the real
Cascadeur behaviour is covered by tests/live/test_physics.py.

Run: python -m unittest discover -s tests -v
"""

from __future__ import annotations

import importlib.util
import os
import sys
import time
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGE = "t18_physics_pkg"


def _package(name: str, path: str) -> types.ModuleType:
    module = types.ModuleType(name)
    module.__path__ = [path]
    sys.modules[name] = module
    return module


def _load(name: str, filename: str):
    full = PACKAGE + "." + name
    spec = importlib.util.spec_from_file_location(full, os.path.join(ROOT, "cascadeur_mcp_plus", filename))
    module = importlib.util.module_from_spec(spec)
    sys.modules[full] = module
    spec.loader.exec_module(module)
    return module


_package(PACKAGE, os.path.join(ROOT, "cascadeur_mcp_plus"))
_package(PACKAGE + ".tools", os.path.join(ROOT, "cascadeur_mcp_plus", "tools"))
errors = _load("errors", "errors.py")
timeline = _load("timeline", "timeline.py")

# ---------------------------------------------------------------------------
# Fake common / rig / dispatch / ui modules
# ---------------------------------------------------------------------------


def _fail(error, hint=None, **data):
    result = {"ok": False, "error": str(error)}
    if hint:
        result["hint"] = str(hint)
    result.update(data)
    return result


def _ok(**data):
    return {"ok": True, **data}


class FakeCommon(types.ModuleType):
    LOG_PATH = os.path.join(os.path.dirname(__file__), "not-a-real-cascadeur-log.log")

    def __init__(self):
        super().__init__(PACKAGE + ".common")
        self.log_lines = []
        self.log_since = None

    def ok(self, **data):
        return _ok(**data)

    def fail(self, error, hint=None, **data):
        return _fail(error, hint, **data)

    def scenes(self):
        return None, WORLD.view, WORLD.scene

    def log_tail(self, since=None, contains=None, lines=40):
        self.log_since = since
        return list(self.log_lines)[-lines:]


common = FakeCommon()
sys.modules[PACKAGE + ".common"] = common


class FakeRig(types.ModuleType):
    def __init__(self):
        super().__init__(PACKAGE + ".rig")

    class RigError(Exception):
        pass

    def resolve(self, names, character=None):
        return [name for name in names]

    def controllers(self, character=None):
        return [
            {"id": name, "name": name}
            for name in ("pelvis", "head", "left_hand", "right_hand", "left_foot", "right_foot")
        ]


rig = FakeRig()
sys.modules[PACKAGE + ".rig"] = rig


class FakeDispatch(types.ModuleType):
    def __init__(self):
        super().__init__(PACKAGE + ".dispatch")
        self.calls = []

    def call_on_gui(self, fn, args, timeout, idle=False):
        self.calls.append((fn, dict(args), timeout))
        return fn(**args)


dispatch = FakeDispatch()
sys.modules[PACKAGE + ".dispatch"] = dispatch


class FakeUi(types.ModuleType):
    def __init__(self):
        super().__init__(PACKAGE + ".ui")
        self.dialog_result = {"dialogs": []}
        self.clicks = []
        self.items = []

    def dialogs(self):
        return self.dialog_result

    def click(self, ref=None, query=None, **kwargs):
        self.clicks.append(ref if ref is not None else query)
        return {"clicked": ref}

    def quick_windows(self, include_hidden=False):
        return [object()] if self.items else []

    def _walk(self, window, include_hidden=False):
        return iter(self.items)


ui = FakeUi()
sys.modules[PACKAGE + ".ui"] = ui

physics = _load("tools.physics", os.path.join("tools", "physics.py"))


# ---------------------------------------------------------------------------
# Fake scene graph
# ---------------------------------------------------------------------------


class FakeBoundary:
    def __init__(self, first=0, last=10):
        self.first_frame = first
        self.last_frame = last
        self.first_visible_frame = first
        self.last_visible_frame = last


class FakeSettingsHandler:
    def get_float_value(self, group, key):
        return 30.0 if (group, key) == ("Scene", "Fps") else 0.0


class FakeViewScene:
    def __init__(self, boundary):
        self._boundary = boundary

    def animation_boundary(self):
        return self._boundary

    def get_setting_handler(self):
        return FakeSettingsHandler()

    def gravity_per_frame(self):
        return 1.0888888888888888


class FakeLayer:
    def __init__(self, keys):
        self._keys = keys

    def key_frame_indices(self):
        return list(self._keys)


class FakeLayersViewer:
    def __init__(self, keys):
        self._keys = keys

    def all_layer_ids(self):
        return ["layer-1"]

    def layer(self, layer_id):
        return FakeLayer(self._keys)

    def last_key_pos(self):
        return max(self._keys) if self._keys else -1


class FakeData:
    def __init__(self, animated=True):
        self.mode = types.SimpleNamespace(name="Animation" if animated else "Static")


class FakeDataViewer:
    def __init__(self, world):
        self.world = world

    def get_data(self, data_id):
        return FakeData(animated=True)

    def get_animation_size(self):
        return len(self.world.poses)

    def get_data_value(self, data_id, frame=None):
        if data_id == "priority":
            return 1 if frame in self.world.priority else 0
        if data_id.startswith("pos:"):
            alias = data_id.split(":", 1)[1]
            return list(self.world.poses[frame][alias][0])
        if data_id.startswith("rot:"):
            alias = data_id.split(":", 1)[1]
            return list(self.world.poses[frame][alias][1])
        raise KeyError(data_id)


class FakeBehaviourViewer:
    def __init__(self, world):
        self.world = world

    def is_valid_behaviour_type(self, name):
        return name == "AutoPhysics"

    def get_behaviours(self, name):
        return ["auto-physics"] if self.world.has_auto_physics else []

    def get_behaviour_data(self, behaviour, name):
        if behaviour == "auto-physics" and name == "priority_frame":
            return "priority"
        if behaviour.startswith("transform:"):
            alias = behaviour.split(":", 1)[1]
            return "pos:" + alias if name == "global_position" else "rot:" + alias
        raise KeyError((behaviour, name))

    def get_behaviour_by_name(self, object_id, name):
        if name == "Transform":
            return "transform:" + object_id
        return None

    def get_behaviour_owner(self, behaviour):
        return "cm" if behaviour == "auto-physics" else None


class FakeModelViewer:
    def __init__(self, world):
        self.world = world
        self.bv = FakeBehaviourViewer(world)
        self.dv = FakeDataViewer(world)

    def behaviour_viewer(self):
        return self.bv

    def data_viewer(self):
        return self.dv

    def get_object_name(self, object_id):
        return object_id + "_MainPoint"


class FakeScene:
    def __init__(self, world):
        self.world = world
        self.frame = 0

    def model_viewer(self):
        return FakeModelViewer(self.world)

    def layers_viewer(self):
        return FakeLayersViewer(self.world.key_frames)

    def get_current_frame(self, clamp=True):
        return self.frame

    def set_current_frame(self, frame):
        # Cascadeur clamps the playhead to the animation range.
        self.frame = max(self.world.boundary.first_frame, min(self.world.boundary.last_frame, int(frame)))


class FakeWorld:
    def __init__(self):
        self.reset()

    def reset(self):
        self.boundary = FakeBoundary()
        self.view = FakeViewScene(self.boundary)
        self.scene = FakeScene(self)
        self.has_auto_physics = True
        self.priority = set()
        self.key_frames = [0, 5, 10]
        self.poses = {
            frame: {alias: ([float(frame), 0.0, 0.0], [0.0, 0.0, 0.0]) for alias in physics.POSE_ALIASES}
            for frame in range(0, 11)
        }


WORLD = FakeWorld()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _state(**overrides):
    state = {
        "enabled": True,
        "frozen": False,
        "show_fulcrums": False,
        "only_key_fulcrums": False,
        "priority_frames": [],
        "source": "toolManager",
        "auto_physics_behaviours": 1,
        "current_frame": 0,
        "first_frame": 0,
        "last_frame": 10,
        "fps": 30.0,
        "gravity_per_frame": 980.0 / 900.0,
    }
    state.update(overrides)
    return state


def _rows(values):
    """Rows in _sample() shape for one object: each row is [position, rotation]."""
    return [[list(position), list(rotation)] for position, rotation in values]


class PhysicsTestCase(unittest.TestCase):
    def setUp(self):
        WORLD.reset()
        common.log_lines = []
        common.log_since = None
        ui.dialog_result = {"dialogs": []}
        ui.clicks = []
        ui.items = []
        dispatch.calls = []
        self.saved = {
            "auto_physics_manager": physics._auto_physics_manager,
            "action_states": physics._action_states,
            "gui_read": physics._gui_read,
            "gui_action": physics._gui_action,
            "settle": physics._settle,
            "call_action": physics._call_action,
            "sample": physics._sample,
            "dialogs": physics._dialogs,
            "new_commands": physics._new_commands,
            "job_status": physics._job_status,
            "find_running": physics._find_running_snap_job,
            "schedule": physics._schedule,
            "ensure_unlocked": physics._ensure_unlocked,
        }
        physics._auto_physics_manager = lambda: None
        physics._action_states = lambda: {}
        physics._call_action = lambda key: dispatch.calls.append(("action", key))
        physics._job_status = lambda job_id: "running"
        physics._find_running_snap_job = lambda state: state.get("job_id") or "job-test"
        physics._ensure_unlocked = lambda object_ids: None
        self.scheduled = []
        physics._schedule = lambda callback: self.scheduled.append(callback)
        physics._snap_state() and physics._clear_snap_state()

    def tearDown(self):
        for name, value in self.saved.items():
            setattr(physics, "_" + name if not name.startswith("_") else name, value)
        physics._clear_snap_state()
        sys.modules.pop(PACKAGE + ".tools.physics", None)


# ---------------------------------------------------------------------------
# Tool table and argument helpers
# ---------------------------------------------------------------------------


class ToolTableTests(PhysicsTestCase):
    def test_tool_table(self):
        by_name = {tool["name"]: tool for tool in physics.TOOLS}
        self.assertEqual(
            set(by_name),
            {
                "get_physics_state",
                "physics_enable",
                "physics_snap",
                "physics_freeze",
                "physics_set_priority_frame",
                "physics_show_fulcrums",
            },
        )
        for name, tool in by_name.items():
            self.assertTrue(tool["description"].strip(), name)
            self.assertEqual(tool["inputSchema"]["type"], "object", name)
            self.assertEqual(("fn" in tool) != ("handler" in tool), True, name)
        snap = by_name["physics_snap"]
        self.assertTrue(snap.get("background"))
        self.assertTrue(callable(snap.get("poll")))
        self.assertTrue(callable(snap.get("cancel")))
        self.assertEqual(snap["job_timeout"], 300)

    def test_bool_argument_is_strict(self):
        self.assertIs(physics._bool(True, "enabled"), True)
        self.assertIs(physics._bool(False, "enabled"), False)
        for value in ("true", 1, None):
            with self.assertRaises(physics._BadArgument):
                physics._bool(value, "enabled")

    def test_physics_enable_reports_bad_argument(self):
        result = physics.physics_enable(enabled="yes")
        self.assertFalse(result["ok"])
        self.assertIn("must be true or false", result["error"])
        self.assertIn("enabled=true", result["hint"])

    def test_physics_enable_defaults_to_false(self):
        physics._gui_read = lambda: _state(enabled=True)
        physics._settle = lambda predicate, timeout=3.0: _state(enabled=False)
        result = physics.physics_enable()
        self.assertTrue(result["ok"])
        self.assertFalse(result["enabled"])


# ---------------------------------------------------------------------------
# State reading
# ---------------------------------------------------------------------------


class FakeManager:
    def __init__(self, **values):
        self.values = values

    def property(self, name):
        return self.values.get(name)


class StateTests(PhysicsTestCase):
    def test_state_comes_from_tool_manager(self):
        physics._auto_physics_manager = lambda: FakeManager(
            isAutoPhysicsEnabled=True,
            isFrozenAutoPhysics=True,
            isAllFulcrumPoints=True,
            isOnlyKeyFulcrumPoints=False,
        )
        WORLD.priority = {3, 7}
        state = physics._read_state()
        self.assertTrue(state["enabled"])
        self.assertTrue(state["frozen"])
        self.assertTrue(state["show_fulcrums"])
        self.assertFalse(state["only_key_fulcrums"])
        self.assertEqual(state["priority_frames"], [3, 7])
        self.assertEqual(state["auto_physics_behaviours"], 1)
        self.assertEqual(state["current_frame"], 0)
        self.assertEqual(state["first_frame"], 0)
        self.assertEqual(state["last_frame"], 10)
        self.assertEqual(state["fps"], 30.0)
        self.assertAlmostEqual(state["gravity_per_frame"], 1.0888888888888888)
        self.assertEqual(state["source"], "toolManager")

    def test_state_falls_back_to_toolbar_buttons(self):
        physics._action_states = lambda: {physics.ACTION_ENABLE: True, physics.ACTION_FREEZE: False}
        state = physics._read_state()
        self.assertTrue(state["enabled"])
        self.assertFalse(state["frozen"])
        self.assertIsNone(state["show_fulcrums"])
        self.assertEqual(state["source"], "toolbar buttons")

    def test_state_is_unavailable_without_a_manager_or_buttons(self):
        state = physics._read_state()
        self.assertIsNone(state["enabled"])
        self.assertEqual(state["source"], "unavailable")

    def test_priority_frames_empty_without_behaviours(self):
        WORLD.has_auto_physics = False
        self.assertEqual(physics._priority_data_ids(), [])
        self.assertEqual(physics._priority_frames(), [])

    def test_priority_frames_are_read_from_animation_data(self):
        WORLD.priority = {2, 9}
        self.assertEqual(physics._priority_frames(), [2, 9])

    def test_manager_detection_requires_real_boolean_properties(self):
        class GenericManager:
            def property(self, name):
                return None

        self.assertFalse(physics._manager_has_physics_state(GenericManager()))
        self.assertTrue(physics._manager_has_physics_state(FakeManager(isAutoPhysicsEnabled=False)))
        self.assertTrue(physics._manager_has_physics_state(FakeManager(isAutoPhysicsEnabled=True)))

    def test_get_physics_state_wraps_the_state(self):
        physics._auto_physics_manager = lambda: FakeManager(isAutoPhysicsEnabled=True)
        result = physics.get_physics_state()
        self.assertTrue(result["ok"])
        self.assertTrue(result["enabled"])


# ---------------------------------------------------------------------------
# Pose comparison
# ---------------------------------------------------------------------------


class PoseTests(PhysicsTestCase):
    def test_vector_handles_sequences_and_rotations(self):
        self.assertEqual(physics._vector([1, 2.5, 3]), [1.0, 2.5, 3.0])
        self.assertEqual(physics._vector(4), [4.0])
        self.assertIsNone(physics._vector(None))

        class Rotation:
            def to_euler_angles_x_y_z(self):
                return [0.0, 1.5707963267948966, 0.0]

        values = physics._vector(Rotation())
        self.assertAlmostEqual(values[1], 90.0)

    def test_values_match_respects_tolerance(self):
        self.assertTrue(physics._values_match([0.0, 1.0], [0.0005, 1.0]))
        self.assertFalse(physics._values_match([0.0, 1.0], [0.01, 1.0]))
        self.assertTrue(physics._values_match(None, None))
        self.assertFalse(physics._values_match(None, [0.0]))

    def test_rows_match(self):
        left = _rows([((0, 0, 0), (0, 0, 0))])
        right = _rows([((0, 0, 0), (0, 0, 0))])
        self.assertTrue(physics._rows_match(left, right))
        right[0][0][0] = 1.0
        self.assertFalse(physics._rows_match(left, right))

    def test_changed_summary_reports_frames_and_max_delta(self):
        objects = [{"alias": "pelvis", "name": "pelvis_MainPoint"}]
        before = _rows([((0, 0, 0), (0, 0, 0)), ((0, 0, 0), (0, 0, 0)), ((0, 0, 0), (0, 0, 0))])
        after = _rows([((0, 0, 0), (0, 0, 0)), ((3, 4, 0), (0, 0, 0)), ((0.0001, 0, 0), (0, 0, 0))])
        summary = physics._changed_summary(before, after, [0, 1, 2], objects)
        self.assertEqual(summary["changed_frames"], [1])
        self.assertEqual(summary["changed_first"], 1)
        self.assertEqual(summary["changed_last"], 1)
        self.assertEqual(summary["changed_frame_count"], 1)
        self.assertAlmostEqual(summary["max_delta_cm"], 5.0)
        self.assertAlmostEqual(summary["max_rotation_delta_deg"], 0.0)
        self.assertEqual(
            summary["objects"],
            [{"alias": "pelvis", "max_position_delta_cm": 5.0, "max_rotation_delta_deg": 0.0}],
        )

    def test_changed_summary_with_nothing_changed(self):
        objects = [{"alias": "pelvis", "name": "pelvis_MainPoint"}]
        rows = _rows([((0, 0, 0), (0, 0, 0))])
        summary = physics._changed_summary(rows, rows, [0], objects)
        self.assertEqual(summary["changed_frames"], [])
        self.assertIsNone(summary["changed_first"])
        self.assertEqual(summary["changed_frame_count"], 0)

    def test_probe_frames_include_keys_and_bounds(self):
        probe = physics._probe_frames(0, 100)
        self.assertEqual(probe[0], 0)
        self.assertEqual(probe[-1], 100)
        self.assertLessEqual(len(probe), physics.PROBE_FRAMES)
        self.assertIn(5, probe)
        self.assertIn(10, probe)

    def test_sample_reads_transform_data(self):
        objects = [{"alias": "pelvis", "name": "pelvis_MainPoint", "position": "pos:pelvis", "rotation": "rot:pelvis"}]
        rows = physics._sample(objects, [0, 5])
        self.assertEqual(rows[0][0], [0.0, 0.0, 0.0])
        self.assertEqual(rows[1][0], [5.0, 0.0, 0.0])


# ---------------------------------------------------------------------------
# Setters (HTTP-thread orchestration)
# ---------------------------------------------------------------------------


class SetterTests(PhysicsTestCase):
    def test_set_flag_is_idempotent(self):
        physics._gui_read = lambda: _state(enabled=True)
        result = physics._set_flag("enabled", physics.ACTION_ENABLE, True, "enabled")
        self.assertTrue(result["ok"])
        self.assertFalse(result["changed"])
        self.assertEqual(dispatch.calls, [])

    def test_set_flag_runs_one_action_and_confirms(self):
        reads = [_state(enabled=False), _state(enabled=True)]

        def gui_read():
            return reads.pop(0) if len(reads) > 1 else reads[0]

        physics._gui_read = gui_read
        physics._settle = lambda predicate, timeout=3.0: _state(enabled=True)
        result = physics._set_flag("enabled", physics.ACTION_ENABLE, True, "enabled")
        self.assertTrue(result["ok"])
        self.assertTrue(result["changed"])
        self.assertIn(("action", physics.ACTION_ENABLE), dispatch.calls)

    def test_freeze_requires_physics_to_be_on(self):
        physics._gui_read = lambda: _state(enabled=False)
        result = physics.physics_freeze(enabled=True)
        self.assertFalse(result["ok"])
        self.assertIn("nothing to freeze", result["error"])

    def test_show_fulcrums_works_while_physics_is_off(self):
        physics._gui_read = lambda: _state(enabled=False, show_fulcrums=False)
        physics._settle = lambda predicate, timeout=3.0: _state(enabled=False, show_fulcrums=True)
        result = physics.physics_show_fulcrums(enabled=True)
        self.assertTrue(result["ok"])
        self.assertTrue(result["changed"])
        self.assertTrue(result["show_fulcrums"])

    def test_show_fulcrums_refuses_while_physics_is_on(self):
        physics._gui_read = lambda: _state(enabled=True, show_fulcrums=False)
        result = physics.physics_show_fulcrums(enabled=True)
        self.assertFalse(result["ok"])
        self.assertIn("simultaneously", result["error"])

    def test_set_flag_fails_without_state(self):
        physics._gui_read = lambda: _state(enabled=None, source="unavailable")
        result = physics._set_flag("enabled", physics.ACTION_ENABLE, True, "enabled")
        self.assertFalse(result["ok"])
        self.assertIn("does not report", result["error"])

    def test_set_flag_fails_when_the_action_does_not_take(self):
        physics._gui_read = lambda: _state(enabled=False)
        physics._settle = lambda predicate, timeout=3.0: _state(enabled=False)
        result = physics._set_flag("enabled", physics.ACTION_ENABLE, True, "enabled")
        self.assertFalse(result["ok"])
        self.assertIn("did not change", result["error"])

    def test_priority_frame_is_idempotent_when_already_set(self):
        physics._gui_read = lambda: _state(priority_frames=[5])
        result = physics.physics_set_priority_frame(frame=5)
        self.assertTrue(result["ok"])
        self.assertFalse(result["changed"])
        self.assertTrue(result["priority"])
        self.assertEqual(dispatch.calls, [])

    def test_priority_frame_moves_the_playhead_and_restores_it(self):
        physics._gui_read = lambda: _state(priority_frames=[])
        physics._settle = lambda predicate, timeout=3.0: _state(priority_frames=[5])
        WORLD.scene.frame = 3
        result = physics.physics_set_priority_frame(frame="5")
        self.assertTrue(result["ok"])
        self.assertTrue(result["changed"])
        self.assertEqual(result["playhead_restored"], 3)
        self.assertEqual(WORLD.scene.frame, 3)
        self.assertIn(("action", physics.ACTION_PRIORITY), dispatch.calls)

    def test_priority_frame_fails_when_cascadeur_rejects_it(self):
        physics._gui_read = lambda: _state(priority_frames=[])
        physics._settle = lambda predicate, timeout=3.0: _state(priority_frames=[])
        common.log_lines = ["Can't set priority frame on non key frame"]
        result = physics.physics_set_priority_frame(frame=6)
        self.assertFalse(result["ok"])
        self.assertIn("did not mark", result["error"])
        self.assertIn("non key frame", " ".join(result["log"]))

    def test_priority_frame_requires_physics_enabled(self):
        physics._gui_read = lambda: _state(enabled=False)
        result = physics.physics_set_priority_frame(frame=5)
        self.assertFalse(result["ok"])
        self.assertIn("Physics Assistant is off", result["error"])

    def test_priority_frame_refuses_unreadable_priority_frames(self):
        physics._gui_read = lambda: _state(priority_frames=None)
        result = physics.physics_set_priority_frame(frame=5)
        self.assertFalse(result["ok"])
        self.assertIn("Could not read", result["error"])
        self.assertEqual(dispatch.calls, [])

    def test_priority_frame_rejects_a_frame_outside_the_animation_range(self):
        physics._gui_read = lambda: _state(priority_frames=[])
        WORLD.scene.frame = 3
        result = physics.physics_set_priority_frame(frame=500)
        self.assertFalse(result["ok"])
        self.assertIn("outside the animation range", result["error"])
        self.assertEqual(WORLD.scene.frame, 3)
        self.assertNotIn(("action", physics.ACTION_PRIORITY), dispatch.calls)

    def test_priority_frame_rejects_a_frame_past_the_keyed_content(self):
        physics._gui_read = lambda: _state(priority_frames=[])
        WORLD.boundary.last_frame = 20
        result = physics.physics_set_priority_frame(frame=15)
        self.assertFalse(result["ok"])
        self.assertIn("keyed animation", result["error"])
        self.assertEqual(WORLD.scene.frame, 0)
        self.assertNotIn(("action", physics.ACTION_PRIORITY), dispatch.calls)

    def test_priority_frame_restores_the_playhead_when_cascadeur_clamps(self):
        physics._gui_read = lambda: _state(priority_frames=[])
        saved = timeline.require_keyed_range
        timeline.require_keyed_range = lambda scene, start=None, end=None: (5, 5)
        self.addCleanup(setattr, timeline, "require_keyed_range", saved)
        WORLD.scene.frame = 2
        original_set = WORLD.scene.set_current_frame
        WORLD.scene.set_current_frame = lambda frame: setattr(WORLD.scene, "frame", min(int(frame), 3))
        self.addCleanup(setattr, WORLD.scene, "set_current_frame", original_set)
        result = physics.physics_set_priority_frame(frame=5)
        self.assertFalse(result["ok"])
        self.assertIn("playhead on frame 3", result["error"])
        self.assertEqual(result["playhead_restored"], 2)
        self.assertEqual(WORLD.scene.frame, 2)
        self.assertNotIn(("action", physics.ACTION_PRIORITY), dispatch.calls)

    def test_priority_frame_restores_the_playhead_when_unconfirmed(self):
        physics._gui_read = lambda: _state(priority_frames=[])
        physics._settle = lambda predicate, timeout=3.0: _state(priority_frames=[])
        WORLD.scene.frame = 2
        result = physics.physics_set_priority_frame(frame=5)
        self.assertFalse(result["ok"])
        self.assertEqual(result["playhead_restored"], 2)
        self.assertEqual(WORLD.scene.frame, 2)

    def test_priority_frame_checks_layer_locks(self):
        physics._gui_read = lambda: _state(priority_frames=[])
        physics._ensure_unlocked = lambda object_ids: _fail("layer locked", "unlock it")
        result = physics.physics_set_priority_frame(frame=5)
        self.assertFalse(result["ok"])
        self.assertIn("layer locked", result["error"])
        self.assertEqual(dispatch.calls, [])

    def test_priority_frame_restores_the_playhead_when_the_action_raises(self):
        physics._gui_read = lambda: _state(priority_frames=[])
        physics._call_action = lambda key: (_ for _ in ()).throw(RuntimeError("action boom"))
        WORLD.scene.frame = 2
        result = physics.physics_set_priority_frame(frame=5)
        self.assertFalse(result["ok"])
        self.assertIn("action boom", result["error"])
        self.assertEqual(WORLD.scene.frame, 2)


# ---------------------------------------------------------------------------
# Snap dialog handling
# ---------------------------------------------------------------------------


def _dialog(texts, buttons):
    return {
        "kind": "dialog",
        "type": "Dialog",
        "texts": list(texts),
        "buttons": [{"ref": "d0:" + str(index), "text": text} for index, text in enumerate(buttons)],
    }


class DialogTests(PhysicsTestCase):
    def test_snap_dialogs_are_recognised(self):
        dialogs = [
            _dialog(["Which frames should AutoPhysics results be applied to?"], ["All Frames", "Only keys"]),
            _dialog(["Unrelated"], ["OK"]),
        ]
        self.assertEqual(len(physics._snap_dialogs(dialogs)), 1)

    def test_error_dialogs_are_recognised(self):
        dialogs = [_dialog(["AutoPhysics error: Wrong cm"], ["OK"])]
        self.assertEqual(len(physics._error_dialogs(dialogs)), 1)

    def test_handler_clicks_all_frames_then_apply(self):
        ui.dialog_result = {
            "dialogs": [
                _dialog(
                    ["Which frames should AutoPhysics results be applied to?"],
                    ["All Frames", "Only keys", "Apply AutoPhysics", "Cancel"],
                )
            ]
        }
        state = {}
        first = physics._handle_snap_dialogs(state)
        self.assertEqual(first, ["All Frames"])
        self.assertTrue(state["all_frames_clicked"])
        self.assertEqual(state["apply_to"], "all_frames")
        state["last_apply_click"] = 0.0
        second = physics._handle_snap_dialogs(state)
        self.assertEqual(second, ["Apply AutoPhysics"])
        self.assertIsNotNone(state["apply_clicked_at"])
        self.assertEqual(ui.clicks, ["d0:0", "d0:2"])

    def test_handler_clicks_yes_for_missing_keyframes(self):
        ui.dialog_result = {
            "dialogs": [
                _dialog(
                    ["The layer is missing a keyframe for AutoPhysics on frame 5. Would you like to add it?"],
                    ["Yes", "No"],
                )
            ]
        }
        state = {}
        self.assertEqual(physics._handle_snap_dialogs(state), ["Yes"])

    def test_handler_records_error_dialogs_without_clicking(self):
        ui.dialog_result = {"dialogs": [_dialog(["AutoPhysics error: no working Autophysics"], ["OK"])]}
        state = {}
        physics._handle_snap_dialogs(state)
        self.assertIn("AutoPhysics error", state["error_dialog"])
        self.assertEqual(ui.clicks, [])


# ---------------------------------------------------------------------------
# Background snap job
# ---------------------------------------------------------------------------


class SnapJobTests(PhysicsTestCase):
    def test_snap_refuses_when_physics_is_off(self):
        physics._gui_read = lambda: _state(enabled=False)
        physics._auto_physics_manager = lambda: FakeManager(isAutoPhysicsEnabled=False)
        result = physics.physics_snap()
        self.assertFalse(result["ok"])
        self.assertIn("Physics Assistant is off", result["error"])

    def test_snap_refuses_a_one_frame_animation(self):
        # The Cascy shape: the timeline is long, but only frame 0 has a key.
        WORLD.boundary.last_frame = 100
        WORLD.key_frames = [0]
        physics._auto_physics_manager = lambda: FakeManager(isAutoPhysicsEnabled=True)
        result = physics.physics_snap()
        self.assertFalse(result["ok"])
        self.assertIn("at least two keyed frames", result["error"])

    def test_snap_uses_the_keyed_range_not_the_timeline_range(self):
        WORLD.boundary.last_frame = 20
        for frame in range(11, 21):
            WORLD.poses[frame] = {alias: ([float(frame), 0.0, 0.0], [0.0, 0.0, 0.0]) for alias in physics.POSE_ALIASES}
        physics._auto_physics_manager = lambda: FakeManager(isAutoPhysicsEnabled=True)
        result = physics.physics_snap()
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["frames"], {"first": 0, "last": 10})
        state = physics._snap_state()
        self.assertEqual(state["frames"][-1], 10)
        physics._clear_snap_state()

    def test_snap_refuses_a_second_job(self):
        physics._set_snap_state({"started": physics._now(), "started_at": time.time(), "job_id": "job-1"})
        result = physics.physics_snap()
        self.assertFalse(result["ok"])
        self.assertIn("still running", result["error"])
        self.assertEqual(result["job_id"], "job-1")

    def test_snap_records_the_pose_and_starts_the_action(self):
        physics._auto_physics_manager = lambda: FakeManager(isAutoPhysicsEnabled=True)
        result = physics.physics_snap()
        self.assertTrue(result["ok"])
        self.assertTrue(result["started"])
        self.assertEqual(result["frames"], {"first": 0, "last": 10})
        state = physics._snap_state()
        self.assertIsNotNone(state)
        self.assertEqual(state["frames"], list(range(0, 11)))
        self.assertEqual(len(state["before"]), 11)
        self.assertNotIn(("action", physics.ACTION_SNAP), dispatch.calls)
        self.assertEqual(len(self.scheduled), 1)
        self.scheduled.pop(0)()
        self.assertIn(("action", physics.ACTION_SNAP), dispatch.calls)
        self.assertTrue(state["action_finished"])
        physics._clear_snap_state()

    def test_poll_reports_progress_while_nothing_happens(self):
        physics._set_snap_state(self._state(now_offset=2.0))
        physics._sample = lambda objects, frames: _rows([((0, 0, 0), (0, 0, 0)) for _ in frames])
        update = physics._snap_poll()
        self.assertFalse(update["done"])
        self.assertGreater(update["progress"], 0.0)
        self.assertLess(update["progress"], 1.0)
        physics._clear_snap_state()

    def test_poll_fails_after_the_no_change_timeout(self):
        physics._set_snap_state(self._state(now_offset=physics.NO_CHANGE_SECONDS + 5))
        physics._sample = lambda objects, frames: _rows([((0, 0, 0), (0, 0, 0)) for _ in frames])
        update = physics._snap_poll()
        self.assertFalse(update["ok"])
        self.assertIn("did not change", update["error"])
        self.assertIsNone(physics._snap_state())

    def test_poll_fails_on_an_autophysics_error_dialog(self):
        state = self._state(now_offset=2.0)
        physics._set_snap_state(state)
        physics._sample = lambda objects, frames: _rows([((0, 0, 0), (0, 0, 0)) for _ in frames])
        ui.dialog_result = {"dialogs": [_dialog(["AutoPhysics error: Wrong cm"], ["OK"])]}
        update = physics._snap_poll()
        self.assertFalse(update["ok"])
        self.assertIn("AutoPhysics error", update["error"])

    def test_poll_fails_when_the_dialog_never_closes(self):
        state = self._state(now_offset=physics.DIALOG_SECONDS + 5)
        physics._set_snap_state(state)
        ui.dialog_result = {
            "dialogs": [_dialog(["Which frames should AutoPhysics results be applied to?"], ["All Frames"])]
        }
        physics._sample = lambda objects, frames: _rows([((0, 0, 0), (0, 0, 0)) for _ in frames])
        update = physics._snap_poll()
        self.assertFalse(update["ok"])
        self.assertIn("frame-choice dialog", update["error"])

    def test_poll_waits_for_a_stable_pose_then_finishes(self):
        before = _rows([((0, 0, 0), (0, 0, 0)), ((0, 0, 0), (0, 0, 0))])
        after = _rows([((0, 0, 0), (0, 0, 0)), ((10, 0, 0), (0, 0, 0))])
        state = self._state(now_offset=5.0)
        state.update(
            {
                "frames": [0, 1],
                "probe": [0, 1],
                "before": before,
                "probe_before": before,
                "last_probe": after,
                "probe_stable": physics.STABLE_POLLS,
                "first_change": time.monotonic() - 5.0,
                "apply_clicked_at": time.monotonic() - 5.0,
                "commands": ["Apply AutoPhysics"],
                "pending_after": after,
            }
        )
        physics._set_snap_state(state)
        physics._sample = lambda objects, frames: after if frames == [0, 1] else after
        physics._new_commands = lambda offset: ["Apply AutoPhysics"]
        update = physics._snap_poll()
        self.assertTrue(update["done"])
        self.assertEqual(update["progress"], 1.0)
        result = update["result"]
        self.assertTrue(result["ok"])
        self.assertEqual(result["changed_first"], 1)
        self.assertEqual(result["changed_last"], 1)
        self.assertEqual(result["changed_frame_count"], 1)
        self.assertEqual(result["changed_frames"], [1])
        self.assertAlmostEqual(result["max_delta_cm"], 10.0)
        self.assertIn("undo", result)
        self.assertIsNone(physics._snap_state())

    def test_poll_takes_a_second_scan_when_the_pose_is_still_moving(self):
        before = _rows([((0, 0, 0), (0, 0, 0))])
        moving = _rows([((5, 0, 0), (0, 0, 0))])
        state = self._state(now_offset=5.0)
        state.update(
            {
                "frames": [0],
                "probe": [0],
                "before": before,
                "probe_before": before,
                "last_probe": moving,
                "probe_stable": physics.STABLE_POLLS,
                "first_change": time.monotonic() - 5.0,
                "commands": ["Apply AutoPhysics"],
                "pending_after": _rows([((1, 0, 0), (0, 0, 0))]),
            }
        )
        physics._set_snap_state(state)
        physics._sample = lambda objects, frames: moving
        physics._new_commands = lambda offset: ["Apply AutoPhysics"]
        update = physics._snap_poll()
        self.assertFalse(update["done"])
        self.assertGreaterEqual(update["progress"], 0.95)
        self.assertIsNotNone(physics._snap_state())

    def test_poll_without_state_fails_cleanly(self):
        update = physics._snap_poll()
        self.assertFalse(update["ok"])
        self.assertIn("lost its state", update["error"])

    def test_cancel_clears_the_state(self):
        physics._set_snap_state(self._state(now_offset=1.0))
        physics._snap_cancel()
        self.assertIsNone(physics._snap_state())

    def test_expired_state_is_released(self):
        physics._set_snap_state(self._state(now_offset=physics.SNAP_STATE_TTL_S + 1))
        self.assertIsNone(physics._snap_state())
        self.assertIsNone(getattr(sys, physics.SNAP_STATE_KEY, None))

    def test_state_is_released_when_the_job_is_not_running(self):
        physics._job_status = lambda job_id: "failed"
        physics._set_snap_state(self._state())
        self.assertIsNone(physics._snap_state())
        self.assertIsNone(getattr(sys, physics.SNAP_STATE_KEY, None))

    def test_state_is_released_when_the_job_is_gone(self):
        physics._job_status = lambda job_id: None
        physics._set_snap_state(self._state())
        self.assertIsNone(physics._snap_state())

    def test_state_without_a_job_id_finds_the_running_job(self):
        state = self._state()
        state["job_id"] = None
        physics._find_running_snap_job = lambda value: "job-7"
        physics._set_snap_state(state)
        active = physics._snap_state()
        self.assertIs(active, state)
        self.assertEqual(active["job_id"], "job-7")

    def test_poll_exception_clears_the_state(self):
        physics._set_snap_state(self._state())
        saved = physics._snap_poll_state

        def boom(value):
            raise RuntimeError("poll boom")

        physics._snap_poll_state = boom
        self.addCleanup(setattr, physics, "_snap_poll_state", saved)
        with self.assertRaises(RuntimeError):
            physics._snap_poll()
        self.assertIsNone(getattr(sys, physics.SNAP_STATE_KEY, None))

    def test_poll_action_error_fails_the_job(self):
        state = self._state()
        state["action_error"] = "Traceback (most recent call last):\nValueError: action boom"
        physics._set_snap_state(state)
        update = physics._snap_poll()
        self.assertFalse(update["ok"])
        self.assertIn("Snap action raised", update["error"])
        self.assertIn("ValueError: action boom", update["traceback"])

    def test_poll_waits_for_the_scheduled_action(self):
        state = self._state()
        state["action_finished"] = False
        physics._set_snap_state(state)
        physics._sample = lambda objects, frames: self.fail("the poll sampled before the action ran")
        update = physics._snap_poll()
        self.assertFalse(update["done"])
        self.assertEqual(update["progress"], 0.05)

    def test_cancelled_snap_does_not_run_the_scheduled_action(self):
        physics._auto_physics_manager = lambda: FakeManager(isAutoPhysicsEnabled=True)
        result = physics.physics_snap()
        self.assertTrue(result["ok"], result)
        physics._snap_cancel()
        self.scheduled.pop(0)()
        self.assertNotIn(("action", physics.ACTION_SNAP), dispatch.calls)

    def test_snap_checks_layer_locks(self):
        physics._ensure_unlocked = lambda object_ids: _fail("layer locked", "unlock it")
        result = physics.physics_snap()
        self.assertFalse(result["ok"])
        self.assertIn("layer locked", result["error"])
        self.assertEqual(self.scheduled, [])

    def _state(self, now_offset=0.0):
        return {
            "started": physics._now() - now_offset,
            "started_at": time.time(),
            "job_id": "job-test",
            "action_finished": True,
            "action_error": None,
            "enabled_before": True,
            "objects": [
                {"alias": "pelvis", "name": "pelvis_MainPoint", "position": "pos:pelvis", "rotation": "rot:pelvis"}
            ],
            "frames": [0, 1],
            "probe": [0, 1],
            "before": _rows([((0, 0, 0), (0, 0, 0)), ((0, 0, 0), (0, 0, 0))]),
            "probe_before": _rows([((0, 0, 0), (0, 0, 0)), ((0, 0, 0), (0, 0, 0))]),
            "last_probe": _rows([((0, 0, 0), (0, 0, 0)), ((0, 0, 0), (0, 0, 0))]),
            "probe_stable": 0,
            "first_change": None,
            "apply_clicked_at": None,
            "last_apply_click": 0.0,
            "commands": [],
            "dialog_actions": [],
            "error_dialog": None,
            "phase": "snap",
            "pending_after": None,
            "log_offset": 0,
        }


if __name__ == "__main__":
    unittest.main()
