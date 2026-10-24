"""Unit tests for the pose tools (T16); no Cascadeur needed.

cascadeur_mcp_plus/tools/pose.py is loaded inside a throw-away package with fake common,
dispatch, rig, timeline, autoposing and layers modules, plus a fake Cascadeur scene model
(objects, Transform data, data viewer/editor, scene updater). That lets the tests check the whole
set_pose flow without Cascadeur: argument validation, layer locks, the AutoPosing order (lock
before write, update inside the same undo step), offsets, rotations, keys, the read-back
differences and the undo steps. The fake package never touches sys.modules["cascadeur_mcp_plus"],
so it cannot disturb the other unit tests. tests/live/test_pose.py checks the same tools against
the running Cascadeur.

Run: python -m unittest discover -s tests -v
"""

from __future__ import annotations

import importlib.util
import math
import os
import sys
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGE_DIR = os.path.join(ROOT, "cascadeur_mcp_plus")
TOOLS_DIR = os.path.join(PACKAGE_DIR, "tools")
PKG = "t16_pose_test_pkg"


# ---------------------------------------------------------------------------
# Fake Cascadeur scene model
# ---------------------------------------------------------------------------


class FakeQuaternion:
    def __init__(self, w=1.0, x=0.0, y=0.0, z=0.0):
        self._values = (w, x, y, z)

    def w(self):
        return self._values[0]

    def x(self):
        return self._values[1]

    def y(self):
        return self._values[2]

    def z(self):
        return self._values[3]


def _euler_to_quaternion(degrees):
    """XYZ Euler degrees -> (w, x, y, z) for q = qz * qy * qx (the fake csc convention)."""
    x, y, z = [math.radians(float(value)) / 2.0 for value in degrees]
    cx, sx = math.cos(x), math.sin(x)
    cy, sy = math.cos(y), math.sin(y)
    cz, sz = math.cos(z), math.sin(z)
    return (
        cz * cy * cx + sz * sy * sx,
        cz * cy * sx - sz * sy * cx,
        cz * sy * cx + sz * cy * sx,
        sz * cy * cx - cz * sy * sx,
    )


def _quaternion_to_euler(quaternion):
    """(w, x, y, z) -> XYZ Euler degrees, the inverse of _euler_to_quaternion."""
    w, x, y, z = [float(value) for value in quaternion]
    roll = math.atan2(2.0 * (w * x + y * z), 1.0 - 2.0 * (x * x + y * y))
    pitch = math.asin(max(-1.0, min(1.0, 2.0 * (w * y - z * x))))
    yaw = math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))
    return [math.degrees(roll), math.degrees(pitch), math.degrees(yaw)]


class FakeRotation:
    """A rotation data value: Euler XYZ angles stored in degrees."""

    def __init__(self, degrees):
        self.degrees = [float(value) for value in degrees]

    def to_euler_angles_x_y_z(self):
        return [math.radians(value) for value in self.degrees]

    def to_quaternion(self):
        return FakeQuaternion(*_euler_to_quaternion(self.degrees))


class FakeCscRotation:
    """A csc.math.Rotation: quaternion-backed, Euler read through the same convention."""

    def __init__(self, quaternion):
        self.quaternion = tuple(float(value) for value in quaternion)

    @property
    def degrees(self):
        return _quaternion_to_euler(self.quaternion)

    def to_euler_angles_x_y_z(self):
        return [math.radians(value) for value in self.degrees]

    def to_quaternion(self):
        return FakeQuaternion(*self.quaternion)


class NullId:
    def is_null(self):
        return True


class DataId:
    def __init__(self, object_name, data_name):
        self.object_name = object_name
        self.data_name = data_name

    def __str__(self):
        return f"{self.object_name}.{self.data_name}"

    def is_null(self):
        return False


class FakeBehaviourViewer:
    def __init__(self, world):
        self.world = world

    def get_behaviour_by_name(self, object_id, name):
        entry = self.world.objects.get(object_id)
        if name != "Transform" or entry is None or "data" not in entry:
            return NullId()
        return ("Transform", object_id)

    def get_behaviour_data(self, transform, name):
        if not isinstance(transform, tuple):
            return NullId()
        _kind, object_id = transform
        data_id = self.world.objects[object_id]["data"].get(name)
        return NullId() if data_id is None else data_id


class FakeDataViewer:
    def __init__(self, world):
        self.world = world

    def get_data_value(self, data_id, frame):
        if data_id not in self.world.values:
            raise RuntimeError(f"no data for {data_id}")
        if self.world.max_frame is not None and int(frame) > self.world.max_frame:
            # Cascadeur refuses frames past a data channel's range.
            raise RuntimeError(
                f"getDataValue: cannot get value on frame {frame} for data {data_id}, "
                f"last frame in animation is {self.world.max_frame}"
            )
        value = self.world.values[data_id]
        if isinstance(value, FakeRotation):
            return value
        if isinstance(value, FakeCscRotation):
            return value
        return list(value)


class FakeModelViewer:
    def __init__(self, world):
        self.world = world
        self._behaviour = FakeBehaviourViewer(world)
        self._data = FakeDataViewer(world)

    def behaviour_viewer(self):
        return self._behaviour

    def data_viewer(self):
        return self._data

    def get_object_name(self, object_id):
        if object_id not in self.world.objects:
            raise RuntimeError("no such object")
        return self.world.objects[object_id]["name"]


class FakeDataEditor:
    def __init__(self, world):
        self.world = world

    def set_data_value(self, data_id, frame, value):
        self.world.writes.append({"data": data_id, "frame": frame, "value": value})


class FakeLayersEditor:
    def __init__(self, world):
        self.world = world

    def normalize_sections(self, scene):
        self.called = True

    def set_section(self, section, pos, layer_id):
        self.world.sections.append({"pos": int(pos), "layer": str(layer_id)})
        # An empty section past the last frame extends the channels up to the frame before it.
        if self.world.max_frame is not None:
            self.world.max_frame = max(self.world.max_frame, int(pos) - 1)


class FakeModelEditor:
    def __init__(self, world):
        self.world = world
        self._data_editor = FakeDataEditor(world)
        self._layers_editor = FakeLayersEditor(world)

    def data_editor(self):
        return self._data_editor

    def layers_editor(self):
        return self._layers_editor

    def fit_animation_size_by_layers(self):
        self.world.fit_calls += 1

    def set_fixed_interpolation_if_need(self, actuals, frame):
        self.world.interpolation_calls.append({"actuals": set(actuals), "frame": frame})


class FakeSceneUpdater:
    def __init__(self, world):
        self.world = world

    def generate_update(self):
        self.world.generate_update_calls += 1

    def run_update(self, data_ids, frame):
        self.world.updates.append({"data": set(data_ids), "frame": frame})
        if self.world.max_frame is not None:
            self.world.max_frame = max(self.world.max_frame, int(frame))
        for write in self.world.writes:
            value = write["value"]
            self.world.values[write["data"]] = value if not isinstance(value, list) else list(value)


