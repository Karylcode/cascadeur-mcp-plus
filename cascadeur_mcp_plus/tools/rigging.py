"""Quick Rigging: turn a bare skeleton into a Cascadeur rig (T29).

Cascadeur's Quick Rigging tool builds the rig elements (control points, boxes, AutoPosing)
from a QRT/QRC template.  The dangerous part is that both its "Add rig elements" and
"Generate rig" steps need the scene to be in Rig mode: exactly one AnimationInfo.  With zero
or several AnimationInfo entries Cascadeur 2026.2.3 raises "Rig mode several animationInfo"
and then crashes (observed twice on 2026-10-18).  Every path here therefore checks the count
immediately before it asks Cascadeur to create rig elements, and again before it lets the
"Generate rig" button run; a failed check returns an error instead of touching the rig.

The module imports no Cascadeur module at load time.  The template parser/matcher is plain
Python, which is what tests/test_rigging.py exercises; everything scene- or UI-related is
imported lazily inside the functions.

The Quick Rigging steps are native Cascadeur commands ("Rig mode on/off", "Create rig from
qrt"), so quick_rig is NOT one MCP undo step and deliberately does not wrap them in
common.edit: nesting a modify call around a command that opens its own command is not
supported by Cascadeur's API.  The result reports how many commands the call wrote.
"""

from __future__ import annotations

import json
import os
import re
from collections import Counter

from ..errors import ToolError
from ._schema import schema

TEMPLATE_EXTENSIONS = (".qrigcasc", ".qrt")
TEMPLATE_DIR_ENV = "CASCADEUR_AUTORIG_TEMPLATES"

# core.rigging.MirrorPlane / rig_gen.rig_context_gen.structures.MirrorPlane
MIRROR_PLANES = {"xy": 0, "xz": 1, "yz": 2, "undef": 3, "none": 3}
MIRROR_PLANE_NAMES = {0: "XY", 1: "XZ", 2: "YZ", 3: "Undef"}

# A template must map at least this share of the skeleton's joints before quick_rig trusts
# it; below that it returns the candidate list instead of guessing.
MIN_COVERAGE = 0.6
MIN_MATCHED = 5
# Two templates this close are treated as ambiguous unless a scene/template name hint breaks
# the tie (e.g. Cascy.casc -> Cascy.qrigcasc).
AMBIGUITY_MARGIN = 0.001

LOG_COMMAND_PREFIX = "Command: "
# Scene/UI bookkeeping, not undo steps.  Cascadeur writes these between the rig commands, and
# reading a log slice without filtering them made undo_steps look like 100+ (T29 live run).
NOISE_COMMANDS = {
    "Select default layer",
    "default constants initialization",
    "load scene",
    "create autoposing",
    "Trajectory: change trajectory interval",
    "Selection",
    "Change frame",
    "Change layers selection",
    "changeLayers",
}


class _RiggingError(ToolError):
    """A failure with a hint and extra result fields for common.fail()."""

    def __init__(self, message: str, hint: str | None = None, **data) -> None:
        super().__init__(message)
        self.hint = hint
        self.data = data


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _common():
    """Import the Cascadeur-side helpers lazily so plain Python can load this module."""
    from .. import common

    return common


def _fail(common, exc: ToolError) -> dict:
    return common.fail(str(exc), hint=getattr(exc, "hint", None), **getattr(exc, "data", {}))


def _cascadeur_path(path: str) -> str:
    """Cascadeur's own scripts pass forward-slash paths to its tools."""
    return os.path.abspath(path).replace("\\", "/")


def _suffix(path) -> str:
    return os.path.splitext(str(path))[1].lower()


def template_directory(directory=None) -> str:
    """Return the folder with Cascadeur's Quick Rigging templates.

    The environment override is used by tests; otherwise try each platform's Cascadeur install
    folders (T55: resources/autorig_templates under paths.install_dirs()) and finally the first
    candidate so the error message names a concrete path.
    """
    if directory:
        return os.path.abspath(str(directory))
    from .. import paths

    candidates = []
    if os.environ.get(TEMPLATE_DIR_ENV):
        candidates.append(os.environ[TEMPLATE_DIR_ENV])
    candidates.extend(os.path.join(str(root), "resources", "autorig_templates") for root in paths.install_dirs())
    for path in candidates:
        if os.path.isdir(path):
            return os.path.abspath(path)
    return os.path.abspath(candidates[0] if candidates else os.path.join("resources", "autorig_templates"))


# ---------------------------------------------------------------------------
# Template parsing and matching (pure Python, unit-testable)
# ---------------------------------------------------------------------------


def _document_names(document):
    """Yield (document title, section name, bone name, joint name, joint path)."""
    for doc in document.get("Document") or []:
        if not isinstance(doc, dict):
            continue
        title = str(doc.get("Title") or "")
        for section in doc.get("Sections") or []:
            if not isinstance(section, dict):
                continue
            section_name = str(section.get("Section") or "")
            for entry in section.get("Names") or []:
                if not isinstance(entry, dict):
                    continue
                yield (
                    title,
                    section_name,
                    str(entry.get("Bone name") or ""),
                    str(entry.get("Joint name") or ""),
                    list(entry.get("Joint path") or []),
                )


def _unique(values):
    seen = set()
    result = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def template_info(path: str) -> dict:
    """Read one .qrigcasc/.qrt template; unreadable files are returned with readable=False."""
    path = os.path.abspath(path)
    entry = {
        "name": os.path.splitext(os.path.basename(path))[0],
        "file": os.path.basename(path),
        "path": path,
        "format": _suffix(path).lstrip("."),
        "readable": False,
        "joints": [],
        "bones": [],
        "sections": [],
        "documents": [],
        "settings": {},
        "bytes": None,
    }
    try:
        entry["bytes"] = os.path.getsize(path)
    except OSError:
        pass
    try:
        with open(path, encoding="utf-8-sig") as handle:
            document = json.load(handle)
    except (OSError, ValueError) as exc:
        entry["error"] = f"Could not read the template: {exc}"
        return entry
    if not isinstance(document, dict):
        entry["error"] = "The template is not a Cascadeur Quick Rigging JSON document."
        return entry
    records = list(_document_names(document))
    entry["joints"] = _unique(record[3] for record in records)
    entry["bones"] = _unique(record[2] for record in records)
    entry["sections"] = _unique(record[1] for record in records)
    entry["documents"] = _unique(record[0] for record in records)
    settings = document.get("Settings")
    entry["settings"] = settings if isinstance(settings, dict) else {}
    if not entry["joints"]:
        entry["error"] = "The template contains no named joints."
        return entry
    entry["readable"] = True
    return entry


