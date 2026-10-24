"""AI inbetweening and motion generation (T20).

Cascadeur can synthesize the frames between a few key poses with its built-in AI models. The
timeline action "Timeline.AI.AI on selected interval" marks the selected interval as AI
interpolation; "View.Inbetweening_RunMotionGeneration" runs the motion generator, which writes a
new motion for the selected interval. Both operations can take a while, so they are background
jobs (T03): the tool call returns a job id immediately and get_job reports the result.

The two actions work on Cascadeur's current selection: the controllers (T13) and the timeline
interval (T14). This module saves the previous selection and playhead, selects the requested
objects and frames, runs the action, and restores what the caller had once the job finishes. It
never sleeps on the GUI thread: the waiting is a QTimer poll of the layer data.

The mechanics that do not need Cascadeur (interval parsing, key prerequisite checks, layer
snapshot comparison and the "where did AI interpolation land" summary) are pure functions so
tests/test_inbetweening.py can exercise them in ordinary Python.
"""

from __future__ import annotations

import functools
import os
import time
import traceback

from .. import common, rig, timeline
from ..errors import ToolError
from ._schema import schema

AI_ACTION = "Timeline.AI.AI on selected interval"
MOTION_ACTION = "View.Inbetweening_RunMotionGeneration"
JOB_TIMEOUT_S = 1800
MIN_SETTLE_S = 1.2
QUIET_POLLS = 8
AI_APPLY_TIMEOUT_S = 60.0
MOTION_CHANGE_TIMEOUT_S = 300.0
DIALOG_CHECK_EVERY = 5  # about 500 ms between QML dialog walks
DIALOG_WORDS = (
    "inbetween",
    "interpolation",
    "motion generation",
    "key frame",
    "keyframe",
    "interval between keys",
    "rig info",
    "points positions",
    "several models",
    "no points",
    "please select at least",
)
DISMISS_LABELS = ("ok", "close", "cancel", "no", "dismiss", "yes")


# The clock is a function so the unit tests can drive begin/poll without waiting in real time.
def _now() -> float:
    return time.monotonic()


# One AI operation at a time. Cascadeur's inbetweening tool has a single editor per scene and the
# job contract gives poll() no job id, so the state of the running operation lives here. A second
# begin() refuses until the first finishes (or its cancel hook runs).
_ACTIVE = None


# ---------------------------------------------------------------------------
# Pure helpers (unit tested without Cascadeur)
# ---------------------------------------------------------------------------


def parse_interval(start, end) -> tuple[int, int]:
    """Return (first, last) for the AI action's inclusive frame interval.

    The action needs a real interval: a single frame has nothing to interpolate. Frames use the
    same parsing and limits as T14 so every timeline tool rejects bad input the same way.
    """
    first = timeline.parse_frame(start, "start")
    last = timeline.parse_frame(end, "end")
    if first >= last:
        raise ToolError(
            f"start ({first}) must be smaller than end ({last}); AI inbetweening needs an interval "
            "with at least two frames."
        )
    return first, last


def key_report(layer_keys, first: int, last: int) -> dict:
    """Summarize the keys of every selected layer around frames first..last.

    layer_keys maps a layer name (or id string) to its sorted key positions. The report keeps the
    union of keys, the keys inside the interval, the closest key before/after it, and which layers
    are missing a boundary key. check_prerequisites() turns that into the tool's error/hint.
    """
    union = set()
    inside = set()
    before = []
    after = []
    missing_boundary = []
    for name, keys in layer_keys.items():
        positions = sorted({int(key) for key in keys or ()})
        union.update(positions)
        inside.update(frame for frame in positions if first <= frame <= last)
        left = [frame for frame in positions if frame <= first]
        right = [frame for frame in positions if frame >= last]
        before.append(max(left) if left else None)
        after.append(min(right) if right else None)
        if not left or not right:
            missing_boundary.append(name)
    return {
        "key_frames": sorted(union),
        "inside": sorted(inside),
        "before": sorted({frame for frame in before if frame is not None}),
        "after": sorted({frame for frame in after if frame is not None}),
        "layers_without_boundary_keys": sorted(missing_boundary),
    }


def check_prerequisites(report: dict, first: int, last: int, *, action: str = "AI inbetweening") -> None:
    """Raise ToolError when Cascadeur's AI action would show a warning dialog instead of working.

    Cascadeur's inbetweening tool refuses an interval with fewer than two keyframes or without a
    key on either side, and the refusal is a modal dialog. Checking it before the action keeps the
    GUI usable and turns the dialog into an actionable tool error.
    """
    if len(report["key_frames"]) < 2:
        raise ToolError(
            f"{action} needs at least two keyframes on the selected layers; found "
            f"{len(report['key_frames'])}. Use set_key to key the poses before and after the interval."
        )
    if not report["before"]:
        raise ToolError(
            f"{action} needs a keyframe at or before frame {first} on the selected layers, but none exists. "
            "Key the starting pose with set_key first."
        )
    if not report["after"]:
        raise ToolError(
            f"{action} needs a keyframe at or after frame {last} on the selected layers, but none exists. "
            "Key the ending pose with set_key first."
        )
    if not report["inside"]:
        raise ToolError(
            f"No keyframe lies between frames {first} and {last}. {action} needs the keys that bracket the "
            "interval to be part of it; widen the interval or key the poses at its ends."
        )
    missing = report.get("layers_without_boundary_keys") or []
    if missing:
        shown = ", ".join(missing[:12]) + (" ..." if len(missing) > 12 else "")
        raise ToolError(
            f"{action} needs a keyframe at or before frame {first} and a keyframe at or after frame {last} "
            f"on every selected layer. These selected layers are missing one: {shown}."
        )