class FakeSession:
    def __init__(self, world):
        self.world = world

    def set_current_frame(self, frame):
        self.world.session_frames.append(frame)
        # Cascadeur's AutoPosingTool.update moves the scene playhead to the session frame.
        self.world.playhead = frame


class FakeLayerId:
    def __init__(self, name):
        self.name = name

    def is_null(self):
        return False

    def __str__(self):
        return self.name

    def __eq__(self, other):
        return isinstance(other, FakeLayerId) and other.name == self.name

    def __hash__(self):
        return hash(self.name)


class FakeNullLayerId:
    def is_null(self):
        return True


class FakeLayersViewer:
    def __init__(self, world):
        self.world = world

    def all_layer_ids(self):
        return [FakeLayerId("layer1"), FakeLayerId("layer2")]

    def layer_id_by_obj_id_or_null(self, object_id):
        name = self.world.object_layer.get(object_id)
        return FakeLayerId(name) if name else FakeNullLayerId()

    def layer(self, layer_id):
        locked = bool(self.world.locked_layers.get(str(layer_id)))
        return types.SimpleNamespace(is_locked=lambda: locked)


class FakeViewScene:
    def __init__(self, world):
        self.world = world

    def layers_viewer(self):
        return self.world.layers_viewer

    def animation_boundary(self):
        return types.SimpleNamespace(first_frame=0, last_frame=100, first_visible_frame=0, last_visible_frame=100)


class FakeScene:
    def __init__(self, world):
        self.world = world
        self._model_viewer = FakeModelViewer(world)
        self._model_editor = FakeModelEditor(world)
        self._scene_updater = FakeSceneUpdater(world)

    def model_viewer(self):
        return self._model_viewer

    def layers_viewer(self):
        return self.world.layers_viewer

    def get_current_frame(self, clamp_animation=True):
        return self.world.playhead

    def set_current_frame(self, frame):
        self.world.playhead = frame

    def modify_update_with_session(self, name, fn):
        self.world.edits.append(name)
        if not self.world.modify_result:
            return False
        fn(self._model_editor, None, self._scene_updater, FakeSession(self.world))
        return True


def _tool_error(message):
    """An instance of the fake package's ToolError (loaded with the pose module)."""
    errors = sys.modules.get(f"{PKG}.errors")
    return getattr(errors, "ToolError", RuntimeError)(message)


class FakeAutoPosing:
    """The autoposing helper module surface pose.py uses, recording the order of calls."""

    ANSWER_CHOICES = ("yes", "no", "cancel")
    AUTOLOCK_ACTION = "AutoPosingTool.AutoUnlock"

    def __init__(self):
        self.reset()

    def reset(self):
        self.calls = []
        self.enabled = True
        self.known = True
        self.stuck_off = False
        self.lock_fails = False
        self.dialog = {"found": False, "answer": "yes", "closed": True}
        self.effect = None
        self.update_error = None

    def answer_argument(self, answer=None):
        text = "yes" if answer is None or str(answer).strip() == "" else str(answer).strip().lower()
        if text not in self.ANSWER_CHOICES:
            raise _tool_error(f"answer must be one of: {', '.join(self.ANSWER_CHOICES)} (got {answer!r}).")
        return text

    def state(self):
        return {
            "enabled": self.enabled if self.known else None,
            "known": self.known,
            "locked_controllers": None,
            "locked_known": False,
        }

    def set_enabled(self, enabled):
        before = self.enabled
        if self.known and not self.stuck_off:
            self.enabled = bool(enabled)
        self.calls.append(("enable", bool(enabled)))
        return {"before": before, "after": self.enabled, "changed": before != self.enabled}

    def lock_pose(self, frame, moved_ids=None):
        self.calls.append(("lock", frame, list(moved_ids or [])))
        if self.lock_fails:
            raise _tool_error("lock failed")
        return {"action": self.AUTOLOCK_ACTION, "undo": "AutoPosing.Autolock"}

    def run_update(self, scene=None, frame=None, *, session=None, name="autoposing_update"):
        self.calls.append(("update", frame, session is not None))
        if self.update_error is not None:
            raise _tool_error(self.update_error)
        if session is not None:
            session.set_current_frame(frame)
        if self.effect is not None:
            self.effect()
        return {"frame": frame, "undo": None if session is not None else "MCP: " + name, "method": "editor"}

    def answer_now(self, answer="yes"):
        self.calls.append(("answer_now", answer))
        return None

    def finish_dialogs(self, answer="yes", timeout=2.0, interval=0.15):
        self.calls.append(("dialogs", answer, timeout))
        return dict(self.dialog)

    def kinds(self):
        return [call[0] for call in self.calls]


class World:
    def __init__(self):
        self.playhead = 7
        self.objects = {}
        self.values = {}
        self.writes = []
        self.updates = []
        self.edits = []
        self.keys = []
        self.interpolation_calls = []
        self.session_frames = []
        self.modify_result = True
        self.max_frame = None  # None: every frame is readable (the scene's keys cover them all)
        self.sections = []
        self.fit_calls = 0
        self.generate_update_calls = 0
        self.controllers = []
        self.lock_result = None
        self.object_layer = {}
        self.locked_layers = {}
        self.layers_viewer = FakeLayersViewer(self)
        self.autoposing = FakeAutoPosing()
        self.scene = FakeScene(self)
        self.view = FakeViewScene(self)

    def add_point(self, name, alias, kind, position, rotation=(0.0, 0.0, 0.0), data_names=None):
        data = {}
        for data_name in data_names or ("global_position", "global_rotation", "local_position", "local_rotation"):
            data_id = DataId(name, data_name)
            data[data_name] = data_id
            if data_name.endswith("position"):
                self.values[data_id] = [float(value) for value in position]
            else:
                self.values[data_id] = FakeRotation(rotation)
        self.objects[name] = {"name": name, "data": data}
        self.object_layer.setdefault(name, "layer1")
        entry = {
            "id": name,
            "name": name,
            "alias": alias,
            "kind": kind,
            "type": "Point" if kind == "main_point" else "Joint",
            "position": list(position),
            "autoposing_locked": None,
            "character": "Rig(0)",
        }
        self.controllers.append(entry)
        return entry

    def _data_name(self, kind, space):
        return f"{'global' if space == 'world' else 'local'}_{kind}"

    def position(self, name, space="world"):
        return list(self.values[self.objects[name]["data"][self._data_name("position", space)]])

    def rotation(self, name, space="world"):
        return list(self.values[self.objects[name]["data"][self._data_name("rotation", space)]].degrees)

    def set_position(self, name, values, space="world"):
        self.values[self.objects[name]["data"][self._data_name("position", space)]] = [float(value) for value in values]

    def drop_rotation(self, name):
        """Make an object rotation-less, like a main control point in Cascadeur 2026.2.3."""
        data = self.objects[name]["data"]
        for data_name in ("global_rotation", "local_rotation"):
            data.pop(data_name, None)

    def call_kinds(self):
        return self.autoposing.kinds()


