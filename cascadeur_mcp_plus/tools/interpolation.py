"""Interpolation and IK/FK tools (T17).

set_interpolation changes the interpolation type of the timeline intervals in a frame range
(bezier, clamped bezier, viscous bezier, linear, step or fixed); set_ik_fk switches the intervals
between IK, FK and global FK (GR); reset_tangents clears custom tangents and reset_easing returns
the easing weights to their neutral value.

set_interpolation and the two reset tools write Cascadeur's timeline data directly
(csc.layers.layer.Section through csc.layers.Editor.change_section) instead of triggering the
"Timeline.* on selected interval" actions: a direct write needs no selection, so the object
selection, the timeline selection and the playhead stay exactly as they were, and the whole call
becomes one "MCP: ..." undo step (common.edit with scene.modify). The interval rule mirrors
Cascadeur's actions: any interval that overlaps the selected frames is changed, except the
interval that starts on the last frame, which continues past the range (selecting 0-20 changes the
interval that starts at 0, not the one at 20).

IK/FK is the exception: writing Section.interval.common.ik_fk makes Cascadeur's dependency graph
abort the undo step with "interpolation root is changed: '<bone>.Position'" (live, 2026-10-24), so
set_ik_fk selects the interval, runs the same "IK type on selected interval" action as the menu,
and restores the object and timeline selections. That action also changes the interval that starts
on the last selected frame (selecting 0-20 changes the sections at 0 and 20), so set_ik_fk reports
both. Cascadeur names that undo step itself.

The tool module is loaded by the registry; the pure argument and section-position helpers are
unit tested in tests/test_interpolation.py, and tests/live/test_interpolation.py checks the
behavior against Cascadeur.
"""

from __future__ import annotations

import functools
import traceback

import csc

from .. import common, rig, timeline
from ..errors import ToolError
from ._schema import schema

# ---------------------------------------------------------------------------
# Arguments (pure Python, unit tested outside Cascadeur)
# ---------------------------------------------------------------------------

# Public kind -> csc.layers.layer.Interpolation member. The order is the schema's enum order.
INTERPOLATION_KINDS = {
    "bezier": "BEZIER",
    "bezier_clamped": "CLAMPED_BEZIER",
    "bezier_viscous": "LOW_AMPLITUDE_BEZIER",
    "linear": "LINEAR",
    "step": "STEP",
    "fixed": "FIXED",
}

# Accepted spellings that are not the canonical tool arguments (the ticket names the first three).
KIND_ALIASES = {
    "clamped_bezier": "bezier_clamped",
    "clamped": "bezier_clamped",
    "viscous": "bezier_viscous",
    "viscous_bezier": "bezier_viscous",
    "low_amplitude_bezier": "bezier_viscous",
}

# Cascadeur has no Python setter that survives its dependency-graph check for the interval's
# IK/FK type: writing csc.layers.layer.Section.interval.common.ik_fk aborts the undo step with
# "interpolation root is changed: '<bone>.Position'" (live, 2026-10-24). The timeline action is
# the supported path, so set_ik_fk selects the interval and calls the same action as the menu.
IK_FK_MODES = {
    "ik": "Timeline.IK FK type.IK type on selected interval",
    "fk": "Timeline.IK FK type.FK type on selected interval",
    "gr": "Timeline.IK FK type.GR type on selected interval",
}

MODE_ALIASES = {
    "global_fk": "gr",
    "globalfk": "gr",
}

# Cascadeur's "Reset easing weights" leaves the tangent handle at one third of the interval
# (0.3333333 on Cascy, live 2026-10-24), the neutral cubic-Bezier handle length.
EASING_WEIGHT = 1.0 / 3.0
EASING_TOLERANCE = 1e-6


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


def _canonical(value, parameter: str, choices, aliases=None, hint: str = "") -> str:
    """Normalize a string argument to one of choices (aliases allowed), or raise."""
    if value is None or isinstance(value, (list, tuple, dict, set, bool)):
        raise _BadArgument(
            f"{parameter} must be one of: {', '.join(choices)} (got {value!r}).",
            hint or f"Pass {parameter} as a string, for example {choices[0]!r}.",
        )
    text = str(value).strip().lower().replace("-", "_").replace(" ", "_")
    if aliases:
        text = aliases.get(text, text)
    if text not in choices:
        raise _BadArgument(
            f"{parameter} must be one of: {', '.join(choices)} (got {value!r}).",
            hint or f"Pass {parameter} as one of: {', '.join(choices)}.",
        )
    return text


