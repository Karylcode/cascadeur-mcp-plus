"""Display mode controls: view mode, ghosts, trajectories, viewport layout, silhouette, surface.

Cascadeur exposes the viewport display modes through csc.view.ViewportDomain, but ghosts,
trajectories, silhouette and surface are driven by QML toolbars. The action ids below are the exact
actionIds of Cascadeur 2026.2.3's own toolbar QML, and the state is read from the QML context
objects the toolbars bind to (toolManager.ghostState, toolManager.trajectoryMode,
toolbarManager.showSurface, ...). Reading the same source the interface draws from keeps
get_display_state consistent with the buttons; the button active flags are only a fallback.

The setters run on the HTTP thread: they read the state on the GUI thread, trigger actions on the
GUI thread, then wait on the HTTP thread (so Cascadeur keeps processing events) until
get_display_state reports the requested value. Repeated calls with the same value do nothing.
"""

from __future__ import annotations

import functools
import time

from ..errors import ToolError
from ._schema import schema

# ---------------------------------------------------------------------------
# Action ids and mode names
# ---------------------------------------------------------------------------

# csc.view.ViewportMode values (resources/scripts/stubs/csc/view/__init__.pyi).
VIEW_MODE_VALUES = {
    "view": 0,
    "autoposing": 1,
    "point_controller": 2,
    "box_controller": 3,
    "joint": 4,
    "mesh": 5,
    "rigging_view": 6,
}
VIEW_MODE_NAMES = {value: name for name, value in VIEW_MODE_VALUES.items()}

# Toolbar actions of the per-viewport mode switch (ViewportModes QML).
VIEW_ACTIONS = {
    "mesh": "VisualizerStateController.AllModes.Mesh mode",
    "joint": "VisualizerStateController.AllModes.Joint mode",
    "box_controller": "VisualizerStateController.AllModes.Box controller mode",
    "point_controller": "VisualizerStateController.AllModes.Point controller mode",
    "autoposing": "VisualizerStateController.AllModes.AutoPosing mode",
    "rigging_view": "VisualizerStateController.AllModes.Rigging view mode",
    "view": "VisualizerStateController.AllModes.View mode",
}

# Ghost tool toolbar (GhostTool.qml in tool_ghost.dll). ghostState values come from GhostToolData:
# Disable=0, Previous=1, Next=2, Neighbors=3, Selected=4. "keyframes" is not a ghostState: it is
# Cascadeur's key-mode selection (isKeysModeSelection), which makes the other modes use keyframes
# instead of neighbouring frames, so it is exposed as a mode of its own.
GHOST_ACTIONS = {
    "none": "GhostTool.Disable ghost",
    "previous": "GhostTool.Previous frames ghosts",
    "next": "GhostTool.Next frames ghosts",
    "neighbors": "GhostTool.Neighbor frame ghosts",
    "keyframes": "GhostTool.Keyframe ghosts",
    "selected_frames": "GhostTool.Ghosts for selected frames",
}
GHOST_STATE_NAMES = {0: "none", 1: "previous", 2: "next", 3: "neighbors", 4: "selected_frames"}
GHOST_BASE_MODES = ("none", "previous", "next", "neighbors", "selected_frames")

# Trajectory tool toolbar (TrajectoryTool QML in tool_trajectory.dll). trajectoryMode values come
# from TrajectoryToolData: Parent=0, All=1, Edges=2, ObjectsAndEdges=3. "off" means isActive false.
TRAJECTORY_ACTIONS = {
    "all": "TrajectoryTool.All trajectories",
    "edges": "TrajectoryTool.Edge trajectories",
    "edges_and_fixed": "TrajectoryTool.Object and edge trajectories",
    "parents": "TrajectoryTool.Parent trajectories",
}
TRAJECTORY_MODE_NAMES = {0: "parents", 1: "all", 2: "edges", 3: "edges_and_fixed"}
TRAJECTORY_SWITCH = "TrajectoryTool.Switch trajectory activity"
TRAJECTORY_ONLY_KEYS = "TrajectoryTool.Using only key frames"

VIEWPORT_ACTIONS = {
    1: "Viewport.One Viewport",
    2: "Viewport.Two Viewports",
    4: "Viewport.Four Viewports",
}

SILHOUETTE_ACTION = "View.Silhouette mode"
SURFACE_ACTION = "View.Surface"

