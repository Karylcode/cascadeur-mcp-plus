"""AutoPhysics tools (T18).

Cascadeur's Physics Assistant ("AutoPhysics") solves a physically plausible motion for the
selected character and can bake that result into the animation. The tool lives in the QML
toolbar; the Python AutoPhysicTool only exposes turn_off()/turn_off_all_fulcrum_points(), so the
state and the buttons are driven through the same QML manager and action ids the toolbar uses:

* state: the toolbar's toolManager QML object exposes isAutoPhysicsEnabled,
  isFrozenAutoPhysics, isAllFulcrumPoints and isOnlyKeyFulcrumPoints; the buttons' active
  property is the fallback (Cascadeur 2026.2.3).
* actions: AutoPhysicsTool.Switch Auto Physics, .Switch Frozen Auto Physics,
  .Snap to Auto Physics, .Set priority frame and .Show all fucrum points (the misspelling is
  Cascadeur's own).
* priority frames: stored in the AutoPhysics behaviour's priority_frame data on the character's
  centre-of-mass object. physics_set_priority_frame moves the playhead to the requested frame and
  runs Cascadeur's own action, so the undo step is Cascadeur's.

Snapping is asynchronous: the Snap action may compute/settle and can open the "Which frames
should AutoPhysics results be applied to?" dialog. physics_snap is therefore a T03 background job:
fn records the pose, triggers the action and returns immediately; poll answers the dialog, watches
the log/pose until the result stops changing, then reports the frames whose pelvis / hands / feet /
head values changed. Nothing in this module sleeps or blocks the GUI thread; the setters wait for
the QML state on the HTTP thread (same pattern as T34's display tools).
"""

from __future__ import annotations

import functools
import os
import sys
import time
import traceback

from .. import common, rig, timeline
from ..errors import ToolError
from ._schema import schema

# ---------------------------------------------------------------------------
# Cascadeur action ids and QML state
# ---------------------------------------------------------------------------

ACTION_ENABLE = "AutoPhysicsTool.Switch Auto Physics"
ACTION_FREEZE = "AutoPhysicsTool.Switch Frozen Auto Physics"
ACTION_SNAP = "AutoPhysicsTool.Snap to Auto Physics"
ACTION_PRIORITY = "AutoPhysicsTool.Set priority frame"
ACTION_FULCRUMS = "AutoPhysicsTool.Show all fucrum points"

ALL_ACTIONS = (ACTION_ENABLE, ACTION_FREEZE, ACTION_SNAP, ACTION_PRIORITY, ACTION_FULCRUMS)

# Toolbar actions whose QML context carries the shared toolManager object. The AutoPhysics strip
# is only instantiated while its tool is open on some layouts, so other toolbars are searched too;
# isAutoPhysicsEnabled lives on the manager itself and is readable either way.
MANAGER_ACTIONS = (
    ACTION_FULCRUMS,
    "GhostTool.Disable ghost",
    "TrajectoryTool.Switch trajectory activity",
    "VisualizerStateController.AllModes.View mode",
    "View.Silhouette mode",
    "View.Surface",
)

# QML toolManager properties the AutoPhysics toolbar binds to.
STATE_PROPS = {
    "enabled": "isAutoPhysicsEnabled",
    "frozen": "isFrozenAutoPhysics",
    "show_fulcrums": "isAllFulcrumPoints",
    "only_key_fulcrums": "isOnlyKeyFulcrumPoints",
}

# Button fallbacks when the QML manager object cannot be reached.
STATE_ACTIONS = {
    "enabled": ACTION_ENABLE,
    "frozen": ACTION_FREEZE,
    "show_fulcrums": ACTION_FULCRUMS,
}

SNAP_STATE_KEY = "_cascadeur_mcp_plus_physics_snap"
POSITION_TOLERANCE_CM = 1e-3
ROTATION_TOLERANCE_DEG = 1e-3
PROBE_FRAMES = 8
STABLE_POLLS = 8
SETTLE_SECONDS = 1.5
POST_APPLY_GRACE = 10.0
NO_CHANGE_SECONDS = 60.0
DIALOG_SECONDS = 20.0
APPLY_CLICK_SECONDS = 1.0
MAX_LAST_CHANGES = 200
# A snap state older than this is stale even if the jobs registry cannot be read. It is longer
# than the job_timeout (300 s) so a real long job still fails through jobs.py first.
SNAP_STATE_TTL_S = 360.0

# Buttons that answer the "which frames" dialog; All Frames bakes the whole animation.
ALL_FRAMES_LABELS = ("all frames", "all")
ONLY_KEYS_LABELS = ("only keys", "keys")
APPLY_LABELS = ("apply autophysics", "apply", "ok")
YES_LABELS = ("yes", "add")
CANCEL_LABELS = ("cancel", "close", "no")

ERROR_KEYWORDS = (
    "autophysics error",
    "wrong cm",
    "no working autophysics",
    "unknown problem in autophysics",
    "not enough frames",
    "no sections",
    "can't set priority frame",
    "cannot set priority frame",
    "unable to select automatic center of mass",
)


def _now() -> float:
    """Monotonic clock; a function so the unit tests can drive the state expiry."""
    return time.monotonic()


class _BadArgument(ToolError):
    """A tool argument is invalid; carries the hint that belongs in the result."""

    def __init__(self, message: str, hint: str):
        super().__init__(message)
        self.hint = hint


