"""Unit tests for the pose operations (T22): mirror_pose, copy_pose, paste_pose, reset_pose.

cascadeur_mcp_plus/tools/pose_ops.py is loaded inside a throw-away package with a fake pose
module (the T16 helpers it calls), fake common/rig/timeline helpers, a fake Cascadeur scene and
a fake csc module. That covers the mirror-plane math, the left/right name pairing, the pose store,
the paste/reset write plans and the undo-step bookkeeping without Cascadeur. The live behaviour
(native MirrorTool, TPose data, inverse bind matrices) is checked by tests/live/test_pose_ops.py.

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
PKG = "t22_pose_ops_test_pkg"


# ---------------------------------------------------------------------------
# Fake values and a fake scene
# ---------------------------------------------------------------------------


class FakeQuaternion:
    def __init__(self, w=1.0, x=0.0, y=0.0, z=0.0):
        self._values = (float(w), float(x), float(y), float(z))

    def w(self):
        return self._values[0]

    def x(self):
        return self._values[1]

    def y(self):
        return self._values[2]

    def z(self):
        return self._values[3]


class FakeRotation:
    """A rotation value: Euler XYZ degrees."""

    def __init__(self, degrees=(0.0, 0.0, 0.0)):
        self.degrees = [float(value) for value in degrees]

    def to_euler_angles_x_y_z(self):
        return [math.radians(value) for value in self.degrees]

    def to_quaternion(self):
        return FakeQuaternion()

    def inverse(self):
        return FakeRotation([-value for value in self.degrees])

    def __mul__(self, other):
        return FakeRotation([a + b for a, b in zip(self.degrees, other.degrees, strict=True)])


class FakeOrthogonalTransform:
    def __init__(self, position=None, rotation=None):
        self.position = list(position or [0.0, 0.0, 0.0])
        self.rotation = rotation or FakeQuaternion()

    def inverse(self):
        return FakeOrthogonalTransform([-value for value in self.position], self.rotation)


class NullId:
    def is_null(self):
        return True


class DataId:
    def __init__(self, kind, object_id, space=None):
        self.kind = kind
        self.object_id = object_id
        self.space = space

    def __str__(self):
        return f"{self.kind}:{self.object_id}:{self.space}"

    def is_null(self):
        return False


class FakeBehaviourViewer:
    def __init__(self, world):
        self.world = world

    def get_behaviour_by_name(self, object_id, name):
        if name == "TPose" and object_id in self.world.tpose:
            return ("TPose", object_id)
        if name == "Transform":
            return ("Transform", object_id)
        if name == "Mirror":
            entry = self.world.by_id().get(object_id)
            return ("Mirror", object_id) if entry is not None else NullId()
        return NullId()

    def get_behaviour_data(self, behaviour, name):
        if isinstance(behaviour, tuple) and behaviour[0] == "TPose" and name in ("position", "rotation"):
            return DataId("tpose", behaviour[1], name)
        return NullId()


class FakeDataViewer:
    def __init__(self, world):
        self.world = world

    def get_data_value(self, data_id, frame=None):
        if data_id.kind == "tpose":
            return self.world.tpose[data_id.object_id].get(data_id.space)
        raise RuntimeError(f"no data for {data_id}")


class FakeModelViewer:
    def __init__(self, world):
        self.world = world
        self._behaviour = FakeBehaviourViewer(world)
        self._data = FakeDataViewer(world)

    def behaviour_viewer(self):
        return self._behaviour

    def data_viewer(self):
        return self._data

    def get_objects(self):
        return [entry["id"] for entry in self.world.controllers]

    def get_object_name(self, object_id):
        entry = self.world.by_id().get(object_id)
        if entry is None:
            raise RuntimeError("no such object")
        return entry["name"]


class FakeDataEditor:
    def __init__(self, world):
        self.world = world

    def set_data_value(self, data_id, frame, value):
        self.world.writes.append({"data": str(data_id), "frame": frame, "value": value})
        entry = self.world.by_id().get(data_id.object_id)
        if entry is None:
            return
        if data_id.kind == "pos":
            stored = [float(item) for item in value]
            self.world.positions.setdefault((data_id.object_id, data_id.space), {})[frame] = stored
            if data_id.space == "local" and entry["name"] in self.world.tpose:
                # The local TPose transform reproduces the TPose world position on the hierarchy.
                self.world.positions.setdefault((data_id.object_id, "world"), {})[frame] = list(
                    self.world.tpose[entry["name"]]["position"]
                )
        elif data_id.kind == "rot":
            self.world.rotations.setdefault((data_id.object_id, data_id.space), {})[frame] = value


class FakeLayersEditor:
    def __init__(self, world):
        self.world = world

    def set_section(self, section, pos, layer_id):
        self.world.sections.append((pos, layer_id))


class FakeModelEditor:
    def __init__(self, world):
        self.world = world
        self._data = FakeDataEditor(world)
        self._layers = FakeLayersEditor(world)

    def data_editor(self):
        return self._data

    def layers_editor(self):
        return self._layers

    def set_fixed_interpolation_if_need(self, actuals, frame):
        self.world.interpolation.append({"actuals": set(actuals), "frame": frame})

    def fit_animation_size_by_layers(self):
        self.world.fit_calls += 1


class FakeSceneUpdater:
    def __init__(self, world):
        self.world = world

    def generate_update(self):
        self.world.generate_update_calls += 1

    def run_update(self, data_ids, frame):
        self.world.updates.append({"data": set(data_ids), "frame": frame})


class FakeScene:
    def __init__(self, world):
        self.world = world
        self._model_viewer = FakeModelViewer(world)
        self._model_editor = FakeModelEditor(world)
        self._scene_updater = FakeSceneUpdater(world)

    def model_viewer(self):
        return self._model_viewer

    def layers_viewer(self):
        return types.SimpleNamespace(all_layer_ids=lambda: ["layer1"])

    def assets_manager(self):
        return types.SimpleNamespace()

    def get_current_frame(self, clamp=True):
        return self.world.playhead

    def set_current_frame(self, frame):
        self.world.playhead = int(frame)


class FakeViewScene:
    def __init__(self, world):
        self.world = world

    def name(self):
        return "FakeCascy.casc"

    def animation_boundary(self):
        return types.SimpleNamespace(first_frame=0, last_frame=100, first_visible_frame=0, last_visible_frame=100)


class World:
    def __init__(self):
        self.controllers = []
        self.positions = {}
        self.rotations = {}
        self.tpose = {}
        self.parents = {}
        self.playhead = 0
        self.lock_result = None
        self.writes = []
        self.updates = []
        self.edits = []
        self.keys = []
        self.sections = []
        self.interpolation = []
        self.fit_calls = 0
        self.generate_update_calls = 0
        self.extend_calls = []
        self.modify_result = True
        self.mirror_plane_value = 2  # RiggingWindow MirrorPlane.YZ -> mirror across x
        self.mirror_calls = []
        self.scene = FakeScene(self)
        self.view = FakeViewScene(self)

    # -- setup ----------------------------------------------------------
    def add(self, name, kind, position, alias=None, rotation=None, parent=None, tpose=None, tpose_rotation=None):
        entry = {
            "id": name,
            "name": name,
            "alias": alias,
            "kind": kind,
            "type": "Point" if kind == "main_point" else ("Joint" if kind == "joint" else "Box"),
            "position": list(position),
            "autoposing_locked": None,
            "character": "Rig(0)",
        }
        self.controllers.append(entry)
        self.positions[(name, "world")] = {0: [float(value) for value in position]}
        if rotation is not None:
            self.rotations[(name, "local")] = {0: FakeRotation(rotation)}
            self.rotations[(name, "world")] = {0: FakeRotation(rotation)}
        if parent:
            self.parents[name] = parent
        if tpose is not None:
            self.tpose[name] = {"position": [float(value) for value in tpose], "rotation": None}
            if tpose_rotation is not None:
                self.tpose[name]["rotation"] = FakeRotation(tpose_rotation)
        return entry

    def by_id(self):
        return {entry["id"]: entry for entry in self.controllers}

    def by_name(self):
        return {entry["name"]: entry for entry in self.controllers}

    def set_position(self, name, values, frame=0, space="world"):
        self.positions.setdefault((name, space), {})[frame] = [float(value) for value in values]

    def position(self, name, frame=0, space="world"):
        frames = self.positions.get((name, space), {})
        if frame in frames:
            return list(frames[frame])
        return None


def populate(world):
    """A Cascy-like character: symmetric hands, a head, a box with a parent and a joint."""
    world.add("hand_MainPoint_l", "main_point", [25.0, 96.0, 0.0], "left_hand", tpose=[55.0, 114.0, 2.0])
    world.add("hand_MainPoint_r", "main_point", [-25.0, 96.0, 0.0], "right_hand", tpose=[-55.0, 114.0, 2.0])
    world.add("head_MainPoint", "main_point", [0.0, 162.0, 0.0], "head", tpose=[0.0, 163.0, 0.0])
    world.add("pelvis_MainPoint", "main_point", [0.0, 95.0, 0.0], "pelvis", tpose=[0.0, 96.0, 0.0])
    world.add(
        "hand_Box_l",
        "box",
        [25.0, 96.0, 0.0],
        parent="hand_MainPoint_l",
        rotation=[2.0, 0.0, 0.0],
        tpose=[55.0, 114.0, 2.0],
        tpose_rotation=[5.0, 0.0, 0.0],
    )
    world.add(
        "hand_Box_r",
        "box",
        [-25.0, 96.0, 0.0],
        parent="hand_MainPoint_r",
        rotation=[-2.0, 0.0, 0.0],
        tpose=[-55.0, 114.0, 2.0],
        tpose_rotation=[-5.0, 0.0, 0.0],
    )
    world.add("hand_joint_l", "joint", [25.0, 95.0, 0.0], parent="hand_Box_l", rotation=[1.0, 0.0, 0.0])
    for frame in (10, 20):
        for entry in list(world.controllers):
            world.positions.setdefault((entry["id"], "world"), {})[frame] = list(entry["position"])
            world.positions.setdefault((entry["id"], "local"), {})[frame] = [0.0, 0.0, 0.0]
    return world


# ---------------------------------------------------------------------------
# Fake pose module (the T16 helpers pose_ops calls)
# ---------------------------------------------------------------------------


def _make_pose_module(name, world):
    module = types.ModuleType(name)

    def read_position(object_id, frame, space, low):
        frames = world.positions.get((object_id, space), {})
        if frame in frames:
            return list(frames[frame]), frame
        if low is None:
            return None, None
        candidates = [item for item in frames if low <= item <= frame]
        if not candidates:
            return None, None
        used = max(candidates)
        return list(frames[used]), used

    def read_rotation(object_id, frame, space, low):
        frames = world.rotations.get((object_id, space), {})
        if frame in frames:
            return list(frames[frame].degrees), frame
        if low is None:
            return None, None
        candidates = [item for item in frames if low <= item <= frame]
        if not candidates:
            return None, None
        used = max(candidates)
        return list(frames[used].degrees), used

    def _read_points(scene, ids, frame, space, by_id, low=None):
        points = []
        for object_id in ids:
            entry = by_id.get(object_id) or world.by_id().get(object_id) or {}
            position, used = read_position(object_id, int(frame), space, low)
            rotation, rotation_used = read_rotation(object_id, int(frame), space, low)
            point = {
                "name": entry.get("name"),
                "alias": entry.get("alias"),
                "kind": entry.get("kind"),
                "position": position,
                "rotation": rotation,
                "quaternion": None,
            }
            if rotation is None:
                point["rotation_available"] = False
            used_frames = [item for item in (used, rotation_used) if item is not None]
            if used_frames and max(used_frames) != int(frame):
                point["frame_used"] = max(used_frames)
            points.append(point)
        return points

    def _controller_entries():
        entries = list(world.controllers)
        return entries, {entry["id"]: entry for entry in entries}

    def _position_data(behaviour_viewer, object_id, space):
        entry = world.by_id().get(object_id) or {}
        if space == "local" and entry.get("kind") == "main_point":
            # Main control points only have a global position in Cascadeur 2026.2.3.
            return None, None
        return DataId("pos", object_id, space), "fake"

    def _rotation_data(behaviour_viewer, object_id, space):
        entry = world.by_id().get(object_id) or {}
        if entry.get("kind") not in ("box", "joint"):
            return None, None
        return DataId("rot", object_id, space), "fake"

    def _parent_object(behaviour_viewer, object_id):
        return world.parents.get(object_id)

    def _requested_targets(scene, ids, parsed, frame, space, key=True, low=0):
        targets = []
        for object_id, (name, operations) in zip(ids, parsed, strict=True):
            target = {"id": object_id, "name": name, "writes": []}
            for kind in ("position", "rotation"):
                if kind not in operations:
                    continue
                data = DataId("pos" if kind == "position" else "rot", object_id, space)
                readable_low = low if key else None
                current, used = (
                    read_position(object_id, frame, space, readable_low)
                    if kind == "position"
                    else read_rotation(object_id, frame, space, readable_low)
                )
                if current is None:
                    return None, {
                        "ok": False,
                        "error": f"no {kind} data for {name} at frame {frame}",
                        "hint": "key the frame first",
                    }
                target["writes"].append(
                    {
                        "kind": kind,
                        "data": data,
                        "raw": None,
                        "current": current,
                        "value": list(operations[kind]),
                    }
                )
                target[f"requested_{kind}"] = list(operations[kind])
                target["before_frame"] = used
            targets.append(target)
        return targets, None

    def _needs_extension(targets, frame):
        return any(
            target.get("before_frame") is not None and int(target["before_frame"]) != int(frame) for target in targets
        )

    def _extend_data_range(view_scene, scene, model_editor, scene_updater, frame):
        world.extend_calls.append(int(frame))
        model_editor.fit_animation_size_by_layers()
        scene_updater.generate_update()
        return max(int(frame) + 1, 101)

    def _edit_step(undo_name, fn):
        world.edits.append("MCP: " + undo_name)
        if not world.modify_result:
            return {"ok": False, "error": "aborted", "hint": "fake abort"}
        fn(world.scene._model_editor, None, world.scene._scene_updater, None)
        return {"ok": True, "undo": "MCP: " + undo_name}

    def _rotation_value(degrees):
        return FakeRotation(degrees)

    def _vector_value(raw, numbers):
        return [float(value) for value in numbers]

    def _boolean(value, name):
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.strip().lower() in ("true", "1", "yes", "on"):
            return True
        if isinstance(value, str) and value.strip().lower() in ("false", "0", "no", "off"):
            return False
        raise _tool_error(f"{name} must be true or false (got {value!r}).")

    def _names(controllers):
        if controllers is None or controllers == "" or controllers == []:
            return []
        if isinstance(controllers, str):
            return [controllers]
        if not isinstance(controllers, (list, tuple)):
            raise _tool_error(f"controllers must be a name or a list of names (got {controllers!r}).")
        return [str(item) for item in controllers]

    def distance(first, second):
        return math.sqrt(sum((float(a) - float(b)) ** 2 for a, b in zip(first, second, strict=True)))

    def angle_difference(after, requested):
        return (float(after) - float(requested) + 180.0) % 360.0 - 180.0

    for function in (
        _controller_entries,
        _read_points,
        _position_data,
        _rotation_data,
        _parent_object,
        _requested_targets,
        _needs_extension,
        _extend_data_range,
        _edit_step,
        _rotation_value,
        _vector_value,
        _boolean,
        _names,
        distance,
        angle_difference,
    ):
        setattr(module, function.__name__, function)
    return module


def _tool_error(message):
    errors = sys.modules.get(f"{PKG}.errors")
    return getattr(errors, "ToolError", RuntimeError)(message)


def _make_rig_module(name, world):
    module = types.ModuleType(name)
    rig_source = importlib.util.spec_from_file_location(f"{PKG}.rig_real", os.path.join(PACKAGE_DIR, "rig.py"))
    real = importlib.util.module_from_spec(rig_source)
    sys.modules[f"{PKG}.rig_real"] = real
    rig_source.loader.exec_module(real)
    module.RigError = real.RigError
    module.UnknownName = real.UnknownName
    module.AmbiguousName = real.AmbiguousName
    module.normalize_name = real.normalize_name
    module.name_kind = real.name_kind

    def controllers(character=None):
        return list(world.controllers)

    def resolve(names, character=None):
        if isinstance(names, str):
            names = [names]
        found = []
        for query in names:
            hits = [entry for entry in world.controllers if entry["name"] == query or entry["alias"] == query]
            if not hits:
                raise real.UnknownName(
                    f"Object or alias not found: {query!r}.",
                    hint="Call list_controllers to see the control point names and aliases.",
                )
            for hit in hits:
                if hit["id"] not in found:
                    found.append(hit["id"])
        return found

    module.controllers = controllers
    module.resolve = resolve
    return module


def _load():
    if PKG in sys.modules:
        return sys.modules[f"{PKG}.tools.pose_ops"], sys.modules[f"{PKG}.world"]

    package = types.ModuleType(PKG)
    package.__path__ = []
    sys.modules[PKG] = package

    errors = types.ModuleType(f"{PKG}.errors")

    class ToolError(Exception):
        pass

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
    common.log_tail = lambda *args, **kwargs: ["fake log line"]
    sys.modules[f"{PKG}.common"] = common
    package.common = common

    world = populate(World())
    world_holder = types.ModuleType(f"{PKG}.world")
    world_holder.world = world
    sys.modules[f"{PKG}.world"] = world_holder

    common.scenes = lambda: (None, world.view, world.scene)

    rig = _make_rig_module(f"{PKG}.rig", world)
    sys.modules[f"{PKG}.rig"] = rig
    package.rig = rig

    timeline_spec = importlib.util.spec_from_file_location(f"{PKG}.timeline", os.path.join(PACKAGE_DIR, "timeline.py"))
    timeline = importlib.util.module_from_spec(timeline_spec)
    sys.modules[f"{PKG}.timeline"] = timeline
    package.timeline = timeline
    timeline_spec.loader.exec_module(timeline)

    def set_key_at(model_editor, object_ids, frame):
        world.keys.append({"ids": list(object_ids), "frame": int(frame)})
        return {"layers": len(object_ids), "changed": len(object_ids), "already": 0}

    timeline.set_key_at = set_key_at
    timeline.boundary_info = lambda view_scene: {
        "first_frame": 0,
        "last_frame": 100,
        "first_visible_frame": 0,
        "last_visible_frame": 100,
    }

    tools_package = types.ModuleType(f"{PKG}.tools")
    tools_package.__path__ = [TOOLS_DIR]
    sys.modules[f"{PKG}.tools"] = tools_package
    package.tools = tools_package

    schema_spec = importlib.util.spec_from_file_location(f"{PKG}.tools._schema", os.path.join(TOOLS_DIR, "_schema.py"))
    schema_module = importlib.util.module_from_spec(schema_spec)
    sys.modules[f"{PKG}.tools._schema"] = schema_module
    tools_package._schema = schema_module
    schema_spec.loader.exec_module(schema_module)

    pose_module = _make_pose_module(f"{PKG}.tools.pose", world)
    sys.modules[f"{PKG}.tools.pose"] = pose_module
    tools_package.pose = pose_module

    layers_package = types.ModuleType(f"{PKG}.tools.layers")
    layers_package.ensure_unlocked = lambda object_ids: world.lock_result
    sys.modules[f"{PKG}.tools.layers"] = layers_package
    tools_package.layers = layers_package

    spec = importlib.util.spec_from_file_location(f"{PKG}.tools.pose_ops", os.path.join(TOOLS_DIR, "pose_ops.py"))
    pose_ops = importlib.util.module_from_spec(spec)
    sys.modules[f"{PKG}.tools.pose_ops"] = pose_ops
    tools_package.pose_ops = pose_ops
    spec.loader.exec_module(pose_ops)
    pose_ops.World = world
    return pose_ops, world


PoseOps = None
World_ = None


class FakeMirrorCore:
    def __init__(self, world):
        self.world = world
        self.plane = None

    def set_plane(self, plane):
        self.plane = plane

    def mirror_frame(self, ids):
        world = self.world
        world.mirror_calls.append({"ids": set(ids), "plane": self.plane})
        index = sys.modules[f"{PKG}.tools.pose_ops"].mirror_index(world.controllers)
        before = {
            entry["name"]: list(world.positions[(entry["id"], "world")].get(0, entry["position"]))
            for entry in world.controllers
        }
        normal = self.plane["normal"]
        offset = sum(float(a) * float(b) for a, b in zip(self.plane["point"], normal, strict=True))

        def reflect(position):
            distance = sum(float(position[i]) * normal[i] for i in range(3)) - offset
            return [float(position[i]) - 2.0 * distance * normal[i] for i in range(3)]

        for entry in world.controllers:
            name = entry["name"]
            counterpart = index.get(name)
            if counterpart is None:
                continue
            source = counterpart if counterpart != name else name
            self.world.set_position(name, reflect(before[source]), frame=0)


class FakeToolManager:
    def __init__(self, world):
        self.world = world

    def get_tool(self, name):
        return self

    def editor(self, scene):
        return self

    def get_character_mirror_plane(self):
        return self.world.mirror_plane_value

    def core(self):
        return FakeMirrorCore(self.world)


def setUpModule():
    global PoseOps, World_
    fake_csc = types.ModuleType("csc")
    fake_csc.math = types.SimpleNamespace(
        Rotation=types.SimpleNamespace(
            from_euler=lambda x, y, z: FakeRotation([math.degrees(x), math.degrees(y), math.degrees(z)]),
            from_quaternion=lambda quaternion: FakeRotation([0.0, 0.0, 0.0]),
        ),
        Quaternion=FakeQuaternion,
        OrthogonalTransform=FakeOrthogonalTransform,
        transforms_difference=lambda parent, own: FakeOrthogonalTransform(
            [o - p for o, p in zip(own.position, parent.position, strict=True)],
            own.rotation,
        ),
        Plane=lambda normal, point: {"normal": list(normal), "point": list(point)},
        decompose_matrix=lambda matrix: types.SimpleNamespace(
            position=[0.0, 0.0, 0.0], rotation=FakeRotation(), scale=[1.0, 1.0, 1.0]
        ),
    )
    fake_csc.layers = types.SimpleNamespace(layer=types.SimpleNamespace(Section=lambda: "empty-section"))
    fake_csc.app = types.SimpleNamespace(
        get_application=lambda: types.SimpleNamespace(
            current_scene=lambda: World_.scene if World_ else None,
            get_tools_manager=lambda: FakeToolManager(World_),
        )
    )
    _PREVIOUS_CSC = sys.modules.get("csc")
    sys.modules["csc"] = fake_csc
    PoseOps, World_ = _load()


def tearDownModule():
    previous = globals().get("_PREVIOUS_CSC")
    if previous is None:
        sys.modules.pop("csc", None)
    else:
        sys.modules["csc"] = previous


class PoseOpsTestCase(unittest.TestCase):
    def setUp(self):
        self.world = World_
        self.world.__init__()
        populate(self.world)
        self.pose_ops = PoseOps
        self.pose_ops._STORE.clear()
        self.pose_ops.pose = sys.modules[f"{PKG}.tools.pose"]
        self.pose_ops.common.scenes = lambda: (None, self.world.view, self.world.scene)
        self.pose_ops.timeline.set_key_at = lambda model_editor, object_ids, frame: (
            self.world.keys.append({"ids": list(object_ids), "frame": int(frame)})
            or {"layers": len(object_ids), "changed": len(object_ids), "already": 0}
        )
        self.pose_ops.timeline.boundary_info = lambda view_scene: {
            "first_frame": 0,
            "last_frame": 100,
            "first_visible_frame": 0,
            "last_visible_frame": 100,
        }
        fake_csc = sys.modules["csc"]
        fake_csc.app = types.SimpleNamespace(
            get_application=lambda: types.SimpleNamespace(
                current_scene=lambda: self.world.scene,
                get_tools_manager=lambda: FakeToolManager(self.world),
            )
        )
        self.world.scene.assets_manager = lambda: types.SimpleNamespace()


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


class ParseKindTests(PoseOpsTestCase):
    def test_default_and_bind(self):
        self.assertEqual(self.pose_ops.parse_kind(None), "default")
        self.assertEqual(self.pose_ops.parse_kind(""), "default")
        self.assertEqual(self.pose_ops.parse_kind(" Default "), "default")
        self.assertEqual(self.pose_ops.parse_kind("bind"), "bind")
        self.assertEqual(self.pose_ops.parse_kind("BIND_POSE"), "bind")

    def test_rejects_other_kinds(self):
        for value in ("tpose2", "ik", 1, True):
            with self.assertRaises(self.pose_ops.ToolError, msg=repr(value)):
                self.pose_ops.parse_kind(value)


class PlaneMathTests(PoseOpsTestCase):
    def test_axis_mappings(self):
        self.assertEqual(self.pose_ops.axis_from_rig_plane(2), "x")
        self.assertEqual(self.pose_ops.axis_from_rig_plane(1), "y")
        self.assertEqual(self.pose_ops.axis_from_rig_plane(0), "z")
        self.assertIsNone(self.pose_ops.axis_from_rig_plane(3))
        self.assertEqual(self.pose_ops.axis_from_behaviour_plane(0), "x")
        self.assertEqual(self.pose_ops.axis_from_behaviour_plane(1), "y")
        self.assertEqual(self.pose_ops.axis_from_behaviour_plane(2), "z")

    def test_plane_for_axis(self):
        plane = self.pose_ops.plane_for_axis("x", 0.5)
        self.assertEqual(plane["normal"], [1.0, 0.0, 0.0])
        self.assertEqual(plane["point"], [0.5, 0.0, 0.0])
        self.assertEqual(plane["offset"], 0.5)
        with self.assertRaises(self.pose_ops.ToolError):
            self.pose_ops.plane_for_axis("w")

    def test_reflect_point(self):
        x_plane = self.pose_ops.plane_for_axis("x", 0.0)
        self.assertEqual(
            [round(value, 4) for value in self.pose_ops.reflect_point([25.0, 136.0, -1.0], x_plane)],
            [-25.0, 136.0, -1.0],
        )
        offset_plane = self.pose_ops.plane_for_axis("x", 10.0)
        self.assertEqual(
            [round(value, 4) for value in self.pose_ops.reflect_point([25.0, 136.0, -1.0], offset_plane)],
            [-5.0, 136.0, -1.0],
        )
        y_plane = self.pose_ops.plane_for_axis("y", 2.0)
        self.assertEqual(
            [round(value, 4) for value in self.pose_ops.reflect_point([1.0, 5.0, 3.0], y_plane)],
            [1.0, -1.0, 3.0],
        )

    def test_reflect_quaternion(self):
        quaternion = {"w": 0.5, "x": 0.5, "y": 0.5, "z": 0.5}
        for axis, expected in (
            ("x", {"w": 0.5, "x": 0.5, "y": -0.5, "z": -0.5}),
            ("y", {"w": 0.5, "x": -0.5, "y": 0.5, "z": -0.5}),
            ("z", {"w": 0.5, "x": -0.5, "y": -0.5, "z": 0.5}),
        ):
            self.assertEqual(self.pose_ops.reflect_quaternion(quaternion, {"axis": axis}), expected)


class MirrorIndexTests(PoseOpsTestCase):
    def test_pairs_cascadeur_names(self):
        index = self.pose_ops.mirror_index(self.world.controllers)
        self.assertEqual(index["hand_MainPoint_l"], "hand_MainPoint_r")
        self.assertEqual(index["hand_MainPoint_r"], "hand_MainPoint_l")
        self.assertEqual(index["head_MainPoint"], "head_MainPoint")
        self.assertEqual(index["pelvis_MainPoint"], "pelvis_MainPoint")
        self.assertEqual(index["hand_Box_l"], "hand_Box_r")
        # The fake rig has a left wrist joint but no right one, so it stays unpaired.
        self.assertIsNone(index["hand_joint_l"])

    def test_pairs_mixamo_and_roblox_names(self):
        entries = [
            {"name": "mixamorig:LeftHand_MainPoint", "kind": "main_point"},
            {"name": "mixamorig:RightHand_MainPoint", "kind": "main_point"},
            {"name": "LeftFoot_MainPoint", "kind": "main_point"},
            {"name": "RightFoot_MainPoint", "kind": "main_point"},
            {"name": "lowerarm_MainPoint_l", "kind": "main_point"},
            {"name": "lowerarm_MainPoint_r", "kind": "main_point"},
        ]
        index = self.pose_ops.mirror_index(entries)
        self.assertEqual(index["mixamorig:LeftHand_MainPoint"], "mixamorig:RightHand_MainPoint")
        self.assertEqual(index["LeftFoot_MainPoint"], "RightFoot_MainPoint")
        self.assertEqual(index["lowerarm_MainPoint_l"], "lowerarm_MainPoint_r")

    def test_unpaired_and_duplicate_sides_stay_none(self):
        entries = [
            {"name": "hand_Box_l", "kind": "box"},
            {"name": "hand_Box_r", "kind": "box"},
            {"name": "mixamorig:LeftHand_Box", "kind": "box"},
            {"name": "foot_MainPoint_l", "kind": "main_point"},
        ]
        index = self.pose_ops.mirror_index(entries)
        # Two names normalise to the same left hand box, so neither side is guessed.
        self.assertIsNone(index["hand_Box_l"])
        self.assertIsNone(index["mixamorig:LeftHand_Box"])
        self.assertIsNone(index["foot_MainPoint_l"])

    def test_centre_names_are_their_own_counterpart(self):
        entries = [
            {"name": "pelvis_MainPoint", "kind": "main_point"},
            {"name": "chest_MainPoint", "kind": "main_point"},
            {"name": "center_of_mass", "kind": "other"},
        ]
        index = self.pose_ops.mirror_index(entries)
        self.assertEqual(index["pelvis_MainPoint"], "pelvis_MainPoint")
        self.assertEqual(index["chest_MainPoint"], "chest_MainPoint")
        self.assertEqual(index["center_of_mass"], "center_of_mass")


class StoreTests(PoseOpsTestCase):
    def test_next_pose_name(self):
        self.assertEqual(self.pose_ops.next_pose_name(), "pose_1")
        self.assertEqual(self.pose_ops.next_pose_name({"pose_1": {}, "pose_3": {}}), "pose_2")

    def test_pose_summary(self):
        summary = self.pose_ops.pose_summary(
            {"name": "pose_1", "frame": 0, "controllers": [1, 2], "created": "now", "scene": "Cascy"}
        )
        self.assertEqual(summary, {"name": "pose_1", "frame": 0, "controllers": 2, "created": "now", "scene": "Cascy"})

    def test_mirror_position_plan(self):
        index = self.pose_ops.mirror_index(self.world.controllers)
        plane = self.pose_ops.plane_for_axis("x", 0.0)
        plan = self.pose_ops.mirror_position_plan(
            [
                {"name": "hand_MainPoint_l", "position": [25.0, 136.0, 0.0]},
                {"name": "head_MainPoint", "position": [1.0, 162.0, 0.0]},
                {"name": "missing", "position": [0.0, 0.0, 0.0]},
            ],
            index,
            plane,
        )
        self.assertEqual(plan[0]["target"], "hand_MainPoint_r")
        self.assertEqual([round(value, 4) for value in plan[0]["position"]], [-25.0, 136.0, 0.0])
        self.assertEqual(plan[1]["target"], "head_MainPoint")
        self.assertEqual([round(value, 4) for value in plan[1]["position"]], [-1.0, 162.0, 0.0])
        self.assertIsNone(plan[2]["target"])


# ---------------------------------------------------------------------------
# copy_pose
# ---------------------------------------------------------------------------


class CopyPoseTests(PoseOpsTestCase):
    def test_stores_the_main_points_and_returns_a_name(self):
        result = self.pose_ops.copy_pose(frame=0)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["name"], "pose_1")
        self.assertEqual(result["frame"], 0)
        self.assertEqual(result["count"], 4)
        names = [item["name"] for item in result["controllers"]]
        self.assertEqual(names, ["hand_MainPoint_l", "hand_MainPoint_r", "head_MainPoint", "pelvis_MainPoint"])
        hand = result["controllers"][0]
        self.assertEqual(hand["position"], [25.0, 96.0, 0.0])
        self.assertIsNone(hand["rotation"])
        self.assertIn("pose_1", self.pose_ops._STORE)
        self.assertEqual(self.world.edits, [])
        self.assertEqual(self.world.playhead, 0)

    def test_second_copy_gets_the_next_name(self):
        self.assertEqual(self.pose_ops.copy_pose(frame=0)["name"], "pose_1")
        self.assertEqual(self.pose_ops.copy_pose(frame=0)["name"], "pose_2")
        self.assertEqual(len(self.pose_ops._STORE), 2)

    def test_named_controllers_include_local_rotations(self):
        result = self.pose_ops.copy_pose(frame=0, controllers=["hand_Box_l"])
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["controllers"][0]["rotation"], [2.0, 0.0, 0.0])

    def test_frame_without_data_is_refused(self):
        result = self.pose_ops.copy_pose(frame=50)
        self.assertFalse(result["ok"], result)
        self.assertIn("no pose data at frame 50", result["error"])
        self.assertIn("set_key", result["hint"])
        self.assertEqual(self.pose_ops._STORE, {})

    def test_unknown_alias_is_returned_as_an_error(self):
        result = self.pose_ops.copy_pose(frame=0, controllers=["not_a_bone"])
        self.assertFalse(result["ok"], result)
        self.assertIn("not_a_bone", result["error"])

    def test_bad_frame_is_refused(self):
        for value in ("ten", -1):
            self.assertFalse(self.pose_ops.copy_pose(frame=value)["ok"], value)


# ---------------------------------------------------------------------------
# paste_pose
# ---------------------------------------------------------------------------


class PastePoseTests(PoseOpsTestCase):
    def test_paste_writes_the_same_positions_and_one_undo_step(self):
        copied = self.pose_ops.copy_pose(frame=0)
        self.assertTrue(copied["ok"], copied)
        self.world.set_position("hand_MainPoint_l", [25.0, 136.0, 0.0], frame=0)
        result = self.pose_ops.paste_pose(frame=20)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["frame"], 20)
        self.assertFalse(result["mirrored"])
        self.assertTrue(result["keyed"])
        self.assertEqual(result["name"], "pose_1")
        self.assertEqual(result["undo"], "MCP: paste_pose")
        self.assertEqual(result["undo_steps"], ["MCP: paste_pose"])
        self.assertEqual(result["undo_count"], 1)
        self.assertEqual(self.world.edits, ["MCP: paste_pose"])
        hand = next(item for item in result["controllers"] if item["name"] == "hand_MainPoint_l")
        self.assertEqual(hand["requested_position"], [25.0, 96.0, 0.0])
        self.assertAlmostEqual(hand["position_error_cm"], 0.0)
        self.assertEqual(self.world.position("hand_MainPoint_l", frame=20), [25.0, 96.0, 0.0])
        self.assertEqual(self.world.keys[0]["frame"], 20)
        self.assertEqual(len(self.world.keys[0]["ids"]), 4)

    def test_paste_extends_and_keys_an_unkeyed_frame(self):
        self.pose_ops.copy_pose(frame=0)
        for entry in self.world.controllers:
            for space in ("world", "local"):
                self.world.positions.get((entry["id"], space), {}).pop(20, None)
        result = self.pose_ops.paste_pose(frame=20)
        self.assertTrue(result["ok"], result)
        self.assertEqual(self.world.extend_calls, [20])
        self.assertEqual(result["extended_to"], 101)

    def test_mirrored_paste_swaps_sides_and_reflects(self):
        self.pose_ops.copy_pose(frame=0)
        self.world.set_position("hand_MainPoint_l", [25.0, 136.0, 0.0], frame=0)
        self.pose_ops.copy_pose(frame=0)  # overwrite pose_2? no: stores the raised hand as pose_2
        result = self.pose_ops.paste_pose(frame=20, name="pose_2", mirrored=True)
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["mirrored"])
        self.assertEqual(self.world.position("hand_MainPoint_r", frame=20), [-25.0, 136.0, 0.0])
        self.assertEqual(self.world.position("hand_MainPoint_l", frame=20), [25.0, 96.0, 0.0])
        self.assertEqual(self.world.position("head_MainPoint", frame=20), [0.0, 162.0, 0.0])

    def test_paste_latest_copy_by_default(self):
        self.pose_ops.copy_pose(frame=0)
        self.world.set_position("hand_MainPoint_l", [25.0, 50.0, 0.0], frame=10)
        self.pose_ops.copy_pose(frame=10)
        result = self.pose_ops.paste_pose(frame=20)
        self.assertEqual(result["name"], "pose_2")
        hand = next(item for item in result["controllers"] if item["name"] == "hand_MainPoint_l")
        self.assertEqual(hand["requested_position"], [25.0, 50.0, 0.0])

    def test_no_copy_named_error(self):
        self.pose_ops.copy_pose(frame=0)
        result = self.pose_ops.paste_pose(frame=20, name="nope")
        self.assertFalse(result["ok"], result)
        self.assertIn("nope", result["error"])
        self.assertEqual(result["available"][0]["name"], "pose_1")

    def test_empty_store_error(self):
        result = self.pose_ops.paste_pose(frame=20)
        self.assertFalse(result["ok"], result)
        self.assertIn("copy_pose", result["hint"])

    def test_locked_layer_refuses_paste(self):
        self.pose_ops.copy_pose(frame=0)
        self.world.lock_result = {"ok": False, "error": "locked layer", "hint": "unlock"}
        result = self.pose_ops.paste_pose(frame=20)
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["error"], "locked layer")
        self.assertEqual(self.world.edits, [])

    def test_bad_mirrored_and_key_arguments(self):
        self.pose_ops.copy_pose(frame=0)
        for arguments in ({"mirrored": "maybe"}, {"key": "maybe"}):
            result = self.pose_ops.paste_pose(frame=20, **arguments)
            self.assertFalse(result["ok"], (arguments, result))


# ---------------------------------------------------------------------------
# reset_pose
# ---------------------------------------------------------------------------


class ResetPoseTests(PoseOpsTestCase):
    def test_default_writes_tpose_and_one_undo_step(self):
        self.world.set_position("hand_MainPoint_l", [25.0, 140.0, 0.0], frame=0)
        result = self.pose_ops.reset_pose(kind="default", frame=0)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["kind"], "default")
        self.assertEqual(result["undo"], "MCP: reset_pose")
        self.assertEqual(result["undo_steps"], ["MCP: reset_pose"])
        self.assertEqual(result["undo_count"], 1)
        self.assertEqual(self.world.edits, ["MCP: reset_pose"])
        hand = next(item for item in result["controllers"] if item["name"] == "hand_MainPoint_l")
        self.assertEqual(hand["expected_position"], [55.0, 114.0, 2.0])
        self.assertEqual(hand["after_position"], [55.0, 114.0, 2.0])
        self.assertAlmostEqual(hand["position_error_cm"], 0.0)
        self.assertEqual(self.world.position("hand_Box_l", frame=0), [55.0, 114.0, 2.0])
        # The fake wrist joint has no TPose, so six of the seven controllers are written.
        self.assertEqual(len(self.world.keys[0]["ids"]), 6)
        self.assertIn("hand_joint_l", result["skipped"])

    def test_default_keys_and_extends_an_unkeyed_frame(self):
        for entry in self.world.controllers:
            for space in ("world", "local"):
                self.world.positions.get((entry["id"], space), {}).pop(20, None)
        result = self.pose_ops.reset_pose(kind="default", frame=20)
        self.assertTrue(result["ok"], result)
        self.assertEqual(self.world.extend_calls, [20])
        self.assertEqual(self.world.keys[0]["frame"], 20)

    def test_bind_without_mesh_data_fails_cleanly(self):
        result = self.pose_ops.reset_pose(kind="bind")
        self.assertFalse(result["ok"], result)
        self.assertIn("No bind pose data", result["error"])
        self.assertEqual(self.world.edits, [])

    def test_unknown_kind_and_names(self):
        self.assertFalse(self.pose_ops.reset_pose(kind="ik")["ok"])
        self.assertFalse(self.pose_ops.reset_pose(kind="default", controllers=["not_a_bone"])["ok"])

    def test_locked_layer_refuses_reset(self):
        self.world.lock_result = {"ok": False, "error": "locked", "hint": "unlock"}
        result = self.pose_ops.reset_pose(kind="default")
        self.assertFalse(result["ok"], result)
        self.assertEqual(self.world.edits, [])

    def test_named_controllers_are_the_only_targets(self):
        result = self.pose_ops.reset_pose(kind="default", controllers=["left_hand"])
        self.assertTrue(result["ok"], result)
        self.assertEqual([item["name"] for item in result["controllers"]], ["hand_MainPoint_l"])
        self.assertEqual(self.world.position("head_MainPoint", frame=0), [0.0, 162.0, 0.0])


# ---------------------------------------------------------------------------
# mirror_pose
# ---------------------------------------------------------------------------


class MirrorPoseTests(PoseOpsTestCase):
    def test_mirror_swaps_the_hands_and_reports_the_plane(self):
        self.world.set_position("hand_MainPoint_l", [25.0, 136.0, 0.0], frame=0)
        result = self.pose_ops.mirror_pose(frame=0)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["frame"], 0)
        self.assertEqual(result["plane"]["axis"], "x")
        self.assertEqual(result["plane"]["offset"], 0.0)
        self.assertEqual(result["plane"]["source"], "RiggingToolWindowTool.get_character_mirror_plane")
        self.assertEqual(result["undo"], "Mirror")
        self.assertEqual(result["undo_steps"], ["Mirror"])
        self.assertEqual(result["undo_count"], 1)
        self.assertIn("one undo step", result["undo_note"])
        self.assertAlmostEqual(result["max_symmetry_error_cm"], 0.0)
        self.assertEqual(self.world.position("hand_MainPoint_r", frame=0), [-25.0, 136.0, 0.0])
        self.assertEqual(self.world.position("hand_MainPoint_l", frame=0), [25.0, 96.0, 0.0])
        self.assertEqual(self.world.edits, [])
        self.assertEqual(len(self.world.mirror_calls), 1)
        self.assertEqual(len(self.world.mirror_calls[0]["ids"]), 7)
        self.assertEqual(self.world.mirror_calls[0]["plane"]["normal"], [1.0, 0.0, 0.0])

    def test_named_controllers_are_reported_but_the_whole_rig_is_mirrored(self):
        self.world.set_position("hand_MainPoint_l", [25.0, 136.0, 0.0], frame=0)
        result = self.pose_ops.mirror_pose(frame=0, controllers=["left_hand"])
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["scope"], "whole character")
        self.assertIn("whole character", result["hint"])
        self.assertEqual(result["requested"], ["left_hand"])
        names = [item["name"] for item in result["controllers"]]
        self.assertEqual(names, ["hand_MainPoint_l", "hand_MainPoint_r"])
        self.assertEqual(len(self.world.mirror_calls[0]["ids"]), 7)

    def test_frame_without_data_is_refused(self):
        result = self.pose_ops.mirror_pose(frame=50)
        self.assertFalse(result["ok"], result)
        self.assertIn("no pose data at frame 50", result["error"])
        self.assertEqual(self.world.mirror_calls, [])

    def test_unknown_controller_is_returned_as_an_error(self):
        result = self.pose_ops.mirror_pose(frame=0, controllers=["not_a_bone"])
        self.assertFalse(result["ok"], result)
        self.assertIn("not_a_bone", result["error"])

    def test_locked_layer_refuses_mirror(self):
        self.world.lock_result = {"ok": False, "error": "locked", "hint": "unlock"}
        result = self.pose_ops.mirror_pose(frame=0)
        self.assertFalse(result["ok"], result)
        self.assertEqual(self.world.mirror_calls, [])

    def test_moves_the_playhead_back_after_a_non_current_frame(self):
        self.world.playhead = 7
        result = self.pose_ops.mirror_pose(frame=0)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["frame"], 0)
        self.assertEqual(self.world.playhead, 7)

    def test_plane_offset_comes_from_the_pelvis(self):
        self.world.set_position("pelvis_MainPoint", [10.0, 95.0, 0.0], frame=0)
        self.world.set_position("hand_MainPoint_l", [25.0, 136.0, 0.0], frame=0)
        result = self.pose_ops.mirror_pose(frame=0)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["plane"]["offset"], 10.0)
        self.assertEqual(result["plane"]["point"], [10.0, 0.0, 0.0])


if __name__ == "__main__":
    unittest.main()