# QML actions whose context exposes the manager objects the toolbars bind to.
_MANAGER_ACTIONS = {
    "ghost": ("GhostTool.Keyframe ghosts", "toolManager"),
    "trajectory": (TRAJECTORY_SWITCH, "toolManager"),
    "toolbar": (SURFACE_ACTION, "toolbarManager"),
}

_ALL_ACTIONS = (
    set(GHOST_ACTIONS.values())
    | set(TRAJECTORY_ACTIONS.values())
    | set(VIEWPORT_ACTIONS.values())
    | {TRAJECTORY_SWITCH, TRAJECTORY_ONLY_KEYS, SILHOUETTE_ACTION, SURFACE_ACTION}
)


class _BadArgument(ToolError):
    """A tool argument is invalid; carries the hint that belongs in the result."""

    def __init__(self, message: str, hint: str):
        super().__init__(message)
        self.hint = hint


# ---------------------------------------------------------------------------
# Reading state (GUI thread)
# ---------------------------------------------------------------------------


def _action_states(action_ids):
    """Return {actionId: True/False} for the action ids found in Cascadeur's QML tree.

    Used as a fallback when a QML manager object is not reachable. An id is True when any element
    carrying it has active or checked set to True; ids that are not in the tree are omitted.
    """
    from .. import ui

    wanted = set(action_ids)
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


def _qml_managers():
    """Return the QML manager objects behind the display toolbars, keyed by role.

    toolManager.ghostState / isKeysModeSelection, toolManager.trajectoryMode / isActive /
    isOnlyKeyFrames and toolbarManager.showSurface / isModeSilhouette are the values the toolbar
    buttons bind to, so they are the most trustworthy state source. Unreachable managers are
    omitted and the caller falls back to the buttons' active flags.
    """
    try:
        from PySide6.QtQml import QQmlEngine

        from .. import ui
    except Exception:
        return {}

    wanted = {action: role for role, (action, _property) in _MANAGER_ACTIONS.items()}
    managers = {}
    for window in ui.quick_windows(include_hidden=True):
        for item in ui._walk(window, include_hidden=True):
            try:
                action_id = item.property("actionId")
            except Exception:
                continue
            role = wanted.get(action_id) if isinstance(action_id, str) else None
            if role is None or role in managers:
                continue
            try:
                context = QQmlEngine.contextForObject(item)
                manager = context.contextProperty(_MANAGER_ACTIONS[role][1]) if context is not None else None
            except Exception:
                manager = None
            if manager is not None:
                managers[role] = manager
        if len(managers) == len(wanted):
            break
    return managers


def _view_mode_name(mode):
    try:
        value = int(mode)
    except (TypeError, ValueError):
        return None
    return VIEW_MODE_NAMES.get(value, f"unknown({value})")


def _ghosts_from_flags(flags):
    """Derive the base ghost mode from button flags (fallback when toolManager is unreachable)."""
    if not any(action in flags for action in GHOST_ACTIONS.values()):
        return None
    for mode in GHOST_BASE_MODES:
        if flags.get(GHOST_ACTIONS[mode]):
            return mode
    return None


def _trajectory_from_flags(flags):
    """Derive (mode, active, only_key_frames) from trajectory button flags (fallback)."""
    active = flags.get(TRAJECTORY_SWITCH)
    only_keys = flags.get(TRAJECTORY_ONLY_KEYS)
    if active is False:
        return "off", False, only_keys
    if active is True:
        for mode, action in TRAJECTORY_ACTIONS.items():
            if flags.get(action):
                return mode, True, only_keys
        return None, True, only_keys
    return None, None, only_keys