def scan_templates(directory=None) -> list[dict]:
    """Return every .qrigcasc/.qrt template in Cascadeur's autorig_templates folder."""
    root = template_directory(directory)
    try:
        names = sorted(os.listdir(root))
    except OSError as exc:
        raise _RiggingError(
            f"Could not list Cascadeur's Quick Rigging templates in {root!r}: {exc}",
            hint="Install Cascadeur 2026.2.3 or later; its templates live in resources/autorig_templates.",
        ) from exc
    templates = []
    for name in names:
        if _suffix(name) not in TEMPLATE_EXTENSIONS:
            continue
        path = os.path.join(root, name)
        if not os.path.isfile(path):
            continue
        templates.append(template_info(path))
    if not templates:
        raise _RiggingError(
            f"No Quick Rigging templates ({', '.join(TEMPLATE_EXTENSIONS)}) found in {root!r}.",
            hint="Check the Cascadeur installation; list_rig_templates returns the folder it read.",
        )
    return templates


def _simple_name(name: str) -> str:
    """Drop a Mixamo/UE namespace and any path prefix: mixamorig:LeftHand -> LeftHand."""
    return re.split(r"[|/:\\]", str(name))[-1]


def _normal_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", _simple_name(name).casefold())


def _hint_boost(template_name: str, hints) -> float:
    template_key = _normal_name(template_name)
    if not template_key:
        return 0.0
    best = 0.0
    for hint in hints or ():
        hint_key = _normal_name(hint)
        if not hint_key:
            continue
        if template_key == hint_key:
            best = max(best, 0.4)
        elif template_key in hint_key:
            best = max(best, 0.3)
        elif hint_key in template_key and len(hint_key) >= 4:
            best = max(best, 0.15)
    return best


def match_template(template: dict, joint_names) -> dict:
    """Score how well a template's joint names match the scene's joints.

    Exact path names score highest, then case-insensitive names, then the simple bone name
    after dropping a namespace, then a punctuation-insensitive form.  coverage is the share of
    the template's mapped joints that exist in the scene.
    """
    joints = [str(name) for name in joint_names if name]
    exact = set(joints)
    folded = {name.casefold() for name in joints}
    simple = {_simple_name(name).casefold() for name in joints}
    normal = {_normal_name(name) for name in joints}
    matched = 0
    score = 0.0
    missing = []
    for name in template.get("joints") or []:
        if name in exact:
            matched += 1
            score += 1.0
        elif name.casefold() in folded:
            matched += 1
            score += 0.97
        elif _simple_name(name).casefold() in simple:
            matched += 1
            score += 0.9
        elif _normal_name(name) in normal:
            matched += 1
            score += 0.8
        elif len(missing) < 20:
            missing.append(name)
    total = len(template.get("joints") or [])
    return {
        "template": template,
        "matched": matched,
        "total": total,
        "coverage": round(matched / total, 4) if total else 0.0,
        "score": round(score, 4),
        "missing": missing,
    }


def _candidate(entry: dict) -> dict:
    template = entry["template"]
    return {
        "name": template["name"],
        "path": template["path"],
        "joints": template.get("joints", [])[:20],
        "template_joints": entry["total"],
        "matched": entry["matched"],
        "coverage": entry["coverage"],
        "missing": entry["missing"],
    }


def choose_template(templates, joint_names, hints=()) -> tuple[dict | None, list[dict], str]:
    """Pick the closest template for a skeleton.

    Returns (template, candidates, reason).  template is None and reason explains why when no
    template is close enough or two templates are equally close; candidates is the ranked list
    the caller can show to the agent.
    """
    readable = [template for template in templates if template.get("readable")]
    if not readable:
        return None, [], "No readable Quick Rigging template was found."
    scored = []
    for template in readable:
        entry = match_template(template, joint_names)
        entry["hint_boost"] = _hint_boost(template["name"], hints)
        scored.append(entry)
    scored.sort(
        key=lambda item: (-item["coverage"], -item["matched"], -item["score"], item["template"]["name"].lower())
    )
    candidates = [_candidate(entry) for entry in scored[:10]]
    best = scored[0]
    if best["matched"] < MIN_MATCHED or best["coverage"] < MIN_COVERAGE:
        return (
            None,
            candidates,
            (
                f"No Quick Rigging template matches this skeleton closely enough "
                f"(best: {best['template']['name']} with {best['matched']}/{best['total']} joints). "
                "Pass template=... explicitly."
            ),
        )
    # A name hint (scene/file/mesh name) can separate templates with the same joint coverage.
    tied = [
        entry
        for entry in scored
        if abs(entry["coverage"] - best["coverage"]) <= AMBIGUITY_MARGIN and entry["matched"] == best["matched"]
    ]
    if len(tied) > 1:
        hinted = [entry for entry in tied if entry["hint_boost"]]
        if hinted:
            hinted.sort(key=lambda entry: (-entry["hint_boost"], -entry["score"], entry["template"]["name"].lower()))
            best = hinted[0]
        else:
            # More matched joints is more specific; only a true dead heat is ambiguous.
            distinct = {entry["score"] for entry in tied}
            if len(distinct) > 1:
                scored.sort(
                    key=lambda item: (
                        -item["coverage"],
                        -item["matched"],
                        -item["score"],
                        item["template"]["name"].lower(),
                    )
                )
                best = scored[0]
            else:
                names = ", ".join(entry["template"]["name"] for entry in tied[:6])
                return (
                    None,
                    candidates,
                    (
                        f"Several Quick Rigging templates match this skeleton equally well ({names}). "
                        "Pass template=... explicitly."
                    ),
                )
    return best["template"], candidates, ""