def interpolation_kind(kind) -> str:
    """Return the canonical interpolation kind name (bezier, linear, ...)."""
    aliases = {alias: canonical for alias, canonical in KIND_ALIASES.items()}
    return _canonical(
        kind,
        "kind",
        tuple(INTERPOLATION_KINDS),
        aliases,
        "Pass kind as bezier, bezier_clamped, bezier_viscous, linear, step or fixed.",
    )


def ik_fk_mode(mode) -> str:
    """Return the canonical IK/FK mode name (ik, fk or gr)."""
    return _canonical(
        mode,
        "mode",
        tuple(IK_FK_MODES),
        dict(MODE_ALIASES),
        "Pass mode as ik, fk or gr (global FK).",
    )


def frame_range(start, end) -> tuple[int, int]:
    """Parse and validate the inclusive frame range start..end."""
    first = timeline.parse_frame(start, "start")
    last = timeline.parse_frame(end, "end")
    if first > last:
        raise _BadArgument(
            f"start ({first}) must not be greater than end ({last}).",
            "Pass start <= end, for example start=0, end=20.",
        )
    return first, last


def section_positions(sections, start: int, end: int, include_end: bool = False) -> list[int]:
    """Frames of the sections that a timeline action on start..end changes.

    sections is a mapping of key frame -> Section. A section describes the interval that starts
    at its key and runs up to the next key minus one. Cascadeur's "on selected interval" actions
    change every interval overlapping the selection. The interpolation actions leave the interval
    that starts on the selection's last frame alone (its interval continues past the range): with
    keys at 0 and 20, selecting 0..20 changes the section at 0 and not the one at 20. The IK/FK
    action does change that last interval too, so set_ik_fk calls this with include_end=True.

    A single-frame range start == end changes the section at that frame when it has one, so
    set_interpolation(..., 10, 10) can still target the interval that starts there.
    """
    positions = sorted(int(position) for position in sections)
    if start == end:
        return [position for position in positions if position == start]
    limit = end + 1 if include_end else end
    selected = []
    for index, position in enumerate(positions):
        if position >= limit:
            continue
        interval_last = positions[index + 1] - 1 if index + 1 < len(positions) else None
        if interval_last is not None and interval_last < start:
            continue
        selected.append(position)
    return selected


# ---------------------------------------------------------------------------
# Cascadeur data access (GUI thread)
# ---------------------------------------------------------------------------


def _enum(group: str, member: str):
    """A csc.layers.layer enum member, with a clear error when the version lacks it."""
    try:
        return getattr(getattr(csc.layers.layer, group), member)
    except Exception as exc:  # noqa: BLE001 - report the version mismatch
        raise ToolError(f"This Cascadeur version does not expose csc.layers.layer.{group}.{member}: {exc}") from exc


def _enum_name(value) -> str:
    """Lower-case name of an enum value (None stays None)."""
    name = getattr(value, "name", None)
    if isinstance(name, str) and name.strip():
        return name.strip().lower()
    return timeline.interpolation_name(value)


def _objects(controllers) -> list:
    """Resolve controllers; omitted means every controller in the scene (whole character)."""
    if controllers is None or controllers == "" or controllers == []:
        return timeline.target_object_ids()
    return rig.resolve(controllers)


def _layer_records(viewer, layer_ids, start: int, end: int, include_end: bool = False) -> list[dict]:
    """Layer names and the selected section positions of each layer, in the given order."""
    records = []
    for layer_id in layer_ids:
        try:
            layer = viewer.layer(layer_id)
            sections = layer.sections
            name = layer.header.name
        except Exception as exc:  # noqa: BLE001 - report the layer that cannot be read
            raise ToolError(f"Could not read timeline layer {layer_id}: {exc}") from exc
        records.append(
            {
                "id": layer_id,
                "name": name or "",
                "sections": sections,
                "positions": section_positions(sections, start, end, include_end),
            }
        )
    return records


def _same_value(before, wanted, tolerance) -> bool:
    """Compare two section values, with a numeric tolerance for the easing weight floats."""
    if tolerance is None:
        return before == wanted
    try:
        left = tuple(before)
        right = tuple(wanted)
    except TypeError:
        return before == wanted
    if len(left) != len(right):
        return False
    try:
        return all(abs(float(a) - float(b)) <= tolerance for a, b in zip(left, right, strict=True))
    except (TypeError, ValueError):
        return before == wanted


