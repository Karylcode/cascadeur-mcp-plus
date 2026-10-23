"""Cycle creation and removal through Cascadeur's own timeline actions (T21).

Cascadeur 2026.2.3's cycle introspection API is unsafe: constructing csc.layers.CyclesViewer
and reading Cycle objects crashed Cascadeur three times on 2026-10-24 (heap corruption,
0xc0000374). The tools in this module therefore never read cycle structures. They validate the
frame range, select it with the T14 timeline helpers and run Cascadeur's own timeline actions:

* Timeline.Create cycle
* Timeline.Create cycle with position offset
* Timeline.Create cycle with position and rotation offsets
* Timeline.Remove cycles

A call is one undo step. The actions are native Cascadeur steps, so the undo entry carries
Cascadeur's own name ("Create cycle with position offset", ...) instead of an "MCP: " prefix;
the result's action/undo fields say which action ran.

Verification must avoid the cycle API too. tests/live/test_cycles.py checks the behaviour with
T12's list_controllers positions: a frame inside the loop and the corresponding frame one period
later have the same pose after a plain cycle, and the pose returns to the original animation
after remove_cycles. Unit tests cover the argument and range checks without Cascadeur.
"""

from __future__ import annotations

import functools
import traceback

from .. import common, timeline
from ..errors import ToolError
from ._schema import schema

OFFSETS = ("none", "position", "position_rotation")
OFFSET_ALIASES = {
    "none": "none",
    "position": "position",
    "position_and_rotation": "position_rotation",
    "position_rotation": "position_rotation",
}
OFFSET_ACTIONS = {
    "none": "Timeline.Create cycle",
    "position": "Timeline.Create cycle with position offset",
    "position_rotation": "Timeline.Create cycle with position and rotation offsets",
}
REMOVE_ACTION = "Timeline.Remove cycles"

START_SCHEMA = {
    "type": ["integer", "string"],
    "description": "First frame of the cycle's active range (0 or more).",
}
END_SCHEMA = {
    "type": ["integer", "string"],
    "description": "Last frame of the cycle's active range (start or more).",
}
OFFSET_SCHEMA = {
    "type": "string",
    "enum": list(OFFSETS),
    "description": "none (plain loop), position (keep moving forward, e.g. a walk cycle) or "
    "position_rotation (keep position and rotation offsets).",
}


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


# ---------------------------------------------------------------------------
# Pure helpers (unit tested without Cascadeur)
# ---------------------------------------------------------------------------


def normalize_offset(value) -> str:
    """Return one of OFFSETS from a user supplied offset value."""
    if value is None:
        return "none"
    text = str(value).strip().lower()
    for old, new in (("-", "_"), (" ", "_"), ("+", "_and_")):
        text = text.replace(old, new)
    while "__" in text:
        text = text.replace("__", "_")
    text = text.strip("_")
    if not text:
        return "none"
    if text not in OFFSET_ALIASES:
        raise ToolError(f"offset must be one of {', '.join(OFFSETS)} (got {value!r}).")
    return OFFSET_ALIASES[text]


def cycle_period(first: int, last: int) -> int:
    """Frames between two repetitions of a cycle that repeats the active range first..last."""
    return int(last) - int(first) + 1


def pose_matches(one, other, tolerance: float = 1e-3) -> bool:
    """True when two world positions (cm sequences) are within tolerance on every axis."""
    try:
        left = list(one)
        right = list(other)
    except TypeError:
        return False
    if not left or len(left) != len(right):
        return False
    values = list(zip(left, right, strict=False))
    for left, right in values:
        try:
            if abs(float(left) - float(right)) > tolerance:
                return False
        except (TypeError, ValueError):
            return False
    return True


# ---------------------------------------------------------------------------
# Cascadeur-side helpers (GUI thread; none of them reads cycle structures)
# ---------------------------------------------------------------------------


def _is_null(value) -> bool:
    try:
        return bool(value.is_null())
    except Exception:
        return value is None


def _layer_ids(viewer) -> list:
    """Every timeline layer id of the scene, in view order."""
    return [layer_id for layer_id in viewer.all_layer_ids() if not _is_null(layer_id)]


def _animation_size(scene):
    """Number of animated frames (data_viewer().get_animation_size()), or None.

    Cascy.casc has 1 (only frame 0 is keyed) even though its timeline range is 0-100; the cycle
    action crashed Cascadeur on such a one-frame animation, so create_cycle refuses it.
    """
    try:
        size = int(scene.model_viewer().data_viewer().get_animation_size())
    except Exception:
        return None
    return size if size >= 0 else None