def _read_state():
    """Read the full display state of the active scene (must run on the GUI thread)."""
    from .. import common

    _, view_scene, _ = common.scenes()
    viewports = list(view_scene.viewports())
    view_modes = []
    for viewport in viewports:
        view_modes.append(_view_mode_name(viewport.domain_viewport().mode_visualizers()))

    active = view_scene.active_viewport()
    if active is not None:
        view_mode = _view_mode_name(active.domain_viewport().mode_visualizers())
    else:
        view_mode = view_modes[0] if view_modes else None

    managers = _qml_managers()
    flags = _action_states(_ALL_ACTIONS)

    ghost_base = None
    ghost_keys = None
    ghost_manager = managers.get("ghost")
    if ghost_manager is not None:
        try:
            state_value = ghost_manager.property("ghostState")
            keys_value = ghost_manager.property("isKeysModeSelection")
        except Exception:
            state_value, keys_value = None, None
        if isinstance(state_value, int):
            ghost_base = GHOST_STATE_NAMES.get(state_value, f"unknown({state_value})")
        if isinstance(keys_value, (bool, int)):
            ghost_keys = bool(keys_value)
    if ghost_base is None:
        ghost_base = _ghosts_from_flags(flags)
    if ghost_keys is None:
        ghost_keys = flags.get(GHOST_ACTIONS["keyframes"])
    ghosts = ghost_base
    if ghost_base not in (None, "none") and ghost_keys:
        ghosts = "keyframes"

    trajectory_mode = None
    trajectory_active = None
    only_keys = None
    trajectory_manager = managers.get("trajectory")
    if trajectory_manager is not None:
        try:
            active_value = trajectory_manager.property("isActive")
            mode_value = trajectory_manager.property("trajectoryMode")
            keys_value = trajectory_manager.property("isOnlyKeyFrames")
        except Exception:
            active_value, mode_value, keys_value = None, None, None
        if isinstance(active_value, (bool, int)):
            trajectory_active = bool(active_value)
        if isinstance(mode_value, int):
            trajectory_mode = TRAJECTORY_MODE_NAMES.get(mode_value, f"unknown({mode_value})")
        if isinstance(keys_value, (bool, int)):
            only_keys = bool(keys_value)
    if trajectory_active is None:
        trajectory_mode, trajectory_active, only_keys = _trajectory_from_flags(flags)
    elif trajectory_mode is not None and not trajectory_active:
        trajectory_mode = "off"

    silhouette = None
    surface = None
    toolbar_manager = managers.get("toolbar")
    if toolbar_manager is not None:
        try:
            silhouette = toolbar_manager.property("isModeSilhouette")
            surface = toolbar_manager.property("showSurface")
        except Exception:
            silhouette, surface = None, None
    if not isinstance(silhouette, bool):
        silhouette = flags.get(SILHOUETTE_ACTION)
    if not isinstance(surface, bool):
        surface = flags.get(SURFACE_ACTION)

    state = {
        "view_mode": view_mode,
        "view_modes": view_modes,
        "viewport_layout": len(viewports),
        "ghosts": ghosts,
        "ghost_base_mode": ghost_base,
        "ghost_keyframes": ghost_keys,
        "trajectory_mode": trajectory_mode,
        "trajectory_active": trajectory_active,
        "only_key_frames": only_keys,
        "silhouette": silhouette,
        "surface": surface,
    }
    missing = sorted(name for name, value in state.items() if value is None)
    if missing:
        state["warnings"] = ["Cascadeur's UI does not report these display values right now: " + ", ".join(missing)]
    return state


def _call_action(key: str) -> None:
    """Trigger a Cascadeur action exactly like its toolbar button."""
    from .. import ui

    ui.call_action(key)


def _set_view_mode_direct(mode: str) -> None:
    """Set the mode on the active viewport, the operation Cascadeur's own rig mode uses."""
    import csc

    from .. import common

    _, view_scene, _ = common.scenes()
    viewport = view_scene.active_viewport()
    if viewport is None:
        raise ToolError("The scene has no active viewport.")
    viewport.domain_viewport().set_mode_visualizers(csc.view.ViewportMode(VIEW_MODE_VALUES[mode]))


def _set_surface_direct(enabled: bool) -> None:
    """Toggle the same QML property the Surface toolbar button toggles."""
    manager = _qml_managers().get("toolbar")
    if manager is None:
        raise ToolError("The Surface toolbar button is not loaded, so its toolbarManager is unavailable.")
    if not manager.setProperty("showSurface", enabled):
        raise ToolError("Cascadeur rejected the showSurface property change.")


# ---------------------------------------------------------------------------
# HTTP-thread orchestration helpers
# ---------------------------------------------------------------------------


def _gui_read() -> dict:
    from .. import dispatch

    return dispatch.call_on_gui(_read_state, {}, 30.0)


def _gui_action(action_id: str) -> None:
    from .. import dispatch

    dispatch.call_on_gui(_call_action, {"key": action_id}, 30.0)


def _gui_direct_view_mode(mode: str) -> None:
    from .. import dispatch

    dispatch.call_on_gui(_set_view_mode_direct, {"mode": mode}, 30.0)


def _gui_set_surface(enabled: bool) -> None:
    from .. import dispatch

    dispatch.call_on_gui(_set_surface_direct, {"enabled": enabled}, 30.0)