def _plan(records, read, wanted, tolerance=None) -> list[tuple]:
    """(layer_id, position, before) of the sections whose current value is not wanted."""
    plan = []
    for record in records:
        sections = record["sections"]
        for position in record["positions"]:
            try:
                before = read(sections[position])
            except Exception:  # noqa: BLE001 - a section without this property cannot match
                before = None
            if not _same_value(before, wanted, tolerance):
                plan.append((record["id"], position, before))
    return plan


def _apply(undo_name: str, plan, write) -> dict:
    """Run one common.edit that writes every planned section; returns the edit result.

    Changing sections does not update animation data, so this uses scene.modify (update=False),
    the same call Cascadeur's own interpolation and IK/FK API examples use. modify_update runs the
    dependency-graph update, which rejects an interval change when it sees the key's interpolation
    root move ("interpolation root is changed") and aborts the whole undo step.
    """
    if not plan:
        return {"ok": True}

    def mod(model_editor, _update_editor, _scene_updater):
        editor = model_editor.layers_editor()
        for layer_id, position, _before in plan:
            editor.change_section(position, layer_id, write)

    return common.edit(undo_name, mod, update=False)


def _layer_entries(viewer, records, entry) -> list[dict]:
    """Read back every selected section through entry(section) after the edit."""
    layers = []
    for record in records:
        try:
            sections = viewer.layer(record["id"]).sections
        except Exception as exc:  # noqa: BLE001 - keep the other layers readable
            layers.append({"name": record["name"], "sections": [], "error": str(exc)})
            continue
        values = []
        for position in record["positions"]:
            try:
                values.append({"frame": int(position), **entry(sections[position])})
            except Exception as exc:  # noqa: BLE001 - keep the other sections readable
                values.append({"frame": int(position), "error": str(exc)})
        layers.append({"name": record["name"], "sections": values})
    return layers


def _counts(entries, field: str) -> dict:
    """Count the values of one readback field, for a one-line summary."""
    counts = {}
    for layer in entries:
        for section in layer["sections"]:
            value = section.get(field)
            key = "unknown" if value is None else str(value)
            counts[key] = counts.get(key, 0) + 1
    return counts


def _prepare(controllers, start, end, include_end: bool = False):
    """Shared preamble: parse the range, resolve controllers and their layers."""
    first, last = frame_range(start, end)
    object_ids = _objects(controllers)
    if not object_ids:
        return (
            None,
            None,
            None,
            first,
            last,
            common.fail(
                "No controllers matched, so no timeline intervals were changed.",
                hint="Call list_controllers to see the control point names and aliases.",
            ),
        )
    _app, _view_scene, scene = common.scenes()
    viewer = scene.layers_viewer()
    layer_ids = timeline.layer_ids_for_viewer(viewer, object_ids)
    if not layer_ids:
        return (
            None,
            None,
            None,
            first,
            last,
            common.fail(
                "None of the matched objects has a timeline layer, so no timeline intervals were changed.",
                hint="Open an animated scene with RigInfo/AnimationInfo (for example Cascy.casc).",
            ),
        )
    records = _layer_records(viewer, layer_ids, first, last, include_end)
    names = timeline.object_names(object_ids)
    return viewer, records, names, first, last, None


def _matching(viewer, records, plan, read, wanted, tolerance=None) -> int:
    """How many planned sections now hold the wanted value (cross-check after the edit)."""
    planned = {(layer_id, position) for layer_id, position, _before in plan}
    count = 0
    for record in records:
        try:
            sections = viewer.layer(record["id"]).sections
        except Exception:  # noqa: BLE001 - a layer that disappeared cannot match
            continue
        for position in record["positions"]:
            if (record["id"], position) not in planned:
                continue
            try:
                if _same_value(read(sections[position]), wanted, tolerance):
                    count += 1
            except Exception:  # noqa: BLE001 - a section without this property cannot match
                continue
    return count


