"""Scene-file tools: create, open, save, list, switch and close scene tabs.

Every function runs on Cascadeur's GUI thread. Scene creation/loading follows the order the
official samples use (create_application_scene + set_current_scene, then DataSourceManager
load_scene), because load_scene loads into the current tab instead of opening a new one.

Closing follows the crash rules measured in T02 and the ticket README:

* close with DataSourceManager.close_scene, never SceneManager.remove_application_scene;
* never touch SceneManager after close_scene in the same call (the tab is being destroyed);
* a modified tab shows the "Closing scene" prompt, which is answered here (Don't Save when
  discarding, Cancel otherwise) so no dialog is left open;
* only the tab the caller names is closed, by exact name or by an index from list_open_scenes.
"""

from __future__ import annotations

import os

import csc

from .. import common
from ._schema import schema

_CASC = ".casc"

# Runtime names seen on similar Cascadeur/binding objects. The public API does not document a
# modified flag (2026.2.3), so this is a probe: when none exists we return None and close_scene
# falls back to answering the "Closing scene" prompt without ever saving.
_MODIFIED_NAMES = (
    "is_modified",
    "isModified",
    "has_unsaved_changes",
    "hasUnsavedChanges",
    "has_changes",
    "hasChanges",
    "is_changed",
    "isChanged",
    "is_dirty",
    "isDirty",
    "dirty",
    "modified",
    "unsaved",
    "needs_save",
    "needs_saving",
    "save_needed",
    "is_save_required",
)

_DISCARD_LABELS = ("don't save", "dont save", "discard changes", "discard")
_CANCEL_LABELS = ("cancel",)
_CLOSE_TEXT = ("closing scene", "save changes", "save your changes", "unsaved changes")


# ---------------------------------------------------------------------------
# Scene lookup and descriptions
# ---------------------------------------------------------------------------


def _view_name(view) -> str:
    try:
        return str(view.name())
    except Exception:
        return ""


def _view_path(view) -> str:
    try:
        return str(view.get_path_name() or "")
    except Exception:
        return ""


def _same_scene(left, right) -> bool:
    if left is None or right is None:
        return False
    if left is right:
        return True
    try:
        return bool(left == right)
    except Exception:
        return False


def _index_of(scenes, target) -> int:
    for index, scene in enumerate(scenes):
        if _same_scene(scene, target):
            return index
    return -1


def _read_flag(candidate, name):
    try:
        value = getattr(candidate, name)
    except Exception:
        return None
    if callable(value):
        try:
            value = value()
        except Exception:
            return None
    return value if isinstance(value, bool) else None


def _modified(view):
    """Return True/False when Cascadeur exposes a modified flag, else None.

    The flag is not part of the published csc API, so look on both the view scene and its
    domain scene: first the common names, then any public getter whose name mentions
    modification. That keeps list_open_scenes useful if a later Cascadeur version renames it.
    """
    candidates = [view]
    try:
        candidates.append(view.domain_scene())
    except Exception:
        pass
    tokens = ("modified", "unsaved", "dirty", "changed")
    for candidate in candidates:
        for name in _MODIFIED_NAMES:
            value = _read_flag(candidate, name)
            if value is not None:
                return value
        try:
            names = dir(candidate)
        except Exception:
            continue
        for name in names:
            if name.startswith("_") or name in _MODIFIED_NAMES:
                continue
            if not any(token in name.lower() for token in tokens):
                continue
            value = _read_flag(candidate, name)
            if value is not None:
                return value
    return None


def _scene_entry(view, index: int, is_current: bool) -> dict:
    return {
        "index": index,
        "name": _view_name(view),
        "path": _view_path(view),
        "current": bool(is_current),
        "modified": _modified(view),
    }


def _managers():
    app = csc.app.get_application()
    return app.get_scene_manager(), app.get_data_source_manager()


def _scenes_and_current():
    manager, data_source = _managers()
    return manager, data_source, list(manager.scenes()), manager.current_scene()