def _settle(predicate, timeout: float = 3.0) -> dict:
    """Poll the display state on this HTTP thread until predicate(state) or timeout.

    Sleeping here (instead of on the GUI thread) lets Cascadeur process the toolbar update and
    repaint, which is why the setters see the new value instead of a stale one.
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
        delay = min(delay * 1.5, 0.5)


# ---------------------------------------------------------------------------
# Argument helpers
# ---------------------------------------------------------------------------


def _choice(value, choices, parameter: str) -> str:
    key = value.strip().lower().replace("-", "_") if isinstance(value, str) else value
    if key not in choices:
        allowed = ", ".join(choices)
        raise _BadArgument(f"{parameter} must be one of: {allowed} (got {value!r}).", f"Pass one of: {allowed}.")
    return key


def _bool(value, parameter: str) -> bool:
    if not isinstance(value, bool):
        raise _BadArgument(
            f"{parameter} must be true or false (got {value!r}).", f"Pass {parameter}=true or {parameter}=false."
        )
    return value


def _layout_count(value) -> int:
    if isinstance(value, bool):
        raise _BadArgument("count must be 1, 2 or 4.", "Pass count=1, count=2 or count=4.")
    try:
        count = int(value)
    except (TypeError, ValueError):
        raise _BadArgument(f"count must be 1, 2 or 4 (got {value!r}).", "Pass count=1, count=2 or count=4.") from None
    if count not in VIEWPORT_ACTIONS:
        raise _BadArgument(f"count must be 1, 2 or 4 (got {value!r}).", "Pass count=1, count=2 or count=4.")
    return count


def _tool(hint: str):
    """Turn exceptions into the shared {"ok": false, "error", "hint"} result."""

    def decorate(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except ToolError as exc:
                return _fail(str(exc), getattr(exc, "hint", None) or hint)
            except Exception as exc:  # noqa: BLE001 - tool boundary: report instead of raising
                return _fail(f"{type(exc).__name__}: {exc}", hint)

        return wrapper

    return decorate


def _ok(**data) -> dict:
    return {"ok": True, **data}


def _fail(error, hint=None, **data) -> dict:
    result = {"ok": False, "error": str(error)}
    if hint:
        result["hint"] = str(hint)
    result.update(data)
    return result


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@_tool("Open a scene and check that Cascadeur is responsive; get_log shows errors.")
def get_display_state() -> dict:
    """Return the display state of the current scene (read-only)."""
    return _ok(**_read_state())


@_tool("Make sure the 3D viewport is visible; get_log may explain a rejected action.")
def set_view_mode(mode: str = "") -> dict:
    """Switch the active viewport's display mode."""
    target = _choice(mode, VIEW_ACTIONS, "mode")
    before = _gui_read()
    if before.get("view_mode") == target:
        return _ok(changed=False, view_mode=target)

    _gui_action(VIEW_ACTIONS[target])
    after = _settle(lambda state: state.get("view_mode") == target, timeout=1.0)
    if after.get("view_mode") != target:
        # The action targets the focused viewport and can be rejected while another tool owns the
        # camera; setting the mode on the active viewport is the same operation rig mode uses.
        _gui_direct_view_mode(target)
        after = _settle(lambda state: state.get("view_mode") == target, timeout=1.0)
    if after.get("view_mode") != target:
        return _fail(
            f"View mode did not change to {target!r} (reported {after.get('view_mode')!r}).",
            "Click into the 3D viewport and retry; get_log shows whether Cascadeur rejected the action.",
            view_mode=after.get("view_mode"),
        )
    return _ok(changed=True, view_mode=target)


def _ghosts_match(state: dict, target: str) -> bool:
    if state.get("ghosts") != target:
        return False
    if target == "keyframes":
        return state.get("ghost_keyframes") is True
    return state.get("ghost_keyframes") is not True