def _finish(result, viewer, records, names, first, last, entry, value_field, read, wanted, plan, tolerance=None):
    """Build the shared success result and hint after a section edit."""
    if not result.get("ok"):
        return result
    entries = _layer_entries(viewer, records, entry)
    sections = sum(len(record["positions"]) for record in records)
    changed = _matching(viewer, records, plan, read, wanted, tolerance)
    result.update(
        start=first,
        end=last,
        controllers=common.jsonable(names),
        layer_count=len(records),
        layers=common.jsonable(entries),
        sections=sections,
        sections_changed=changed,
        requested_changes=len(plan),
        readback=_counts(entries, value_field),
    )
    if sections == 0:
        result["hint"] = (
            f"No timeline keys overlap frames {first}-{last}. Add keys with set_key first; "
            "interpolation applies to the interval that starts at a key."
        )
    elif not plan:
        result["hint"] = "Every selected section already had the requested value, so nothing changed."
    elif changed < len(plan):
        result["warning"] = (
            f"Only {changed} of {len(plan)} planned sections report the new value; "
            "check get_log and the layer readback."
        )
    return common.jsonable(result)


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@_tool("Check that the scene is animated and that the frames/controllers are valid.")
def set_interpolation(kind=None, start=None, end=None, controllers=None) -> dict:
    """Set the interpolation type of the timeline intervals in a frame range."""
    wanted_kind = interpolation_kind(kind)
    viewer, records, names, first, last, failure = _prepare(controllers, start, end)
    if failure is not None:
        return failure
    wanted = _enum("Interpolation", INTERPOLATION_KINDS[wanted_kind])
    # _plan compares against Cascadeur's enum name (clamped_bezier, low_amplitude_bezier), which
    # differs from the tool's kind names for the two bezier variants.
    required = _enum_name(wanted)

    def read(section):
        return _enum_name(section.interval.interpolation)

    def write(section):
        section.interval.interpolation = wanted

    plan = _plan(records, read, required)
    result = _apply("set_interpolation", plan, write)
    result = _finish(
        result,
        viewer,
        records,
        names,
        first,
        last,
        lambda section: {"interpolation": _enum_name(section.interval.interpolation)},
        "interpolation",
        read,
        required,
        plan,
    )
    if result.get("ok"):
        result["kind"] = wanted_kind
    return result


def _object_selection(scene):
    """Snapshot the object selection and its pivot for an action fallback."""
    selector = scene.selector()
    selection = selector.selected()
    try:
        ordered = list(selection.ordered_ids)
    except Exception:
        ordered = list(selection.ids)
    pivot = ordered[0] if ordered else csc.model.ObjectId.null()
    return ordered, pivot


def _restore_selection(scene, ids, pivot, layers) -> list[str]:
    """Restore the object and timeline selections an action fallback had to replace."""
    errors = []
    try:
        scene.selector().select(set(ids), pivot)
    except Exception as exc:  # noqa: BLE001 - the timeline restore below is still worth trying
        errors.append(f"object selection: {type(exc).__name__}: {exc}")
    try:
        scene.get_layers_selector().set_full_selection_by_parts(layers)
    except Exception as exc:  # noqa: BLE001 - report it with the result
        errors.append(f"timeline selection: {type(exc).__name__}: {exc}")
    return errors