def _last_key_frame(viewer) -> int:
    """The last keyed frame of the timeline (-1 when there is none)."""
    try:
        return int(viewer.last_key_pos())
    except Exception:
        return -1


def _range_limits(view_scene, scene, viewer) -> dict:
    """Frame limits used to validate a cycle range, read without the cycle API."""
    info = timeline.boundary_info(view_scene)
    size = _animation_size(scene)
    last_key = _last_key_frame(viewer)
    limits = {
        "first_frame": info["first_frame"],
        "last_frame": info["last_frame"],
        "animation_size": size,
        "last_key_frame": last_key,
    }
    return limits


def _check_cycle_range(first: int, last: int, limits: dict) -> dict | None:
    """Return a failure result when first..last cannot be looped, otherwise None."""
    if first < limits["first_frame"] or last > limits["last_frame"]:
        return common.fail(
            f"Frames {first}-{last} are outside the animation range {limits['first_frame']}-{limits['last_frame']}.",
            hint="Widen the timeline with set_animation_range (or get_timeline to read the range), then retry.",
            **limits,
        )
    size = limits["animation_size"]
    if size is not None and size < 2:
        return common.fail(
            f"The timeline has only {size} frame of keyed animation; a cycle needs at least two frames.",
            hint=(
                "Key the frames you want to loop first (set_key with frames such as 0 and 30), then retry "
                "create_cycle. Cascadeur 2026.2.3 crashes when a cycle is created on a one-frame animation."
            ),
            **limits,
        )
    last_key = limits["last_key_frame"]
    if last_key < 1:
        return common.fail(
            "The timeline has only 1 frame of keyed animation; a cycle needs at least two frames.",
            hint=(
                "Key the frames you want to loop first (set_key with frames such as 0 and 30), then retry "
                "create_cycle. Cascadeur 2026.2.3 crashes when a cycle is created on a one-frame animation."
            ),
            **limits,
        )
    limit = last_key if size is None else min(last_key, size - 1)
    if last > limit:
        return common.fail(
            f"Frames {first}-{last} go past the animated content (frames 0-{limit}).",
            hint=f"Pass an end frame of {limit} or less, or key more frames with set_key first.",
            **limits,
        )
    return None


def _select_interval(first: int, last: int, layer_ids) -> dict:
    """Select the range on every layer; returns the selector's answer or a failure result."""
    try:
        selected = timeline.select_interval(int(first), int(last), layer_ids)
    except ToolError as exc:
        return common.fail(str(exc), hint="Open a scene with timeline layers, then retry.")
    if int(selected["first"]) != int(first) or int(selected["last"]) != int(last):
        return common.fail(
            f"Cascadeur selected frames {selected['first']}-{selected['last']} instead of {first}-{last}.",
            hint="Use set_animation_range so the whole range is selectable, then retry.",
            selection=common.jsonable(selected),
        )
    return common.ok(**common.jsonable(selected))


def _call_action(key: str) -> dict:
    """Run a Cascadeur action; returns a failure result when it is rejected."""
    try:
        _csc().app.get_application().get_action_manager().call_action(key)
    except Exception as exc:  # noqa: BLE001 - report the action failure
        return common.fail(
            f"Cascadeur refused the action {key!r}: {type(exc).__name__}: {exc}",
            hint="Check that the scene is animated and the range is valid, then retry; get_log may explain it.",
            action=key,
        )
    return common.ok(action=key)


def _csc():
    """The Cascadeur csc module (imported lazily so unit tests can substitute it)."""
    import csc

    return csc


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@_tool("Check that the scene is animated; get_timeline shows the range.")
def create_cycle(start=None, end=None, offset="none") -> dict:
    """Turn a frame range into a cycle with Cascadeur's own Timeline action."""
    first = timeline.parse_frame(start, "start")
    last = timeline.parse_frame(end, "end")
    if first > last:
        raise _BadArgument(
            f"start ({first}) must not be greater than end ({last}).",
            "Pass start <= end, for example create_cycle(start=0, end=30, offset='position').",
        )
    if first == last:
        raise _BadArgument(
            f"A cycle needs at least two frames (got {first}).",
            "Pass a longer range, for example create_cycle(start=0, end=30).",
        )
    mode = normalize_offset(offset)
    _app, view_scene, scene = common.scenes()
    viewer = scene.layers_viewer()
    layer_ids = _layer_ids(viewer)
    if not layer_ids:
        return common.fail(
            "The scene has no timeline layers, so no cycle was created.",
            hint="Open an animated scene with a character (for example Dracorex_walk_cycle.casc).",
        )
    limits = _range_limits(view_scene, scene, viewer)
    failure = _check_cycle_range(first, last, limits)
    if failure is not None:
        return failure
    selection = _select_interval(first, last, layer_ids)
    if not selection.get("ok"):
        return selection
    action = OFFSET_ACTIONS[mode]
    called = _call_action(action)
    if not called.get("ok"):
        return called
    result = common.ok(
        start=first,
        end=last,
        offset=mode,
        action=action,
        selection=selection,
        undo=None,
        hint=(
            "Cascadeur's Timeline action made this loop; its single undo step has Cascadeur's own name "
            "instead of 'MCP: create_cycle'. Cycle structures cannot be read back safely on this build, "
            "so verify the result by comparing a frame inside the range with the same frame one cycle later."
        ),
    )
    return common.jsonable(result)