def _resolve_by_name(name: str, templates) -> dict:
    wanted = str(name).strip()
    if not wanted:
        raise _RiggingError("template must not be empty.", hint="Omit template to pick one automatically.")
    if os.path.isabs(wanted):
        if not os.path.isfile(wanted):
            raise _RiggingError(
                f"Quick Rigging template not found: {wanted!r}.",
                hint="Call list_rig_templates to see the installed .qrigcasc files.",
            )
        if _suffix(wanted) not in TEMPLATE_EXTENSIONS:
            raise _RiggingError(
                f"Unsupported template file {wanted!r}.",
                hint=f"Quick Rigging templates end with {' or '.join(TEMPLATE_EXTENSIONS)}.",
            )
        entry = template_info(wanted)
        if not entry["readable"]:
            raise _RiggingError(
                f"Could not use template {wanted!r}: {entry.get('error')}",
                hint="Pass a different template from list_rig_templates.",
            )
        return entry
    key = wanted.casefold()
    stem = os.path.splitext(key)[0]
    matches = []
    for template in templates:
        names = {
            template["name"].casefold(),
            template["file"].casefold(),
            os.path.splitext(template["file"])[0].casefold(),
        }
        if key in names or stem in names:
            matches.append(template)
    if len(matches) == 1:
        return _require_readable(matches[0])
    if len(matches) > 1:
        raise _RiggingError(
            f"Template name {wanted!r} is ambiguous.",
            hint="Pass the file name from list_rig_templates.",
            candidates=[{"name": item["name"], "path": item["path"]} for item in matches],
        )
    available = [item for item in templates if item.get("readable")]
    raise _RiggingError(
        f"Unknown Quick Rigging template {wanted!r}.",
        hint="Call list_rig_templates, or omit template to match the skeleton automatically.",
        candidates=[{"name": item["name"], "path": item["path"]} for item in available[:20]],
    )


def _require_readable(template: dict) -> dict:
    if not template.get("readable"):
        raise _RiggingError(
            f"Template {template.get('name')!r} cannot be read: {template.get('error')}",
            hint="Choose another template from list_rig_templates.",
            candidates=[{"name": template.get("name"), "path": template.get("path")}],
        )
    return template


def mirror_plane_value(value) -> int | None:
    """Normalize mirror_plane to core.rigging.MirrorPlane (XY=0, XZ=1, YZ=2, Undef=3)."""
    if value is None:
        return None
    if isinstance(value, bool):
        raise _RiggingError(
            f"mirror_plane must be XY, XZ, YZ or an integer 0-3 (got {value!r}).",
            hint="Use 'YZ' for a character that faces -Z, or omit mirror_plane.",
        )
    if isinstance(value, int):
        if 0 <= value <= 3:
            return value
        raise _RiggingError(
            f"mirror_plane {value} is out of range (XY=0, XZ=1, YZ=2, Undef=3).",
            hint="Use 'YZ' (2) for the usual character mirror plane.",
        )
    key = str(value).strip().lower()
    if key.isdigit() and 0 <= int(key) <= 3:
        return int(key)
    if key in MIRROR_PLANES:
        return MIRROR_PLANES[key]
    raise _RiggingError(
        f"Unknown mirror_plane {value!r}.",
        hint="Use 'XY', 'XZ', 'YZ', 'Undef' or 0-3; omit it to keep Quick Rigging's current setting.",
    )


# ---------------------------------------------------------------------------
# Scene helpers (GUI thread only)
# ---------------------------------------------------------------------------


def _behaviour_count(scene, name: str) -> int:
    try:
        viewer = scene.model_viewer().behaviour_viewer()
        if not viewer.is_valid_behaviour_type(name):
            return 0
        return len(viewer.get_behaviours(name))
    except Exception:
        return 0


def _animation_info_count(scene) -> int:
    return _behaviour_count(scene, "AnimationInfo")


def _rig_info_count(scene) -> int:
    return _behaviour_count(scene, "RigInfo")


def _joint_names(scene) -> list[str]:
    """Scene joint object names, in behaviour order."""
    model_viewer = scene.model_viewer()
    behaviour_viewer = model_viewer.behaviour_viewer()
    names = []
    for behaviour_id in behaviour_viewer.get_behaviours("Joint"):
        try:
            name = model_viewer.get_object_name(behaviour_viewer.get_behaviour_owner(behaviour_id))
        except Exception:
            continue
        if name and name not in names:
            names.append(name)
    return names


def _scene_hints(view_scene, scene, joint_names) -> list[str]:
    hints = []
    try:
        hints.extend([view_scene.name(), view_scene.get_path_name()])
    except Exception:
        pass
    try:
        for object_id in scene.model_viewer().get_objects():
            type_name = scene.model_viewer().get_object_type_name(object_id)
            if type_name in ("Mesh Object", "Rig info"):
                hints.append(scene.model_viewer().get_object_name(object_id))
    except Exception:
        pass
    hints.extend(joint_names[:1])
    return [hint for hint in hints if hint]


def _require_rig_mode(scene, action: str) -> int:
    """Return the AnimationInfo count when it is exactly 1; otherwise raise without touching anything."""
    count = _animation_info_count(scene)
    if count != 1:
        raise _RiggingError(
            f"Refusing to {action}: the scene has {count} AnimationInfo, but Quick Rigging needs exactly 1 "
            "(Rig mode). Cascadeur 2026.2.3 crashes when rig elements are generated outside Rig mode.",
            hint="Call enter_rig_mode first, or omit enter_rig_mode=false so quick_rig enters Rig mode itself.",
            stage="rig_mode_check",
            animation_info=count,
            rig_mode=False,
        )
    return count


def _rigging_tool(app, view_scene):
    return app.get_tools_manager().get_tool("RiggingToolWindowTool").editor(view_scene)


# ---------------------------------------------------------------------------
# UI dialogs and windows (GUI thread only)
# ---------------------------------------------------------------------------


def _ui():
    from .. import ui

    return ui


def _top_dialogs(ui) -> list:
    """One top-level dialog per window, without the nested QML items.

    Cascadeur's DialogManager produces a tree (ButtonsDialog > BaseButtonsDialog > DialogButton)
    and ui.dialogs() reports every level as its own "dialog".  Clicking all of them presses the
    same button repeatedly, which can trigger the dialog's action several times.  Keep only the
    outermost dialog per window.
    """
    groups = {}
    for dialog in _dialogs(ui):
        if dialog.get("kind") != "dialog":
            continue
        if dialog.get("type") == "DialogButton":
            continue
        groups.setdefault(dialog.get("window"), []).append(dialog)
    result = []
    for items in groups.values():
        items.sort(key=lambda dialog: 0 if dialog.get("type") == "ButtonsDialog" else 1)
        result.append(items[0])
    return result


def _dialog_signature(dialog: dict) -> tuple:
    """Stable identity of a top-level dialog, independent of its QML refs and window index."""
    return (
        str(dialog.get("type") or ""),
        tuple(str(text) for text in dialog.get("texts") or []),
        tuple(sorted(_button_label(button).casefold() for button in dialog.get("buttons") or [])),
    )


