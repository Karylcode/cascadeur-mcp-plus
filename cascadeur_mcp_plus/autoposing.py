"""AutoPosing helpers shared by the AutoPosing tools and the pose tools (T15).

AutoPosing lets an animator move a few main control points and have Cascadeur pose the rest of
the body. This module holds the mechanics:

* state() reads whether the tool is on from the QML toolbar button's ``active`` property, which
  is the interface's own state; Cascadeur 2026.2.3 has no Python getter for it.
* set_enabled() switches the tool with the ``AutoPosingTool.AutoPosing`` action and reports the
  state before and after.
* run_update() recomputes the current frame's pose with ``AutoPosingTool.update(session)``
  inside a ``scene.modify_with_session`` step (one undo step).
* set_lock() locks or unlocks controllers with ``AutoPosingTool.SwitchLock`` /
  ``AutoPosingTool.AutoUnlock``, which act on the selection.
* lock_dialog(), answer_lock_dialog() and finish_dialogs() handle the modal prompt that
  Cascadeur shows when a frame posed by a script is first touched by AutoPosing
  ("Pose in the current frame is different from the AutoPosing prediction ... Adjust lock
  state?"). The prompt swallows later UI input, so every AutoPosing operation answers it.
* autolock() runs Cascadeur's AutoPosing.Autolock command (QML action
  AutoPosingTool.AutoUnlock, tooltip "Adjust AutoPosing lock state"), which locks the frame's
  AutoPosing points. It must run *before* a script writes a pose into the frame: afterwards
  AutoPosingTool.update keeps the locked points and lets the free chain follow.
* lock_pose(frame, moved_ids) is that call for T16: run it before writing a frame's pose.
* solve(scene, frame, moved_ids) is what T16's set_pose calls after writing the points: it
  makes sure AutoPosing is on, recomputes the frame and answers the prompt. Without the
  lock_pose() call first, update restores the prediction and solve reports the points in
  "restored".

Cascadeur 2026.2.3 has no Python query for *which* controllers are AutoPosing-locked (T12):
the lock lives in the tool's private data. state() therefore reports the switch and
``locked_controllers: null``; lock_controllers is verified through its effect on positions.

The pure helpers at the top (answer_argument, normalize_label, is_lock_dialog,
find_lock_dialog, choose_button, merge_enabled) do not import Cascadeur and are unit tested
outside it (tests/test_autoposing.py). Everything else that touches Qt or the scene must run on
Cascadeur's GUI thread, except finish_dialogs, which sleeps on the HTTP thread and calls back
through dispatch (use it from a tool handler).
"""

from __future__ import annotations

import re
import time

from .errors import ToolError

TOOL_NAME = "AutoPosingTool"
TOGGLE_ACTION = "AutoPosingTool.AutoPosing"
UPDATE_ACTION = "AutoPosingTool.Update"
SWITCH_LOCK_ACTION = "AutoPosingTool.SwitchLock"
# Cascadeur calls this action "Adjust AutoPosing lock state" (tooltip) although its id says
# AutoUnlock: it runs the scene command "AutoPosing.Autolock", which locks the AutoPosing
# points of the current frame (verified by saving the scene and reading tool/auto_posing.json).
AUTOLOCK_ACTION = "AutoPosingTool.AutoUnlock"
# Kept for callers that used the old name in the T15 branch.
AUTO_UNLOCK_ACTION = AUTOLOCK_ACTION

ANSWER_CHOICES = ("yes", "no", "cancel")

# Button labels Cascadeur has used for these prompts, best first. "Autolock" is what the
# interface calls the effect of Yes on the "Adjust AutoPosing lock state?" prompt.
ANSWER_LABELS = {
    "yes": ("yes", "autolock", "auto lock", "lock", "ok", "apply"),
    "no": ("no", "keep", "keep locks", "unchanged", "don't change", "dont change", "leave", "skip"),
    "cancel": ("cancel", "close", "dismiss"),
}

# The prompt's title and text; both contain "AutoPosing" plus one of these phrases.
DIALOG_PHRASES = ("lock state", "prediction", "different from", "adjust")