def _same_path(left: str, right: str) -> bool:
    if not left or not right:
        return False
    return os.path.normcase(os.path.normpath(left)) == os.path.normcase(os.path.normpath(right))


def _find_by_name(scenes, name: str):
    matches = [index for index, scene in enumerate(scenes) if _view_name(scene) == name]
    if not matches:
        matches = [index for index, scene in enumerate(scenes) if _same_path(_view_path(scene), name)]
    if not matches:
        available = ", ".join(repr(_view_name(scene)) for scene in scenes) or "none"
        raise LookupError(
            f"No open scene is named {name!r}. Open scenes: {available}. Pass an index or the exact name."
        )
    if len(matches) > 1:
        raise LookupError(f"{len(matches)} open scenes are named {name!r} (indexes {matches}). Pass the index instead.")
    return matches[0]


def _select_scene(scene):
    """Return (manager, data_source, scenes, target, index, current)."""
    manager, data_source, scenes, current = _scenes_and_current()
    if isinstance(scene, bool):
        raise LookupError("scene must be an index (integer) or an exact scene name (string), not a boolean.")
    if isinstance(scene, int):
        if 0 <= scene < len(scenes):
            return manager, data_source, scenes, scenes[scene], scene, current
        raise LookupError(
            f"Scene index {scene} is out of range; there are {len(scenes)} open scenes (indexes 0..{len(scenes) - 1})."
        )
    if isinstance(scene, str):
        index = _find_by_name(scenes, scene)
        return manager, data_source, scenes, scenes[index], index, current
    raise LookupError("scene must be an index (integer) or an exact scene name (string).")


def _check_scene_path(path) -> str:
    if not isinstance(path, str) or not path.strip():
        raise ValueError("path must be a non-empty string.")
    normalized = os.path.normpath(path)
    if not os.path.isabs(normalized):
        raise ValueError(f"path must be absolute (got {path!r}).")
    if os.path.splitext(normalized)[1].lower() != _CASC:
        raise ValueError(f"path must end with {_CASC} (got {normalized!r}).")
    if not os.path.isfile(normalized):
        raise ValueError(f"Scene file does not exist: {normalized!r}.")
    return normalized


# ---------------------------------------------------------------------------
# Closing dialogs (UI thread only)
# ---------------------------------------------------------------------------


def _dialog_label(button) -> str:
    return str(button.get("text") or button.get("toolTip") or "").strip().lower().replace("’", "'")


def _dialog_labels(dialog) -> list[str]:
    return [_dialog_label(button) for button in dialog.get("buttons") or []]


def _is_close_prompt(dialog) -> bool:
    if dialog.get("kind") != "dialog":
        return False
    text = " ".join(str(item) for item in dialog.get("texts") or []).lower()
    if any(needle in text for needle in _CLOSE_TEXT):
        return True
    labels = _dialog_labels(dialog)
    save = any(label == "save" or label.startswith("save as") for label in labels)
    discard = any(any(wanted in label for wanted in _DISCARD_LABELS) for label in labels)
    return save and discard and any(label == "cancel" for label in labels)


def _find_button(dialog, wanted: tuple[str, ...]):
    for button in dialog.get("buttons") or []:
        label = _dialog_label(button)
        if any(label == choice or choice in label for choice in wanted):
            return button, label
    return None, None


def _click_dialog_button(dialog, wanted: tuple[str, ...]) -> tuple[bool, str | None, str | None]:
    button, label = _find_button(dialog, wanted)
    if button is None:
        return False, None, None
    from .. import ui

    try:
        ui.click(ref=button["ref"])
    except Exception as exc:
        return False, label, str(exc)
    return True, label, None