def _dialog_signatures(ui) -> set:
    """The top-level dialogs that were already open before this tool call."""
    return {_dialog_signature(dialog) for dialog in _top_dialogs(ui)}


def _new_dialogs(ui, baseline) -> list:
    """Top-level dialogs that appeared after baseline (never the user's pre-existing ones)."""
    baseline = baseline or set()
    return [dialog for dialog in _top_dialogs(ui) if _dialog_signature(dialog) not in baseline]


def _dialogs(ui) -> list:
    """List dialogs, tolerating a window that is being destroyed while it is walked.

    ui.dialogs() walks every QQuickWindow.  Closing the Quick Rigging window can leave a
    top-level window whose contentItem() is already None for one event-loop turn; instead of
    failing the whole tool call on that transient state, treat it as "no dialogs" (the window
    is gone, which is what the caller wanted anyway).
    """
    try:
        return ui.dialogs().get("dialogs") or []
    except Exception:
        return []


def _dialog_text(dialog: dict) -> str:
    return " ".join(str(text) for text in dialog.get("texts") or [])


def _button_label(button: dict) -> str:
    return str(button.get("text") or "").strip()


def _click_button(ui, button: dict) -> None:
    ui.click(ref=button["ref"])


def _try_click_button(ui, button: dict) -> bool:
    """Click a dialog/button ref; False when the window disappeared between listing and clicking.

    ui.click walks the window tree to enforce the rig-generation guard, and a dialog that was
    dismissed by a previous click can leave its window without a contentItem for one event-loop
    turn.  A transient stale ref must not abort the whole quick_rig call.
    """
    try:
        _click_button(ui, button)
        return True
    except Exception:
        return False


def _find_dialog(dialogs, contains=(), labels=()) -> dict | None:
    wanted_texts = [text.lower() for text in contains]
    wanted_labels = {label.lower() for label in labels}
    for dialog in dialogs:
        if dialog.get("kind") != "dialog":
            continue
        text = _dialog_text(dialog).lower()
        buttons = dialog.get("buttons") or []
        if wanted_texts and any(needle in text for needle in wanted_texts):
            return dialog
        if wanted_labels and any(_button_label(button).lower() in wanted_labels for button in buttons):
            return dialog
    return None


def _dialog_button(dialog: dict, labels) -> dict | None:
    """Find a button by exact, case-insensitive label; never by substring.

    Substring matching made "no" press "Normal" and "close" press "Closed"; a dialog button
    whose label is not exactly one of the safe labels must be left for the caller.
    """
    wanted = [label.casefold() for label in labels]
    buttons = dialog.get("buttons") or []
    for label in wanted:
        for button in buttons:
            if _button_label(button).casefold() == label:
                return button
    return None


def _answer_helper_dialog(ui, warnings: list) -> None:
    """Dismiss the "Rig mode helper" prompt that enter_rig_mode shows after an import.

    The prompt offers to launch Quick Rigging; quick_rig opens the tool itself, so pressing
    Yes here would open a second tool window.  Cancel is the safe answer.  Re-list after every
    click because one click can destroy several popup items at once.
    """
    closed, _stayed = _close_windows(ui, ("rig mode helper",), warnings)
    if closed:
        return
    for _ in range(4):
        dialogs = _top_dialogs(ui)
        dialog = _find_dialog(dialogs, contains=("rig mode helper",))
        if dialog is None:
            dialog = _find_dialog(dialogs, contains=("quick rigging",))
        if dialog is None:
            return
        button = _dialog_button(dialog, ("cancel", "no", "close"))
        if button is None:
            warnings.append("The 'Rig mode helper' dialog has no Cancel button; left it open.")
            return
        if _try_click_button(ui, button):
            warnings.append("Dismissed the 'Rig mode helper' dialog.")
            return


def _answer_recognition_dialogs(ui, warnings: list, baseline) -> None:
    """Dismiss Quick Rigging's informational dialogs (character recognition, warnings).

    Only dialogs whose buttons are just OK/Close are answered here, so a destructive choice
    (Yes/Generate) can never be pressed by accident.  A "could not be fully recognized" warning
    is kept in warnings: the tool still proceeds, because Quick Rigging can create a partial rig
    and the agent can inspect the result.
    """
    # The same informational popup can appear in several windows; the dialog manager closes the
    # group with one press.  Press only the first one and re-list later rather than clicking the
    # duplicates, which each re-run the dialog's scene load.
    for dialog in _new_dialogs(ui, baseline):
        text = _dialog_text(dialog).lower()
        buttons = dialog.get("buttons") or []
        labels = {_button_label(button).casefold() for button in buttons}
        informational = "recognized" in text or (bool(labels) and labels <= {"ok", "close"})
        if not informational:
            continue
        button = _dialog_button(dialog, ("ok", "close"))
        if button is None:
            continue
        if "could not be fully recognized" in text:
            warnings.append("Quick Rigging reports that not all bones were recognized.")
        if _try_click_button(ui, button):
            label = _dialog_text(dialog)[:120] or "<no text>"
            warnings.append(f"Dismissed Quick Rigging's information dialog: {label}")
        return


def _find_generate_dialog(ui, baseline) -> dict | None:
    dialogs = _new_dialogs(ui, baseline)
    return _find_dialog(
        dialogs,
        contains=("rig elements have been added",),
        labels=("generate rig",),
    )


def _click_generate(ui, baseline) -> bool:
    dialog = _find_generate_dialog(ui, baseline)
    if dialog is None:
        return False
    button = _dialog_button(dialog, ("generate rig", "generate"))
    if button is None:
        return False
    return _try_click_button(ui, button)


def _dismiss_leftover_dialogs(ui, warnings: list, baseline) -> list:
    """Close only dialogs this call opened; never the user's existing ones.

    Buttons are matched by exact label.  Save prompts are deliberately not answered here:
    pressing "Don't Save"/"Discard" could throw away the user's work, so such a dialog is
    returned in the leftover list instead.
    """
    left = []
    for dialog in _new_dialogs(ui, baseline):
        if dialog.get("type") == "QQuickPopupItem" and not dialog.get("buttons"):
            continue  # a toolbar tooltip, not a dialog (see T10's live-test note)
        button = _dialog_button(dialog, ("ok", "close", "cancel", "no", "continue editing"))
        if button is None:
            left.append(dialog)
            continue
        if _try_click_button(ui, button):
            warnings.append(f"Dismissed a leftover dialog with {_button_label(button)!r}.")
        else:
            left.append(dialog)
    return left