LOCKED_NOTE = (
    "Cascadeur 2026.2.3 has no Python query for which controllers are AutoPosing-locked; the "
    "lock lives in the AutoPosing tool's private data (tool/auto_posing.json lockedFrames, T12). "
    "Only the AutoPosing.Autolock command changes it (lock_controllers/lock_pose); there is no "
    "unlock action for model points. locked_controllers is null because the list cannot be read "
    "back at run time."
)

_QML_SUFFIX = re.compile(r"(_QMLTYPE_\d+|_QML_\d+)$")


# ---------------------------------------------------------------------------
# Pure helpers (no Cascadeur import; unit tested outside it)
# ---------------------------------------------------------------------------


def normalize_label(text) -> str:
    """Lower-case a button/dialog label, dropping ellipses, mnemonics and extra spaces."""
    cleaned = str(text or "")
    cleaned = cleaned.replace("\u2026", " ").replace("...", " ")
    cleaned = cleaned.replace("&", "").replace("\n", " ")
    cleaned = " ".join(cleaned.lower().split())
    return cleaned.strip(" .:!?")


def answer_argument(answer=None) -> str:
    """Validate the answer argument: yes (default), no or cancel."""
    text = "yes" if answer is None or str(answer).strip() == "" else str(answer).strip().lower()
    if text not in ANSWER_CHOICES:
        raise ToolError(
            f"answer must be one of: {', '.join(ANSWER_CHOICES)} (got {answer!r}). "
            "yes (the default) locks the points whose pose differs from the AutoPosing prediction, "
            "no keeps the current locks, cancel closes the prompt without changing them."
        )
    return text


def dialog_text(dialog) -> str:
    """All text of a ui.dialogs() entry, joined lower-case (title, texts and button labels)."""
    parts = []
    if isinstance(dialog, dict):
        parts.extend(str(item) for item in dialog.get("texts") or () if item)
        for key in ("title", "text", "message"):
            value = dialog.get(key)
            if isinstance(value, str) and value.strip():
                parts.append(value)
        parts.extend(str(button.get("text")) for button in dialog.get("buttons") or () if button.get("text"))
    return " ".join(parts).lower()


# Popup types that are not the prompt: tooltips, menus and the like can carry the same text
# (the Autolock button's tooltip is literally "Adjust AutoPosing lock state").
_NOT_DIALOG_TYPES = ("tooltip", "menu", "combobox", "contextmenu")


def is_dialog_type(type_name) -> bool:
    """Whether a QML type name is a dialog rather than a tooltip/menu overlay."""
    text = str(type_name or "").strip().lower()
    if not text or any(word in text for word in _NOT_DIALOG_TYPES):
        return False
    return any(word in text for word in ("dialog", "messagebox", "popup"))


def is_answer_button(button) -> bool:
    """Whether a button (a {"text": ...} dict or a label) means one of the prompt's answers."""
    if isinstance(button, dict):
        label = normalize_label(button.get("text"))
    else:
        label = normalize_label(button)
    if not label:
        return False
    return any(label == candidate or candidate in label for labels in ANSWER_LABELS.values() for candidate in labels)


def has_answer_button(buttons) -> bool:
    """Whether any button in a ui.dialogs() entry looks like Yes/No/Cancel/Autolock/OK."""
    return any(is_answer_button(button) for button in buttons or ())


def is_lock_dialog(dialog) -> bool:
    """Whether a ui.dialogs() entry is the "Adjust AutoPosing lock state?" prompt.

    The prompt's title/text is also the AutoPosing toolbar button's tooltip ("Adjust AutoPosing
    lock state"), and Qt shows tooltips as popups, so matching text alone would treat a hovered
    tooltip as an open modal. A real entry must be a dialog (not a window, tooltip or menu) and
    carry at least one answer button.
    """
    if not isinstance(dialog, dict):
        return False
    if str(dialog.get("kind") or "dialog").strip().lower() != "dialog":
        return False
    if not is_dialog_type(dialog.get("type")):
        return False
    if not has_answer_button(dialog.get("buttons")):
        return False
    text = dialog_text(dialog)
    if "autoposing" not in text and "auto posing" not in text:
        return False
    return any(phrase in text for phrase in DIALOG_PHRASES)