def longest_key_gap(layer_keys, first: int, last: int):
    """The longest distance between consecutive keys that overlaps first..last, or None.

    Cascadeur refuses an AI interval longer than its "InbetweeningTool/MaxIntervalLength" setting
    and writes which key pair was too far apart. Checking the same gaps before the action avoids a
    warning dialog.
    """
    longest = None
    pair = None
    for keys in layer_keys.values():
        positions = sorted({int(key) for key in keys or ()})
        for left, right in zip(positions, positions[1:], strict=False):
            if right <= first or left >= last:
                continue
            gap = right - left
            if longest is None or gap > longest:
                longest = gap
                pair = [left, right]
    return {"length": longest, "frames": pair}


def snapshot_layers(viewer, layer_ids) -> dict:
    """Read keys and interpolation of every layer (GUI thread, read-only).

    The result is a plain dict keyed by the layer id string, suitable for storing between begin()
    and poll(). Reading the layer data does not evaluate the AI interpolation, so polling it never
    changes the scene.
    """
    result = {}
    for layer_id in layer_ids:
        try:
            layer = viewer.layer(layer_id)
        except Exception:
            try:
                layer = viewer.find_layer(layer_id)
            except Exception:
                continue
        keys = timeline.key_frames(layer)
        interpolation = timeline.interpolation_by_key(layer)
        result[str(layer_id)] = {
            "name": _layer_name(layer, layer_id),
            "keys": keys,
            "interpolation": interpolation,
        }
    return result


def _layer_name(layer, layer_id) -> str:
    for attribute in ("real_name", "name"):
        try:
            value = getattr(layer, attribute)
        except Exception:
            continue
        if isinstance(value, str) and value.strip():
            return value
    try:
        header = layer.header
        name = getattr(header, "name", None)
        if isinstance(name, str) and name.strip():
            return name
    except Exception:
        pass
    return str(layer_id)


def diff_snapshots(before: dict, after: dict, first: int, last: int) -> dict:
    """Compare two snapshot_layers() results inside first..last.

    Returns the added/removed keys, changed interpolation positions, the changed layers by name,
    and the smallest frame range that actually changed (None when nothing did).
    """
    keys_added = 0
    keys_removed = 0
    changed_frames = set()
    changed_layers = []
    for layer_id, entry in after.items():
        old = before.get(layer_id, {})
        old_keys = set(old.get("keys") or ())
        new_keys = set(entry.get("keys") or ())
        added = {frame for frame in new_keys - old_keys if first <= frame <= last}
        removed = {frame for frame in old_keys - new_keys if first <= frame <= last}
        old_interpolation = old.get("interpolation") or {}
        new_interpolation = entry.get("interpolation") or {}
        interpolation_changed = {
            frame
            for frame in set(old_interpolation) | set(new_interpolation)
            if first <= frame <= last and old_interpolation.get(frame) != new_interpolation.get(frame)
        }
        if added or removed or interpolation_changed or old_keys != new_keys:
            changed_layers.append(entry.get("name") or layer_id)
        keys_added += len(added)
        keys_removed += len(removed)
        changed_frames.update(added)
        changed_frames.update(removed)
        changed_frames.update(interpolation_changed)
    removed_layers = [before[layer_id].get("name") or layer_id for layer_id in before if layer_id not in after]
    changed_layers.extend(removed_layers)
    return {
        "keys_added": keys_added,
        "keys_removed": keys_removed,
        "changed_frames": sorted(changed_frames),
        "changed_layers": changed_layers,
        "removed_layers": removed_layers,
        "changed_first": min(changed_frames) if changed_frames else None,
        "changed_last": max(changed_frames) if changed_frames else None,
    }


def interval_summary(snapshot: dict, first: int, last: int) -> dict:
    """Summarize the interpolation segments of first..last and how many layers are AI.

    A layer counts as AI when any part of first..last is covered by an "ai" interval, which is
    exactly what Timeline.AI.AI on selected interval sets.
    """
    counts = {}
    ai_layers = []
    for layer_id, entry in snapshot.items():
        summary = timeline.summarize_keys(
            entry.get("keys") or (),
            entry.get("interpolation") or {},
            first,
            last,
        )
        names = {segment.get("interpolation") for segment in summary["segments"]}
        for name in names:
            if name is not None:
                counts[name] = counts.get(name, 0) + 1
        if "ai" in names:
            ai_layers.append(entry.get("name") or layer_id)
    return {"ai_layers": ai_layers, "interpolation": counts}