def _close_windows(ui, titles, warnings: list) -> tuple[list, list]:
    """Close top-level windows whose title contains one of titles.

    Returns (closed titles, titles that stayed).  hide() is used after close() so a QML window
    that refuses the close event (or closes asynchronously) is not left in front of the user.
    """
    closed = []
    stayed = []
    wanted = [title.casefold() for title in titles]
    for window in list(ui.quick_windows(include_hidden=False)):
        title = window.title()
        key = title.casefold()
        if not any(needle in key for needle in wanted):
            continue
        try:
            window.close()
            if window.isVisible():
                window.hide()
            closed.append(title)
            warnings.append(f"Closed the {title!r} window.")
        except Exception:
            stayed.append(title)
    return closed, stayed


def _close_quick_rigging_window(ui, warnings: list) -> list:
    """Close the Quick Rigging tool and Rig mode helper windows; returns titles that stayed."""
    _closed, stayed = _close_windows(ui, ("quick rigging", "rig mode helper"), warnings)
    return stayed


# ---------------------------------------------------------------------------
# Log helpers
# ---------------------------------------------------------------------------


def _log_position(common):
    try:
        return common._log_size()
    except Exception:
        return None


def _commands_since(common, position) -> list[str]:
    """The undoable Cascadeur commands written since position (scene/UI noise removed)."""
    if position is None:
        return []
    try:
        lines = common.log_tail(since=position, lines=500)
    except Exception:
        return []
    commands = []
    for line in lines:
        text = str(line)
        index = text.find(LOG_COMMAND_PREFIX)
        if index < 0:
            continue
        name = text[index + len(LOG_COMMAND_PREFIX) :].strip()
        if name and name not in NOISE_COMMANDS:
            commands.append(name)
    return commands


# ---------------------------------------------------------------------------
# Scene result
# ---------------------------------------------------------------------------


def _controller_summary(scene) -> dict:
    from .. import rig

    characters = rig.characters()
    rig_characters = [character for character in characters if character.get("mode") == "rig"]
    summary = {"total": 0, "main_points": 0, "by_kind": {}, "names": []}
    if not characters or not rig_characters:
        return summary
    try:
        controllers = rig.controllers(character=rig_characters[0]["name"])
    except Exception:
        return summary
    counts = Counter(item.get("kind") or "other" for item in controllers)
    summary["total"] = len(controllers)
    summary["main_points"] = counts.get("main_point", 0)
    summary["by_kind"] = dict(counts)
    summary["names"] = [item.get("name") for item in controllers if item.get("kind") == "main_point"][:20]
    return summary


def _character_summary(scene) -> dict | None:
    from .. import rig

    characters = [character for character in rig.characters() if character.get("mode") == "rig"]
    if not characters:
        return None
    character = characters[0]
    return {
        "name": character.get("name"),
        "info": character.get("info"),
        "root_joint": character.get("root_joint"),
        "controllers": character.get("controllers"),
        "joints": character.get("joints"),
        "meshes": character.get("meshes"),
    }


def _autoposing_count(scene) -> int:
    """Number of AutoPosingInfo behaviours (the actual behaviour name in 2026.2.3)."""
    return _behaviour_count(scene, "AutoPosingInfo") or _behaviour_count(scene, "AutoPosing")


def _autoposing_active(ui):
    """Read AutoPosing's toolbar toggle (QML active property), or None when it is not on screen.

    T15 established that the button's on-state is the QML `active` property, not `checked`.
    This is a second signal next to the AutoPosingInfo behaviour count, which can stay zero on
    builds that keep the tool state outside the scene graph.
    """
    try:
        tree = ui.tree(query="AutoPosingTool.AutoPosing", interactive_only=True, limit=20)
    except Exception:
        return None
    for element in tree.get("elements", []):
        if element.get("actionId") == "AutoPosingTool.AutoPosing":
            return bool(element.get("active"))
    return None


def _proto_count(scene) -> int:
    """Number of prototype rig elements (TechnicalLinks) in the scene."""
    return _behaviour_count(scene, "TechnicalLinks")


def _leave_rig_mode(scene) -> None:
    """Leave Rig mode through Cascadeur's own rig_mode.off entry point.

    This is the same code path as the "Generate rig" button and the official
    samples/casc_import_export.py; it builds the final RigInfo and AutoPosing.  The caller must
    have verified AnimationInfo == 1 immediately before.
    """
    _require_rig_mode(scene, "generate the rig")
    import rig_mode.off as rig_mode_off

    rig_mode_off.run(scene, True)


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


def list_rig_templates() -> dict:
    """List Quick Rigging templates installed with Cascadeur."""
    common = _common()
    try:
        root = template_directory()
        templates = scan_templates(root)
    except ToolError as exc:
        return _fail(common, exc)
    return common.ok(
        directory=root,
        count=len(templates),
        templates=[
            {
                "name": template["name"],
                "file": template["file"],
                "path": template["path"],
                "format": template["format"],
                "readable": template["readable"],
                "joints": len(template["joints"]),
                "bones": len(template["bones"]),
                "sections": template["sections"],
                "error": template.get("error"),
            }
            for template in templates
        ],
    )