def find_lock_dialog(dialogs):
    """The first AutoPosing lock-state prompt in a list of ui.dialogs() entries, or None."""
    for dialog in dialogs or ():
        if is_lock_dialog(dialog):
            return dialog
    return None


def choose_button(buttons, answer="yes"):
    """Pick the button that means ``answer`` from [{"text": ...}, ...], or None.

    Exact labels beat labels that only contain the word (so "Yes" wins over "Yes, always"),
    and the order in ANSWER_LABELS decides ties (yes before autolock).
    """
    wanted = answer_argument(answer)
    best = None
    for index, button in enumerate(buttons or ()):
        label = normalize_label(button.get("text"))
        if not label:
            continue
        number = None
        for rank, candidate in enumerate(ANSWER_LABELS[wanted]):
            if label == candidate:
                number = rank
            elif label.replace(" ", "") == candidate.replace(" ", ""):
                number = 10 + rank
            elif candidate in label:
                number = 100 + rank
            else:
                continue
            break
        if number is None:
            continue
        key = (number, index)
        if best is None or key < best[0]:
            best = (key, button)
    return None if best is None else best[1]


def merge_enabled(sources) -> bool | None:
    """Decide the AutoPosing switch from QML property readings.

    Each source is {"property": "active"|"checked", "value": bool, "visible": bool}. ``active``
    readings win over ``checked`` (the interface's AutoPosing button uses active); visible
    widgets win over hidden copies of the same action. True when any winning reading is true,
    False when they are all false, None when nothing could be read.
    """
    readings = [item for item in sources or () if isinstance(item.get("value"), bool)]
    if not readings:
        return None
    active = [item for item in readings if item.get("property") == "active"]
    pool = active or readings
    visible = [item for item in pool if item.get("visible")]
    return any(item["value"] for item in visible or pool)


def describe_dialog(found) -> dict:
    """A JSON-safe summary of a dialog found by lock_dialog() (drops QML objects)."""
    if not found:
        return {}
    return {
        "texts": [str(item) for item in found.get("texts") or ()],
        "buttons": [str(button.get("text")) for button in found.get("buttons") or ()],
        "source": found.get("source"),
    }


# ---------------------------------------------------------------------------
# QML helpers (GUI thread)
# ---------------------------------------------------------------------------


def _ui():
    from . import ui

    return ui


def _csc():
    import csc

    return csc


def _app():
    return _csc().app.get_application()


def _scene():
    from . import common

    _app_obj, _view_scene, scene = common.scenes()
    return scene


def _type_name(item) -> str:
    try:
        return _QML_SUFFIX.sub("", item.metaObject().className())
    except Exception:
        return ""


def _iter_items(root):
    """Every descendant item of root, including hidden ones (dialog buttons may not be laid out)."""
    stack = [root]
    while stack:
        item = stack.pop()
        yield item
        try:
            children = item.childItems()
        except Exception:
            children = []
        stack.extend(reversed(children))


def _window_items(window):
    try:
        root = window.contentItem()
    except Exception:
        return iter(())
    start = root.parentItem() or root
    return _iter_items(start)


def _string_property(item, name) -> str:
    try:
        value = item.property(name)
    except Exception:
        return ""
    return " ".join(value.split()) if isinstance(value, str) and value.strip() else ""


def _bool_property(item, names=("active", "checked")):
    for name in names:
        try:
            value = item.property(name)
        except Exception:
            continue
        if isinstance(value, bool):
            return name, value
    return None, None


def action_items(action_id):
    """(window, item) pairs whose QML actionId is action_id, including hidden toolbar copies."""
    found = []
    for window in _ui().quick_windows(include_hidden=True):
        for item in _window_items(window):
            if _string_property(item, "actionId") == action_id:
                found.append((window, item))
    return found


def _fallback_sources():
    """Property readings of AutoPosing-looking widgets when the actionId is not in the tree."""
    wanted = {"autoposing", "auto posing", "autoposing tool", "enable autoposing tool"}
    sources = []
    for window in _ui().quick_windows(include_hidden=True):
        for item in _window_items(window):
            prop, value = _bool_property(item)
            if prop is None:
                continue
            labels = {normalize_label(_string_property(item, name)) for name in ("toolTip", "text", "objectName")}
            if labels & wanted:
                sources.append(
                    {
                        "source": "label",
                        "action": TOGGLE_ACTION,
                        "property": prop,
                        "value": value,
                        "visible": bool(item.isVisible()),
                        "window": window.title(),
                    }
                )
    return sources


