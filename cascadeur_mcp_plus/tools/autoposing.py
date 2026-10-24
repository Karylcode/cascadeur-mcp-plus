"""AutoPosing tools (T15): switch it on/off, recompute a pose and lock controllers.

The mechanics and the "Adjust AutoPosing lock state?" prompt handling live in
cascadeur_mcp_plus/autoposing.py; this module is the tool surface plus argument validation.
The tools that can trigger the prompt (autoposing_enable, autoposing_update,
lock_controllers) are handler tools: they run the Cascadeur call on the GUI thread, then wait
and answer the prompt on the HTTP thread, so no dialog is left open.
"""

from __future__ import annotations

import traceback

from .. import autoposing, common, dispatch
from ..errors import ToolError
from ._schema import schema

ANSWER_SCHEMA = {
    "type": "string",
    "enum": list(autoposing.ANSWER_CHOICES),
    "description": (
        "Answer for Cascadeur's 'Adjust AutoPosing lock state?' prompt: yes (default; locks the "
        "points whose pose differs from the AutoPosing prediction, the prompt's Autolock), no "
        "(keep the current locks) or cancel (close the prompt without changing locks)."
    ),
}
ENABLED_SCHEMA = {
    "type": "boolean",
    "description": "true turns AutoPosing on, false turns it off.",
}
LOCKED_SCHEMA = {
    "type": "boolean",
    "description": (
        "true locks the controllers; false is rejected because Cascadeur 2026.2.3 has no unlock action for "
        "model control points."
    ),
}
NAMES_SCHEMA = {
    "type": "array",
    "items": {"type": "string"},
    "description": 'Controller names or aliases, for example ["left_foot", "right_foot"].',
}

STUCK_DIALOG_HINT = (
    "The 'Adjust AutoPosing lock state?' prompt did not close. Inspect it with dialogs and press "
    "a button with ui_click (answer='yes' expects a Yes/Autolock button), then retry."
)