def _handle_close_prompt(before: list, discard: bool) -> dict:
    """Answer a newly appeared "Closing scene" prompt; never touches SceneManager.

    Returns {"prompt": bool, "dismissed": bool, "discarded": bool, ...}. When discard is False
    and a prompt appears, Cancel is clicked and the caller reports the unsaved-change error.
    """
    from .. import ui

    after = list(ui.dialogs().get("dialogs") or [])
    new_dialogs = [dialog for dialog in after if dialog not in before]
    prompt = next((dialog for dialog in new_dialogs if _is_close_prompt(dialog)), None)
    if prompt is None:
        return {"prompt": False, "dismissed": False, "discarded": False}
    if discard:
        clicked, label, error = _click_dialog_button(prompt, _DISCARD_LABELS)
        if clicked:
            return {"prompt": True, "dismissed": True, "discarded": True, "button": label}
        # A close prompt without a discard button must not be left open: Cancel it if possible.
        cancel_clicked, cancel_label, cancel_error = _click_dialog_button(prompt, _CANCEL_LABELS)
        return {
            "prompt": True,
            "dismissed": cancel_clicked,
            "discarded": False,
            "button": cancel_label,
            "error": error or cancel_error or "The close prompt has no Don't Save button.",
        }
    clicked, label, error = _click_dialog_button(prompt, _CANCEL_LABELS)
    return {
        "prompt": True,
        "dismissed": clicked,
        "discarded": False,
        "button": label,
        "error": error or (None if clicked else "The close prompt has no Cancel button."),
    }


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


def new_scene() -> dict:
    """Open a new empty scene tab and switch to it."""
    try:
        manager, _, _, _ = _scenes_and_current()
        view = manager.create_application_scene()
        if view is None:
            return common.fail(
                "Cascadeur did not create a new scene.",
                hint="Check get_log for an error and try again.",
            )
        manager.set_current_scene(view)
        scenes = list(manager.scenes())
        index = _index_of(scenes, view)
        return common.ok(scene=_scene_entry(view, index, True), open_scenes=len(scenes))
    except Exception as exc:
        return common.fail(
            f"Could not create a new scene: {exc}",
            hint="Check that Cascadeur is responsive and get_log for details.",
        )


def open_scene(path: str) -> dict:
    """Open a .casc file in a new scene tab and switch to it."""
    try:
        normalized = _check_scene_path(path)
    except ValueError as exc:
        return common.fail(str(exc), hint="Pass an absolute path to an existing .casc file.")
    manager, data_source, _, previous = _scenes_and_current()
    view = None
    try:
        view = manager.create_application_scene()
        if view is None:
            return common.fail(
                "Cascadeur did not create a scene tab for the file.",
                hint="Check get_log for an error and try again.",
            )
        manager.set_current_scene(view)
        loaded = data_source.load_scene(normalized)
        if not loaded:
            # Restore the previous tab, then drop the empty one. Do not touch SceneManager
            # after close_scene: the blank tab's QML objects are being destroyed.
            if previous is not None:
                try:
                    manager.set_current_scene(previous)
                except Exception:
                    pass
            try:
                data_source.close_scene(view)
            except Exception:
                pass
            return common.fail(
                f"Cascadeur did not load {normalized!r}.",
                hint="Check that the file is a valid .casc scene and that Cascadeur's log has no load error.",
            )
        scenes = list(manager.scenes())
        index = _index_of(scenes, view)
        return common.ok(scene=_scene_entry(view, index, True), open_scenes=len(scenes))
    except Exception as exc:
        if view is not None:
            if previous is not None:
                try:
                    manager.set_current_scene(previous)
                except Exception:
                    pass
            try:
                data_source.close_scene(view)
            except Exception:
                pass
        return common.fail(
            f"Could not open {normalized!r}: {exc}",
            hint="Check that the file is a valid .casc scene and that Cascadeur's log has no load error.",
        )