@_tool("Check that the scene is animated and that the frames/controllers are valid.")
def set_ik_fk(mode=None, start=None, end=None, controllers=None) -> dict:
    """Switch the IK/FK type of the timeline intervals in a frame range."""
    wanted_mode = ik_fk_mode(mode)
    # The IK/FK action includes the interval that starts on the last selected frame (live check
    # on Cascy: selecting 0-20 changes the sections at 0 and 20), so include_end=True.
    viewer, records, names, first, last, failure = _prepare(controllers, start, end, include_end=True)
    if failure is not None:
        return failure

    # The action changes the interval through Cascadeur's own command (a direct Section write is
    # rejected by the dependency graph), so select the target layers/frames, call it, and restore
    # the selection exactly as it was. The action itself creates the undo step.
    _app, _view_scene, scene = common.scenes()
    selector = scene.get_layers_selector()
    saved_layers = selector.selection()
    saved_ids, pivot = _object_selection(scene)
    before = {
        (record["id"], position): _enum_name(record["sections"][position].interval.common.ik_fk)
        for record in records
        for position in record["positions"]
    }
    action = IK_FK_MODES[wanted_mode]
    action_error = None
    try:
        timeline.select_interval(first, last, [record["id"] for record in records])
        csc.app.get_application().get_action_manager().call_action(action)
    except Exception as exc:  # noqa: BLE001 - report the action failure as a tool failure
        action_error = exc
    restore_errors = _restore_selection(scene, saved_ids, pivot, saved_layers)
    if action_error is not None:
        return common.fail(
            f"Cascadeur could not run the IK/FK action {action!r}: {type(action_error).__name__}: {action_error}",
            hint="Check that a scene is animated, no dialog is open, and read get_log.",
            action=action,
            mode=wanted_mode,
            selection_restore_errors=restore_errors or None,
        )

    entries = _layer_entries(
        viewer,
        records,
        lambda section: {
            "interval": _enum_name(section.interval.common.ik_fk),
            "key": _enum_name(section.key.common.ik_fk),
        },
    )
    changed = 0
    for record in records:
        try:
            fresh = viewer.layer(record["id"]).sections
        except Exception:  # noqa: BLE001 - a layer that disappeared cannot count as changed
            continue
        for position in record["positions"]:
            try:
                if _enum_name(fresh[position].interval.common.ik_fk) != before[(record["id"], position)]:
                    changed += 1
            except Exception:  # noqa: BLE001 - unreadable section
                continue
    result = common.ok(
        mode=wanted_mode,
        action=action,
        start=first,
        end=last,
        controllers=common.jsonable(names),
        layer_count=len(records),
        layers=common.jsonable(entries),
        sections=sum(len(record["positions"]) for record in records),
        sections_changed=changed,
        readback=_counts(entries, "interval"),
        # Cascadeur names the undo step itself; it cannot be wrapped in common.edit because the
        # dependency-graph update aborts a direct rewrite (see the module docstring).
        undo_source="Cascadeur action",
    )
    if restore_errors:
        result["warning"] = "The IK/FK action ran, but the previous selection could not be restored: " + "; ".join(
            restore_errors
        )
    if not result["readback"]:
        result["hint"] = (
            f"No timeline keys overlap frames {first}-{last}. Add keys with set_key first; "
            "the IK/FK action applies to the interval that starts at a key."
        )
    elif changed == 0:
        result["hint"] = "Every selected interval already had the requested IK/FK type, so nothing changed."
    return common.jsonable(result)


@_tool("Check that the scene is animated and that the frames/controllers are valid.")
def reset_tangents(start=None, end=None, controllers=None) -> dict:
    """Clear the custom tangents of the keys in a frame range (continuity restored)."""
    viewer, records, names, first, last, failure = _prepare(controllers, start, end)
    if failure is not None:
        return failure
    wanted = _enum("Tangents", "Continuous")

    def read(section):
        return _enum_name(section.key.tangents)

    def write(section):
        section.key.tangents = wanted

    plan = _plan(records, read, "continuous")
    result = _apply("reset_tangents", plan, write)
    result = _finish(
        result,
        viewer,
        records,
        names,
        first,
        last,
        lambda section: {"tangents": _enum_name(section.key.tangents)},
        "tangents",
        read,
        "continuous",
        plan,
    )
    if result.get("ok"):
        result["reset"] = "continuous"
    return result


@_tool("Check that the scene is animated and that the frames/controllers are valid.")
def reset_easing(start=None, end=None, controllers=None) -> dict:
    """Reset the easing weights of the keys in a frame range to their neutral value."""
    viewer, records, names, first, last, failure = _prepare(controllers, start, end)
    if failure is not None:
        return failure

    def read(section):
        return (float(section.key.left_weight_velocity), float(section.key.right_weight_velocity))

    def write(section):
        section.key.left_weight_velocity = EASING_WEIGHT
        section.key.right_weight_velocity = EASING_WEIGHT

    plan = _plan(records, read, (EASING_WEIGHT, EASING_WEIGHT), EASING_TOLERANCE)
    result = _apply("reset_easing", plan, write)
    result = _finish(
        result,
        viewer,
        records,
        names,
        first,
        last,
        lambda section: {
            "left_weight_velocity": float(section.key.left_weight_velocity),
            "right_weight_velocity": float(section.key.right_weight_velocity),
        },
        "left_weight_velocity",
        read,
        (EASING_WEIGHT, EASING_WEIGHT),
        plan,
        EASING_TOLERANCE,
    )
    if result.get("ok"):
        result["weight"] = EASING_WEIGHT
    return result