def state() -> dict:
    """Whether AutoPosing is on (from the interface), plus what is known about the locks."""
    scene = _scene()
    frame = int(scene.get_current_frame(False))
    sources = []
    for window, item in action_items(TOGGLE_ACTION):
        prop, value = _bool_property(item)
        if prop is None:
            continue
        sources.append(
            {
                "source": "actionId",
                "action": TOGGLE_ACTION,
                "property": prop,
                "value": value,
                "visible": bool(item.isVisible()),
                "window": window.title(),
            }
        )
    if not sources:
        sources = _fallback_sources()
    enabled = merge_enabled(sources)
    return {
        "enabled": enabled,
        "known": enabled is not None,
        "action": TOGGLE_ACTION,
        "frame": frame,
        "sources": sources[:8],
        "locked_controllers": None,
        "locked_known": False,
        "note": LOCKED_NOTE,
    }


def call_action(key: str) -> dict:
    """Trigger a Cascadeur action by key (GUI thread)."""
    _app().get_action_manager().call_action(key)
    return {"called": key}


def set_enabled(enabled) -> dict:
    """Switch AutoPosing to enabled; one action call, then read the interface again."""
    wanted = bool(enabled)
    before = state()
    if before["enabled"] is None:
        raise ToolError(
            "Could not read the AutoPosing switch from Cascadeur's interface, so it was not changed. "
            "Check that a rigged scene is open and that the AutoPosing toolbar is visible."
        )
    if before["enabled"] == wanted:
        return {
            "before": before["enabled"],
            "after": before["enabled"],
            "changed": False,
            "action_called": None,
        }
    call_action(TOGGLE_ACTION)
    after = state()
    return {
        "before": before["enabled"],
        "after": after["enabled"],
        "changed": after["enabled"] != before["enabled"],
        "action_called": TOGGLE_ACTION,
    }


def tool_editor():
    """The AutoPosingTool editor for the active scene."""
    app = _app()
    return app.get_tools_manager().get_tool(TOOL_NAME).editor(app.current_scene())


def _set_session_frame(session, frame) -> None:
    try:
        session.set_current_frame(int(frame))
    except Exception:
        pass


def autolock(object_ids=None, frame=None) -> dict:
    """Run Cascadeur's AutoPosing.Autolock command for a frame (GUI thread).

    Cascadeur locks the AutoPosing points of that frame (verified 2026-10-24 by saving the scene
    and reading tool/auto_posing.json: 16 points gained lockedFrames, the forearm/arm stayed
    free). Run it *before* writing a scripted pose: when it runs after the write it also locks
    the moved chain (forearm/arm), so the following update has nothing left to solve and the
    body does not follow.

    object_ids selects the points to pass through the tool selection; Cascadeur still decides
    the full lock set. The playhead is moved to frame for the command and restored afterwards.
    AutoPosingTool.SwitchLock (tooltip "Lock selected controllers") and SwitchLockOnInterval do
    nothing for model control points, so this is the only lock operation the 2026.2.3 API
    offers.
    """
    scene = _scene()
    previous_frame = int(scene.get_current_frame(False))
    move_playhead = frame is not None and int(frame) != previous_frame
    if move_playhead:
        scene.set_current_frame(int(frame))
    selector = scene.selector()
    previous = _selected_ids(selector)
    ids = list(object_ids or ())
    try:
        if ids:
            selector.select(set(ids), ids[0])
        call_action(AUTOLOCK_ACTION)
    finally:
        if ids:
            pivot = previous[0] if previous else _csc().model.ObjectId.null()
            selector.select(set(previous), pivot)
        if move_playhead:
            scene.set_current_frame(previous_frame)
    return {"action": AUTOLOCK_ACTION, "undo": "AutoPosing.Autolock"}


def lock_pose(frame, moved_ids=None) -> dict:
    """Lock frame's AutoPosing points before a script writes a pose into it (T16 helper).

    Call this before writing the values of a frame (set_pose), then write them and call
    solve(); the update keeps the written (locked) points and lets AutoPosing pose the rest of
    the body. Calling it after the write locks the moved chain and stops the body from
    following.
    """
    return autolock(moved_ids, frame=frame)