def populate(world):
    """A Cascy-like character: four main points, one joint and one non-controller object."""
    world.add_point("hand_MainPoint_l", "left_hand", "main_point", [25.0, 96.6, 0.0], [0.0, 0.0, 0.0])
    world.add_point("forearm_MainPoint_l", "left_elbow", "main_point", [20.0, 80.0, -5.0], [0.0, 10.0, 0.0])
    world.add_point("head_MainPoint", "head", "main_point", [0.0, 162.3, -2.4], [0.0, 0.0, 0.0])
    world.add_point("pelvis_MainPoint", "pelvis", "main_point", [0.0, 95.6, 0.0], [0.0, 0.0, 0.0])
    world.add_point("hand_joint_l", "left_wrist_joint", "joint", [25.0, 96.0, 0.0], [0.0, 0.0, 0.0])
    world.add_point("Bone_L", None, "other", [1.0, 2.0, 3.0], [0.0, 0.0, 0.0])
    return world


def build_world():
    return populate(World())


# ---------------------------------------------------------------------------
# Module loading with fakes
# ---------------------------------------------------------------------------


def _load():
    if PKG in sys.modules:
        return sys.modules[f"{PKG}.tools.pose"], sys.modules[f"{PKG}.world"]

    package = types.ModuleType(PKG)
    package.__path__ = []
    sys.modules[PKG] = package

    errors = types.ModuleType(f"{PKG}.errors")

    class ToolError(Exception):
        """Same shape as cascadeur_mcp_plus.errors.ToolError."""

    errors.ToolError = ToolError
    sys.modules[f"{PKG}.errors"] = errors
    package.errors = errors

    common = types.ModuleType(f"{PKG}.common")

    def ok(**data):
        return {"ok": True, **data}

    def fail(error, hint=None, **data):
        result = {"ok": False, "error": str(error)}
        if hint:
            result["hint"] = str(hint)
        result.update(data)
        return result

    common.ok = ok
    common.fail = fail
    common.jsonable = lambda value: value
    common.log_tail = lambda since=None, contains=None, lines=40: ["fake log line"]
    sys.modules[f"{PKG}.common"] = common
    package.common = common

    dispatch = types.ModuleType(f"{PKG}.dispatch")
    calls = []

    def call_on_gui(fn, args, timeout, idle=False):
        calls.append((fn, dict(args), timeout))
        return fn(**args)

    dispatch.call_on_gui = call_on_gui
    dispatch.calls = calls
    sys.modules[f"{PKG}.dispatch"] = dispatch
    package.dispatch = dispatch

    rig = types.ModuleType(f"{PKG}.rig")

    class RigError(Exception):
        def __init__(self, message, hint=None, candidates=None, **extra):
            super().__init__(message)
            self.hint = hint
            self.candidates = list(candidates or [])
            self.extra = extra

        def result(self):
            data = {"ok": False, "error": str(self)}
            if self.hint:
                data["hint"] = self.hint
            if self.candidates:
                data["candidates"] = self.candidates
            data.update(self.extra)
            return data

    class UnknownName(RigError):
        pass

    class AmbiguousName(RigError):
        pass

    world = build_world()

    def controllers(character=None):
        return list(world.controllers)

    def resolve(names, character=None):
        if isinstance(names, str):
            names = [names]
        found = []
        for name in names:
            if name == "ambig":
                raise AmbiguousName(
                    "'ambig' matches 2 control points; pick one by its exact name.",
                    hint="Use one of the candidate names.",
                    candidates=[{"name": "hand_MainPoint_l"}, {"name": "forearm_MainPoint_l"}],
                )
            hits = [entry for entry in world.controllers if entry["name"] == name or entry["alias"] == name]
            if not hits:
                raise UnknownName(
                    f"Object or alias not found: {name!r}.",
                    hint="Call list_controllers to see the control point names and aliases.",
                )
            for hit in hits:
                if hit["id"] not in found:
                    found.append(hit["id"])
        return found

    def name_kind(name, type_name=""):
        text = str(name or "")
        if text.endswith("_MainPoint"):
            return "main_point"
        if text.endswith("_Box_l") or text.endswith("_Box_r"):
            return "box"
        return "other"

    rig.RigError = RigError
    rig.UnknownName = UnknownName
    rig.AmbiguousName = AmbiguousName
    rig.controllers = controllers
    rig.resolve = resolve
    rig.name_kind = name_kind
    sys.modules[f"{PKG}.rig"] = rig
    package.rig = rig

    autoposing = types.ModuleType(f"{PKG}.autoposing")
    autoposing.ANSWER_CHOICES = ("yes", "no", "cancel")  # read by pose.TOOLS at import time
    sys.modules[f"{PKG}.autoposing"] = autoposing
    package.autoposing = autoposing
    world_holder = types.ModuleType(f"{PKG}.world")
    world_holder.world = world
    sys.modules[f"{PKG}.world"] = world_holder

    tools_package = types.ModuleType(f"{PKG}.tools")
    tools_package.__path__ = [TOOLS_DIR]
    sys.modules[f"{PKG}.tools"] = tools_package
    package.tools = tools_package

    for name, filename in (("_schema", "_schema.py"), ("layers", "layers.py")):
        spec = importlib.util.spec_from_file_location(f"{PKG}.tools.{name}", os.path.join(TOOLS_DIR, filename))
        module = importlib.util.module_from_spec(spec)
        sys.modules[f"{PKG}.tools.{name}"] = module
        setattr(tools_package, name, module)
        if name == "_schema":
            spec.loader.exec_module(module)
        else:
            # The real layers.py imports csc/common; pose.py only needs ensure_unlocked.
            module.ensure_unlocked = lambda object_ids: world.lock_result
            module.__dict__["__all__"] = ["ensure_unlocked"]

    timeline_spec = importlib.util.spec_from_file_location(f"{PKG}.timeline", os.path.join(PACKAGE_DIR, "timeline.py"))
    timeline = importlib.util.module_from_spec(timeline_spec)
    sys.modules[f"{PKG}.timeline"] = timeline
    package.timeline = timeline
    timeline_spec.loader.exec_module(timeline)

    def set_key_at(model_editor, object_ids, frame):
        world.keys.append({"ids": list(object_ids), "frame": frame})
        if world.max_frame is not None:
            # Keying extends the scene's data range to the frame (measured on Cascy: one key at
            # frame 10 made every channel readable at frame 10).
            world.max_frame = max(world.max_frame, int(frame))
        return {"layers": len(object_ids), "changed": len(object_ids), "already": 0}

    timeline.set_key_at = set_key_at
    timeline.boundary_info = lambda view_scene: {
        "first_frame": 0,
        "last_frame": 100,
        "first_visible_frame": 0,
        "last_visible_frame": 100,
    }

    spec = importlib.util.spec_from_file_location(f"{PKG}.tools.pose", os.path.join(TOOLS_DIR, "pose.py"))
    pose = importlib.util.module_from_spec(spec)
    sys.modules[f"{PKG}.tools.pose"] = pose
    tools_package.pose = pose
    pose.autoposing_helpers = world.autoposing
    spec.loader.exec_module(pose)
    return pose, world