def _boolean(value, name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in ("true", "1", "yes", "on"):
            return True
        if text in ("false", "0", "no", "off"):
            return False
    raise ValueError(f"{name} must be true or false (got {value!r}).")


def _answer(value) -> str:
    try:
        return autoposing.answer_argument(value)
    except ToolError as exc:
        raise ValueError(str(exc)) from None


def _unexpected(action: str, exc: Exception) -> dict:
    return common.fail(
        f"Could not {action}: {type(exc).__name__}: {exc}",
        hint=(
            "Check that a rigged scene is open and that no other dialog is blocking Cascadeur; "
            "get_log has the traceback."
        ),
        traceback=traceback.format_exc(),
    )


def _state() -> dict:
    return dispatch.call_on_gui(autoposing.state, {}, 30.0)


def _clear_prompt(answer: str):
    """Answer a prompt that is already open before the operation, so it cannot swallow it."""
    try:
        outcome = autoposing.finish_dialogs(answer, timeout=0.0)
    except Exception:  # noqa: BLE001 - the operation reports its own prompt outcome
        return None
    return outcome if outcome.get("found") else None


def get_autoposing_state() -> dict:
    """Report whether AutoPosing is on and what is known about the locks (GUI thread)."""
    try:
        data = autoposing.state()
    except Exception as exc:  # noqa: BLE001 - tool boundary
        return _unexpected("read the AutoPosing state", exc)
    if not data.get("known"):
        return common.fail(
            "Could not read whether AutoPosing is on from Cascadeur's interface.",
            hint=(
                "Check that a rigged scene (for example Cascy.casc) is open and that the AutoPosing "
                "toolbar is visible; ui_tree(query='AutoPosing') lists the button."
            ),
            **common.jsonable(data),
        )
    return common.ok(**common.jsonable(data))


def autoposing_enable(enabled=True, answer="yes") -> dict:
    """Turn AutoPosing on or off, idempotently, and answer the lock-state prompt."""
    try:
        wanted = _boolean(enabled, "enabled")
        wanted_answer = _answer(answer)
    except ValueError as exc:
        return common.fail(str(exc), hint="Pass enabled=true or false and answer=yes, no or cancel.")
    try:
        before = _state()
    except Exception as exc:  # noqa: BLE001 - tool boundary
        return _unexpected("read the AutoPosing state", exc)
    if not before.get("known"):
        return common.fail(
            "Could not read whether AutoPosing is on from Cascadeur's interface, so it was not changed.",
            hint="Check that a rigged scene is open and that the AutoPosing toolbar is visible; ui_tree(query='AutoPosing') lists the button.",
            before=before.get("enabled"),
            sources=common.jsonable(before.get("sources")),
        )
    if before["enabled"] == wanted:
        return common.ok(
            requested=wanted,
            enabled=before["enabled"],
            before=before["enabled"],
            changed=False,
            action_called=None,
            dialog=None,
        )
    attempts = []
    dialog = _clear_prompt(wanted_answer)
    try:
        attempts.append(dispatch.call_on_gui(autoposing.set_enabled, {"enabled": wanted}, 30.0))
        dialog = autoposing.finish_dialogs(wanted_answer, timeout=2.0)
        after = _state()
        if after.get("enabled") != wanted and after.get("enabled") == before["enabled"]:
            # The prompt can swallow the first switch. Retry only while the interface still
            # reads its old value, so a switch that is merely late cannot be toggled twice.
            attempts.append(dispatch.call_on_gui(autoposing.set_enabled, {"enabled": wanted}, 30.0))
            extra = autoposing.finish_dialogs(wanted_answer, timeout=1.5)
            if extra.get("found") or extra.get("error") or extra.get("closed") is False:
                dialog = extra
            after = _state()
    except Exception as exc:  # noqa: BLE001 - tool boundary
        return _unexpected("switch AutoPosing", exc)
    enabled_now = after.get("enabled")
    outcome = {
        "requested": wanted,
        "enabled": enabled_now,
        "before": before["enabled"],
        "changed": enabled_now != before["enabled"],
        "action": autoposing.TOGGLE_ACTION,
        "attempts": len(attempts),
        "dialog": dialog,
    }
    if dialog and (dialog.get("closed") is False or dialog.get("error")):
        return common.fail(
            "The AutoPosing switch reached the requested state, but the 'Adjust AutoPosing lock state?' prompt is still open.",
            hint=STUCK_DIALOG_HINT,
            **common.jsonable(outcome),
        )
    if enabled_now == wanted:
        return common.ok(**common.jsonable(outcome))
    return common.fail(
        f"Cascadeur's AutoPosing switch still reads {enabled_now!r} instead of {wanted!r}.",
        hint=(
            "Cascadeur's AutoPosing switch did not reach the requested state. Check that the scene "
            "has an AutoPosing rig (Cascy.casc does), answer any prompt with dialogs/ui_click and retry."
        ),
        **common.jsonable(outcome),
    )


def autoposing_update(answer="yes") -> dict:
    """Recompute the current frame's pose with AutoPosing (one undo step)."""
    try:
        wanted_answer = _answer(answer)
    except ValueError as exc:
        return common.fail(str(exc), hint="Pass answer=yes, no or cancel.")
    _clear_prompt(wanted_answer)
    try:
        blocked = dispatch.call_on_gui(autoposing.layer_block, {}, 30.0)
    except Exception as exc:  # noqa: BLE001 - tool boundary
        return _unexpected("check the timeline layer locks", exc)
    if blocked is not None:
        return blocked
    try:
        before = _state()
        info = dispatch.call_on_gui(autoposing.run_update, {}, 60.0)
    except Exception as exc:  # noqa: BLE001 - tool boundary
        return _unexpected("update the AutoPosing pose", exc)
    dialog = autoposing.finish_dialogs(wanted_answer, timeout=2.0)
    try:
        after = _state()
    except Exception:  # noqa: BLE001 - the update already happened; report what we have
        after = {}
    data = {
        "frame": info.get("frame"),
        "undo": info.get("undo"),
        "enabled": after.get("enabled"),
        "enabled_before": before.get("enabled"),
        "dialog": dialog if dialog.get("found") or dialog.get("error") else None,
    }
    if dialog.get("closed") is False or dialog.get("error"):
        return common.fail(
            "The AutoPosing pose was recomputed, but the 'Adjust AutoPosing lock state?' prompt is still open.",
            hint=STUCK_DIALOG_HINT,
            **common.jsonable(data),
        )
    result = common.ok(**common.jsonable(data))
    hints = []
    if after.get("enabled") is False:
        hints.append(
            "AutoPosing is off, so the update may not have changed the pose; call autoposing_enable(true) and retry."
        )
    if dialog.get("found"):
        hints.append(f"Answered the AutoPosing lock-state prompt with {dialog.get('button')!r}.")
    if hints:
        result["hint"] = " ".join(hints)
    return result


def lock_controllers(names, locked=True, answer="yes") -> dict:
    """Lock or unlock AutoPosing controllers by name/alias (acts through the selection)."""
    try:
        wanted_locked = _boolean(locked, "locked")
        wanted_answer = _answer(answer)
    except ValueError as exc:
        return common.fail(str(exc), hint="Pass locked=true or false and answer=yes, no or cancel.")
    _clear_prompt(wanted_answer)
    try:
        info = dispatch.call_on_gui(autoposing.set_lock, {"names": names, "locked": wanted_locked}, 60.0)
    except Exception as exc:  # noqa: BLE001 - tool boundary
        return _unexpected("lock the AutoPosing controllers", exc)
    if not isinstance(info, dict) or info.get("ok") is False:
        return info if isinstance(info, dict) else common.fail("Cascadeur did not lock the controllers.")
    dialog = autoposing.finish_dialogs(wanted_answer, timeout=1.5)
    data = {
        "locked": wanted_locked,
        "action": info.get("action"),
        "count": len(info.get("controllers") or []),
        "controllers": info.get("controllers"),
        "selection_restored": True,
        "scope": info.get("scope"),
        "undo": info.get("undo"),
        "undoable": info.get("undoable"),
        "readback": None,
        "note": autoposing.LOCKED_NOTE,
        "dialog": dialog if dialog.get("found") or dialog.get("error") else None,
    }
    if dialog.get("closed") is False or dialog.get("error"):
        return common.fail(
            "The controllers were locked, but the 'Adjust AutoPosing lock state?' prompt is still open.",
            hint=STUCK_DIALOG_HINT,
            **common.jsonable(data),
        )
    result = common.ok(**common.jsonable(data))
    if dialog.get("found"):
        result["hint"] = f"Answered the AutoPosing lock-state prompt with {dialog.get('button')!r}."
    return result


TOOLS = [
    {
        "name": "get_autoposing_state",
        "description": (
            "Report whether AutoPosing is on in the current scene (read from the AutoPosing toolbar "
            "button's active state in Cascadeur's interface) and what is known about the locked "
            "controllers. Use it before set_pose or autoposing_update. Cascadeur 2026.2.3 has no "
            "Python query for the locked set, so locked_controllers is null and locked_known is "
            "false; check a lock by its effect on positions. Read-only."
        ),
        "inputSchema": schema(),
        "requires": [
            "csc.Guid",
            "csc.app.get_application",
            "csc.domain.Scene.get_current_frame",
            "csc.math.OrthogonalTransform",
            "csc.math.Quaternion",
            "csc.math.Rotation",
            "csc.math.ScaledTransform",
            "csc.model.ObjectId",
            "action:AutoPosingTool.AutoPosing",
        ],
        "fn": get_autoposing_state,
    },
    {
        "name": "autoposing_enable",
        "description": (
            "Turn AutoPosing on or off. It reads the interface first and only triggers the "
            "AutoPosingTool.AutoPosing action when the switch differs, so calling it twice has the "
            "same result (the second call reports changed=false). It also answers Cascadeur's "
            "'Pose in the current frame is different from the AutoPosing prediction... Adjust lock "
            "state?' prompt: answer yes (default; Autolock), no or cancel, and the prompt is closed "
            "before the tool returns. Returns the requested and actual state, whether it changed, "
            "the action used and what happened to the prompt."
        ),
        "inputSchema": schema({"enabled": ENABLED_SCHEMA, "answer": ANSWER_SCHEMA}),
        "requires": [
            "csc.Guid",
            "csc.app.ActionManager.call_action",
            "csc.app.Application.get_action_manager",
            "csc.app.get_application",
            "csc.domain.Scene.get_current_frame",
            "csc.math.OrthogonalTransform",
            "csc.math.Quaternion",
            "csc.math.Rotation",
            "csc.math.ScaledTransform",
            "csc.model.ObjectId",
            "action:AutoPosingTool.AutoPosing",
        ],
        "handler": autoposing_enable,
    },
    {
        "name": "autoposing_update",
        "description": (
            "Recompute the pose of the current frame from the current AutoPosing locks (the "
            "'Update AutoPosing' operation), as one undoable step named 'MCP: autoposing_update'. "
            "Only AutoPosing-locked points keep the positions a script wrote; unlocked points are "
            "restored to AutoPosing's prediction, so lock a scripted frame with lock_controllers "
            "or the autoposing.lock_pose helper before updating it. The update rewrites the whole "
            "pose of the frame, so it is refused while any of the character's control points is on "
            "a locked timeline layer (unlock it with set_layer_locked). It answers the 'Adjust "
            "AutoPosing lock state?' prompt (answer yes by default) so no dialog is left open. "
            "Returns the frame, the undo step and whether AutoPosing is on; when it is off the pose "
            "may not change, so call autoposing_enable(true) first."
        ),
        "inputSchema": schema({"answer": ANSWER_SCHEMA}),
        "requires": [
            "csc.Guid",
            "csc.app.Application.get_tools_manager",
            "csc.app.CascadeurTool.editor",
            "csc.app.SceneManager.scenes",
            "csc.app.ToolsManager.get_tool",
            "csc.app.get_application",
            "csc.domain.Scene.get_current_frame",
            "csc.domain.Scene.layers_viewer",
            "csc.domain.Scene.model_viewer",
            "csc.domain.Scene.set_current_frame",
            "csc.math.OrthogonalTransform",
            "csc.math.Quaternion",
            "csc.math.Rotation",
            "csc.math.ScaledTransform",
            "csc.model.BehaviourViewer.get_behaviours",
            "csc.model.ModelViewer.get_objects",
            "csc.model.ObjectId",
            "action:AutoPosingTool.AutoPosing",
        ],
        "handler": autoposing_update,
    },
    {
        "name": "lock_controllers",
        "description": (
            "Lock AutoPosing controllers by name or alias (for example left_foot) for the current "
            "frame, so AutoPosing keeps their position when other points move. It selects the "
            "controllers and runs Cascadeur's AutoPosing.Autolock command (the AutoPosingTool."
            "AutoUnlock action, tooltip 'Adjust AutoPosing lock state'), then restores the "
            "selection; Cascadeur chooses the full lock set of the frame, so more points than the "
            "named ones may be locked. locked=false fails because Cascadeur 2026.2.3 has no unlock "
            "action for model control points (AutoPosingTool.SwitchLock added no lock). Returns the "
            "controllers, the action and the 'AutoPosing.Autolock' undo step. The lock state cannot "
            "be read back, so readback is null."
        ),
        "inputSchema": schema(
            {"names": NAMES_SCHEMA, "locked": LOCKED_SCHEMA, "answer": ANSWER_SCHEMA},
            ["names"],
        ),
        "requires": [
            "csc.Guid",
            "csc.app.ActionManager.call_action",
            "csc.app.Application.get_action_manager",
            "csc.app.get_application",
            "csc.domain.Scene.get_current_frame",
            "csc.domain.Scene.model_viewer",
            "csc.domain.Scene.set_current_frame",
            "csc.math.OrthogonalTransform",
            "csc.math.Quaternion",
            "csc.math.Rotation",
            "csc.math.ScaledTransform",
            "csc.model.BehaviourViewer.get_behaviours",
            "csc.model.ModelViewer.get_objects",
            "csc.model.ObjectId",
            "csc.model.ObjectId.null",
            "action:AutoPosing.Autolock",
            "action:AutoPosingTool.AutoUnlock",
            "action:AutoPosingTool.SwitchLock",
            "action:AutoPosingTool.SwitchLockOnInterval",
        ],
        "handler": lock_controllers,
    },
]