def _character_object_ids() -> list:
    """Object ids of every control point of the scene's characters (what an update rewrites)."""
    from . import rig

    return [entry["id"] for entry in rig.controllers()]


def _ensure_unlocked(object_ids):
    """Run T23's timeline layer guard; kept separate so the unit tests can replace it."""
    from .tools.layers import ensure_unlocked

    return ensure_unlocked(object_ids)


def layer_block():
    """Return the common.fail(...) result when a character point is on a locked layer, else None.

    AutoPosingTool.update rewrites the whole pose of the frame (every AutoPosing point), so a
    locked layer anywhere on the character must block it, not only the controllers a caller
    happens to write. Run this before any path that calls the AutoPosing update. It fails
    closed when the control points or the layer lock state cannot be read.
    """
    from . import common

    try:
        object_ids = _character_object_ids()
    except Exception as exc:  # noqa: BLE001 - fail closed: an unreadable scope may be locked
        return common.fail(
            f"Could not list the character's control points: {type(exc).__name__}: {exc}",
            hint="Open a rigged scene and check get_log; nothing was updated.",
        )
    if not object_ids:
        return None
    try:
        return _ensure_unlocked(object_ids)
    except Exception as exc:  # noqa: BLE001 - fail closed like ensure_unlocked
        return common.fail(
            f"Could not confirm whether the character's layers are unlocked: {type(exc).__name__}: {exc}",
            hint="Check get_log; the AutoPosing update was not run.",
        )


def run_update(scene=None, frame=None, *, session=None, name="autoposing_update") -> dict:
    """Recompute the pose of frame with Cascadeur's AutoPosing tool (one undo step).

    Call it with session=... inside the callback of scene.modify_with_session to fold the
    update into the caller's undo step (T16 does this); otherwise it opens its own
    "MCP: <name>" step. Returns {"frame", "undo", "method"}.

    The update rewrites every AutoPosing point of the frame, so it raises ToolError when any
    character control point is on a locked timeline layer (call layer_block() first for the
    structured failure). T16 runs this inside its set_pose modify callback, where the exception
    aborts the whole modification instead of writing through the lock.
    """
    block = layer_block()
    if block is not None:
        message = block.get("error") or "The character is on a locked timeline layer."
        hint = block.get("hint")
        raise ToolError(f"{message} {hint}".strip() if hint else message)
    from . import common

    _app_obj, _view_scene, active = common.scenes()
    scene = scene or active
    frame = int(scene.get_current_frame(False)) if frame is None else int(frame)
    editor = tool_editor()
    if session is not None:
        _set_session_frame(session, frame)
        editor.update(session)
        return {"frame": frame, "undo": None, "method": "editor"}

    def mod(model_editor, update_editor, scene_updater, mod_session):
        _set_session_frame(mod_session, frame)
        editor.update(mod_session)

    if not scene.modify_with_session("MCP: " + name, mod):
        raise ToolError(
            f"Cascadeur rejected the AutoPosing update ({name!r}); the traceback is in the Cascadeur log (get_log)."
        )
    return {"frame": frame, "undo": "MCP: " + name, "method": "editor"}


def _selected_ids(selector):
    selection = selector.selected()
    try:
        ordered = list(selection.ordered_ids)
    except Exception:
        ordered = []
    return ordered or list(selection.ids)