def changed_interpolation_range(before: dict, after: dict, first: int, last: int):
    """The first/last frame whose interpolation differs between two snapshots, or None.

    A key position alone under-reports the range: changing the interpolation of the key at frame 0
    changes the whole interval up to the next key. This compares the timeline's per-frame segments.
    """
    changed = set()
    for layer_id, entry in after.items():
        old = before.get(layer_id) or {}
        old_summary = timeline.summarize_keys(
            old.get("keys") or (),
            old.get("interpolation") or {},
            first,
            last,
        )
        new_summary = timeline.summarize_keys(
            entry.get("keys") or (),
            entry.get("interpolation") or {},
            first,
            last,
        )
        old_names = _segment_names(old_summary["segments"])
        new_names = _segment_names(new_summary["segments"])
        changed.update(frame for frame in range(first, last + 1) if old_names.get(frame) != new_names.get(frame))
    if not changed:
        return None
    return min(changed), max(changed)


def _segment_names(segments) -> dict:
    names = {}
    for segment in segments:
        name = segment.get("interpolation")
        for frame in range(int(segment["first"]), int(segment["last"]) + 1):
            names[frame] = name
    return names


def log_messages(lines) -> list[str]:
    """Keep the AI/motion-generation lines from a log tail (for results and error hints)."""
    result = []
    for line in lines or ():
        lowered = str(line).lower()
        if "inbetweening" in lowered or "motion generation" in lowered or "motiongeneration" in lowered:
            result.append(str(line))
    return result


def log_error(lines):
    """Return the first AI/motion-generation log line that reports a failure, or None."""
    for line in log_messages(lines):
        lowered = line.lower()
        if " error:" in lowered or "failed" in lowered or "exception" in lowered:
            return line
    return None


def log_done(lines) -> bool:
    """True when the log says the AI/motion-generation operation finished."""
    for line in log_messages(lines):
        lowered = line.lower()
        if lowered.endswith(": done") or "inbetweening: done" in lowered or "motion generation: done" in lowered:
            return True
    return False


def signature(snapshot: dict):
    """A hashable value that changes whenever the keys or interpolation change."""
    return tuple(
        sorted(
            (
                layer_id,
                tuple(entry.get("keys") or ()),
                tuple(sorted((entry.get("interpolation") or {}).items())),
            )
            for layer_id, entry in snapshot.items()
        )
    )


# ---------------------------------------------------------------------------
# Tool wrapper and scene state
# ---------------------------------------------------------------------------