def _tool(hint: str):
    """Turn exceptions into the shared {"ok": false, "error", "hint"} result."""

    def decorate(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except _BadArgument as exc:
                return common.fail(str(exc), exc.hint)
            except ToolError as exc:
                return common.fail(str(exc), getattr(exc, "hint", None) or hint)
            except Exception as exc:  # noqa: BLE001 - tool boundary: report instead of raising
                return common.fail(
                    f"{type(exc).__name__}: {exc}",
                    hint + " get_log has the traceback.",
                    traceback=traceback.format_exc(),
                )

        return wrapper

    return decorate


def _bool(value, parameter: str) -> bool:
    if not isinstance(value, bool):
        raise _BadArgument(
            f"{parameter} must be true or false (got {value!r}).",
            f"Pass {parameter}=true or {parameter}=false.",
        )
    return value


# ---------------------------------------------------------------------------
# Reading the Physics Assistant state (GUI thread)
# ---------------------------------------------------------------------------


def _manager_has_physics_state(manager) -> bool:
    """True when the QML object really exposes the AutoPhysics boolean properties.

    Some QML contexts carry a generic toolManager without those properties; property() returns
    None for them instead of raising, so a successful read is not enough here.
    """
    try:
        value = manager.property("isAutoPhysicsEnabled")
    except Exception:
        return False
    return isinstance(value, bool) or isinstance(value, int)


def _auto_physics_manager():
    """The QML toolManager that holds the Physics Assistant state, or None when unreachable.

    The toolbar binds its buttons to toolManager.isAutoPhysicsEnabled etc., so the manager is the
    same source of truth the interface draws from. It is found through the context of a toolbar
    element (T34's display tools use the same technique) or the main window's root context. The
    AutoPhysics strip may not be instantiated before its tool is opened, so other toolbars that
    share toolManager are searched too; the properties live on the manager, not on the strip.
    """
    try:
        from PySide6.QtQml import QQmlEngine

        from .. import ui
    except Exception:
        return None
    wanted = set(MANAGER_ACTIONS)
    for window in ui.quick_windows(include_hidden=True):
        try:
            context = QQmlEngine.contextForObject(window.contentItem())
            manager = context.contextProperty("toolManager") if context is not None else None
            if manager is not None and _manager_has_physics_state(manager):
                return manager
        except Exception:
            pass
        for item in ui._walk(window, include_hidden=True):
            try:
                action_id = item.property("actionId")
            except Exception:
                continue
            if not isinstance(action_id, str) or action_id not in wanted:
                continue
            try:
                context = QQmlEngine.contextForObject(item)
                manager = context.contextProperty("toolManager") if context is not None else None
            except Exception:
                manager = None
            if manager is not None and _manager_has_physics_state(manager):
                return manager
    return None


def _bool_prop(obj, name):
    """Read a boolean QML property; int 0/1 is accepted; unknown values return None."""
    try:
        value = obj.property(name)
    except Exception:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return bool(value)
    return None


def _action_states():
    """{actionId: bool} for the AutoPhysics buttons found in the QML tree (fallback)."""
    try:
        from .. import ui
    except Exception:
        return {}
    wanted = set(ALL_ACTIONS)
    states: dict[str, bool] = {}
    for window in ui.quick_windows(include_hidden=True):
        for item in ui._walk(window, include_hidden=True):
            try:
                action_id = item.property("actionId")
            except Exception:
                continue
            if not isinstance(action_id, str) or action_id not in wanted:
                continue
            try:
                active = item.property("active") is True or item.property("checked") is True
            except Exception:
                active = False
            states[action_id] = states.get(action_id, False) or active
    return states


def _priority_data_ids():
    """Data ids of the AutoPhysics behaviour's priority_frame values (may be empty)."""
    try:
        _, _view_scene, scene = common.scenes()
        bv = scene.model_viewer().behaviour_viewer()
        if not bv.is_valid_behaviour_type("AutoPhysics"):
            return []
        behaviours = list(bv.get_behaviours("AutoPhysics"))
    except Exception:
        return []
    data_ids = []
    for behaviour in behaviours:
        try:
            data_id = bv.get_behaviour_data(behaviour, "priority_frame")
        except Exception:
            continue
        if data_id is None:
            continue
        try:
            if data_id.is_null():
                continue
        except Exception:
            pass
        data_ids.append(data_id)
    return data_ids


def _truthy(value) -> bool:
    if value is None:
        return False
    if isinstance(value, (bool, int, float)):
        return bool(value)
    try:
        return any(bool(item) for item in value)
    except TypeError:
        return bool(value)


def _is_null(value) -> bool:
    if value is None:
        return True
    try:
        return bool(value.is_null())
    except AttributeError:
        return False


def _priority_object_ids():
    """Object ids that own the AutoPhysics behaviour (the centre-of-mass objects)."""
    try:
        _, _view_scene, scene = common.scenes()
        bv = scene.model_viewer().behaviour_viewer()
        if not bv.is_valid_behaviour_type("AutoPhysics"):
            return []
        behaviours = list(bv.get_behaviours("AutoPhysics"))
    except Exception:
        return []
    object_ids = []
    for behaviour in behaviours:
        try:
            owner = bv.get_behaviour_owner(behaviour)
        except Exception:
            continue
        if _is_null(owner):
            continue
        if owner not in object_ids:
            object_ids.append(owner)
    return object_ids


def _ensure_unlocked(object_ids):
    """Return T23's layer-lock failure for object_ids, or None when they may be changed."""
    if not object_ids:
        return None
    try:
        from .layers import ensure_unlocked
    except Exception:
        return None
    try:
        return ensure_unlocked(object_ids)
    except Exception as exc:  # noqa: BLE001 - a guard failure must not crash the tool
        return common.fail(
            f"Could not check the layer lock state: {type(exc).__name__}: {exc}",
            hint="Unlock the layers manually or check get_log, then retry.",
        )


def _priority_frames(data_ids=None):
    """Frame numbers marked as AutoPhysics priority frames, or None when unreadable."""
    data_ids = _priority_data_ids() if data_ids is None else data_ids
    if not data_ids:
        return []
    try:
        _, view_scene, scene = common.scenes()
        info = timeline.boundary_info(view_scene)
        dv = scene.model_viewer().data_viewer()
    except Exception:
        return None
    frames = set()
    for data_id in data_ids:
        try:
            data = dv.get_data(data_id)
            mode = getattr(data, "mode", None)
            mode_name = str(getattr(mode, "name", mode)).lower()
            animated = "animation" in mode_name
        except Exception:
            animated = True
        if not animated:
            try:
                if _truthy(dv.get_data_value(data_id)):
                    frames.add(info["first_frame"])
            except Exception:
                pass
            continue
        for frame in range(info["first_frame"], info["last_frame"] + 1):
            try:
                if _truthy(dv.get_data_value(data_id, frame)):
                    frames.add(frame)
            except Exception:
                continue
    return sorted(frames)


def _current_frame(scene) -> int:
    try:
        return int(scene.get_current_frame(False))
    except Exception:
        return int(scene.get_current_frame())


def _read_state() -> dict:
    """Read the Physics Assistant state of the active scene (GUI thread)."""
    manager = _auto_physics_manager()
    state = {"enabled": None, "frozen": None, "show_fulcrums": None, "only_key_fulcrums": None}
    source = None
    if manager is not None:
        for key, prop in STATE_PROPS.items():
            value = _bool_prop(manager, prop)
            if value is not None:
                state[key] = value
        source = "toolManager"
    missing = [key for key in STATE_ACTIONS if state[key] is None]
    if missing:
        flags = _action_states()
        for key in missing:
            action = STATE_ACTIONS[key]
            if action in flags:
                state[key] = flags[action]
                source = source or "toolbar buttons"
    if source is None:
        source = "unavailable"

    _, view_scene, scene = common.scenes()
    boundary = timeline.boundary_info(view_scene)
    fps = timeline.scene_fps(view_scene)
    gravity = None
    try:
        gravity = float(view_scene.gravity_per_frame())
    except Exception:
        gravity = None
    if gravity is None and fps:
        gravity = 980.0 / (fps**2)
    data_ids = _priority_data_ids()
    data = {
        **state,
        "source": source,
        "current_frame": _current_frame(scene),
        "first_frame": boundary["first_frame"],
        "last_frame": boundary["last_frame"],
        "fps": fps,
        "gravity_per_frame": gravity,
        "priority_frames": _priority_frames(data_ids),
        "auto_physics_behaviours": len(data_ids),
    }
    if fps:
        data["gravity_cm_per_frame2"] = round(980.0 / (fps**2), 6)
    return data


# ---------------------------------------------------------------------------
# Running actions and waiting for their state (HTTP thread orchestration)
# ---------------------------------------------------------------------------


def _call_action(key: str) -> None:
    """Trigger one of Cascadeur's own actions, like its toolbar button."""
    import csc

    csc.app.get_application().get_action_manager().call_action(key)


def _gui_read() -> dict:
    from .. import dispatch

    return dispatch.call_on_gui(_read_state, {}, 30.0)


def _gui_action(action_id: str) -> None:
    from .. import dispatch

    dispatch.call_on_gui(_call_action, {"key": action_id}, 30.0)


def _settle(predicate, timeout: float = 3.0) -> dict:
    """Poll the Physics Assistant state on this HTTP thread until predicate(state).

    Sleeping here (not on the GUI thread) lets Cascadeur process the action and repaint.
    """
    deadline = time.monotonic() + max(0.0, timeout)
    delay = 0.1
    while True:
        time.sleep(delay)
        state = _gui_read()
        if predicate(state):
            return state
        if time.monotonic() >= deadline:
            return state
        delay = min(delay * 1.4, 0.4)


def _set_flag(key: str, action: str, want: bool, label: str) -> dict:
    before = _gui_read()
    current = before.get(key)
    if current is None:
        return common.fail(
            f"Cascadeur does not report the Physics Assistant {label} state, so it was not changed.",
            "Open Cascadeur's main window (the AutoPhysics toolbar must be loaded) and retry; get_log may explain why.",
            source=before.get("source"),
        )
    if current is want:
        return common.ok(changed=False, **{key: want}, source=before.get("source"))
    _gui_action(action)
    after = _settle(lambda state: state.get(key) is want, timeout=3.0)
    if after.get(key) is not want:
        return common.fail(
            f"Physics Assistant {label} did not change to {want!r} (reported {after.get(key)!r}).",
            "Make sure a scene with a character is open and Cascadeur is responsive; get_log may explain the rejection.",
            **{key: after.get(key)},
            source=after.get("source"),
        )
    return common.ok(changed=True, **{key: want}, source=after.get("source"))


# ---------------------------------------------------------------------------
# Pose sampling and comparison (GUI thread)
# ---------------------------------------------------------------------------


def _vector(value):
    """A numeric list for a Data value (vectors, rotations, quaternions), or None."""
    if value is None:
        return None
    if isinstance(value, (int, float, bool)):
        return [float(value)]
    try:
        return [float(item) for item in value]
    except TypeError:
        try:
            import math

            return [math.degrees(float(angle)) for angle in value.to_euler_angles_x_y_z()]
        except Exception:
            return None


def _data_ids(object_id, bv):
    """(position data id, rotation data id) of an object's Transform behaviour."""
    try:
        behaviour = bv.get_behaviour_by_name(object_id, "Transform")
        if _is_null(behaviour):
            return None, None
    except Exception:
        return None, None
    ids = []
    for name in ("global_position", "global_rotation"):
        try:
            data_id = bv.get_behaviour_data(behaviour, name)
            if not _is_null(data_id):
                ids.append(data_id)
            else:
                ids.append(None)
        except Exception:
            ids.append(None)
    return ids[0], ids[1]


POSE_ALIASES = ("pelvis", "head", "left_hand", "right_hand", "left_foot", "right_foot")


def _pose_objects():
    """The objects whose motion physics_snap compares, with their Transform data ids."""
    _, _view_scene, scene = common.scenes()
    mv = scene.model_viewer()
    bv = mv.behaviour_viewer()
    objects = []
    for alias in POSE_ALIASES:
        try:
            ids = rig.resolve([alias])
        except Exception:
            continue
        if not ids:
            continue
        object_id = ids[0]
        position_id, rotation_id = _data_ids(object_id, bv)
        if position_id is None and rotation_id is None:
            continue
        try:
            name = mv.get_object_name(object_id)
        except Exception:
            name = alias
        objects.append({"alias": alias, "name": name, "position": position_id, "rotation": rotation_id})
    return objects


def _read_value(dv, data_id, frame):
    if data_id is None:
        return None
    try:
        return _vector(dv.get_data_value(data_id, frame))
    except Exception:
        try:
            return _vector(dv.get_data_value(data_id))
        except Exception:
            return None


def _sample(objects, frames):
    """One row of (position, rotation) values per frame for objects."""
    _, _view_scene, scene = common.scenes()
    dv = scene.model_viewer().data_viewer()
    rows = []
    for frame in frames:
        row = []
        for obj in objects:
            row.append(_read_value(dv, obj["position"], frame))
            row.append(_read_value(dv, obj["rotation"], frame))
        rows.append(row)
    return rows


def _values_match(left, right) -> bool:
    if left is None or right is None:
        return left is right
    if len(left) != len(right):
        return False
    return all(abs(a - b) <= POSITION_TOLERANCE_CM for a, b in zip(left, right, strict=False))


def _rows_match(left, right) -> bool:
    if left is None or right is None or len(left) != len(right):
        return False
    for left_row, right_row in zip(left, right, strict=False):
        if len(left_row) != len(right_row):
            return False
        for left_value, right_value in zip(left_row, right_row, strict=False):
            if not _values_match(left_value, right_value):
                return False
    return True


def _changed_summary(before, after, frames, objects) -> dict:
    """Compare two _sample() results; return changed frames and per-kind largest moves.

    Position deltas are Euclidean distances in cm; rotation deltas are the largest Euler-angle
    change in degrees. Keeping them apart matters: a degree value is not a centimetre.
    """
    changed = []
    max_position = 0.0
    max_rotation = 0.0
    per_object = {obj["alias"]: {"position_cm": 0.0, "rotation_deg": 0.0} for obj in objects}
    for index, frame in enumerate(frames):
        frame_changed = False
        for obj_index, obj in enumerate(objects):
            base = obj_index * 2
            for kind, tolerance, unit in (
                (base, POSITION_TOLERANCE_CM, "position_cm"),
                (base + 1, ROTATION_TOLERANCE_DEG, "rotation_deg"),
            ):
                left = before[index][kind] if index < len(before) and kind < len(before[index]) else None
                right = after[index][kind] if index < len(after) and kind < len(after[index]) else None
                if left is None or right is None or len(left) != len(right):
                    if left is not right:
                        frame_changed = True
                        delta = tolerance * 2
                    else:
                        continue
                elif unit == "position_cm" and len(left) == 3:
                    delta = sum((a - b) ** 2 for a, b in zip(left, right, strict=False)) ** 0.5
                else:
                    delta = max((abs(a - b) for a, b in zip(left, right, strict=False)), default=0.0)
                if delta > tolerance:
                    frame_changed = True
                    per_object[obj["alias"]][unit] = max(per_object[obj["alias"]][unit], delta)
                    if unit == "position_cm":
                        max_position = max(max_position, delta)
                    else:
                        max_rotation = max(max_rotation, delta)
        if frame_changed:
            changed.append(frame)
    return {
        "changed_frames": changed,
        "changed_frame_count": len(changed),
        "changed_first": changed[0] if changed else None,
        "changed_last": changed[-1] if changed else None,
        "max_delta_cm": round(max_position, 6),
        "max_rotation_delta_deg": round(max_rotation, 6),
        "objects": [
            {
                "alias": alias,
                "max_position_delta_cm": round(values["position_cm"], 6),
                "max_rotation_delta_deg": round(values["rotation_deg"], 6),
            }
            for alias, values in per_object.items()
            if values["position_cm"] > 0 or values["rotation_deg"] > 0
        ],
    }


def _probe_frames(first: int, last: int) -> list[int]:
    """A small, deterministic sample of frames used to detect that the pose moved.

    Keyframes first (that is where a key-only snap writes), then an even spread over the
    animation range, always including the first and last frame.
    """
    frames = set()
    try:
        _, _view_scene, scene = common.scenes()
        viewer = scene.layers_viewer()
        for layer_id in viewer.all_layer_ids():
            try:
                layer = viewer.layer(layer_id)
            except Exception:
                continue
            frames.update(frame for frame in timeline.key_frames(layer) if first <= frame <= last)
    except Exception:
        pass
    span = max(1, last - first)
    step = max(1, span // max(1, PROBE_FRAMES - 2))
    frames.update(range(first, last + 1, step))
    frames.add(first)
    frames.add(last)
    ordered = sorted(frames)
    if len(ordered) <= PROBE_FRAMES:
        return ordered
    inner = ordered[1:-1]
    slots = PROBE_FRAMES - 2
    if slots <= 0 or len(inner) <= slots:
        return sorted({ordered[0], ordered[-1], *inner})
    indexes = sorted({round(index * (len(inner) - 1) / max(1, slots - 1)) for index in range(slots)})
    return sorted({ordered[0], ordered[-1], *(inner[index] for index in indexes)})


# ---------------------------------------------------------------------------
# Snap dialog handling (GUI thread)
# ---------------------------------------------------------------------------


def _dialogs():
    try:
        from .. import ui

        return list(ui.dialogs().get("dialogs") or [])
    except Exception:
        return []


def _dialog_text(dialog) -> str:
    parts = list(dialog.get("texts") or [])
    parts.extend(button.get("text", "") for button in dialog.get("buttons") or [])
    return " ".join(part for part in parts if part)


def _snap_dialogs(dialogs):
    return [
        dialog
        for dialog in dialogs
        if dialog.get("kind") == "dialog"
        and any(
            marker in _dialog_text(dialog).lower()
            for marker in (
                "which frames",
                "autophysics results",
                "apply autophysics",
                "all frames",
                "only keys",
                "missing a keyframe",
                "would you like to add",
            )
        )
    ]


def _error_dialogs(dialogs):
    errors = []
    for dialog in dialogs:
        text = _dialog_text(dialog)
        if any(keyword in text.lower() for keyword in ERROR_KEYWORDS):
            errors.append(text)
    return errors


def _click_label(dialog, labels) -> str | None:
    """Click the first dialog button whose label matches, returning the label clicked."""
    from .. import ui

    wanted = set(labels)
    for button in dialog.get("buttons") or []:
        label = (button.get("text") or "").strip()
        if label.lower() in wanted:
            ui.click(ref=button["ref"])
            return label
    return None


def _handle_snap_dialogs(state, dialogs=None) -> list[str]:
    """Answer the snap dialogs that are open; returns the labels clicked this poll."""
    dialogs = _dialogs() if dialogs is None else dialogs
    errors = _error_dialogs(dialogs)
    if errors:
        state["error_dialog"] = " ".join(errors)
        return []
    clicked = []
    now = time.monotonic()
    for dialog in _snap_dialogs(dialogs):
        text = _dialog_text(dialog).lower()
        if "missing a keyframe" in text or "would you like to add" in text:
            label = _click_label(dialog, YES_LABELS)
            if label:
                clicked.append(label)
            continue
        if "all frames" in text and not state.get("all_frames_clicked"):
            label = _click_label(dialog, ALL_FRAMES_LABELS)
            if label:
                state["all_frames_clicked"] = True
                state["apply_to"] = "all_frames"
                clicked.append(label)
                continue
        if "only keys" in text and not state.get("all_frames_clicked") and not state.get("only_keys_clicked"):
            label = _click_label(dialog, ONLY_KEYS_LABELS)
            if label:
                state["only_keys_clicked"] = True
                state["apply_to"] = "keys"
                clicked.append(label)
                continue
        last = state.get("last_apply_click", 0.0)
        if now - last >= APPLY_CLICK_SECONDS:
            label = _click_label(dialog, APPLY_LABELS)
            if label:
                state["last_apply_click"] = now
                state["apply_clicked_at"] = now
                state["apply_to"] = state.get("apply_to") or "all_frames"
                clicked.append(label)
    if clicked:
        state.setdefault("dialog_actions", []).extend(clicked)
    return clicked


# ---------------------------------------------------------------------------
# Background snap job (GUI thread callbacks)
# ---------------------------------------------------------------------------


def _log_size():
    try:
        return os.path.getsize(common.LOG_PATH)
    except OSError:
        return None


def _new_commands(offset):
    if offset is None:
        return []
    lines = common.log_tail(since=offset, lines=80)
    commands = []
    for line in lines:
        marker = "Command: "
        if marker in line:
            commands.append(line.split(marker, 1)[1].strip())
    return commands


def _schedule(callback) -> None:
    """Run callback on the next GUI event-loop turn (unit tests replace this)."""
    from PySide6 import QtCore

    QtCore.QTimer.singleShot(0, callback)


def _run_snap_action(state: dict) -> None:
    """Run the blocking Snap action outside the begin() dispatcher job (GUI thread)."""
    if getattr(sys, SNAP_STATE_KEY, None) is not state:
        # cancel_job or a timeout cleared the state before the next event-loop turn.
        return
    try:
        _call_action(ACTION_SNAP)
    except Exception:
        state["action_error"] = traceback.format_exc()
    finally:
        state["action_finished"] = True


def _job_status(job_id):
    """The jobs.py status for job_id, or None when the job is gone/unreadable."""
    try:
        from .. import jobs

        snapshot = jobs.get(job_id)
    except Exception:
        return None
    if not isinstance(snapshot, dict):
        return None
    return snapshot.get("status")


def _find_running_snap_job(state):
    """Find the physics_snap job that owns state when its id is not recorded yet.

    begin() runs before jobs.start_job() records the job, so fn cannot know the id. The first
    poll matches the running physics_snap job by kind and start time and stores its id.
    """
    try:
        from .. import jobs

        recent = jobs.recent(limit=20)
    except Exception:
        return None
    started_at = state.get("started_at")
    for snapshot in recent:
        if not isinstance(snapshot, dict):
            continue
        if snapshot.get("kind") != "physics_snap" or snapshot.get("status") != "running":
            continue
        try:
            if started_at is None or abs(float(snapshot.get("started_at") or 0.0) - float(started_at)) <= 5.0:
                return snapshot.get("job_id")
        except (TypeError, ValueError):
            continue
    return None


def _snap_state():
    """The active snap state, or None when it is finished, lost or stale.

    A restart marks the owning job lost and a poll exception marks it failed, but neither clears
    this sys flag by itself. Checking the recorded job id (and the start time when the first poll
    has not recorded the id yet) releases the flag instead of blocking every later snap.
    """
    state = getattr(sys, SNAP_STATE_KEY, None)
    if not isinstance(state, dict):
        return None
    started = state.get("started")
    if not isinstance(started, (int, float)) or _now() - started > SNAP_STATE_TTL_S:
        _clear_snap_state()
        return None
    job_id = state.get("job_id")
    if job_id:
        if _job_status(job_id) != "running":
            _clear_snap_state()
            return None
    else:
        job_id = _find_running_snap_job(state)
        if job_id is None:
            _clear_snap_state()
            return None
        state["job_id"] = job_id
    return state


def _set_snap_state(state):
    setattr(sys, SNAP_STATE_KEY, state)


def _clear_snap_state():
    setattr(sys, SNAP_STATE_KEY, None)


def _snap_failure(state, error, hint, **data):
    _clear_snap_state()
    result = {"ok": False, "error": str(error), "hint": str(hint)}
    if data:
        result.update(data)
    return result


def physics_snap() -> dict:
    """Start the background Snap to Auto Physics job."""
    active = _snap_state()
    if active is not None:
        return common.fail(
            "Another physics_snap job is still running.",
            "Call get_job for its id, or cancel_job it before starting another snap.",
            job_id=active.get("job_id"),
        )
    state_info = _read_state()
    if state_info.get("enabled") is False:
        return common.fail(
            "Physics Assistant is off, so there is no physics result to snap.",
            "Call physics_enable(enabled=true), wait for the solve, then call physics_snap again.",
            enabled=False,
        )
    objects = _pose_objects()
    if not objects:
        return common.fail(
            "No character control points could be read for the pose comparison.",
            "Open an animated character scene with a rig (for example Backflip_animation.casc).",
        )
    _, _view_scene, scene = common.scenes()
    checked = timeline.require_keyed_range(scene)
    if isinstance(checked, dict):
        return checked
    first, last = checked
    if last <= first:
        return common.fail(
            f"The keyed animation is only {last - first + 1} frame(s) long, so AutoPhysics has nothing to solve.",
            "Open an animation with several keyed frames (for example Backflip_animation.casc).",
        )
    blocked = _ensure_unlocked(timeline.target_object_ids())
    if blocked is not None:
        return blocked
    frames = list(range(first, last + 1))
    probe = _probe_frames(first, last)
    before = _sample(objects, frames)
    probe_before = _sample(objects, probe)
    state = {
        "started": _now(),
        "started_at": time.time(),
        "job_id": None,
        "action_finished": False,
        "action_error": None,
        "enabled_before": state_info.get("enabled"),
        "objects": objects,
        "frames": frames,
        "probe": probe,
        "before": before,
        "probe_before": probe_before,
        "last_probe": probe_before,
        "probe_stable": 0,
        "first_change": None,
        "apply_clicked_at": None,
        "last_apply_click": 0.0,
        "commands": [],
        "dialog_actions": [],
        "error_dialog": None,
        "phase": "snap",
        "pending_after": None,
        "log_offset": _log_size(),
    }
    _set_snap_state(state)
    try:
        _schedule(lambda: _run_snap_action(state))
    except Exception:
        _clear_snap_state()
        raise
    return common.ok(started=True, frames={"first": first, "last": last}, probe_frames=probe)


def _snap_poll() -> dict:
    """QTimer poll: answer dialogs, wait until the snap stops changing the pose."""
    state = _snap_state()
    if state is None:
        return {
            "ok": False,
            "error": "The physics_snap job lost its state.",
            "hint": "Restart Cascadeur and try again.",
        }
    try:
        return _snap_poll_state(state)
    except Exception:
        # A poll exception fails the job in jobs._tick; clear the sys flag here as well so the
        # next physics_snap call is not refused forever.
        _clear_snap_state()
        raise


def _snap_poll_state(state) -> dict:
    if state.get("action_error"):
        text = str(state["action_error"])
        return _snap_failure(
            state,
            "The AutoPhysics Snap action raised before it could run.",
            "Check get_log for the traceback and retry with a valid character and playhead.",
            traceback=text,
        )
    if not state.get("action_finished"):
        return {"done": False, "progress": 0.05}
    now = _now()
    elapsed = now - state["started"]

    # Walking Cascadeur's QML tree clears ui_tree/ui_dialogs refs, so do not do it on every
    # 100 ms tick once the first dialog has had time to appear.
    state["poll_count"] = state.get("poll_count", 0) + 1
    check_dialogs = state["poll_count"] <= 4 or state["poll_count"] % 3 == 0
    dialogs = _dialogs() if check_dialogs else []
    _handle_snap_dialogs(state, dialogs)
    if state.get("error_dialog"):
        return _snap_failure(
            state,
            state["error_dialog"],
            "Fix the AutoPhysics setup (select a character with a centre of mass, key the frames) and retry.",
        )
    if check_dialogs:
        state["dialog_open"] = bool(_snap_dialogs(dialogs))
    snap_dialog_open = bool(state.get("dialog_open", False))
    if snap_dialog_open and elapsed > DIALOG_SECONDS:
        return _snap_failure(
            state,
            "The AutoPhysics frame-choice dialog did not close.",
            "Check Cascadeur for a dialog that needs the user; its buttons are listed in 'dialogs'.",
        )

    commands = _new_commands(state.get("log_offset"))
    applied = bool(state.get("apply_clicked_at")) or bool(commands)
    if commands:
        state["commands"] = commands

    probe_now = _sample(state["objects"], state["probe"])
    probe_changed = not _rows_match(probe_now, state["probe_before"])
    if probe_changed and state["first_change"] is None:
        state["first_change"] = now
    if _rows_match(probe_now, state["last_probe"]):
        state["probe_stable"] += 1
    else:
        state["probe_stable"] = 0
        state["last_probe"] = probe_now
    if probe_changed:
        applied = True

    if not applied:
        if state["first_change"] is None and not snap_dialog_open and elapsed > NO_CHANGE_SECONDS:
            return _snap_failure(
                state,
                f"AutoPhysics did not change the animation within {NO_CHANGE_SECONDS:g}s.",
                "Make sure Physics Assistant is on, the character has a centre of mass, and the timeline has keyed "
                "frames; get_log shows Cascadeur's AutoPhysics errors.",
            )
        progress = min(0.8, 0.1 + elapsed / max(1.0, NO_CHANGE_SECONDS) * 0.7)
        return {"done": False, "progress": progress}

    # Once something has been applied, wait until the pose stops moving (or a dialog is gone),
    # then compare every frame in a separate poll so a late write is not missed. A click on
    # Apply is not proof that Cascadeur already wrote the data, so give it a grace period when
    # neither the probe nor the log shows a change yet.
    observed = probe_changed or bool(commands)
    if applied and not observed and state.get("apply_clicked_at") is not None:
        if now - state["apply_clicked_at"] < POST_APPLY_GRACE:
            return {"done": False, "progress": 0.82}
    stable_enough = state["probe_stable"] >= STABLE_POLLS and not snap_dialog_open
    settled_long_enough = (
        state.get("apply_clicked_at") is not None and (now - state["apply_clicked_at"]) >= SETTLE_SECONDS
    )
    if state.get("first_change") is not None and (now - state["first_change"]) >= SETTLE_SECONDS:
        settled_long_enough = True
    if state.get("commands") and elapsed >= SETTLE_SECONDS:
        settled_long_enough = True
    if not (stable_enough and settled_long_enough):
        progress = min(0.95, 0.8 + min(0.15, elapsed / 120.0))
        return {"done": False, "progress": progress}

    if state.get("pending_after") is None:
        state["pending_after"] = _sample(state["objects"], state["frames"])
        return {"done": False, "progress": 0.96}
    after = _sample(state["objects"], state["frames"])
    if not _rows_match(after, state["pending_after"]):
        state["pending_after"] = after
        return {"done": False, "progress": 0.97}

    summary = _changed_summary(state["before"], after, state["frames"], state["objects"])
    result = {
        "ok": True,
        "frames": {"first": state["frames"][0], "last": state["frames"][-1]},
        "changed_first": summary["changed_first"],
        "changed_last": summary["changed_last"],
        "changed_frame_count": summary["changed_frame_count"],
        "changed_frames": summary["changed_frames"][:MAX_LAST_CHANGES],
        "truncated": summary["changed_frame_count"] > MAX_LAST_CHANGES,
        "max_delta_cm": summary["max_delta_cm"],
        "max_rotation_delta_deg": summary["max_rotation_delta_deg"],
        "objects": summary["objects"],
        "enabled_before": state["enabled_before"],
        "apply_to": state.get("apply_to") or "unknown",
        "dialog_actions": state.get("dialog_actions") or [],
        "commands": state.get("commands") or [],
        "duration_s": round(elapsed, 3),
    }
    if not summary["changed_frames"]:
        result["hint"] = (
            "AutoPhysics reported the snap but no sampled pose value changed; the result may already have been "
            "snapped, or the physics solve produced no correction for this animation."
        )
    if state.get("commands"):
        result["undo"] = (
            "Cascadeur's own undo should revert the snap command(s) listed in 'commands'; call undo() once and "
            "compare the pose."
        )
    _clear_snap_state()
    return {"done": True, "progress": 1.0, "result": result}


def _snap_cancel() -> None:
    state = _snap_state()
    if state is None:
        return
    for dialog in _snap_dialogs(_dialogs()):
        _click_label(dialog, CANCEL_LABELS)
    _clear_snap_state()


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@_tool("Open an animated scene; get_log may explain why Cascadeur cannot read the Physics Assistant.")
def get_physics_state() -> dict:
    """Return the Physics Assistant state of the active scene."""
    return common.ok(**_read_state())


@_tool("Make sure Cascadeur's AutoPhysics toolbar is loaded and a character scene is open.")
def physics_enable(enabled=False) -> dict:
    """Turn the Physics Assistant on or off (idempotent)."""
    want = _bool(enabled, "enabled")
    return _set_flag("enabled", ACTION_ENABLE, want, "enabled")


@_tool("Make sure Cascadeur's AutoPhysics toolbar is loaded and a character scene is open.")
def physics_freeze(enabled=False) -> dict:
    """Freeze or unfreeze the Physics Assistant result (idempotent)."""
    want = _bool(enabled, "enabled")
    before = _gui_read()
    if before.get("enabled") is False:
        return common.fail(
            "Physics Assistant is off, so there is nothing to freeze.",
            "Call physics_enable(enabled=true) first, wait for the solve, then freeze it.",
            enabled=False,
        )
    return _set_flag("frozen", ACTION_FREEZE, want, "frozen")


@_tool("Make sure Cascadeur's AutoPhysics toolbar is loaded and a character scene is open.")
def physics_show_fulcrums(enabled=False) -> dict:
    """Show or hide the AutoPhysics fulcrum points (idempotent)."""
    want = _bool(enabled, "enabled")
    before = _gui_read()
    if want and before.get("enabled") is True:
        return common.fail(
            "Physics Assistant is on, so Cascadeur's Fulcrum Visualizer cannot be shown simultaneously.",
            "Call physics_enable(enabled=false) first, then physics_show_fulcrums(enabled=true). "
            "Showing the solved AutoPhysics fulcrums does not need this switch.",
            enabled=True,
        )
    return _set_flag("show_fulcrums", ACTION_FULCRUMS, want, "show_fulcrums")


@_tool("Make sure a character with AutoPhysics is in the scene; get_log may explain a rejection.")
def physics_set_priority_frame(frame=None) -> dict:
    """Move the playhead to a frame and mark it as an AutoPhysics priority frame."""
    wanted = timeline.parse_frame(frame, "frame")
    before = _gui_read()
    priority_before = before.get("priority_frames")
    if before.get("enabled") is False:
        return common.fail(
            "Physics Assistant is off, so priority frames have no effect.",
            "Call physics_enable(enabled=true) first.",
            enabled=False,
        )
    if priority_before is None:
        return common.fail(
            "Could not read the current AutoPhysics priority frames, so the toggle was not pressed.",
            "Check get_log and call get_physics_state; Set priority frame is a toggle, so pressing it blind "
            "could turn an existing priority frame off.",
            frame=wanted,
            priority=False,
        )
    if wanted in priority_before:
        return common.ok(
            changed=False,
            frame=wanted,
            priority=True,
            priority_frames=priority_before,
            source=before.get("source"),
        )
    _, _view_scene, scene = common.scenes()
    checked = timeline.require_keyed_range(scene, wanted, wanted)
    if isinstance(checked, dict):
        return checked
    blocked = _ensure_unlocked(_priority_object_ids())
    if blocked is not None:
        return blocked

    def apply():
        _, _view_scene, scene = common.scenes()
        original = int(scene.get_current_frame(False))
        try:
            scene.set_current_frame(wanted)
            actual = int(scene.get_current_frame(False))
            if actual != wanted:
                return {
                    "ok": False,
                    "actual": actual,
                    "original": original,
                    "error": f"Cascadeur put the playhead on frame {actual} instead of {wanted}.",
                }
            _call_action(ACTION_PRIORITY)
            return {"ok": True, "actual": actual, "original": original}
        finally:
            # Restore the playhead on every path, including a rejected/clamped frame.
            try:
                if int(scene.get_current_frame(False)) != original:
                    scene.set_current_frame(original)
            except Exception:
                pass

    from .. import dispatch

    outcome = dispatch.call_on_gui(apply, {}, 30.0)
    if not outcome.get("ok"):
        return common.fail(
            outcome.get("error") or "Cascadeur rejected the priority-frame action.",
            "Use a frame inside the animation range; Set priority frame is a toggle, so nothing was pressed.",
            frame=wanted,
            priority=False,
            priority_frames=priority_before,
            playhead_restored=outcome.get("original"),
        )
    after = _settle(
        lambda state: isinstance(state.get("priority_frames"), list) and wanted in state["priority_frames"],
        timeout=3.0,
    )
    frames = after.get("priority_frames")
    if isinstance(frames, list) and wanted in frames:
        return common.ok(
            changed=True,
            frame=wanted,
            priority=True,
            priority_frames=frames,
            playhead_restored=outcome.get("original"),
            source=after.get("source"),
        )
    log = common.log_tail(lines=30)
    return common.fail(
        f"Cascadeur did not mark frame {wanted} as an AutoPhysics priority frame.",
        "The frame probably has no keyframe (AutoPhysics can only prioritise keyed frames); call set_key first, "
        "or use set_frame and the AutoPhysics toolbar. The last log lines are in 'log'.",
        frame=wanted,
        priority=False,
        priority_frames=frames,
        playhead_restored=outcome.get("original"),
        log=log,
    )


TOOLS = [
    {
        "name": "get_physics_state",
        "description": "Return the Physics Assistant (AutoPhysics) state of the current scene: whether AutoPhysics "
        "is enabled, frozen, showing fulcrum points or using only key fulcrums, the priority frames, the current and "
        "animation frames, the scene fps and the gravity per frame in cm (980 / fps^2). Use it before changing physics "
        "settings or snapping. Read-only.",
        "inputSchema": schema(),
        "fn": get_physics_state,
    },
    {
        "name": "physics_enable",
        "description": "Turn Cascadeur's Physics Assistant (AutoPhysics) on or off. Use it to start the physical solve "
        "before physics_snap, or to stop it. Reads the state first and does nothing when it already matches, so "
        "repeating the call is safe. Returns the enabled state afterwards.",
        "inputSchema": schema(
            {"enabled": {"type": "boolean", "description": "True turns Physics Assistant on, false turns it off."}},
            ["enabled"],
        ),
        "handler": physics_enable,
        "settle": True,
    },
    {
        "name": "physics_snap",
        "description": "Bake the AutoPhysics result into the animation (Snap to Auto Physics) as a background job: "
        "returns a job_id immediately, and get_job reports progress and the result. Use it after physics_enable to "
        "accept the solved physics; the job answers Cascadeur's frame-choice dialog with All Frames, waits until the "
        "pose stops changing, and returns the animation frame range, the changed frame range/count, the largest "
        "change in cm and the objects that moved. The job runs on the GUI thread only in short polls, so "
        "get_scene_info and the rest of the UI keep answering while AutoPhysics computes.",
        "inputSchema": schema(),
        "fn": physics_snap,
        "poll": _snap_poll,
        "cancel": _snap_cancel,
        "background": True,
        "job_timeout": 300,
    },
    {
        "name": "physics_freeze",
        "description": "Freeze or unfreeze Cascadeur's AutoPhysics result. A frozen result keeps the solved physics "
        "from following further animation edits; unfreeze it to solve again. Reads the state first and does nothing "
        "when it already matches, so repeating the call is safe. Returns the frozen state afterwards.",
        "inputSchema": schema(
            {"enabled": {"type": "boolean", "description": "True freezes the physics result, false unfreezes it."}},
            ["enabled"],
        ),
        "handler": physics_freeze,
        "settle": True,
    },
    {
        "name": "physics_set_priority_frame",
        "description": "Mark a frame as an AutoPhysics priority frame: the solver keeps that frame's pose while it "
        "solves the rest of the animation. Moves the playhead to the frame and runs Cascadeur's Set priority frame "
        "action; the frame must be inside the keyed animation range (a range with at least two keyed frames), not "
        "necessarily a keyed frame itself. Use priority frames to pin a landing pose or a contact. Returns the "
        "frame, whether it is now a priority frame, the full priority-frame list and whether the playhead was restored.",
        "inputSchema": schema(
            {"frame": {"type": ["integer", "string"], "description": "Frame number (0 or more)."}},
            ["frame"],
        ),
        "handler": physics_set_priority_frame,
        "settle": True,
    },
    {
        "name": "physics_show_fulcrums",
        "description": "Show or hide Cascadeur's Fulcrum Visualizer, which draws all fulcrum (contact) points of the "
        "character so you can inspect which points touch the ground or another surface. Cascadeur cannot show the "
        "Fulcrum Visualizer while the Physics Assistant is on, so call physics_enable(enabled=false) first; while it is "
        "on this returns an error explaining the conflict. Reads the state first and does nothing when it already "
        "matches, so repeating the call is safe. Returns the show_fulcrums state afterwards.",
        "inputSchema": schema(
            {"enabled": {"type": "boolean", "description": "True shows all fulcrum points, false hides them."}},
            ["enabled"],
        ),
        "handler": physics_show_fulcrums,
        "settle": True,
    },
]