@_tool("Check that the Ghost toolbar is available; get_log may explain a rejected action.")
def set_ghosts(mode: str = "") -> dict:
    """Set the Ghost tool mode."""
    target = _choice(mode, GHOST_ACTIONS, "mode")
    before = _gui_read()
    base = before.get("ghost_base_mode")
    keys = before.get("ghost_keyframes")
    if base is None or keys is None:
        return _fail(
            "Could not read the Ghost tool state: its toolbar is not loaded.",
            "Open Cascadeur's Ghost toolbar (or restart Cascadeur) and retry; get_log may show why the tool failed to load.",
        )
    if _ghosts_match(before, target):
        return _ok(changed=False, ghosts=target, ghost_keyframes=keys)

    actions = []
    if target == "keyframes":
        if base == "none":
            actions.append(GHOST_ACTIONS["previous"])
        if not keys:
            actions.append(GHOST_ACTIONS["keyframes"])
    else:
        if keys:
            actions.append(GHOST_ACTIONS["keyframes"])
        if base != target:
            actions.append(GHOST_ACTIONS[target])
    for action in actions:
        _gui_action(action)
    after = _settle(lambda state: _ghosts_match(state, target))
    if not _ghosts_match(after, target):
        return _fail(
            f"Ghost mode did not change to {target!r} (reported {after.get('ghosts')!r}, "
            f"keyframes={after.get('ghost_keyframes')!r}).",
            "Make sure a scene is open and the Ghost tool is enabled; get_log may explain the rejection.",
            ghosts=after.get("ghosts"),
            ghost_keyframes=after.get("ghost_keyframes"),
        )
    return _ok(changed=True, ghosts=target, ghost_keyframes=after.get("ghost_keyframes"))


def _trajectory_matches(state: dict, target: str, want_keys) -> bool:
    if target == "off":
        if state.get("trajectory_active") is not False:
            return False
    elif state.get("trajectory_active") is not True or state.get("trajectory_mode") != target:
        return False
    if want_keys is not None and state.get("only_key_frames") is not want_keys:
        return False
    return True


@_tool("Check that the Trajectory toolbar is available; get_log may explain a rejected action.")
def set_trajectories(mode: str = "", only_key_frames=None) -> dict:
    """Set the Trajectory tool mode and optionally restrict it to keyframes."""
    target = _choice(mode, ("off", *TRAJECTORY_ACTIONS), "mode")
    want_keys = None if only_key_frames is None else _bool(only_key_frames, "only_key_frames")
    before = _gui_read()
    active = before.get("trajectory_active")
    keys = before.get("only_key_frames")
    if active is None or keys is None:
        return _fail(
            "Could not read the Trajectory tool state: its toolbar is not loaded.",
            "Open the Trajectory tool (or restart Cascadeur) and retry; get_log may show why it failed to load.",
        )
    if _trajectory_matches(before, target, want_keys):
        return _ok(changed=False, trajectory_mode=before.get("trajectory_mode"), only_key_frames=keys)

    actions = []
    if target == "off":
        if active:
            actions.append(TRAJECTORY_SWITCH)
    else:
        if not active:
            actions.append(TRAJECTORY_SWITCH)
        if before.get("trajectory_mode") != target:
            actions.append(TRAJECTORY_ACTIONS[target])
    if want_keys is not None and keys is not want_keys:
        actions.append(TRAJECTORY_ONLY_KEYS)
    for action in actions:
        _gui_action(action)
    after = _settle(lambda state: _trajectory_matches(state, target, want_keys))
    if not _trajectory_matches(after, target, want_keys):
        return _fail(
            f"Trajectory mode did not reach {target!r}"
            + (f" with only_key_frames={want_keys}" if want_keys is not None else "")
            + f" (reported mode {after.get('trajectory_mode')!r}, active={after.get('trajectory_active')!r}, "
            f"only_key_frames={after.get('only_key_frames')!r}).",
            "Make sure the Trajectory tool is available and a scene is open; get_log may explain the rejection.",
            trajectory_mode=after.get("trajectory_mode"),
            only_key_frames=after.get("only_key_frames"),
        )
    return _ok(changed=True, trajectory_mode=after.get("trajectory_mode"), only_key_frames=after.get("only_key_frames"))


@_tool("Make sure the 3D viewport is visible; get_log may explain a rejected action.")
def set_viewport_layout(count=0) -> dict:
    """Switch between 1, 2 and 4 viewports."""
    target = _layout_count(count)
    before = _gui_read()
    if before.get("viewport_layout") == target:
        return _ok(changed=False, viewport_layout=target)

    _gui_action(VIEWPORT_ACTIONS[target])
    after = _settle(lambda state: state.get("viewport_layout") == target, timeout=2.0)
    if after.get("viewport_layout") != target:
        return _fail(
            f"Viewport layout did not change to {target} (reported {after.get('viewport_layout')!r}).",
            "Retry from the 3D viewport; get_log may show whether Cascadeur rejected the layout change.",
            viewport_layout=after.get("viewport_layout"),
        )
    return _ok(changed=True, viewport_layout=target)