def set_lock(names, locked=True) -> dict:
    """Lock the named controllers' current frame with Cascadeur's Autolock command.

    Cascadeur 2026.2.3 has no per-point lock API and no unlock action for model control points
    (AutoPosingTool.SwitchLock and SwitchLockOnInterval added no lockedFrames; verified by
    saving the scene and reading tool/auto_posing.json). The working operation is
    AutoPosing.Autolock (QML action AutoPosingTool.AutoUnlock, tooltip "Adjust AutoPosing lock
    state"): it locks the AutoPosing points of the current frame, and the named points are
    selected beforehand. locked=False therefore returns a failure that explains the limitation.
    """
    from . import rig

    try:
        ids = rig.resolve(names)
    except rig.RigError as exc:
        # Return the structured failure instead of raising: dispatch.call_on_gui would flatten
        # the exception to a string and the candidates would be lost.
        return exc.result()
    if not ids:
        return {
            "ok": False,
            "error": "No controllers matched, so no AutoPosing lock was changed.",
            "hint": "Call list_controllers to see the control point names and aliases.",
        }
    if not locked:
        return {
            "ok": False,
            "error": "Cascadeur 2026.2.3 has no unlock action that acts on model control points.",
            "hint": (
                "AutoPosingTool.AutoUnlock is Cascadeur's Autolock command (it locks the current "
                "frame), and AutoPosingTool.SwitchLock / SwitchLockOnInterval added no lock for "
                "model points. Unlock the points in the AutoPosing interface instead."
            ),
            "tried": [SWITCH_LOCK_ACTION, "AutoPosingTool.SwitchLockOnInterval", AUTOLOCK_ACTION],
        }
    scene = _scene()
    viewer = scene.model_viewer()
    controllers = []
    for object_id in ids:
        try:
            name = viewer.get_object_name(object_id)
        except Exception:
            name = None
        controllers.append({"name": name, "id": str(object_id)})
    info = autolock(ids)
    return {
        "ok": True,
        "controllers": controllers,
        "action": info["action"],
        "locked": True,
        "undo": info["undo"],
        "undoable": True,
        "scope": "current frame (Cascadeur chooses the full AutoPosing lock set)",
        "readback": None,
    }


# ---------------------------------------------------------------------------
# The "Adjust AutoPosing lock state?" prompt
# ---------------------------------------------------------------------------


def _dialog_container(item):
    """The nearest dialog/popup ancestor of a QML item (None when it is not in one)."""
    current = item
    while current is not None:
        type_name = _type_name(current).lower()
        if "dialog" in type_name or "popup" in type_name or "messagebox" in type_name:
            return current
        current = current.parentItem()
    return None


def _item_has_marker(item) -> bool:
    for name in ("text", "title", "toolTip"):
        text = _string_property(item, name).lower()
        if not text:
            continue
        if ("autoposing" in text or "auto posing" in text) and any(phrase in text for phrase in DIALOG_PHRASES):
            return True
    return False


def _is_clickable(item) -> bool:
    try:
        meta = item.metaObject()
    except Exception:
        return False
    return meta.indexOfSignal("clicked()") >= 0 or meta.indexOfSignal("pressed()") >= 0


def _scan_dialog():
    """Find the prompt by walking the QML tree, including items that are not laid out yet."""
    ui = _ui()
    for window in ui.quick_windows(include_hidden=True):
        for item in _window_items(window):
            if not _item_has_marker(item):
                continue
            container = _dialog_container(item)
            if container is None:
                continue
            if not container.isVisible():
                continue
            if not is_dialog_type(_type_name(container)):
                continue
            texts = []
            buttons = []
            for child in _iter_items(container):
                text = _string_property(child, "text") or _string_property(child, "title")
                if text and len(text) <= 300 and text not in texts:
                    texts.append(text)
                if _is_clickable(child):
                    label = _string_property(child, "text") or _string_property(child, "toolTip")
                    if label:
                        buttons.append({"text": label, "item": child})
            # A tooltip popup can carry the same "Adjust AutoPosing lock state" text; only a
            # popup with a Yes/No/Cancel-style button is the modal prompt.
            if not has_answer_button(buttons):
                continue
            return {
                "source": "scan",
                "texts": texts[:12],
                "buttons": buttons[:12],
            }
    return None


def lock_dialog():
    """The open AutoPosing lock-state prompt as {"source", "texts", "buttons"}, or None.

    ui.dialogs() is tried first; the QML scan finds the prompt even when it has not been laid
    out yet (a freshly created modal dialog has no on-screen rectangle for one event-loop turn).
    The "dialogs" source carries ui_click refs; the "scan" source carries QML items for
    _press_scan_button.
    """
    ui = _ui()
    listed = ui.dialogs()
    for dialog in listed.get("dialogs") or ():
        if is_lock_dialog(dialog):
            return {
                "source": "dialogs",
                "dialog": dialog,
                "texts": list(dialog.get("texts") or ())[:12],
                "buttons": [
                    {"text": button.get("text"), "ref": button.get("ref")} for button in dialog.get("buttons") or ()
                ],
            }
    return _scan_dialog()