Pose = None
World_ = None


def _fake_from_quaternion(*args):
    """Rotation.from_quaternion(w, x, y, z) or from_quaternion(Quaternion)."""
    if len(args) == 1:
        q = args[0]
        return FakeCscRotation((q.w(), q.x(), q.y(), q.z()))
    return FakeCscRotation(tuple(float(value) for value in args))


def setUpModule():
    global Pose, World_
    # pose.py imports csc and numpy lazily when it reads/writes a rotation; fake both.
    fake_csc = types.ModuleType("csc")
    fake_csc.math = types.SimpleNamespace(
        Rotation=types.SimpleNamespace(from_quaternion=_fake_from_quaternion),
        euler_angles_to_quaternion_x_y_z=lambda angles: FakeQuaternion(
            *_euler_to_quaternion([math.degrees(float(value)) for value in angles])
        ),
        quaternion_to_euler_angles_x_y_z=lambda q: [
            math.radians(value) for value in _quaternion_to_euler((q.w(), q.x(), q.y(), q.z()))
        ],
    )
    fake_csc.layers = types.SimpleNamespace(layer=types.SimpleNamespace(Section=lambda: "empty-section"))
    fake_numpy = types.ModuleType("numpy")
    fake_numpy.asarray = lambda values, dtype=None: [float(value) for value in values]
    fake_numpy.radians = lambda values: [math.radians(float(value)) for value in values]
    _PREVIOUS_CSC = sys.modules.get("csc")
    _PREVIOUS_NUMPY = sys.modules.get("numpy")
    sys.modules["csc"] = fake_csc
    sys.modules["numpy"] = fake_numpy
    Pose, World_ = _load()


def tearDownModule():
    for name, previous in (("csc", globals().get("_PREVIOUS_CSC")), ("numpy", globals().get("_PREVIOUS_NUMPY"))):
        if previous is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = previous


class PoseTestCase(unittest.TestCase):
    def setUp(self):
        self.world = World_
        self.world.__init__()
        populate(self.world)
        self.pose = Pose
        # The fake package modules point at the world created at import time; rebuild them so
        # every test starts from the same Cascy-like pose.
        self.pose.autoposing_helpers = self.world.autoposing
        for name, module in sys.modules.items():
            if not name.startswith(PKG):
                continue
            if name == f"{PKG}.rig":
                module.controllers = lambda character=None: list(self.world.controllers)
                module.resolve = _resolver(self.world)
            elif name == f"{PKG}.tools.layers":
                module.ensure_unlocked = lambda object_ids: self.world.lock_result
            elif name == f"{PKG}.timeline":
                module.set_key_at = _set_key_at(self.world)
        self.pose.common.scenes = lambda: (None, self.world.view, self.world.scene)


def _resolver(world):
    def resolve(names, character=None):
        if isinstance(names, str):
            names = [names]
        found = []
        for name in names:
            if name == "ambig":
                raise sys.modules[f"{PKG}.rig"].AmbiguousName(
                    "'ambig' matches 2 control points; pick one by its exact name.",
                    hint="Use one of the candidate names.",
                    candidates=[{"name": "hand_MainPoint_l"}, {"name": "forearm_MainPoint_l"}],
                )
            hits = [entry for entry in world.controllers if entry["name"] == name or entry["alias"] == name]
            if not hits:
                raise sys.modules[f"{PKG}.rig"].UnknownName(
                    f"Object or alias not found: {name!r}.",
                    hint="Call list_controllers to see the control point names and aliases.",
                )
            for hit in hits:
                if hit["id"] not in found:
                    found.append(hit["id"])
        return found

    return resolve


def _set_key_at(world):
    def set_key_at(model_editor, object_ids, frame):
        world.keys.append({"ids": list(object_ids), "frame": frame})
        if world.max_frame is not None:
            world.max_frame = max(world.max_frame, int(frame))
        return {"layers": len(object_ids), "changed": len(object_ids), "already": 0}

    return set_key_at


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


class ParseSpaceTests(PoseTestCase):
    def test_world_and_local(self):
        self.assertEqual(self.pose.parse_space(None), "world")
        self.assertEqual(self.pose.parse_space(""), "world")
        self.assertEqual(self.pose.parse_space("WORLD"), "world")
        self.assertEqual(self.pose.parse_space(" Local "), "local")

    def test_rejects_other_spaces(self):
        for value in ("global", "parent", 1, True):
            with self.assertRaises(self.pose.ToolError, msg=repr(value)):
                self.pose.parse_space(value)


class ParseVectorTests(PoseTestCase):
    def test_accepts_numbers_and_numeric_strings(self):
        self.assertEqual(self.pose.parse_vector([1, 2.5, "3"], "x"), [1.0, 2.5, 3.0])

    def test_rejects_wrong_shapes_and_values(self):
        for value in ([1, 2], [1, 2, 3, 4], "1,2,3", None, {"x": 1}, [1, 2, True], [1, 2, "x"]):
            with self.assertRaises(self.pose.ToolError, msg=repr(value)):
                self.pose.parse_vector(value, "x")

    def test_rejects_non_finite_numbers(self):
        for value in ([float("nan"), 0, 0], [0, float("inf"), 0]):
            with self.assertRaises(self.pose.ToolError):
                self.pose.parse_vector(value, "x")

    def test_error_names_the_field(self):
        with self.assertRaises(self.pose.ToolError) as caught:
            self.pose.parse_vector([1, 2], "values['left_hand']['position']")
        self.assertIn("values['left_hand']['position']", str(caught.exception))


class ParseValuesTests(PoseTestCase):
    def test_position_offset_rotation_are_parsed(self):
        parsed = self.pose.parse_values(
            {
                "left_hand": {"offset": [0, 40, 0]},
                "pelvis": {"position": [0, 95, 0], "rotation": [0, 0, 30]},
            }
        )
        self.assertEqual([name for name, _ops in parsed], ["left_hand", "pelvis"])
        self.assertEqual(parsed[0][1], {"offset": [0.0, 40.0, 0.0]})
        self.assertEqual(parsed[1][1], {"position": [0.0, 95.0, 0.0], "rotation": [0.0, 0.0, 30.0]})

    def test_rejects_bad_values(self):
        cases = [
            None,
            {},
            "left_hand",
            {"": {"offset": [0, 1, 0]}},
            {"left_hand": [0, 1, 0]},
            {"left_hand": {}},
            {"left_hand": {"position": [0, 1, 0], "offset": [0, 1, 0]}},
            {"left_hand": {"rot": [0, 1, 0]}},
            {"left_hand": {"offset": [0, 1]}},
        ]
        for value in cases:
            with self.assertRaises(self.pose.ToolError, msg=repr(value)):
                self.pose.parse_values(value)

    def test_reports_the_unknown_key(self):
        with self.assertRaises(self.pose.ToolError) as caught:
            self.pose.parse_values({"left_hand": {"rot": [0, 1, 0]}})
        self.assertIn("rot", str(caught.exception))

    def test_position_and_offset_error_explains_the_alternative(self):
        with self.assertRaises(self.pose.ToolError) as caught:
            self.pose.parse_values({"left_hand": {"position": [0, 1, 0], "offset": [0, 1, 0]}})
        self.assertIn("offset", str(caught.exception))