@_tool("Make sure the 3D viewport is visible; get_log may explain a rejected action.")
def set_silhouette(enabled=False) -> dict:
    """Turn silhouette display on or off."""
    want = _bool(enabled, "enabled")
    before = _gui_read()
    current = before.get("silhouette")
    if current is None:
        return _fail(
            "Could not read silhouette mode: its toolbar is not loaded.",
            "Open Cascadeur's main window and retry; get_log may show why the button is missing.",
        )
    if current is want:
        return _ok(changed=False, silhouette=want)

    _gui_action(SILHOUETTE_ACTION)
    after = _settle(lambda state: state.get("silhouette") is want, timeout=2.0)
    if after.get("silhouette") is not want:
        return _fail(
            f"Silhouette mode did not change to {want!r} (reported {after.get('silhouette')!r}).",
            "Click into the 3D viewport and retry; get_log may explain the rejection.",
            silhouette=after.get("silhouette"),
        )
    return _ok(changed=True, silhouette=want)


@_tool("Make sure the 3D viewport is visible; get_log may explain a rejected action.")
def set_surface(enabled=False) -> dict:
    """Turn surface display on or off."""
    want = _bool(enabled, "enabled")
    before = _gui_read()
    current = before.get("surface")
    if current is None:
        return _fail(
            "Could not read surface mode: its toolbar is not loaded.",
            "Open Cascadeur's main window and retry; get_log may show why the button is missing.",
        )
    if current is want:
        return _ok(changed=False, surface=want)

    _gui_set_surface(want)
    after = _settle(lambda state: state.get("surface") is want, timeout=2.0)
    if after.get("surface") is not want:
        return _fail(
            f"Surface mode did not change to {want!r} (reported {after.get('surface')!r}).",
            "Click into the 3D viewport and retry; get_log may explain the rejection.",
            surface=after.get("surface"),
        )
    return _ok(changed=True, surface=want)