def save_scene(path: str | None = None, overwrite=False) -> dict:
    """Save the current scene, optionally as a new .casc file."""
    try:
        _, view, _ = common.scenes()
    except Exception as exc:
        return common.fail(f"No current scene to save: {exc}", hint="Open or create a scene first.")
    if path is not None:
        try:
            target = common.output_path(path, overwrite=overwrite, extensions=_CASC)
        except Exception as exc:
            return common.fail(
                str(exc),
                hint="Pass an absolute .casc path in an existing folder; pass overwrite=true only to replace an existing file.",
            )
        existed = os.path.exists(target)
    else:
        current_path = _view_path(view)
        if not current_path:
            return common.fail(
                "The current scene has never been saved, so there is no file to save it to.",
                hint="Pass path=<absolute .casc file> to save it for the first time.",
            )
        if overwrite is not True:
            return common.fail(
                f"Refusing to overwrite {current_path!r} without overwrite=true.",
                hint="Pass overwrite=true to save over the current file, or path=<new .casc file> to save a copy.",
            )
        try:
            target = common.output_path(current_path, overwrite=True, extensions=_CASC)
        except Exception as exc:
            return common.fail(str(exc), hint="Pass a new absolute .casc path to save the scene elsewhere.")
        existed = True
    try:
        saved = view.save(target)
    except Exception as exc:
        return common.fail(f"Cascadeur could not save {target!r}: {exc}", hint="Check get_log for details.")
    if saved is False:
        return common.fail(f"Cascadeur did not save {target!r}.", hint="Check get_log for the save error.")
    if not os.path.isfile(target):
        return common.fail(
            f"Cascadeur reported no error but {target!r} was not written.",
            hint="Check the folder's permissions and get_log.",
        )
    try:
        _, _, scenes, _ = _scenes_and_current()
        index = _index_of(scenes, view)
        entry = _scene_entry(view, index, True)
    except Exception:
        entry = {"name": _view_name(view), "path": _view_path(view) or target}
    return common.ok(saved_to=target, overwritten=existed, scene=entry)


def list_open_scenes() -> dict:
    """List every open scene tab and mark the current one."""
    try:
        manager, _, scenes, current = _scenes_and_current()
        entries = []
        current_index = -1
        for index, view in enumerate(scenes):
            is_current = _same_scene(view, current)
            if is_current and current_index < 0:
                current_index = index
            entries.append(_scene_entry(view, index, is_current))
        return common.ok(scenes=entries, count=len(entries), current=current_index)
    except Exception as exc:
        return common.fail(f"Could not list open scenes: {exc}", hint="Check get_log for details.")


def switch_scene(scene) -> dict:
    """Make an open scene current by index or exact name."""
    try:
        manager, _, scenes, target, index, current = _select_scene(scene)
        if not _same_scene(target, current):
            manager.set_current_scene(target)
        return common.ok(scene=_scene_entry(target, index, True), was_current=_same_scene(target, current))
    except LookupError as exc:
        return common.fail(str(exc), hint="Call list_open_scenes to see the open scenes and their indexes.")
    except Exception as exc:
        return common.fail(f"Could not switch scenes: {exc}", hint="Check get_log for details.")


def close_scene(scene, discard_changes=False) -> dict:
    """Close one open scene tab, refusing to discard unsaved changes unless asked."""
    try:
        _, data_source, scenes, target, index, current = _select_scene(scene)
    except LookupError as exc:
        return common.fail(str(exc), hint="Call list_open_scenes to see the open scenes and their indexes.")
    except Exception as exc:
        return common.fail(f"Could not find the scene to close: {exc}", hint="Call list_open_scenes first.")
    info = _scene_entry(target, index, _same_scene(target, current))
    modified = info["modified"]
    if modified is True and discard_changes is not True:
        return common.fail(
            f"Scene {info['name']!r} has unsaved changes, so it was left open.",
            hint="Save it with save_scene, or pass discard_changes=true to close it without saving.",
            scene=info,
        )
    # A known-unmodified scene cannot show the close prompt, so do not walk the UI after
    # closing it. Unknown flags (None) and modified scenes still need prompt handling.
    needs_prompt_check = modified is not False
    before = []
    if needs_prompt_check:
        try:
            from .. import ui

            before = list(ui.dialogs().get("dialogs") or [])
        except Exception:
            before = []
    try:
        data_source.close_scene(target)
    except Exception as exc:
        return common.fail(
            f"Cascadeur could not close scene {info['name']!r}: {exc}",
            hint="The scene may still be open; call list_open_scenes and get_log.",
            scene=info,
        )
    # SceneManager must not be touched from here on: the closed tab's QML objects are going away.
    if needs_prompt_check:
        try:
            prompt = _handle_close_prompt(before, discard_changes is True)
        except Exception as exc:
            return common.fail(
                f"Closed scene {info['name']!r}, but could not answer the close prompt: {exc}",
                hint="Call dialogs and press Cancel/Don't Save, then check list_open_scenes.",
                scene=info,
            )
    else:
        prompt = {"prompt": False, "dismissed": False, "discarded": False}
    if prompt["prompt"] and not prompt["dismissed"]:
        return common.fail(
            f"Scene {info['name']!r} has unsaved changes and the close prompt could not be dismissed.",
            hint="Call dialogs and answer the prompt; the scene was not closed.",
            scene=info,
            dialog=prompt,
        )
    if prompt["prompt"] and not prompt["discarded"]:
        return common.fail(
            f"Scene {info['name']!r} has unsaved changes, so it was left open.",
            hint="Save it with save_scene, or pass discard_changes=true to close it without saving.",
            scene=info,
            dialog=prompt,
        )
    return common.ok(closed=info, discarded=bool(prompt["discarded"]), prompt=prompt if prompt["prompt"] else None)