class ComparisonTests(PoseTestCase):
    def test_quaternion_angle_deg(self):
        identity = {"w": 1.0, "x": 0.0, "y": 0.0, "z": 0.0}
        self.assertAlmostEqual(self.pose.quaternion_angle_deg(identity, identity), 0.0)
        # 90 degrees around Z.
        z90 = {"w": math.cos(math.pi / 4), "x": 0.0, "y": 0.0, "z": math.sin(math.pi / 4)}
        self.assertAlmostEqual(self.pose.quaternion_angle_deg(identity, z90), 90.0, places=6)
        # Sign-flipped quaternions are the same rotation (q and -q).
        negative = {key: -value for key, value in z90.items()}
        self.assertAlmostEqual(self.pose.quaternion_angle_deg(z90, negative), 0.0, places=6)
        # 180 degrees around Z.
        z180 = {"w": 0.0, "x": 0.0, "y": 0.0, "z": 1.0}
        self.assertAlmostEqual(self.pose.quaternion_angle_deg(identity, z180), 180.0, places=6)
        # A rotation that differs by 5 degrees must not read as 175 because of Euler wrapping.
        z5 = {
            "w": math.cos(math.radians(2.5)),
            "x": 0.0,
            "y": 0.0,
            "z": math.sin(math.radians(2.5)),
        }
        self.assertAlmostEqual(self.pose.quaternion_angle_deg(z5, z180), 175.0, places=6)

    def test_distance(self):
        self.assertAlmostEqual(self.pose.distance([0, 0, 0], [0, 0, 3]), 3.0)


class ArgumentHelperTests(PoseTestCase):
    def test_boolean(self):
        for value, expected in ((True, True), (False, False), ("true", True), ("off", False)):
            self.assertEqual(self.pose._boolean(value, "key"), expected)
        for value in (None, 1, "maybe"):
            with self.assertRaises(self.pose.ToolError):
                self.pose._boolean(value, "key")

    def test_names(self):
        self.assertEqual(self.pose._names(None), [])
        self.assertEqual(self.pose._names(""), [])
        self.assertEqual(self.pose._names("left_hand"), ["left_hand"])
        self.assertEqual(self.pose._names(["left_hand", "pelvis"]), ["left_hand", "pelvis"])
        with self.assertRaises(self.pose.ToolError):
            self.pose._names(3)


# ---------------------------------------------------------------------------
# get_pose
# ---------------------------------------------------------------------------


class GetPoseTests(PoseTestCase):
    def test_main_points_by_default_at_the_current_frame(self):
        result = self.pose.get_pose()
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["frame"], 7)
        self.assertEqual(result["current_frame"], 7)
        self.assertEqual(result["space"], "world")
        names = [item["name"] for item in result["controllers"]]
        self.assertEqual(names, ["hand_MainPoint_l", "forearm_MainPoint_l", "head_MainPoint", "pelvis_MainPoint"])
        hand = result["controllers"][0]
        self.assertEqual(hand["alias"], "left_hand")
        self.assertEqual(hand["kind"], "main_point")
        self.assertEqual(hand["position"], [25.0, 96.6, 0.0])
        self.assertEqual(hand["rotation"], [0.0, 0.0, 0.0])
        self.assertEqual(hand["quaternion"], {"w": 1.0, "x": 0.0, "y": 0.0, "z": 0.0})

    def test_named_controllers_and_another_frame_do_not_move_the_playhead(self):
        result = self.pose.get_pose(controllers=["left_hand", "left_elbow"], frame=10)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["frame"], 10)
        self.assertEqual(result["current_frame"], 7)
        self.assertEqual(self.world.playhead, 7)
        self.assertEqual([item["alias"] for item in result["controllers"]], ["left_hand", "left_elbow"])

    def test_local_space_reads_local_data(self):
        self.world.set_position("hand_MainPoint_l", [1.0, 2.0, 3.0], space="local")
        result = self.pose.get_pose(controllers="left_hand", space="local")
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["space"], "local")
        self.assertEqual(result["controllers"][0]["position"], [1.0, 2.0, 3.0])

    def test_unknown_name_returns_the_rig_error(self):
        result = self.pose.get_pose(controllers=["not_a_bone"])
        self.assertFalse(result["ok"], result)
        self.assertIn("not_a_bone", result["error"])
        self.assertIn("list_controllers", result["hint"])

    def test_bad_space_and_frame(self):
        self.assertFalse(self.pose.get_pose(space="global")["ok"])
        self.assertFalse(self.pose.get_pose(frame=-1)["ok"])
        self.assertFalse(self.pose.get_pose(frame="ten")["ok"])

    def test_frame_without_data_reports_the_frame_used(self):
        self.world.max_frame = 0
        result = self.pose.get_pose(controllers=["left_hand"], frame=10)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["frame"], 10)
        hand = result["controllers"][0]
        self.assertEqual(hand["frame_used"], 0)
        self.assertEqual(hand["position"], [25.0, 96.6, 0.0])
        self.assertIn("frame 0", result["hint"])

    def test_object_without_transform_reports_null(self):
        self.world.objects["hand_MainPoint_l"]["data"].clear()
        result = self.pose.get_pose(controllers="left_hand")
        self.assertTrue(result["ok"], result)
        self.assertIsNone(result["controllers"][0]["position"])
        self.assertIsNone(result["controllers"][0]["rotation"])
        self.assertIsNone(result["controllers"][0]["quaternion"])


# ---------------------------------------------------------------------------
# set_pose
# ---------------------------------------------------------------------------