@_tool("Check that an animated scene is open and that start/end are valid frames.")
def remove_cycles(start=None, end=None) -> dict:
    """Remove the cycles that intersect a frame range with Cascadeur's Timeline.Remove cycles action."""
    wanted_first = None if start is None else timeline.parse_frame(start, "start")
    wanted_last = None if end is None else timeline.parse_frame(end, "end")
    if wanted_first is not None and wanted_last is not None and wanted_first > wanted_last:
        raise _BadArgument(
            f"start ({wanted_first}) must not be greater than end ({wanted_last}).",
            "Pass start <= end, or omit both to remove every cycle.",
        )
    _app, view_scene, scene = common.scenes()
    viewer = scene.layers_viewer()
    layer_ids = _layer_ids(viewer)
    if not layer_ids:
        return common.fail(
            "The scene has no timeline layers, so it has no cycles.",
            hint="Open an animated scene with a character (for example Dracorex_walk_cycle.casc).",
        )
    info = timeline.boundary_info(view_scene)
    first = info["first_frame"] if wanted_first is None else wanted_first
    last = info["last_frame"] if wanted_last is None else wanted_last
    if first > last:
        raise _BadArgument(
            f"start ({first}) must not be greater than end ({last}).",
            "Pass start <= end, or omit both to remove every cycle.",
        )
    selection = _select_interval(first, last, layer_ids)
    if not selection.get("ok"):
        return selection
    called = _call_action(REMOVE_ACTION)
    if not called.get("ok"):
        return called
    result = common.ok(
        start=first,
        end=last,
        action=REMOVE_ACTION,
        selection=selection,
        undo=None,
        hint=(
            "Cascadeur's Timeline.Remove cycles action cleared the cycles in this selection; its single undo "
            "step has Cascadeur's own name instead of 'MCP: remove_cycles'. Verify by comparing the animation "
            "after the last loop, or by undoing."
        ),
    )
    return common.jsonable(result)


TOOLS = [
    {
        "name": "create_cycle",
        "description": "Turn the frame range start..end into a timeline cycle (loop) with Cascadeur's own "
        "'Timeline.Create cycle' action. offset='none' repeats the same pose, 'position' keeps moving forward "
        "(walk cycles), 'position_rotation' also keeps rotating. Use it after the pose keys are done and before "
        "rendering a looping animation; the range must be inside the animation range and contain at least two "
        "frames of keyed animation (Cascadeur crashes when a cycle is created on a one-frame animation, so such "
        "calls are refused with a hint). Returns the requested range, the action that ran and the confirmed timeline "
        "selection. A call is one native Cascadeur undo step; cycle structures cannot be read back safely on this "
        "build, so verify by comparing a frame inside the range with the same frame one cycle later.",
        "inputSchema": schema(
            {"start": START_SCHEMA, "end": END_SCHEMA, "offset": OFFSET_SCHEMA},
            ["start", "end"],
        ),
        "fn": create_cycle,
    },
    {
        "name": "remove_cycles",
        "description": "Remove the timeline cycles that intersect start..end with Cascadeur's own "
        "'Timeline.Remove cycles' action, or every cycle in the animation range when both are omitted. Use it to "
        "replace a loop or to stop an animation after the last repetition. Pass only start to remove from that "
        "frame to the end of the animation, or only end to remove up to that frame. Returns the range, the action "
        "that ran and the confirmed timeline selection. One native Cascadeur undo step; does not delete "
        "keyframes.",
        "inputSchema": schema(
            {
                "start": {
                    "type": ["integer", "string"],
                    "description": "First frame of the range (default: animation start).",
                },
                "end": {
                    "type": ["integer", "string"],
                    "description": "Last frame of the range (default: animation end).",
                },
            }
        ),
        "fn": remove_cycles,
    },
]