TOOLS = [
    {
        "name": "new_scene",
        "description": "Create a new empty scene tab and make it current. Use it to start from scratch without replacing any open scene. Returns the new scene's index, name and path.",
        "inputSchema": schema(),
        "fn": new_scene,
        "settle": True,
    },
    {
        "name": "open_scene",
        "description": "Open an existing .casc file in a new scene tab and make it current. Use it instead of loading into the current tab so an open scene is never replaced. Returns the new scene's index, name and path; path must be absolute.",
        "inputSchema": schema(
            {"path": {"type": "string", "description": "Absolute path to an existing .casc file."}},
            ["path"],
        ),
        "fn": open_scene,
        "settle": True,
    },
    {
        "name": "save_scene",
        "description": "Save the current scene, or save it as a new .casc file when path is given. Saving over an existing file, including the scene's own file, requires overwrite=true. Returns the saved path and scene info.",
        "inputSchema": schema(
            {
                "path": {
                    "type": "string",
                    "description": "Absolute .casc path to save as; omit to save back to the scene's current file.",
                },
                "overwrite": {
                    "type": "boolean",
                    "description": "Allow replacing an existing file (must be true for the scene's own file).",
                },
            }
        ),
        "fn": save_scene,
        "settle": True,
    },
    {
        "name": "list_open_scenes",
        "description": "List every open scene tab with its index, name, path, whether it is current, and whether it has unsaved changes when Cascadeur exposes that (null otherwise). Use it to choose an index or name for switch_scene and close_scene.",
        "inputSchema": schema(),
        "fn": list_open_scenes,
    },
    {
        "name": "switch_scene",
        "description": "Make an open scene tab current by index or exact scene name. Use list_open_scenes first; indexes are only valid for the current tab order. Returns the new current scene's index, name and path.",
        "inputSchema": schema(
            {
                "scene": {
                    "type": ["integer", "string"],
                    "description": "Scene index from list_open_scenes, or an exact scene name (a path also works).",
                }
            },
            ["scene"],
        ),
        "fn": switch_scene,
        "settle": True,
    },
    {
        "name": "close_scene",
        "description": "Close one open scene tab without touching any other tab. Use it when a temporary or test tab is no longer needed; a scene with unsaved changes is left open unless discard_changes=true, which discards them without saving. Returns the closed scene's index, name and path.",
        "inputSchema": schema(
            {
                "scene": {
                    "type": ["integer", "string"],
                    "description": "Scene index from list_open_scenes, or an exact scene name (a path also works).",
                },
                "discard_changes": {
                    "type": "boolean",
                    "description": "Close without saving when the scene has unsaved changes (default false).",
                },
            },
            ["scene"],
        ),
        "fn": close_scene,
        "settle": True,
    },
]