class SetPoseTests(PoseTestCase):
    def test_offset_writes_keys_and_lets_autoposing_pose_the_body(self):
        posed = [30.0, 126.6, 0.0]

        def follow():
            self.world.set_position("forearm_MainPoint_l", posed)

        self.world.autoposing.effect = follow
        moved_cm = round(self.pose.distance(posed, [20.0, 80.0, -5.0]), 3)
        result = self.pose.set_pose(values={"left_hand": {"offset": [0, 40, 0]}}, frame=10)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["frame"], 10)
        self.assertEqual(result["space"], "world")
        self.assertTrue(result["keyed"])
        self.assertEqual(result["keys"], {"layers": 1, "changed": 1, "already": 0})
        hand = result["controllers"][0]
        self.assertEqual(hand["name"], "hand_MainPoint_l")
        self.assertEqual(hand["alias"], "left_hand")
        self.assertEqual(hand["requested"]["position"], [25.0, 136.6, 0.0])
        self.assertEqual(hand["before"]["position"], [25.0, 96.6, 0.0])
        self.assertEqual(hand["after"]["position"][1], 136.6)
        self.assertAlmostEqual(hand["position_error_cm"], 0.0)
        # Only the requested channels get an error number.
        self.assertNotIn("rotation_error_deg", hand)
        self.assertIn(
            {"name": "forearm_MainPoint_l", "alias": "left_elbow", "moved_cm": moved_cm},
            result["adjusted"],
        )
        self.assertEqual(result["undo"], "MCP: set_pose")
        self.assertEqual(result["undo_steps"], ["MCP: set_pose", "AutoPosing.Autolock"])
        self.assertEqual(result["undo_count"], 2)
        self.assertIn("two undos", result["undo_note"])
        self.assertTrue(result["autoposing"]["locked"])
        self.assertEqual(result["autoposing"]["lock_undo"], "AutoPosing.Autolock")
        # Order: the lock comes before the write, the update runs inside the same undo step.
        self.assertEqual(
            self.world.call_kinds(),
            ["dialogs", "lock", "answer_now", "dialogs", "update", "answer_now", "dialogs"],
        )
        self.assertEqual(self.world.edits, ["MCP: set_pose"])
        self.assertEqual(self.world.keys, [{"ids": ["hand_MainPoint_l"], "frame": 10}])
        self.assertEqual(len(self.world.interpolation_calls), 1)
        self.assertEqual(self.world.interpolation_calls[0]["frame"], 10)
        self.assertEqual(self.world.updates[0]["frame"], 10)
        self.assertTrue(result["autoposing_update"]["undo"] is None)
        self.assertIsNone(result["dialog"])

    def test_position_is_absolute(self):
        result = self.pose.set_pose(values={"left_hand": {"position": [0.0, 100.0, 5.0]}}, frame=10)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["controllers"][0]["after"]["position"], [0.0, 100.0, 5.0])
        self.assertAlmostEqual(result["controllers"][0]["position_error_cm"], 0.0)

    def test_local_space_writes_local_data(self):
        result = self.pose.set_pose(
            values={"left_hand": {"offset": [0, 5, 0]}}, frame=10, space="local", autoposing=False
        )
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["space"], "local")
        self.assertEqual(self.world.position("hand_MainPoint_l", space="local"), [25.0, 101.6, 0.0])
        self.assertEqual(self.world.position("hand_MainPoint_l", space="world"), [25.0, 96.6, 0.0])

    def test_autoposing_false_only_writes_and_skips_lock_and_update(self):
        result = self.pose.set_pose(values={"left_hand": {"offset": [0, 40, 0]}}, frame=10, autoposing=False)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["undo_steps"], ["MCP: set_pose"])
        self.assertEqual(result["undo_count"], 1)
        self.assertFalse(result["autoposing"]["locked"])
        self.assertIsNone(result["autoposing_update"])
        # Only the "answer a prompt left open" check runs; no AutoPosing call is made.
        self.assertEqual(self.world.call_kinds(), ["dialogs"])
        self.assertEqual(result["adjusted"], [])
        self.assertEqual(self.world.position("forearm_MainPoint_l"), [20.0, 80.0, -5.0])

    def test_key_false_writes_without_keying(self):
        result = self.pose.set_pose(values={"left_hand": {"offset": [0, 1, 0]}}, frame=10, key=False)
        self.assertTrue(result["ok"], result)
        self.assertFalse(result["keyed"])
        self.assertIsNone(result["keys"])
        self.assertEqual(self.world.keys, [])
        self.assertEqual(self.world.interpolation_calls, [])
        self.assertEqual(self.world.position("hand_MainPoint_l")[1], 97.6)

    def test_rotation_is_written_as_euler_degrees(self):
        result = self.pose.set_pose(values={"left_hand": {"rotation": [10.0, -20.0, 30.0]}}, frame=10, autoposing=False)
        self.assertTrue(result["ok"], result)
        item = result["controllers"][0]
        self.assertFalse("position_error_cm" in item)
        self.assertAlmostEqual(item["rotation_error_deg"], 0.0, places=6)
        for actual, expected in zip(item["after"]["rotation"], [10.0, -20.0, 30.0], strict=True):
            self.assertAlmostEqual(actual, expected, places=6)
        self.assertEqual(item["requested"]["rotation"], [10.0, -20.0, 30.0])
        rotation = self.world.values[self.world.objects["hand_MainPoint_l"]["data"]["global_rotation"]]
        self.assertIsInstance(rotation, FakeCscRotation)
        for actual, expected in zip(rotation.degrees, [10.0, -20.0, 30.0], strict=True):
            self.assertAlmostEqual(actual, expected, places=6)

    def test_two_axis_rotation_round_trips(self):
        # Reading and writing go through Cascadeur's own euler<->quaternion pair, so a two-axis
        # rotation must read back unchanged (the ticket's review found from_euler and
        # to_euler_angles_x_y_z disagree once more than one axis is involved).
        wanted = [30.0, 45.0, 0.0]
        result = self.pose.set_pose(values={"left_hand": {"rotation": wanted}}, frame=10, autoposing=False)
        self.assertTrue(result["ok"], result)
        item = result["controllers"][0]
        self.assertLess(item["rotation_error_deg"], 0.01)
        for actual, expected in zip(item["after"]["rotation"], wanted, strict=True):
            self.assertAlmostEqual(actual, expected, places=4)
        first_quaternion = dict(item["after"]["quaternion"])
        # Write the read-back values again: the orientation must not drift.
        again = self.pose.set_pose(
            values={"left_hand": {"rotation": item["after"]["rotation"]}}, frame=10, autoposing=False
        )
        self.assertTrue(again["ok"], again)
        second_quaternion = again["controllers"][0]["after"]["quaternion"]
        for key in ("w", "x", "y", "z"):
            self.assertAlmostEqual(second_quaternion[key], first_quaternion[key], places=6)
        self.assertLess(again["controllers"][0]["rotation_error_deg"], 0.01)

    def test_position_and_rotation_in_one_entry(self):
        result = self.pose.set_pose(
            values={"left_hand": {"position": [1.0, 2.0, 3.0], "rotation": [4.0, 5.0, 6.0]}},
            frame=10,
            autoposing=False,
        )
        self.assertTrue(result["ok"], result)
        item = result["controllers"][0]
        self.assertEqual(item["after"]["position"], [1.0, 2.0, 3.0])
        for actual, expected in zip(item["after"]["rotation"], [4.0, 5.0, 6.0], strict=True):
            self.assertAlmostEqual(actual, expected, places=6)

    def test_playhead_is_restored_after_the_autoposing_update(self):
        # The in-session AutoPosing update moves the playhead to the target frame (measured on
        # 2026-10-24); set_pose must put the playhead back where it was.
        self.world.playhead = 0
        result = self.pose.set_pose(values={"left_hand": {"offset": [0, 40, 0]}}, frame=10)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["frame"], 10)
        self.assertEqual(self.world.playhead, 0)
        self.assertEqual(self.world.session_frames, [10])

    def test_frame_defaults_to_the_playhead_and_does_not_move_it(self):
        result = self.pose.set_pose(values={"left_hand": {"offset": [0, 1, 0]}})
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["frame"], 7)
        self.assertEqual(self.world.session_frames, [7])
        self.assertEqual(self.world.playhead, 7)

    def test_autoPosing_is_enabled_before_locking(self):
        self.world.autoposing.enabled = False
        result = self.pose.set_pose(values={"left_hand": {"offset": [0, 1, 0]}}, frame=10)
        self.assertTrue(result["ok"], result)
        self.assertTrue(self.world.autoposing.enabled)
        self.assertTrue(result["autoposing"]["toggled"])
        self.assertEqual(
            self.world.call_kinds(),
            ["dialogs", "enable", "lock", "answer_now", "dialogs", "update", "answer_now", "dialogs"],
        )

    def test_autoPosing_that_stays_off_fails_before_writing(self):
        world = self.world
        world.autoposing.enabled = False
        world.autoposing.stuck_off = True  # the interface reports off and refuses to switch
        result = self.pose.set_pose(values={"left_hand": {"offset": [0, 1, 0]}}, frame=10)
        self.assertFalse(result["ok"], result)
        self.assertIn("AutoPosing", result["error"])
        self.assertIn("autoposing=false", result["hint"])
        self.assertEqual(world.writes, [])
        self.assertEqual(world.edits, [])
        self.assertNotIn("lock", world.call_kinds())
        self.assertEqual(result["undo_steps"], [])

    def test_locked_layer_stops_the_call_before_locking(self):
        self.world.lock_result = {
            "ok": False,
            "error": "Refusing to change objects on locked layer(s): 'T23 locked' holds hand_MainPoint_l.",
            "hint": "Unlock the layer first with set_layer_locked(name=<layer>, locked=false); ...",
        }
        result = self.pose.set_pose(values={"left_hand": {"offset": [0, 1, 0]}}, frame=10)
        self.assertFalse(result["ok"], result)
        self.assertIn("locked layer", result["error"])
        self.assertEqual(self.world.call_kinds(), ["dialogs"])
        self.assertEqual(self.world.edits, [])
        self.assertEqual(self.world.writes, [])

    def test_two_names_for_the_same_controller_are_rejected(self):
        self.world.controllers.append(
            {
                "id": "hand_MainPoint_l",
                "name": "hand_MainPoint_l",
                "alias": "left_hand",
                "kind": "main_point",
                "type": "Point",
                "position": [0, 0, 0],
                "character": "Rig(0)",
            }
        )
        result = self.pose.set_pose(
            values={"left_hand": {"offset": [0, 1, 0]}, "hand_MainPoint_l": {"offset": [0, 2, 0]}},
            frame=10,
        )
        self.assertFalse(result["ok"], result)
        self.assertIn("same controller", result["error"])
        self.assertEqual(self.world.edits, [])

    def test_ambiguous_name_keeps_the_candidates(self):
        result = self.pose.set_pose(values={"ambig": {"offset": [0, 1, 0]}}, frame=10)
        self.assertFalse(result["ok"], result)
        self.assertEqual(len(result["candidates"]), 2)
        self.assertEqual(self.world.edits, [])

    def test_object_without_transform_fails_with_a_hint(self):
        self.world.objects["hand_MainPoint_l"].clear()  # no Transform behaviour at all
        result = self.pose.set_pose(values={"left_hand": {"offset": [0, 1, 0]}}, frame=10, autoposing=False)
        self.assertFalse(result["ok"], result)
        self.assertIn("Transform", result["error"])
        self.assertEqual(self.world.edits, [])

    def test_missing_local_data_names_the_space(self):
        data = self.world.objects["hand_MainPoint_l"]["data"]
        del data["local_position"]
        result = self.pose.set_pose(
            values={"left_hand": {"offset": [0, 1, 0]}}, frame=10, space="local", autoposing=False
        )
        self.assertFalse(result["ok"], result)
        self.assertIn("local", result["error"])
        self.assertTrue(result["ok"] is False)

    def test_rejected_undo_step_reports_the_log_and_the_autolock_step(self):
        self.world.modify_result = False
        result = self.pose.set_pose(values={"left_hand": {"offset": [0, 1, 0]}}, frame=10)
        self.assertFalse(result["ok"], result)
        self.assertIn("MCP: set_pose", result["error"])
        self.assertEqual(result["log"], ["fake log line"])
        self.assertEqual(result["undo_steps"], ["AutoPosing.Autolock"])

    def test_leftover_prompt_marks_the_call_failed(self):
        self.world.autoposing.dialog = {"found": True, "answering": False, "closed": False, "button": "Yes"}
        result = self.pose.set_pose(values={"left_hand": {"offset": [0, 1, 0]}}, frame=10)
        self.assertFalse(result["ok"], result)
        self.assertIn("prompt is still open", result["error"])
        self.assertEqual(result["dialog"]["button"], "Yes")

    def test_autoPosing_restoring_the_value_is_reported(self):
        def restore():
            self.world.set_position("hand_MainPoint_l", [25.0, 96.6, 0.0])

        self.world.autoposing.effect = restore
        result = self.pose.set_pose(values={"left_hand": {"offset": [0, 40, 0]}}, frame=10)
        self.assertTrue(result["ok"], result)
        item = result["controllers"][0]
        self.assertAlmostEqual(item["position_error_cm"], 40.0, places=6)
        self.assertIn("restored its prediction", result["hint"])

    def test_frame_without_data_extends_the_range_and_keys_before_writing(self):
        # Cascy-like: the channels only reach frame 0 until the frame is keyed. Cascadeur refuses
        # the write until the layers reach the frame, so the edit extends them first.
        self.world.max_frame = 0
        result = self.pose.set_pose(values={"left_hand": {"offset": [0, 40, 0]}}, frame=10)
        self.assertTrue(result["ok"], result)
        hand = result["controllers"][0]
        self.assertEqual(hand["before"]["position"], [25.0, 96.6, 0.0])
        self.assertEqual(hand["before"]["frame"], 0)
        self.assertEqual(hand["after"]["position"], [25.0, 136.6, 0.0])
        self.assertAlmostEqual(hand["position_error_cm"], 0.0)
        # The empty section goes past the animation's last frame (101 keeps the 0-100 range).
        self.assertEqual(self.world.sections, [{"pos": 101, "layer": "layer1"}])
        self.assertEqual(self.world.fit_calls, 1)
        self.assertEqual(self.world.generate_update_calls, 1)
        self.assertEqual(result["extended_to"], 101)
        self.assertIn("extended to frame 101", result["hint"])
        self.assertEqual(self.world.keys, [{"ids": ["hand_MainPoint_l"], "frame": 10}])

    def test_a_readable_frame_is_not_extended(self):
        result = self.pose.set_pose(values={"left_hand": {"offset": [0, 40, 0]}}, frame=10)
        self.assertTrue(result["ok"], result)
        self.assertEqual(self.world.sections, [])
        self.assertIsNone(result["extended_to"])

    def test_extension_touches_only_the_target_layers(self):
        # The written controller lives on layer2; layer1 (another character or layer) must keep
        # its sections untouched.
        self.world.object_layer["hand_MainPoint_l"] = "layer2"
        self.world.max_frame = 0
        result = self.pose.set_pose(values={"left_hand": {"offset": [0, 40, 0]}}, frame=10)
        self.assertTrue(result["ok"], result)
        self.assertEqual(self.world.sections, [{"pos": 101, "layer": "layer2"}])
        self.assertEqual(result["extended_to"], 101)

    def test_extension_refuses_a_locked_target_layer(self):
        self.world.object_layer["hand_MainPoint_l"] = "layer2"
        self.world.locked_layers["layer2"] = True
        self.world.max_frame = 0
        result = self.pose.set_pose(values={"left_hand": {"offset": [0, 40, 0]}}, frame=10)
        self.assertFalse(result["ok"], result)
        self.assertIn("locked layer", result["error"])
        self.assertEqual(self.world.sections, [])
        self.assertEqual(self.world.edits, [])
        self.assertEqual(self.world.writes, [])

    def test_rotation_without_rotation_data_fails_with_the_reason(self):
        # A real main control point has no rotation data at all.
        self.world.drop_rotation("hand_MainPoint_l")
        result = self.pose.set_pose(values={"left_hand": {"rotation": [0, 30, 0]}}, frame=10, autoposing=False)
        self.assertFalse(result["ok"], result)
        self.assertIn("no rotation data", result["error"])
        self.assertIn("hand_Box_l", result["hint"])
        self.assertEqual(self.world.edits, [])

    def test_refused_rotation_does_not_lock_or_enable_autoposing(self):
        # The refusal must happen before AutoPosing.Autolock: no lock, no undo step, no switch.
        self.world.drop_rotation("hand_MainPoint_l")
        self.world.autoposing.enabled = False
        result = self.pose.set_pose(values={"left_hand": {"rotation": [0, 30, 0]}}, frame=10)
        self.assertFalse(result["ok"], result)
        self.assertIn("no rotation data", result["error"])
        self.assertNotIn("lock", self.world.call_kinds())
        self.assertNotIn("enable", self.world.call_kinds())
        self.assertFalse(self.world.autoposing.enabled)
        self.assertEqual(self.world.edits, [])
        self.assertEqual(result["undo_steps"], [])

    def test_refused_missing_value_does_not_lock_autoposing(self):
        self.world.max_frame = 0
        result = self.pose.set_pose(values={"left_hand": {"offset": [0, 40, 0]}}, frame=10, key=False)
        self.assertFalse(result["ok"], result)
        self.assertIn("set_key", result["hint"])
        self.assertNotIn("lock", self.world.call_kinds())
        self.assertEqual(self.world.edits, [])
        self.assertEqual(result["undo_steps"], [])

    def test_get_pose_marks_missing_rotation(self):
        self.world.drop_rotation("hand_MainPoint_l")
        result = self.pose.get_pose(controllers=["left_hand"], frame=10)
        self.assertTrue(result["ok"], result)
        hand = result["controllers"][0]
        self.assertIsNone(hand["rotation"])
        self.assertIsNone(hand["quaternion"])
        self.assertFalse(hand["rotation_available"])

    def test_key_false_without_data_fails_with_the_key_hint(self):
        self.world.max_frame = 0
        result = self.pose.set_pose(values={"left_hand": {"offset": [0, 40, 0]}}, frame=10, autoposing=False, key=False)
        self.assertFalse(result["ok"], result)
        self.assertIn("set_key", result["hint"])
        self.assertIn("frame 10", result["hint"])
        self.assertEqual(self.world.writes, [])
        self.assertEqual(self.world.edits, [])

    def test_bad_arguments_do_not_touch_the_scene(self):
        gui_calls = len(self.pose.dispatch.calls)
        for kwargs in (
            {"values": None},
            {"values": {}},
            {"values": {"left_hand": {"offset": [0, 1]}}},
            {"values": {"left_hand": {"offset": [0, 1, 0]}}, "frame": -2},
            {"values": {"left_hand": {"offset": [0, 1, 0]}}, "space": "parent"},
            {"values": {"left_hand": {"offset": [0, 1, 0]}}, "autoposing": "maybe"},
            {"values": {"left_hand": {"offset": [0, 1, 0]}}, "answer": "maybe"},
        ):
            result = self.pose.set_pose(**kwargs)
            self.assertFalse(result["ok"], (kwargs, result))
        self.assertEqual(self.pose.dispatch.calls[gui_calls:], [])
        self.assertEqual(self.world.edits, [])
        self.assertEqual(self.world.writes, [])