def quick_rig(
    template=None,
    mirror_plane=None,
    create_autoposing: bool = True,
    replace: bool = False,
    enter_rig_mode: bool = True,
    timeout: float | None = None,
) -> dict:
    """Generate a Cascadeur rig from a bare skeleton with Quick Rigging."""
    common = _common()
    try:
        if not isinstance(create_autoposing, bool):
            raise _RiggingError(
                f"create_autoposing must be true or false (got {create_autoposing!r}).",
                hint="Pass true to create AutoPosing, false to skip it.",
            )
        if not isinstance(replace, bool):
            raise _RiggingError(
                f"replace must be true or false (got {replace!r}).",
                hint="Leave it false for a bare skeleton; pass true only to replace an existing rig.",
            )
        if not isinstance(enter_rig_mode, bool):
            raise _RiggingError(
                f"enter_rig_mode must be true or false (got {enter_rig_mode!r}).",
                hint="Leave it true in normal use; pass false only when the scene must already be in Rig mode.",
            )
        plane = mirror_plane_value(mirror_plane)
        templates = scan_templates()
    except ToolError as exc:
        return _fail(common, exc)

    try:
        app, view_scene, scene = common.scenes()
        joints = _joint_names(scene)
    except Exception as exc:
        return common.fail(
            f"Could not read the current scene before Quick Rigging: {exc}",
            hint="Check that a scene is open and that MCP Plus is healthy (get_scene_info).",
            log=common.log_tail(lines=40),
        )
    if not joints:
        return common.fail(
            "The scene has no Joint objects, so there is no skeleton to rig.",
            hint="Import a character FBX/GLB with import_model, or open a scene that contains joints.",
            animation_info=_animation_info_count(scene),
        )

    try:
        if template is None:
            selected, candidates, reason = choose_template(templates, joints, _scene_hints(view_scene, scene, joints))
            auto_selected = True
            if selected is None:
                raise _RiggingError(
                    f"Could not choose a Quick Rigging template for this skeleton. {reason}",
                    hint="Call list_rig_templates and pass template=... explicitly.",
                    candidates=candidates,
                )
        else:
            selected = _resolve_by_name(template, templates)
            candidates = []
            auto_selected = False
    except ToolError as exc:
        return _fail(common, exc)

    warnings: list[str] = []
    entered = False
    entry_path = None
    position = _log_position(common)
    tool = None
    ui = None
    baseline = set()
    autoposing_before = 0
    template_applied = False
    existing_rig = 0
    existing_proto = 0
    try:
        ui = _ui()
        baseline = _dialog_signatures(ui)
        animation_info = _animation_info_count(scene)
        rig_info = _rig_info_count(scene)
        existing_rig = rig_info
        existing_proto = _proto_count(scene)
        if (existing_rig or existing_proto) and not replace:
            raise _RiggingError(
                f"Refusing to rig: the scene already has {existing_rig} RigInfo and {existing_proto} prototype "
                "rig element(s). quick_rig is for a bare skeleton; running it here would finalize a half-made "
                "Rig or rebuild the existing one without saying so.",
                hint="Pass replace=true to rebuild the existing rig with the selected template, or open/import "
                "the bare skeleton in a new scene.",
                stage="existing_rig",
                animation_info=animation_info,
                rig_info=existing_rig,
                proto=existing_proto,
                replace=False,
            )
        if animation_info != 1:
            if not enter_rig_mode:
                raise _RiggingError(
                    f"Refusing to generate a rig: the scene has {animation_info} AnimationInfo, but Quick Rigging "
                    "needs exactly 1 (Rig mode). Cascadeur crashes when rig elements are generated outside Rig mode.",
                    hint="Omit enter_rig_mode=false so quick_rig enters Rig mode, or call enter_rig_mode first.",
                    stage="rig_mode_check",
                    animation_info=animation_info,
                    rig_info=rig_info,
                    rig_mode=False,
                )
            from .. import scene_tools

            entry = scene_tools.enter_rig_mode()
            if "error" in entry:
                raise _RiggingError(
                    f"Could not enter Rig mode: {entry['error']}",
                    hint="Resolve the scene's AnimationInfo/RigInfo state, then call quick_rig again.",
                    stage="enter_rig_mode",
                    animation_info=animation_info,
                    rig_info=rig_info,
                    rig_mode=False,
                )
            entered = True
            entry_path = entry.get("path")
            # Rig mode reloads the scene; take fresh handles before touching anything else.
            app, view_scene, scene = common.scenes()
            _require_rig_mode(scene, "start Quick Rigging")

        _answer_helper_dialog(ui, warnings)
        _require_rig_mode(scene, "open Quick Rigging")

        try:
            tool = _rigging_tool(app, view_scene)
        except Exception as exc:
            raise _RiggingError(
                f"Could not get Cascadeur's Quick Rigging tool: {exc}",
                hint="Check get_log; the tool name may differ in this Cascadeur version.",
                stage="get_tool",
                animation_info=_animation_info_count(scene),
            ) from exc

        autoposing_before = _autoposing_count(scene)
        autoposing_setting = None
        try:
            tool.open_quick_rigging_tool()
            tool.set_is_create_autoposing(bool(create_autoposing))
            if plane is not None:
                tool.set_character_mirror_plane(int(plane))
            try:
                autoposing_setting = bool(tool.get_is_create_autoposing())
            except Exception:
                autoposing_setting = None
            tool.load_template_by_fileName(_cascadeur_path(selected["path"]))
        except Exception as exc:
            raise _RiggingError(
                f"Quick Rigging could not load template {selected['name']!r}: {exc}",
                hint="Call list_rig_templates and pass a template whose joint names match this skeleton.",
                stage="load_template",
                template={"name": selected["name"], "path": selected["path"]},
                animation_info=_animation_info_count(scene),
            ) from exc

        _answer_recognition_dialogs(ui, warnings, baseline)
        warnings.append(
            f"After loading the template: proto={_proto_count(scene)}, rig_info={_rig_info_count(scene)}, "
            f"animation_info={_animation_info_count(scene)}."
        )

        # Cascadeur 2026.2.3 keeps the two Quick Rigging phases behind two entry points:
        # load_template_by_fileName maps the skeleton to the template, create_from_qrt_by_fileName
        # adds the prototype controls, and generate_rig_elements (or the confirmation dialog)
        # turns them into the final RigInfo.  The create call is made even for replace=true:
        # skipping it because proto elements exist is exactly how the old implementation
        # "succeeded" while never applying the selected template.
        _require_rig_mode(scene, "add rig elements")
        # Loading a template can reset the tool's options; set them again right before the
        # call that creates/finalizes the rig so AutoPosing and the mirror plane are honoured.
        tool.set_is_create_autoposing(bool(create_autoposing))
        if plane is not None:
            tool.set_character_mirror_plane(int(plane))
        proto_before_create = _proto_count(scene)
        rig_before_create = _rig_info_count(scene)
        create_position = _log_position(common)
        try:
            tool.create_from_qrt_by_fileName(_cascadeur_path(selected["path"]))
        except Exception as exc:
            raise _RiggingError(
                f"Quick Rigging could not create the rig elements from template {selected['name']!r}: {exc}",
                hint="Check get_log; the scene may need undo or a clean re-import. Cascadeur did not crash.",
                stage="create_from_qrt",
                template={"name": selected["name"], "path": selected["path"]},
                animation_info=_animation_info_count(scene),
            ) from exc
        _answer_recognition_dialogs(ui, warnings, baseline)
        create_commands = _commands_since(common, create_position)
        if (
            "Create rig from qrt" in create_commands
            or _proto_count(scene) > proto_before_create
            or _rig_info_count(scene) > rig_before_create
        ):
            template_applied = True
        warnings.append(
            f"After create_from_qrt_by_fileName: proto={_proto_count(scene)}, "
            f"rig_info={_rig_info_count(scene)}, animation_info={_animation_info_count(scene)}, "
            f"template_applied={template_applied}."
        )

        if _proto_count(scene) == 0 and _rig_info_count(scene) == 0:
            # Some builds expose the first phase only through generate_rig_elements().
            _require_rig_mode(scene, "add rig elements")
            proto_before_generate = _proto_count(scene)
            rig_before_generate = _rig_info_count(scene)
            try:
                tool.generate_rig_elements()
            except Exception as exc:
                raise _RiggingError(
                    f"Quick Rigging failed to add the rig elements: {exc}",
                    hint="Check get_log; the scene may need undo or a clean re-import. Cascadeur did not crash.",
                    stage="generate_rig_elements",
                    template={"name": selected["name"], "path": selected["path"]},
                    animation_info=_animation_info_count(scene),
                ) from exc
            _answer_recognition_dialogs(ui, warnings, baseline)
            if _proto_count(scene) > proto_before_generate or _rig_info_count(scene) > rig_before_generate:
                template_applied = True
            warnings.append(
                f"After generate_rig_elements: proto={_proto_count(scene)}, "
                f"rig_info={_rig_info_count(scene)}, animation_info={_animation_info_count(scene)}, "
                f"template_applied={template_applied}."
            )

        # generate_rig_elements() adds the prototype controls, then asks whether to create the
        # real rig.  That second step is the other crash point, so check Rig mode once more
        # immediately before clicking it.
        generate_dialog = _find_generate_dialog(ui, baseline)
        if generate_dialog is not None:
            _require_rig_mode(scene, "generate the rig")
            tool.set_is_create_autoposing(bool(create_autoposing))
            if plane is not None:
                tool.set_character_mirror_plane(int(plane))
            try:
                clicked = _click_generate(ui, baseline)
            except Exception as exc:
                raise _RiggingError(
                    f"Could not press Quick Rigging's 'Generate rig' button: {exc}",
                    hint="The rig elements are in the scene but in Rig mode. Use ui_tree/dialogs and click "
                    "'Generate rig' by hand, or undo and call quick_rig again.",
                    stage="click_generate",
                    template={"name": selected["name"], "path": selected["path"]},
                    animation_info=_animation_info_count(scene),
                ) from exc
            if not clicked:
                raise _RiggingError(
                    "The 'Rig elements have been added' dialog is open but no 'Generate rig' button could be found.",
                    hint="Inspect dialogs/ui_tree and press 'Generate rig' by hand; do not leave Rig mode through "
                    "other means.",
                    stage="click_generate",
                    template={"name": selected["name"], "path": selected["path"]},
                    animation_info=_animation_info_count(scene),
                )
        elif _rig_info_count(scene) == 0 and _proto_count(scene) > 0 and not _dialogs(ui):
            # Some builds add the elements without asking.  Leaving Rig mode through
            # rig_mode.off is the same final step the button runs, and it is safe here because
            # _leave_rig_mode checks AnimationInfo again immediately before the call.
            try:
                _leave_rig_mode(scene)
                warnings.append("Quick Rigging did not show its confirmation; left Rig mode through rig_mode.off.")
            except Exception as exc:
                raise _RiggingError(
                    f"Quick Rigging added the rig elements, but generating the final rig failed: {exc}",
                    hint="Check get_log; the scene is still in Rig mode with prototype elements, so undo before "
                    "retrying.",
                    stage="leave_rig_mode",
                    template={"name": selected["name"], "path": selected["path"]},
                    animation_info=_animation_info_count(scene),
                ) from exc
        else:
            warnings.append("Quick Rigging did not show its 'Generate rig' confirmation.")

        try:
            app, view_scene, scene = common.scenes()
        except Exception as exc:
            raise _RiggingError(
                f"Quick Rigging ran, but reading the scene afterwards failed: {exc}",
                hint="Check get_scene_info/get_log; if the rig is missing, undo and try again.",
                stage="read_after_generate",
                template={"name": selected["name"], "path": selected["path"]},
            ) from exc

        rig_info_after = _rig_info_count(scene)
        animation_info_after = _animation_info_count(scene)
        if not template_applied:
            _close_quick_rigging_window(ui, warnings)
            left = _dismiss_leftover_dialogs(ui, warnings, baseline)
            raise _RiggingError(
                "Quick Rigging did not apply the selected template: no rig elements were created and no RigInfo "
                "was generated.",
                hint="Check get_log. The scene was not finalized; undo any prototype elements and retry with a "
                "template that matches this skeleton.",
                stage="verify_template",
                template={"name": selected["name"], "path": selected["path"]},
                animation_info=animation_info_after,
                rig_info=rig_info_after,
                existing={"rig_info": existing_rig, "proto": existing_proto},
                dialogs=left,
            )
        if animation_info_after != 0:
            _close_quick_rigging_window(ui, warnings)
            left = _dismiss_leftover_dialogs(ui, warnings, baseline)
            raise _RiggingError(
                f"Quick Rigging did not leave Rig mode: the scene still has {animation_info_after} AnimationInfo.",
                hint="Check get_log and press the remaining Quick Rigging dialog by hand; do not report this as a "
                "finished rig. The scene may still have prototype elements, so undo before retrying.",
                stage="still_in_rig_mode",
                template={"name": selected["name"], "path": selected["path"]},
                animation_info=animation_info_after,
                rig_info=rig_info_after,
                existing={"rig_info": existing_rig, "proto": existing_proto},
                dialogs=left,
            )
        if rig_info_after != 1:
            _close_quick_rigging_window(ui, warnings)
            left = _dismiss_leftover_dialogs(ui, warnings, baseline)
            raise _RiggingError(
                f"Expected exactly one RigInfo after Quick Rigging, but the scene has {rig_info_after}.",
                hint="Check get_log. If several rigs were created, undo the extra ones; if none, retry from a clean "
                "skeleton import.",
                stage="verify_rig",
                template={"name": selected["name"], "path": selected["path"]},
                animation_info=animation_info_after,
                rig_info=rig_info_after,
                existing={"rig_info": existing_rig, "proto": existing_proto},
                dialogs=left,
            )

        # Close the tool/helper windows first: their QML trees contain duplicate dialog items and
        # walking them after a click can press the same button again (and reload the scene).
        stayed = _close_quick_rigging_window(ui, warnings)
        left = _dismiss_leftover_dialogs(ui, warnings, baseline)
        autoposing_after = _autoposing_count(scene)
        autoposing_active = _autoposing_active(ui)
        result = {
            "template": {
                "name": selected["name"],
                "path": selected["path"],
                "auto_selected": auto_selected,
                "matched": match_template(selected, joints)["matched"],
                "template_joints": len(selected["joints"]),
                "candidates": candidates,
            },
            "rig_mode": {
                "entered": entered,
                "path": entry_path,
                "animation_info": animation_info_after,
            },
            "mirror_plane": MIRROR_PLANE_NAMES.get(plane) if plane is not None else None,
            "rig": _character_summary(scene),
            "controllers": _controller_summary(scene),
            "autoposing": {
                "requested": bool(create_autoposing),
                "created": bool(create_autoposing and (autoposing_after > 0 or autoposing_active is True)),
                "behaviours": autoposing_after,
                "before": autoposing_before,
                "active": autoposing_active,
                "tool_setting": autoposing_setting,
            },
            "rig_info": rig_info_after,
            "animation_info": animation_info_after,
            "replace": bool(replace),
            "existing": {"rig_info": existing_rig, "proto": existing_proto},
            "template_applied": template_applied,
            "warnings": warnings,
        }
        if left:
            result["dialogs"] = left
            result["hint"] = "Some dialogs could not be closed; inspect them with dialogs and close them by hand."
        if stayed:
            result["windows"] = stayed
            result["hint"] = "The Quick Rigging window could not be closed; close it by hand."
        if not result["autoposing"]["created"] and create_autoposing:
            result["autoposing"]["note"] = (
                "AutoPosing was requested but no AutoPosing behaviour was counted; check the AutoPosing toggle in "
                "the Cascadeur interface (T15's get_autoposing_state is the authoritative check)."
            )
        commands = _commands_since(common, position)
        result["undo_commands"] = commands
        result["undo_steps"] = len(commands) if position is not None else None
        return common.ok(**result)
    except ToolError as exc:
        if ui is not None:
            try:
                # Close the tool window before walking dialogs: its QML tree contains duplicate
                # dialog items and walking it first can press the same button again.
                _close_quick_rigging_window(ui, warnings)
                _dismiss_leftover_dialogs(ui, warnings, baseline)
            except Exception:
                pass
        result = _fail(common, exc)
        result["warnings"] = warnings
        try:
            result["animation_info"] = _animation_info_count(scene)
            result["rig_info"] = _rig_info_count(scene)
        except Exception:
            pass
        commands = _commands_since(common, position)
        result["undo_commands"] = commands
        result["undo_steps"] = len(commands) if position is not None else None
        result["log"] = common.log_tail(lines=60)
        return result
    except Exception as exc:
        if ui is not None:
            try:
                _close_quick_rigging_window(ui, warnings)
                _dismiss_leftover_dialogs(ui, warnings, baseline)
            except Exception:
                pass
        result = common.fail(
            f"Quick Rigging failed unexpectedly: {exc}",
            hint="Check get_log. Cascadeur is still running; the scene may be left in Rig mode with prototype rig "
            "elements, so undo before retrying.",
            stage="unexpected",
            template={"name": selected["name"], "path": selected["path"]},
            warnings=warnings,
            log=common.log_tail(lines=80),
        )
        commands = _commands_since(common, position)
        result["undo_commands"] = commands
        result["undo_steps"] = len(commands) if position is not None else None
        return result