def _press_scan_button(button) -> None:
    item = button.get("item")
    if item is None:
        raise ToolError("The AutoPosing dialog button is gone; check dialogs and answer it with ui_click.")
    from PySide6.QtCore import QMetaObject

    if not QMetaObject.invokeMethod(item, "clicked"):
        raise ToolError(
            "Could not press the AutoPosing dialog button through QML; answer the prompt with "
            "dialogs and ui_click instead."
        )


def answer_lock_dialog(answer="yes") -> dict:
    """Answer an open AutoPosing lock-state prompt once (GUI thread)."""
    wanted = answer_argument(answer)
    found = lock_dialog()
    if found is None:
        return {"found": False, "answer": wanted, "closed": True}
    info = describe_dialog(found)
    info.update(found=True, answer=wanted, answered=False)
    choice = choose_button(found.get("buttons"), wanted)
    if choice is None:
        info["closed"] = lock_dialog() is None
        info["error"] = f"The AutoPosing prompt has no button for answer {wanted!r} (buttons: {info.get('buttons')})."
        info["hint"] = "Pass answer='yes', 'no' or 'cancel', or answer the prompt with dialogs and ui_click."
        return info
    if found.get("source") == "dialogs":
        _ui().click(ref=choice["ref"])
    else:
        _press_scan_button(choice)
    info.update(answered=True, button=choice.get("text"))
    info["closed"] = lock_dialog() is None
    return info


def answer_now(answer="yes"):
    """Answer the prompt if it is already open, without waiting (GUI thread; for solve)."""
    wanted = answer_argument(answer)
    if lock_dialog() is None:
        return None
    return answer_lock_dialog(wanted)


def finish_dialogs(answer="yes", timeout=2.0, interval=0.15) -> dict:
    """Wait for the AutoPosing prompt, answer it and wait for it to close (HTTP thread).

    Call this from a tool handler after the GUI call that can trigger the prompt. It returns
    {"found": bool, "answer", "button", "closed": bool, ...}; found is False when no prompt
    appeared within timeout. A dialog that will not close comes back with closed False and a
    hint instead of being left behind silently.
    """
    wanted = answer_argument(answer)
    from . import dispatch

    deadline = time.monotonic() + max(0.0, float(timeout))
    answered = None
    attempts = 0
    while True:
        try:
            found = dispatch.call_on_gui(lock_dialog, {}, 20.0)
        except Exception as exc:  # noqa: BLE001 - report instead of leaving a dialog open
            return {
                "found": False,
                "closed": None,
                "error": f"Could not inspect Cascadeur's dialogs: {type(exc).__name__}: {exc}",
            }
        if found is None:
            if answered is None:
                if time.monotonic() >= deadline:
                    return {"found": False, "answer": wanted, "closed": True}
            else:
                answered["closed"] = True
                return answered
        else:
            if attempts >= 3:
                answered["closed"] = False
                answered["hint"] = (
                    "The AutoPosing prompt is still open after three answers; answer it with "
                    "dialogs and ui_click (some prompts need a second button)."
                )
                return answered
            attempts += 1
            try:
                answered = dispatch.call_on_gui(answer_lock_dialog, {"answer": wanted}, 20.0)
            except Exception as exc:  # noqa: BLE001 - report instead of leaving a dialog open
                return {
                    "found": True,
                    "closed": False,
                    "error": f"Could not answer the AutoPosing dialog: {type(exc).__name__}: {exc}",
                    "hint": "Answer it with dialogs and ui_click, then retry the AutoPosing call.",
                }
            if answered.get("closed"):
                return answered
            if time.monotonic() >= deadline:
                answered["closed"] = False
                answered["hint"] = (
                    "The AutoPosing prompt did not close; answer it with dialogs and ui_click and "
                    "retry the AutoPosing call."
                )
                return answered
        time.sleep(interval)


# ---------------------------------------------------------------------------
# What T16's set_pose calls
# ---------------------------------------------------------------------------


def _names(scene, object_ids) -> list:
    viewer = scene.model_viewer()
    names = []
    for object_id in object_ids:
        try:
            names.append(viewer.get_object_name(object_id))
        except Exception:
            names.append(None)
    return names