# ---------------------------------------------------------------------------
# Tool table
# ---------------------------------------------------------------------------


class ToolTableTests(PoseTestCase):
    def test_names_and_handlers(self):
        tools = {tool["name"]: tool for tool in self.pose.TOOLS}
        self.assertEqual(set(tools), {"get_pose", "set_pose"})
        self.assertIn("fn", tools["get_pose"])
        self.assertNotIn("handler", tools["get_pose"])
        self.assertIn("handler", tools["set_pose"])
        self.assertNotIn("fn", tools["set_pose"])

    def test_required_arguments(self):
        tools = {tool["name"]: tool for tool in self.pose.TOOLS}
        self.assertEqual(tools["get_pose"]["inputSchema"]["required"], [])
        self.assertEqual(tools["set_pose"]["inputSchema"]["required"], ["values"])
        properties = tools["set_pose"]["inputSchema"]["properties"]
        self.assertEqual(set(properties), {"values", "frame", "autoposing", "key", "space", "answer"})
        self.assertEqual(properties["space"]["enum"], ["world", "local"])

    def test_descriptions_cover_the_contract(self):
        tools = {tool["name"]: tool for tool in self.pose.TOOLS}
        get_pose = tools["get_pose"]["description"]
        set_pose = tools["set_pose"]["description"]
        self.assertIn("set_pose", get_pose)
        self.assertIn("cm", get_pose)
        self.assertIn("degrees", get_pose)
        self.assertIn("playhead is not moved", get_pose)
        self.assertIn("Always pose with set_pose", set_pose)
        self.assertIn("AutoPosing", set_pose)
        self.assertIn("two undos", set_pose)
        self.assertIn("locked", set_pose)
        self.assertIn("pelvis", set_pose)
        self.assertIn("feet", set_pose)
        self.assertIn("cm", set_pose)
        self.assertIn("degrees", set_pose)


if __name__ == "__main__":
    unittest.main()