TOOLS = [
    {
        "name": "list_rig_templates",
        "description": "List the Quick Rigging templates installed with Cascadeur (resources/autorig_templates, "
        "*.qrigcasc and older *.qrt). Use it before quick_rig when the automatic template match is wrong or "
        "ambiguous. Returns each template's name, file, absolute path, format, whether it is readable, its joint "
        "count and body sections.",
        "inputSchema": schema({}),
        "fn": list_rig_templates,
    },
    {
        "name": "quick_rig",
        "description": "Create a Cascadeur rig (rig elements, control points and optionally AutoPosing) for a bare "
        "skeleton using Quick Rigging. It refuses a scene that already has a RigInfo or prototype rig elements, so "
        "it cannot silently finalize a half-made Rig or rebuild an existing one; pass replace=true to rebuild the "
        "existing rig with the selected template (the tool enters Rig mode, loads the template, and relies on Quick "
        "Rigging's replace-existing setting before finalizing). It first ensures the scene is in Rig mode and "
        "verifies that there is exactly one AnimationInfo; if not it returns an error instead of generating, "
        "because generating rig elements outside Rig mode crashes Cascadeur 2026.2.3. Then it loads a Quick Rigging "
        "template (the closest match to the scene's joint names, or the template you pass), adds the rig elements, "
        "presses 'Generate rig' and closes Quick Rigging. Success requires the selected template to have been "
        "applied, exactly one RigInfo, and zero AnimationInfo (Rig mode off). Returns the chosen template, the "
        "RigInfo/character, controller counts, whether AutoPosing was created, and the Cascadeur undo commands the "
        "call wrote. This is NOT one MCP undo step: Rig mode, rig-element creation and Rig mode off each record "
        "their own command. Use it after import_model on a skeleton with no rig; generate_rig_elements/Generate rig "
        "are never called unless AnimationInfo is exactly 1.",
        "inputSchema": schema(
            {
                "template": {
                    "type": "string",
                    "description": "Template name, file name or absolute .qrigcasc path (default: closest match).",
                },
                "mirror_plane": {
                    "type": ["string", "integer"],
                    "description": "Character mirror plane: XY, XZ, YZ or 0-3 (default: keep Quick Rigging's setting).",
                },
                "create_autoposing": {
                    "type": "boolean",
                    "description": "Create AutoPosing with the rig (default true).",
                },
                "replace": {
                    "type": "boolean",
                    "description": "Rebuild a scene that already has a RigInfo/prototype rig elements (default "
                    "false, which returns an error). Quick Rigging's replace-existing setting does the replacement.",
                },
                "enter_rig_mode": {
                    "type": "boolean",
                    "description": "Enter Rig mode when the scene is not in it (default true). Pass false only to "
                    "require that the scene is already in Rig mode; the tool refuses to generate otherwise.",
                },
                "timeout": {
                    "type": "number",
                    "description": "Seconds to wait for Quick Rigging (default 60, max 600).",
                },
            },
            [],
        ),
        "fn": quick_rig,
        "idle": True,
        "timeout_arg": True,
    },
]