def _global_positions(scene, object_ids, frame) -> dict:
    """{ObjectId: [x, y, z] cm} for the objects that expose Transform.global_position."""
    viewer = scene.model_viewer()
    behaviour_viewer = viewer.behaviour_viewer()
    data_viewer = viewer.data_viewer()
    result = {}
    for object_id in object_ids:
        try:
            transform = behaviour_viewer.get_behaviour_by_name(object_id, "Transform")
            data_id = behaviour_viewer.get_behaviour_data(transform, "global_position")
            result[object_id] = [float(value) for value in data_viewer.get_data_value(data_id, int(frame))]
        except Exception:
            continue
    return result


def _position_deltas(scene, before, after, tolerance=0.5) -> list:
    """Objects that moved more than tolerance cm between two _global_positions() snapshots."""
    viewer = scene.model_viewer()
    deltas = []
    for object_id, old in before.items():
        new = after.get(object_id)
        if new is None:
            continue
        distance = sum((a - b) ** 2 for a, b in zip(new, old, strict=True)) ** 0.5
        if distance <= tolerance:
            continue
        try:
            name = viewer.get_object_name(object_id)
        except Exception:
            name = None
        deltas.append({"name": name, "moved_cm": round(distance, 3)})
    return deltas


def solve(scene, frame, moved_ids=None, *, answer="yes", session=None, ensure_enabled=True) -> dict:
    """Let AutoPosing pose the rest of the body after main control points moved.

    Call it on the GUI thread right after writing the points of frame (T16's set_pose with
    autoposing=true), after the frame was locked with lock_pose(). It makes sure AutoPosing is
    on, recomputes the frame through AutoPosingTool.update and answers the "Adjust AutoPosing
    lock state?" prompt. The lock is essential: update alone throws scripted positions away and
    restores the AutoPosing prediction, so solve() reports them in "restored" when that happens.

    The playhead is moved to frame for the AutoPosing calls and restored afterwards. Pass
    session=... to fold the recompute into the caller's scene.modify_update_with_session undo
    step; without it the recompute is its own "MCP: autoposing_solve" step. Returns {"ok",
    "frame", "moved", "autoposing", "undo", "restored", ...}; "dialog" describes a prompt that
    was answered ("dialog_open" is true when it would not close, so the caller can finish it
    with finish_dialogs).
    """
    wanted = answer_argument(answer)
    frame = int(frame)
    block = layer_block()
    if block is not None:
        return {**block, "frame": frame}
    result = {"ok": True, "frame": frame, "moved": [], "dialog": None}
    moved = list(moved_ids or ())
    if moved:
        result["moved"] = _names(scene, moved)
    previous_frame = int(scene.get_current_frame(False))
    if previous_frame != frame:
        scene.set_current_frame(frame)
    positions_before = _global_positions(scene, moved, frame) if moved and session is None else {}
    try:
        if ensure_enabled:
            state_before = state()
            enabled = state_before.get("enabled")
            if enabled is False:
                call_action(TOGGLE_ACTION)
                state_after = state()
                result["autoposing"] = {
                    "enabled_before": False,
                    "enabled_after": state_after.get("enabled"),
                    "toggled": True,
                    "dialog": answer_now(wanted),
                }
            else:
                result["autoposing"] = {"enabled_before": enabled, "enabled_after": enabled, "toggled": False}
        if session is not None:
            _set_session_frame(session, frame)
            tool_editor().update(session)
            result["undo"] = None
        else:
            result.update(run_update(scene=scene, frame=frame, name="autoposing_solve"))
            if positions_before:
                restored = _position_deltas(scene, positions_before, _global_positions(scene, moved, frame))
                if restored:
                    result["restored"] = restored
                    result["hint"] = (
                        "AutoPosing restored its prediction for these points because they were not "
                        "locked; call autoposing.lock_pose(frame, moved) before writing the pose and "
                        "solve again."
                    )
    finally:
        if previous_frame != frame:
            scene.set_current_frame(previous_frame)
    dialog = answer_now(wanted)
    if dialog is not None:
        result["dialog"] = dialog
        result["dialog_open"] = not dialog.get("closed", False)
    return result