def _tool(hint: str):
    """Turn exceptions into the shared {"ok": false, "error", "hint"} result."""

    def decorate(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except rig.RigError as exc:
                return exc.result()
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


def _objects(controllers) -> list:
    """Resolve the controllers argument; omitted means every controller in the scene."""
    if controllers is None or controllers == "" or controllers == []:
        return timeline.target_object_ids()
    return rig.resolve(controllers)


def _scene():
    """(application, view scene, domain scene) for the active tab."""
    return common.scenes()


def _layer_ids(object_ids) -> list:
    _app, _view_scene, scene = _scene()
    return timeline.layer_ids_for_viewer(scene.layers_viewer(), object_ids)


def _ensure_unlocked(object_ids):
    """T23's layer-lock guard, imported lazily so the unit tests can replace it.

    Motion generation has no undo step, so writing a locked layer would be unrecoverable. Both
    tools call this before they select anything or schedule an action.
    """
    from .layers import ensure_unlocked

    return ensure_unlocked(object_ids)


def _max_interval_length(view_scene):
    """Cascadeur's "InbetweeningTool/MaxIntervalLength" setting, or None when unavailable."""
    handler = None
    try:
        handler = view_scene.get_setting_handler()
    except Exception:
        handler = None
    if handler is not None:
        for group, key in (
            ("InbetweeningTool", "MaxIntervalLength"),
            ("Inbetweening", "MaxIntervalLength"),
            ("Timeline", "MaxIntervalLength"),
        ):
            for method in ("get_int_value", "get_float_value"):
                try:
                    value = getattr(handler, method)(group, key)
                except Exception:
                    continue
                try:
                    number = int(value)
                except (TypeError, ValueError):
                    continue
                if number > 0:
                    return number
    try:
        import csc

        manager = csc.app.get_application().get_setting_manager()
    except Exception:
        return None
    for key in ("InbetweeningTool/MaxIntervalLength", "Inbetweening/MaxIntervalLength"):
        for method in ("get_int_value", "get_float_value"):
            try:
                value = getattr(manager, method)(key)
            except Exception:
                continue
            try:
                number = int(value)
            except (TypeError, ValueError):
                continue
            if number > 0:
                return number
    return None


def _pro_available():
    """True/False when Cascadeur reports the license state, None when the API is unavailable."""
    try:
        import csc

        app = csc.app.get_application()
        return bool(app.is_pro_features_available())
    except Exception:
        return None


def _selection_signature(selection):
    """An exact (layer id, frames) signature for an IndicesContainer, or None when unreadable.

    Saving only all_included_layer_ids() plus frames_interval() loses which layer had which
    frames: restoring that would select every layer across the outer interval. direct_indices()
    keeps each layer's own frame set, so begin and restore can be compared exactly.
    """
    if selection is None:
        return None
    try:
        direct = selection.direct_indices()
    except Exception:
        direct = None
    if direct is not None:
        result = []
        for layer_id, frames in direct.items():
            try:
                values = tuple(sorted({int(frame) for frame in frames}))
            except Exception:
                values = ()
            result.append((str(layer_id), values))
        return tuple(sorted(result))
    try:
        ids = tuple(sorted(str(item) for item in selection.item_ids()))
        interval = selection.frames_interval()
        return (ids, (int(interval.first), int(interval.last)))
    except Exception:
        return None


def _dialogs():
    """Visible dialogs/popups from the MCP UI helper, or [] when the QML tree cannot be read."""
    try:
        from .. import ui

        return list(ui.dialogs().get("dialogs") or [])
    except Exception:
        return []


def _dialog_text(dialog) -> str:
    parts = list(dialog.get("texts") or [])
    parts.extend(button.get("text") or "" for button in dialog.get("buttons") or [])
    return " ".join(part for part in parts if part).strip()


def _dialog_signature(dialog):
    return (
        dialog.get("kind"),
        dialog.get("type"),
        tuple(dialog.get("texts") or ()),
        tuple((button.get("text") or "") for button in dialog.get("buttons") or ()),
    )


def _dialog_fingerprints(dialogs=None):
    return {_dialog_signature(dialog) for dialog in (_dialogs() if dialogs is None else dialogs)}


def _new_dialogs(before):
    """Dialogs that were not open when the job began (only these belong to this job)."""
    return [dialog for dialog in _dialogs() if _dialog_signature(dialog) not in before]


def _looks_like_inbetweening_dialog(text: str) -> bool:
    lowered = text.lower()
    return any(word in lowered for word in DIALOG_WORDS)


def _click_dialog(dialog, labels=DISMISS_LABELS):
    """Click the first safe button of a dialog; returns its label or None."""
    wanted = tuple(label.lower() for label in labels)
    buttons = list(dialog.get("buttons") or [])
    ranked = [button for label in wanted for button in buttons if (button.get("text") or "").strip().lower() == label]
    for button in ranked:
        label = (button.get("text") or "").strip()
        try:
            from .. import ui

            ui.click(ref=button["ref"])
        except Exception:
            continue
        return label
    return None


def _check_dialogs(state) -> None:
    """Dismiss a new inbetweening refusal dialog and remember why the job has to fail.

    Only dialogs that appeared after begin() are considered, so the user's own prompt is never
    clicked. The action may still be blocked on the dialog; poll waits for action_finished and
    then reports the failure instead of restoring the selection underneath the running action.
    """
    if state.get("dialog_error") or state.get("dialogs_before") is None:
        return
    if state.get("poll_count", 0) % DIALOG_CHECK_EVERY:
        return
    for dialog in _new_dialogs(state["dialogs_before"]):
        text = _dialog_text(dialog)
        if not _looks_like_inbetweening_dialog(text):
            continue
        label = _click_dialog(dialog)
        state["dialog_error"] = text or "Cascadeur opened an inbetweening refusal dialog."
        state.setdefault("dialog_actions", []).append(label or "unclicked")
        return


def _selection_state(scene) -> dict:
    """Save the exact object/timeline selections and the playhead (best-effort, never raises)."""
    state = {"ids": None, "layers": None, "frame": None}
    try:
        selector = scene.selector()
        selection = selector.selected()
        state["ids"] = list(getattr(selection, "ordered_ids", None) or selection.ids)
    except Exception:
        pass
    try:
        state["layers"] = scene.get_layers_selector().selection()
    except Exception:
        pass
    try:
        state["frame"] = int(scene.get_current_frame(False))
    except Exception:
        pass
    return state


def _select(scene, object_ids) -> dict:
    """Select the controllers and the timeline interval the AI action works on."""
    selector = scene.selector()
    pivot = object_ids[0] if object_ids else None
    if pivot is None:
        try:
            import csc

            pivot = csc.model.ObjectId.null()
        except Exception:
            pivot = None
    selector.select(set(object_ids), pivot)
    return {}


def _restore(scene, saved: dict) -> dict:
    """Put the caller's object selection, exact timeline selection and playhead back.

    Returns {"restored": bool, "warning": str, "errors": [...]} and never raises. "restored" is
    only true after reading the values back: a setter that silently ignores the restore must not
    be reported as success.
    """
    errors = []
    if saved.get("ids") is not None:
        ids = list(saved["ids"])
        try:
            import csc

            # selector.pivot() is a Pivot object, not the ObjectId select() wants; T13 also uses
            # the first selected id (or a null ObjectId for an empty selection) as the pivot.
            pivot = ids[0] if ids else csc.model.ObjectId.null()
        except Exception:
            pivot = ids[0] if ids else None
        try:
            selector = scene.selector()
            selector.select(set(ids), pivot)
            actual = selector.selected()
            actual_ids = set(getattr(actual, "ordered_ids", None) or actual.ids)
            if actual_ids != set(ids):
                errors.append(f"object selection is {len(actual_ids)} object(s), expected {len(ids)}")
        except Exception as exc:  # noqa: BLE001 - keep restoring the other state
            errors.append(f"object selection: {type(exc).__name__}: {exc}")
    if saved.get("layers") is not None:
        try:
            selector = scene.get_layers_selector()
            # T17's exact form: hand the saved IndicesContainer back, not a re-created interval.
            selector.set_full_selection_by_parts(saved["layers"])
            expected = _selection_signature(saved["layers"])
            actual = _selection_signature(selector.selection())
            if expected is not None and actual != expected:
                errors.append("timeline selection differs after restore")
        except Exception as exc:  # noqa: BLE001 - keep restoring the playhead
            errors.append(f"timeline selection: {type(exc).__name__}: {exc}")
    if saved.get("frame") is not None:
        try:
            wanted = int(saved["frame"])
            if int(scene.get_current_frame(False)) != wanted:
                scene.set_current_frame(wanted)
            actual = int(scene.get_current_frame(False))
            if actual != wanted:
                errors.append(f"playhead is {actual}, expected {wanted}")
        except Exception as exc:  # noqa: BLE001 - report, do not raise
            errors.append(f"playhead: {type(exc).__name__}: {exc}")
    if errors:
        return {
            "restored": False,
            "warning": "Cascadeur did not restore the previous selection/playhead: " + "; ".join(errors),
            "errors": errors,
        }
    return {"restored": True}


def _last_line() -> str:
    text = traceback.format_exc().strip().splitlines()
    return text[-1] if text else "unknown error"


def _traceback_last_line(text) -> str:
    lines = [line.strip() for line in str(text or "").strip().splitlines() if line.strip()]
    return lines[-1] if lines else "unknown error"


def _log_offset():
    try:
        return os.path.getsize(common.LOG_PATH)
    except OSError:
        return None


def _log_lines(since):
    try:
        return common.log_tail(since=since, lines=120)
    except Exception:
        return []


def _clear_active() -> None:
    global _ACTIVE
    _ACTIVE = None


def _active() -> dict | None:
    """The running operation, discarding one that can no longer receive a poll."""
    global _ACTIVE
    if _ACTIVE is not None and _now() - _ACTIVE["started"] > JOB_TIMEOUT_S + 120:
        _clear_active()
    return _ACTIVE


# ---------------------------------------------------------------------------
# Begin: validate, select, schedule the action (GUI thread)
# ---------------------------------------------------------------------------


def _schedule(callback) -> None:
    """Run callback on the next GUI event-loop turn (unit tests replace this with an immediate call)."""
    from PySide6 import QtCore

    QtCore.QTimer.singleShot(0, callback)


def _run_action(state: dict) -> None:
    """Run the blocking Cascadeur action and mark the state finished (GUI thread)."""
    try:
        app = common.scenes()[0]
        app.get_action_manager().call_action(state["action"])
    except Exception:
        state["action_error"] = traceback.format_exc()
    finally:
        state["action_finished"] = True
        if _ACTIVE is not state:
            # cancel_job or a timeout cleared the state while the action was running.
            try:
                _app, _view_scene, scene = _scene()
                _restore(scene, state.get("selection") or {})
            except Exception:
                pass


def _begin(kind: str, action: str, start, end, controllers) -> dict:
    """Start an AI operation: preflight, select objects/interval, call the action."""
    global _ACTIVE
    if _active() is not None:
        return common.fail(
            "Another AI inbetweening or motion-generation job is still running.",
            hint="Wait for it with get_job, or cancel it with cancel_job, before starting another one.",
        )
    try:
        first, last = parse_interval(start, end)
    except ToolError as exc:
        return common.fail(str(exc), "Pass start and end as whole frames with start < end.")
    if _pro_available() is False:
        return common.fail(
            "This Cascadeur license does not include AI features.",
            hint="AI inbetweening and motion generation need a Pro (or trial) license; "
            "Cascadeur reports that is_pro_features_available() is False.",
        )
    try:
        object_ids = _objects(controllers)
    except rig.RigError as exc:
        return exc.result()
    if not object_ids:
        return common.fail(
            "No controllers matched, so there is nothing to interpolate.",
            hint="Call list_controllers to see the control point names and aliases.",
        )
    try:
        blocked = _ensure_unlocked(object_ids)
    except Exception:
        return common.fail(
            f"Could not check the layer locks: {_last_line()}",
            hint="Check get_log; the tool refuses to write when it cannot prove a layer is unlocked.",
            traceback=traceback.format_exc(),
        )
    if blocked is not None:
        return blocked
    layer_ids = _layer_ids(object_ids)
    if not layer_ids:
        return common.fail(
            "None of the matched controllers has a timeline layer.",
            hint="Open an animated scene with RigInfo/AnimationInfo (for example Cascy.casc) and use set_key first.",
        )
    _app, view_scene, scene = _scene()
    viewer = scene.layers_viewer()
    selected_baseline = snapshot_layers(viewer, layer_ids)
    layer_keys = {entry["name"]: entry["keys"] for entry in selected_baseline.values()}
    report = key_report(layer_keys, first, last)
    try:
        check_prerequisites(report, first, last, action=("AI inbetweening" if kind == "ai" else "Motion generation"))
    except ToolError as exc:
        return common.fail(
            str(exc),
            "Use set_key on the listed layers (or pass fewer controllers whose layers have keys at both ends), "
            "then retry.",
        )
    limit = _max_interval_length(view_scene)
    gap = longest_key_gap(layer_keys, first, last)
    if limit is not None and gap["length"] is not None and gap["length"] > limit:
        return common.fail(
            f"The longest key interval here is {gap['length']} frames ({gap['frames'][0]}-{gap['frames'][1]}), "
            f"which is longer than Cascadeur's limit of {limit} frames.",
            hint="Add a key inside the interval (set_key) and retry, or shorten start/end.",
        )
    # Motion generation can add or change layers outside the selected ones, so its result compares
    # every layer. AI interpolation only touches the selected layers.
    if kind == "motion":
        try:
            tracked_layers = list(viewer.all_layer_ids())
        except Exception:
            tracked_layers = layer_ids
    else:
        tracked_layers = layer_ids
    baseline = snapshot_layers(viewer, tracked_layers)
    saved = _selection_state(scene)
    # Snapshot the dialogs that already existed: only new ones can be this action's refusal.
    dialogs_before = _dialog_fingerprints()
    offset = _log_offset()
    try:
        _select(scene, object_ids)
        selected = timeline.select_interval(first, last, layer_ids)
    except Exception:
        failure = traceback.format_exc()
        _restore(scene, saved)
        return common.fail(
            f"Cascadeur did not select the interval for {action!r}: {_last_line()}",
            hint="Check that the scene is animated, no modal dialog is open, and get_log for the traceback.",
            traceback=failure,
        )
    state = {
        "kind": kind,
        "action": action,
        "first": first,
        "last": last,
        "started": _now(),
        "objects": object_ids,
        "layers": layer_ids,
        "tracked": tracked_layers,
        "selected_baseline": selected_baseline,
        "controllers": timeline.object_names(object_ids),
        "baseline": baseline,
        "selection": saved,
        "log_offset": offset,
        "last_signature": None,
        "last_snapshot": None,
        "quiet": 0,
        "limit": limit,
        "selected": selected,
        "action_finished": False,
        "action_error": None,
        "dialogs_before": dialogs_before,
        "dialog_error": None,
        "dialog_actions": [],
        "poll_count": 0,
    }
    _ACTIVE = state
    try:
        # The action itself runs synchronously on the GUI thread (Run Motion Generation can take
        # tens of seconds), so queue it for the next event-loop turn: begin() returns first and the
        # job exists before the blocking call starts. poll() sees action_finished and ignores the
        # action's intermediate data.
        _schedule(lambda: _run_action(state))
    except Exception:
        _clear_active()
        _restore(scene, saved)
        return common.fail(
            f"Cascadeur did not schedule {action!r}: {_last_line()}",
            hint="Check get_log; the scene may have been closed.",
            traceback=traceback.format_exc(),
        )
    return common.ok(job="started", first_frame=first, last_frame=last, layers=len(layer_ids))


# ---------------------------------------------------------------------------
# Poll: wait until the timeline data stops changing and the action took effect
# ---------------------------------------------------------------------------


def _fail_active(message: str, hint: str, **data):
    """Restore the caller's state, forget the operation, and return a failed poll update."""
    global _ACTIVE
    state = _ACTIVE
    _clear_active()
    warning = None
    if state is not None:
        try:
            _app, _view_scene, scene = _scene()
            result = _restore(scene, state.get("selection") or {})
            if not result.get("restored"):
                warning = result.get("warning")
        except Exception:
            warning = "Cascadeur did not restore the previous selection/playhead: " + _last_line()
    if warning:
        data.setdefault("warning", warning)
    return common.fail(message, hint=hint, **data)


def _result(state: dict, snapshot: dict, log_lines) -> dict:
    first, last = state["first"], state["last"]
    diff = diff_snapshots(state["baseline"], snapshot, first, last)
    summary = interval_summary(snapshot, first, last)
    changed_first = diff["changed_first"]
    changed_last = diff["changed_last"]
    interpolation_range = changed_interpolation_range(state["baseline"], snapshot, first, last)
    plans = [bounds for bounds in ((changed_first, changed_last), interpolation_range) if bounds[0] is not None]
    if plans:
        changed_first = min(bounds[0] for bounds in plans)
        changed_last = max(bounds[1] for bounds in plans)
    else:
        # An action that only marks the interval can leave no key/interpolation difference when
        # Cascadeur reports the same interpolation; the requested interval is the safe answer.
        changed_first, changed_last = first, last
    data = {
        "action": state["action"],
        "first_frame": first,
        "last_frame": last,
        "frame_count": last - first + 1,
        "changed_first": changed_first,
        "changed_last": changed_last,
        "layers": len(state["layers"]),
        "tracked_layers": len(state["tracked"]),
        "layers_changed": len(diff["changed_layers"]),
        "changed_layer_names": diff["changed_layers"][:20],
        "controllers": common.jsonable(state["controllers"]),
        "keys_added": diff["keys_added"],
        "keys_removed": diff["keys_removed"],
        "interpolation": summary["interpolation"],
        "ai_layers": len(summary["ai_layers"]),
        "ai_layer_names": summary["ai_layers"][:20],
        "seconds": round(_now() - state["started"], 3),
    }
    if state.get("limit") is not None:
        data["max_interval_length"] = state["limit"]
    messages = log_messages(log_lines)
    if messages:
        data["log"] = messages[-10:]
    return common.ok(**common.jsonable(data))


@_tool("Check that an animated scene is open and that the timeline action is available.")
def _poll(start=None, end=None, controllers=None) -> dict:
    """Poll the running AI operation; returns {done, progress, result} for jobs.py."""
    global _ACTIVE
    state = _active()
    if state is None:
        return common.fail(
            "There is no AI inbetweening or motion-generation job to poll.",
            hint="Start ai_inbetween or generate_motion again; a server restart loses the job state.",
        )
    state["poll_count"] = state.get("poll_count", 0) + 1
    _check_dialogs(state)
    if state.get("dialog_error") and state.get("action_finished"):
        return _fail_active(
            f"Cascadeur refused {state['action']!r} with a dialog: {state['dialog_error']}",
            "The dialog was dismissed. Key every selected layer at both ends of the interval, keep one rig, "
            "and stay within InbetweeningTool/MaxIntervalLength; then retry.",
            dialog=state["dialog_error"],
            dialog_actions=state.get("dialog_actions") or [],
            action=state["action"],
        )
    if not state.get("action_finished"):
        return {"done": False, "progress": 0.05}
    if state.get("action_error"):
        return _fail_active(
            f"Cascadeur's {state['action']!r} raised: {_traceback_last_line(state['action_error'])}",
            "Check get_log for the traceback; the previous selection and playhead were restored.",
            traceback=state["action_error"],
            action=state["action"],
        )
    try:
        _app, _view_scene, scene = _scene()
        viewer = scene.layers_viewer()
        snapshot = snapshot_layers(viewer, state["tracked"])
    except Exception:
        return _fail_active(
            f"Could not read the timeline while the {state['action']!r} job ran: {_last_line()}",
            "Check get_log; the scene may have been closed or a modal dialog is blocking Cascadeur.",
        )
    lines = _log_lines(state.get("log_offset"))
    error = log_error(lines)
    if error:
        return _fail_active(
            f"Cascadeur reported an error while running {state['action']!r}: {error}",
            "See the log in this result; check that the character has one rig and that enough keys are set.",
            log=log_messages(lines)[-20:],
        )
    current_signature = signature(snapshot)
    if state["last_signature"] is None:
        state["last_signature"] = current_signature
        state["last_snapshot"] = snapshot
        state["quiet"] = 0
        return {"done": False, "progress": 0.3}
    if current_signature == state["last_signature"]:
        state["quiet"] += 1
    else:
        state["last_signature"] = current_signature
        state["quiet"] = 0
    state["last_snapshot"] = snapshot
    elapsed = _now() - state["started"]
    summary = interval_summary(snapshot, state["first"], state["last"])
    diff = diff_snapshots(state["baseline"], snapshot, state["first"], state["last"])
    if state["kind"] == "ai":
        ready = bool(summary["ai_layers"])
        progress = 0.9 if ready else 0.2
        if not ready and elapsed > AI_APPLY_TIMEOUT_S:
            return _fail_active(
                "Cascadeur did not mark the selected interval as AI interpolation.",
                "Check that the selected layers belong to one rig, the interval has two keyframes, "
                "and the log below; Cascadeur may have refused the action.",
                log=log_messages(lines)[-20:],
                action=state["action"],
            )
    else:
        ready = bool(diff["keys_added"] or diff["keys_removed"] or diff["changed_layers"])
        progress = 0.9 if ready else 0.2
        if not ready and elapsed > MOTION_CHANGE_TIMEOUT_S:
            return _fail_active(
                "Motion generation finished without changing the selected layers.",
                "Check that the interval has keys and that the character has one rig; see the log below.",
                log=log_messages(lines)[-20:],
                action=state["action"],
            )
    done_seen = log_done(lines)
    stable = state["quiet"] >= (2 if done_seen else QUIET_POLLS)
    if ready and stable and (elapsed >= MIN_SETTLE_S or done_seen):
        result = _result(state, snapshot, lines)
        restore = _restore(scene, state.get("selection") or {})
        _clear_active()
        if not restore.get("restored"):
            result["warning"] = restore.get("warning")
        return {"done": True, "progress": 1.0, "result": result}
    return {"done": False, "progress": progress}


# ---------------------------------------------------------------------------
# Cancel hook (jobs.py runs it on the GUI thread)
# ---------------------------------------------------------------------------


def _cancel(start=None, end=None, controllers=None) -> None:
    """Restore the caller's selection/playhead; Cascadeur cannot abort a running AI model.

    While the action is still on the GUI thread the restore is left to _run_action()'s finally
    block, so the action does not lose the selection it is working with.
    """
    state = _active()
    _clear_active()
    if state is None or not state.get("action_finished"):
        return
    try:
        _app, _view_scene, scene = _scene()
        _restore(scene, state.get("selection") or {})
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Tool table
# ---------------------------------------------------------------------------

START_SCHEMA = {
    "type": ["integer", "string"],
    "description": "First frame of the interval (0 or more).",
}
END_SCHEMA = {
    "type": ["integer", "string"],
    "description": "Last frame of the interval (greater than start).",
}
CONTROLLERS_SCHEMA = {
    "type": ["string", "array"],
    "items": {"type": "string"},
    "description": "Controller names or aliases such as left_hand (omit for the whole character).",
}


@_tool("Check that an animated scene with at least two keys is open.")
def ai_inbetween(start=None, end=None, controllers=None) -> dict:
    """Apply AI interpolation to an interval (begin callable for the background tool)."""
    return _begin("ai", AI_ACTION, start, end, controllers)


@_tool("Check that an animated scene with at least two keys is open.")
def generate_motion(start=None, end=None, controllers=None) -> dict:
    """Run Cascadeur's motion generator for an interval (begin callable for the background tool)."""
    return _begin("motion", MOTION_ACTION, start, end, controllers)


TOOLS = [
    {
        "name": "ai_inbetween",
        "description": "Fill the frames between key poses with Cascadeur's AI inbetweening: selects the given controllers "
        "and timeline interval and runs 'Timeline.AI.AI on selected interval'. Use it after keying the poses around the "
        "interval with set_key; Cascadeur needs at least two keyframes and one key on each side of the interval, its "
        "per-key interval must fit the InbetweeningTool/MaxIntervalLength setting, and the AI features need a Pro license. "
        "This is a background job: it returns a job id immediately, get_job reports progress and the result (the changed "
        "frame range, the layers where AI interpolation landed, and key counts). The previous selection and playhead are "
        "restored when the job finishes. AI interpolation keeps the keyed poses and synthesizes the motion between them; "
        "use generate_motion to create a new motion instead.",
        "inputSchema": schema(
            {"start": START_SCHEMA, "end": END_SCHEMA, "controllers": CONTROLLERS_SCHEMA}, ["start", "end"]
        ),
        "fn": ai_inbetween,
        "poll": _poll,
        "cancel": _cancel,
        "background": True,
        "job_timeout": JOB_TIMEOUT_S,
    },
    {
        "name": "generate_motion",
        "description": "Generate a new motion for the selected interval with Cascadeur's Run Motion Generation "
        "('View.Inbetweening_RunMotionGeneration'). Use it when the keyed poses are only a rough idea and you want "
        "Cascadeur's motion generator to replace the animation between the keys; unlike ai_inbetween, which only changes "
        "the interpolation and keeps the keyed poses, Run Motion Generation rewrites the keyed values and can set the "
        "interval to fixed interpolation, so copy the animation first if the keyed poses must survive. Cascadeur 2026.2.3 "
        "does not create an undo step for the generated motion. Needs keyframes that bracket the interval and a Pro "
        "license. This is a background job: it returns a job id immediately, get_job reports progress and the result (the "
        "changed frame range, key counts and which layers changed). The previous selection and playhead are restored when "
        "the job finishes. The first Run Motion Generation in a Cascadeur session loads its Torch model synchronously "
        "(about 20 s); the tool still returns its job id immediately, but Cascadeur's GUI may not respond during that "
        "first load. Later calls are fast.",
        "inputSchema": schema(
            {"start": START_SCHEMA, "end": END_SCHEMA, "controllers": CONTROLLERS_SCHEMA}, ["start", "end"]
        ),
        "fn": generate_motion,
        "poll": _poll,
        "cancel": _cancel,
        "background": True,
        "job_timeout": JOB_TIMEOUT_S,
    },
]