TOOLS = [
    {
        "name": "get_display_state",
        "description": "Return the current display state: view mode, ghost mode, trajectory mode and keyframe-only flag, "
        "viewport layout, silhouette and surface. Use it before changing the display and to confirm the result of the "
        "set_* display tools. Read-only.",
        "inputSchema": schema(),
        "requires": [
            "csc.app.get_application",
            "csc.view.Viewport.domain_viewport",
            "action:GhostTool.Disable ghost",
            "action:GhostTool.Ghosts for selected frames",
            "action:GhostTool.Keyframe ghosts",
            "action:GhostTool.Neighbor frame ghosts",
            "action:GhostTool.Next frames ghosts",
            "action:GhostTool.Previous frames ghosts",
            "action:TrajectoryTool.All trajectories",
            "action:TrajectoryTool.Edge trajectories",
            "action:TrajectoryTool.Object and edge trajectories",
            "action:TrajectoryTool.Parent trajectories",
            "action:TrajectoryTool.Switch trajectory activity",
            "action:TrajectoryTool.Using only key frames",
            "action:View.Silhouette mode",
            "action:View.Surface",
            "action:Viewport.Four Viewports",
            "action:Viewport.One Viewport",
            "action:Viewport.Two Viewports",
        ],
        "fn": get_display_state,
    },
    {
        "name": "set_view_mode",
        "description": "Switch the active 3D viewport's display mode: mesh, joint, box_controller, point_controller, "
        "autoposing, rigging_view or view. Use it to see the character as shaded geometry, joints, controller boxes or "
        "points, or in Cascadeur's rigging view. Returns the mode that is active afterwards.",
        "inputSchema": schema(
            {
                "mode": {
                    "type": "string",
                    "enum": list(VIEW_ACTIONS),
                    "description": "Display mode to switch to.",
                }
            },
            ["mode"],
        ),
        "requires": [
            "csc.app.ActionManager.call_action",
            "csc.app.Application.get_action_manager",
            "csc.app.get_application",
            "csc.view.Viewport.domain_viewport",
            "csc.view.ViewportMode",
            "action:GhostTool.Disable ghost",
            "action:GhostTool.Ghosts for selected frames",
            "action:GhostTool.Keyframe ghosts",
            "action:GhostTool.Neighbor frame ghosts",
            "action:GhostTool.Next frames ghosts",
            "action:GhostTool.Previous frames ghosts",
            "action:TrajectoryTool.All trajectories",
            "action:TrajectoryTool.Edge trajectories",
            "action:TrajectoryTool.Object and edge trajectories",
            "action:TrajectoryTool.Parent trajectories",
            "action:TrajectoryTool.Switch trajectory activity",
            "action:TrajectoryTool.Using only key frames",
            "action:View.Silhouette mode",
            "action:View.Surface",
            "action:Viewport.Four Viewports",
            "action:Viewport.One Viewport",
            "action:Viewport.Two Viewports",
            "action:VisualizerStateController.AllModes.AutoPosing mode",
            "action:VisualizerStateController.AllModes.Box controller mode",
            "action:VisualizerStateController.AllModes.Joint mode",
            "action:VisualizerStateController.AllModes.Mesh mode",
            "action:VisualizerStateController.AllModes.Point controller mode",
            "action:VisualizerStateController.AllModes.Rigging view mode",
            "action:VisualizerStateController.AllModes.View mode",
        ],
        "handler": set_view_mode,
        "settle": True,
    },
    {
        "name": "set_ghosts",
        "description": "Set the Ghost tool mode: none, previous, next, neighbors, keyframes or selected_frames. keyframes "
        "turns on Cascadeur's key-mode selection so the ghosts are placed on keyframes (previous-frame ghosts are enabled "
        "first when ghosts are off); the other modes use neighbouring or selected frames. Returns the ghost mode that is "
        "active afterwards; calling it again with the same mode changes nothing.",
        "inputSchema": schema(
            {
                "mode": {
                    "type": "string",
                    "enum": list(GHOST_ACTIONS),
                    "description": "Ghost mode to switch to.",
                }
            },
            ["mode"],
        ),
        "requires": [
            "csc.app.ActionManager.call_action",
            "csc.app.Application.get_action_manager",
            "csc.app.get_application",
            "csc.view.Viewport.domain_viewport",
            "action:GhostTool.Disable ghost",
            "action:GhostTool.Ghosts for selected frames",
            "action:GhostTool.Keyframe ghosts",
            "action:GhostTool.Neighbor frame ghosts",
            "action:GhostTool.Next frames ghosts",
            "action:GhostTool.Previous frames ghosts",
            "action:TrajectoryTool.All trajectories",
            "action:TrajectoryTool.Edge trajectories",
            "action:TrajectoryTool.Object and edge trajectories",
            "action:TrajectoryTool.Parent trajectories",
            "action:TrajectoryTool.Switch trajectory activity",
            "action:TrajectoryTool.Using only key frames",
            "action:View.Silhouette mode",
            "action:View.Surface",
            "action:Viewport.Four Viewports",
            "action:Viewport.One Viewport",
            "action:Viewport.Two Viewports",
        ],
        "handler": set_ghosts,
        "settle": True,
    },
    {
        "name": "set_trajectories",
        "description": "Set the Trajectory tool mode: off, all, edges, edges_and_fixed or parents; optionally restrict the "
        "trajectories to keyframes with only_key_frames. Use it to show the arcs behind selected or parented objects. "
        "Returns the trajectory mode and only_key_frames flag afterwards; repeating the same call changes nothing.",
        "inputSchema": schema(
            {
                "mode": {
                    "type": "string",
                    "enum": ["off", *TRAJECTORY_ACTIONS],
                    "description": "Trajectory mode; off turns the trajectory display off.",
                },
                "only_key_frames": {
                    "type": "boolean",
                    "description": "Show trajectories only through keyframes (optional; leave out to keep the current setting).",
                },
            },
            ["mode"],
        ),
        "requires": [
            "csc.app.ActionManager.call_action",
            "csc.app.Application.get_action_manager",
            "csc.app.get_application",
            "csc.view.Viewport.domain_viewport",
            "action:GhostTool.Disable ghost",
            "action:GhostTool.Ghosts for selected frames",
            "action:GhostTool.Keyframe ghosts",
            "action:GhostTool.Neighbor frame ghosts",
            "action:GhostTool.Next frames ghosts",
            "action:GhostTool.Previous frames ghosts",
            "action:TrajectoryTool.All trajectories",
            "action:TrajectoryTool.Edge trajectories",
            "action:TrajectoryTool.Object and edge trajectories",
            "action:TrajectoryTool.Parent trajectories",
            "action:TrajectoryTool.Switch trajectory activity",
            "action:TrajectoryTool.Using only key frames",
            "action:View.Silhouette mode",
            "action:View.Surface",
            "action:Viewport.Four Viewports",
            "action:Viewport.One Viewport",
            "action:Viewport.Two Viewports",
        ],
        "handler": set_trajectories,
        "settle": True,
    },
    {
        "name": "set_viewport_layout",
        "description": "Change the 3D viewport layout to 1, 2 or 4 viewports. Use it before comparing cameras or views. "
        "Returns the new viewport count; calling it with the current count changes nothing.",
        "inputSchema": schema(
            {
                "count": {
                    "type": "integer",
                    "enum": [1, 2, 4],
                    "description": "Number of viewports.",
                }
            },
            ["count"],
        ),
        "requires": [
            "csc.app.ActionManager.call_action",
            "csc.app.Application.get_action_manager",
            "csc.app.get_application",
            "csc.view.Viewport.domain_viewport",
            "action:GhostTool.Disable ghost",
            "action:GhostTool.Ghosts for selected frames",
            "action:GhostTool.Keyframe ghosts",
            "action:GhostTool.Neighbor frame ghosts",
            "action:GhostTool.Next frames ghosts",
            "action:GhostTool.Previous frames ghosts",
            "action:TrajectoryTool.All trajectories",
            "action:TrajectoryTool.Edge trajectories",
            "action:TrajectoryTool.Object and edge trajectories",
            "action:TrajectoryTool.Parent trajectories",
            "action:TrajectoryTool.Switch trajectory activity",
            "action:TrajectoryTool.Using only key frames",
            "action:View.Silhouette mode",
            "action:View.Surface",
            "action:Viewport.Four Viewports",
            "action:Viewport.One Viewport",
            "action:Viewport.Two Viewports",
        ],
        "handler": set_viewport_layout,
        "settle": True,
    },
    {
        "name": "set_silhouette",
        "description": "Turn the silhouette display of the 3D viewport on or off. Use it to check the character's outline "
        "against the background. Returns the silhouette state; calling it with the current value changes nothing.",
        "inputSchema": schema(
            {"enabled": {"type": "boolean", "description": "True turns silhouette display on, false turns it off."}},
            ["enabled"],
        ),
        "requires": [
            "csc.app.ActionManager.call_action",
            "csc.app.Application.get_action_manager",
            "csc.app.get_application",
            "csc.view.Viewport.domain_viewport",
            "action:GhostTool.Disable ghost",
            "action:GhostTool.Ghosts for selected frames",
            "action:GhostTool.Keyframe ghosts",
            "action:GhostTool.Neighbor frame ghosts",
            "action:GhostTool.Next frames ghosts",
            "action:GhostTool.Previous frames ghosts",
            "action:TrajectoryTool.All trajectories",
            "action:TrajectoryTool.Edge trajectories",
            "action:TrajectoryTool.Object and edge trajectories",
            "action:TrajectoryTool.Parent trajectories",
            "action:TrajectoryTool.Switch trajectory activity",
            "action:TrajectoryTool.Using only key frames",
            "action:View.Silhouette mode",
            "action:View.Surface",
            "action:Viewport.Four Viewports",
            "action:Viewport.One Viewport",
            "action:Viewport.Two Viewports",
        ],
        "handler": set_silhouette,
        "settle": True,
    },
    {
        "name": "set_surface",
        "description": "Turn the surface display of the 3D viewport on or off. Use it to switch between shaded surfaces "
        "and the wireframe/joint view. Returns the surface state; calling it with the current value changes nothing.",
        "inputSchema": schema(
            {"enabled": {"type": "boolean", "description": "True turns surfaces on, false turns them off."}},
            ["enabled"],
        ),
        "requires": [
            "csc.app.get_application",
            "csc.view.Viewport.domain_viewport",
            "action:GhostTool.Disable ghost",
            "action:GhostTool.Ghosts for selected frames",
            "action:GhostTool.Keyframe ghosts",
            "action:GhostTool.Neighbor frame ghosts",
            "action:GhostTool.Next frames ghosts",
            "action:GhostTool.Previous frames ghosts",
            "action:TrajectoryTool.All trajectories",
            "action:TrajectoryTool.Edge trajectories",
            "action:TrajectoryTool.Object and edge trajectories",
            "action:TrajectoryTool.Parent trajectories",
            "action:TrajectoryTool.Switch trajectory activity",
            "action:TrajectoryTool.Using only key frames",
            "action:View.Silhouette mode",
            "action:View.Surface",
            "action:Viewport.Four Viewports",
            "action:Viewport.One Viewport",
            "action:Viewport.Two Viewports",
        ],
        "handler": set_surface,
        "settle": True,
    },
]