TOOLS = [
    {
        "name": "set_interpolation",
        "description": "Set the interpolation type of the timeline intervals from start to end: bezier, "
        "bezier_clamped, bezier_viscous, linear, step or fixed. Use it after keying poses to change how the "
        "animation moves between them; controllers defaults to the whole character. It writes Cascadeur's "
        "timeline sections directly, so the object selection, timeline selection and playhead do not change. "
        "An interval is changed when it overlaps start..end; the interval that starts on the last frame is "
        "left alone because it continues past the range (selecting 0-20 changes the interval at 0). The whole "
        "call is one undoable step named 'MCP: set_interpolation'. Returns the range, controllers, per-layer "
        "sections and the interpolation read back after the edit.",
        "inputSchema": schema(
            {
                "kind": {
                    "type": "string",
                    "enum": list(INTERPOLATION_KINDS),
                    "description": "bezier, bezier_clamped, bezier_viscous, linear, step or fixed.",
                },
                "start": {"type": ["integer", "string"], "description": "First frame of the range (0 or more)."},
                "end": {"type": ["integer", "string"], "description": "Last frame of the range (start or more)."},
                "controllers": {
                    "type": ["string", "array"],
                    "items": {"type": "string"},
                    "description": "Controller names or aliases such as left_hand (omit for the whole character).",
                },
            },
            ["kind", "start", "end"],
        ),
        "fn": set_interpolation,
    },
    {
        "name": "set_ik_fk",
        "description": "Switch the IK/FK type of the timeline intervals from start to end: ik, fk or gr (global FK). "
        "Use it when a limb should follow inverse kinematics, forward kinematics or global FK; controllers "
        "defaults to the whole character. It temporarily selects the interval and runs Cascadeur's own "
        "'IK type on selected interval' action (a direct Section write is rejected by the dependency graph), "
        "then restores the object and timeline selection, so only the intervals change. Unlike "
        "set_interpolation, the action also changes the interval that starts on the last selected frame. "
        "Cascadeur creates the undo step for the action; its name is Cascadeur's, not 'MCP: set_ik_fk'. "
        "Returns the per-layer interval mode and the key mode read back after the edit.",
        "inputSchema": schema(
            {
                "mode": {
                    "type": "string",
                    "enum": list(IK_FK_MODES),
                    "description": "ik, fk or gr (global FK).",
                },
                "start": {"type": ["integer", "string"], "description": "First frame of the range (0 or more)."},
                "end": {"type": ["integer", "string"], "description": "Last frame of the range (start or more)."},
                "controllers": {
                    "type": ["string", "array"],
                    "items": {"type": "string"},
                    "description": "Controller names or aliases such as left_foot (omit for the whole character).",
                },
            },
            ["mode", "start", "end"],
        ),
        "fn": set_ik_fk,
    },
    {
        "name": "reset_tangents",
        "description": "Clear the custom tangents of the timeline keys from start to end, restoring continuous "
        "tangents (Cascadeur's 'Clear custom tangents on selected interval'). Use it after editing tangents to "
        "remove overshoot or a hand-made curve; controllers defaults to the whole character. It writes the key "
        "data directly, so the object selection, timeline selection and playhead do not change. The whole call "
        "is one undoable step named 'MCP: reset_tangents'. Returns the per-layer tangents read back after the edit.",
        "inputSchema": schema(
            {
                "start": {"type": ["integer", "string"], "description": "First frame of the range (0 or more)."},
                "end": {"type": ["integer", "string"], "description": "Last frame of the range (start or more)."},
                "controllers": {
                    "type": ["string", "array"],
                    "items": {"type": "string"},
                    "description": "Controller names or aliases (omit for the whole character).",
                },
            },
            ["start", "end"],
        ),
        "fn": reset_tangents,
    },
    {
        "name": "reset_easing",
        "description": "Reset the easing weights of the timeline keys from start to end to their neutral value "
        "(one third, Cascadeur's 'Reset easing weights on selected interval'). Use it after easing edits to make the "
        "motion between keys symmetric again; controllers defaults to the whole character. It writes the key "
        "data directly, so the object selection, timeline selection and playhead do not change. The whole call "
        "is one undoable step named 'MCP: reset_easing'. Returns the left and right weight velocities read back "
        "after the edit.",
        "inputSchema": schema(
            {
                "start": {"type": ["integer", "string"], "description": "First frame of the range (0 or more)."},
                "end": {"type": ["integer", "string"], "description": "Last frame of the range (start or more)."},
                "controllers": {
                    "type": ["string", "array"],
                    "items": {"type": "string"},
                    "description": "Controller names or aliases (omit for the whole character).",
                },
            },
            ["start", "end"],
        ),
        "fn": reset_easing,
    },
]
