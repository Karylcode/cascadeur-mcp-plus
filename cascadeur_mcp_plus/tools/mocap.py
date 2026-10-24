"""Video motion capture (T26).

Cascadeur's video mocap has no Python API: View.MocapTool is a QML/C++ tool that reads the
scene's reference video, the selected object and the timeline interval. The reference video first
has to be imported as a scene plane (File > Import > Bind video, action "View.Bind video").
This module automates that fixed workflow as one T03 background job: the call returns a job id
immediately, and a QTimer poll on Cascadeur's GUI thread drives the Load video dialog, the native
file and folder pickers, the Import button, the selection, the confirmation, the mocap progress
window and the cleanup.

Cascadeur 2026.2.3 facts this module is built on (verified on the live application, see T26's
handoff notes):
* View.Bind video opens a QML window titled "Load video" (type LoadVideoDialog).
  Its viewModel property cannot be read from Python ("Can't find converter for
  view::textures::LoadVideoDialog*"), so every field is driven through the dialog's own controls.
* "Choose..." opens a native Windows #32770 dialog (edit control 1148 for the file, 1152 for the
  frames folder, button 1 = Open/Select). The module fills the edit and clicks the button with
  Win32 messages; PostMessage is used so the click runs after the poll returns.
* The default frames folder is "<scene folder>/<scene name> - clip", which is read-only for
  Cascadeur's bundled samples under Program Files. The module points it at a writable %TEMP%
  folder first, otherwise the import fails with "Couldn't create frames directory".
* View.MocapTool opens the "Mocap <scene>" progress window (type MocapDialog) and a "Mocap"
  confirmation ("Video-mocap for the selected interval of N frames will take approximately X.
  Proceed?") with Yes/No. The reference video plane's object is the one that must be selected.

Nothing here sleeps or blocks on the GUI thread; every wait is a poll tick. Cascadeur runs the
mocap itself on a worker thread, so the interface keeps answering while it runs.

Known limitations (see T26's handoff notes):
* mocap quality, duration, the completion/failure dialogs after a real run and the overwrite
  semantics of existing keys can only be checked with a real 3-5 s single-person video;
* from_frame/to_frame are typed into the dialog's frame fields (best effort); this path is not
  verified yet;
* Cascadeur writes its own undo step for the baked keys, which this module cannot merge with the
  import/delete steps, so one Ctrl+Z does not revert the whole call.
"""

from __future__ import annotations

import functools
import os
import re
import shutil
import time
import traceback

from .. import common, rig, timeline
from ..errors import ToolError
from ._schema import schema

MOCAP_ACTION = "View.MocapTool"
# File > Import > Bind video opens the Load Video dialog (Cascadeur 2026.2.3's File menu).
IMPORT_ACTIONS = ("View.Bind video", "add_videoreference")
LOAD_DIALOG_TYPE = "LoadVideoDialog"
MOCAP_DIALOG_TYPE = "MocapDialog"
NATIVE_FILE_EDIT = 1148
NATIVE_FOLDER_EDIT = 1152
BM_CLICK = 0x00F5
WM_COMMAND = 0x0111
# Relative to Cascadeur's per-user data folder (paths.data_dir()); see model_file().
MODEL_PARTS = ("resources", "mocap.pt")
MODEL_URL = "https://cdn.cascadeur.com/animations/files/17/mocap_010725.pt"
VIDEO_SUFFIX = ".mp4"

JOB_TIMEOUT_S = 6 * 3600  # safety net; the tool's own "timeout" argument is what callers use
DEFAULT_TIMEOUT_S = 600.0
DIALOG_TIMEOUT_S = 20.0
SOURCE_TIMEOUT_S = 30.0
IMPORT_TIMEOUT_S = 300.0
CONFIRM_TIMEOUT_S = 20.0
RESULT_GRACE_S = 3.0
CANCEL_WAIT_S = 20.0
DIALOG_POLL_TICKS = 3
LOG_POLL_TICKS = 5

# The cancel hook runs after jobs.py already stopped the timer, so it finishes the cleanup from
# a QTimer chain. Its report (partial keys left behind, which scene was touched) is stored here
# so a live test can read it through run_python: sys.
CANCEL_REPORT_ATTR = "_cascadeur_mcp_plus_mocap_cancel"

# Strings from tools.dll (MocapRequest/MocapViewModel) and presenter_lib.dll (LoadVideoDialog).
CONFIRM_MARKER = "video-mocap for the selected interval"
SUCCESS_MARKER = "all frames have been processed"
FAILED_FRAMES_MARKER = "frames containing unrecognized poses"
FAILED_MARKER = "mocap failed"
MODEL_MISSING_MARKER = "mocap model is not installed"
DOWNLOAD_MARKER = "to use this feature an additional"
CORRUPT_MARKER = "the downloaded data are corrupted"
SELECT_OBJECT_MARKER = "please select an object with an animation reference"
SELECT_FRAMES_MARKER = "selected frames do not contain the animation reference"
TEXTURE_MARKER = "selected textures are in different sizes"
IMPORT_ERROR_MARKERS = (
    "video decoding error",
    "couldn't create frames directory",
    "load video error",
)

PROCEED_LABELS = ("yes", "proceed", "ok", "continue")
CANCEL_LABELS = ("no", "cancel", "close")
OK_LABELS = ("ok", "close")

CONFIRM_ROLE = "confirm"
SUCCESS_ROLE = "success"
FAILED_FRAMES_ROLE = "failed_frames"
FAILED_ROLE = "failed"
MODEL_MISSING_ROLE = "model_missing"
DOWNLOAD_ROLE = "download"
CORRUPT_ROLE = "corrupt"
SELECT_OBJECT_ROLE = "select_object"
SELECT_FRAMES_ROLE = "select_frames"
TEXTURE_ROLE = "textures"
IMPORT_ERROR_ROLE = "import_error"

# MocapDialog.qml's stateText strings (MocapViewModel::State order).
MOCAP_STATES = (
    ("model checking", "check", 0.10),
    ("download start", "start_download", 0.15),
    ("downloading", "downloading", 0.20),
    ("preparing", "preparing", 0.35),
    ("model loading", "loading", 0.50),
    ("mocaping", "mocap", 0.60),
)

# Which object is selected when View.MocapTool runs. The reference video plane carries the
# "animation reference" (its texture frames), so it is the first choice; if Cascadeur asks for
# another object the job retries with the character and then with both selected.
TARGET_ORDERS = (("plane",), ("character",), ("plane", "character"))

# One job at a time: the T03 contract gives poll() no job id, so the running operation lives here.
_ACTIVE = None


# ---------------------------------------------------------------------------
# Errors and the tool wrapper
# ---------------------------------------------------------------------------


class _Error(ToolError):
    """A ToolError that also carries the result dictionary's hint and extra fields."""

    def __init__(self, message, hint=None, **data):
        super().__init__(message)
        self.hint = hint
        self.data = data


def _tool(hint: str):
    """Turn exceptions into the shared {"ok": false, "error", "hint"} result."""

    def decorate(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except _Error as exc:
                return common.fail(str(exc), exc.hint, **exc.data)
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


def _last_line() -> str:
    lines = [line.strip() for line in traceback.format_exc().strip().splitlines() if line.strip()]
    return lines[-1] if lines else "unknown error"


# ---------------------------------------------------------------------------
# Pure helpers (unit tested without Cascadeur)
# ---------------------------------------------------------------------------


def validate_video(video_path) -> str:
    """Return the normalized absolute .mp4 path, or raise ToolError explaining what is wrong."""
    if not video_path or not isinstance(video_path, str):
        raise _Error(
            "video_path must be the absolute path of an .mp4 file.",
            hint="Pass the full path, for example C:\\videos\\walk.mp4.",
        )
    normalized = os.path.normpath(video_path)
    if not os.path.isabs(normalized):
        raise _Error(
            f"video_path must be absolute (got {video_path!r}).",
            hint="Join the file name with its folder first.",
        )
    if os.path.splitext(normalized)[1].lower() != VIDEO_SUFFIX:
        raise _Error(
            f"Cascadeur's reference video import only accepts .mp4 files (got {normalized!r}).",
            hint="Convert the clip to .mp4 first; Cascadeur 2026.2.3 has no other video importer.",
        )
    if not os.path.isfile(normalized):
        raise _Error(
            f"Video file does not exist: {normalized!r}.",
            hint="Check the path; it must point at the .mp4 file itself, not its folder.",
        )
    try:
        size = os.path.getsize(normalized)
    except OSError as exc:
        raise _Error(f"The video file cannot be read: {exc}", hint="Check the file permissions.") from None
    if size <= 0:
        raise _Error(f"The video file is empty: {normalized!r}.", hint="Record or copy a valid clip first.")
    return normalized


def model_file(base=None) -> str:
    """Path of Cascadeur's mocap model inside its per-user data folder.

    On Windows that is %LOCALAPPDATA%\\Nekki Limited\\Cascadeur\\resources\\mocap.pt, the
    file Cascadeur downloads on the first mocap run; the other platforms use the data folder
    resolved by paths.data_dir() (T55). base overrides that data folder for tests and is the
    folder that also holds settings.json, resources/ and logs/.
    """
    if base is not None:
        root = base
    else:
        from .. import paths

        root = str(paths.data_dir())
    return os.path.normpath(os.path.join(root, *MODEL_PARTS))


def check_model(path=None) -> str:
    """Return the model path, or raise ToolError when the first-use download is missing."""
    found = model_file(path)
    if not os.path.isfile(found) or os.path.getsize(found) <= 0:
        raise _Error(
            f"Cascadeur's mocap model is not installed at {found!r}.",
            hint=(
                "Cascadeur downloads it on the first mocap run (about 825 MB, "
                f"{MODEL_URL}); open View > Mocap once with a video and let the download finish, "
                "or copy mocap.pt there."
            ),
            model=found,
            download_url=MODEL_URL,
        )
    return found


def parse_interval(start, end):
    """Return (first, last) from the start/end arguments; both or neither may be given.

    Frames use T14's parsing and limits so every timeline tool rejects bad input the same way.
    Neither argument means "use the timeline selection", which resolve_selection() validates.
    """
    given_start = start is not None and start != ""
    given_end = end is not None and end != ""
    if given_start != given_end:
        raise _Error(
            "Pass both start and end, or neither of them.",
            hint="Neither uses the timeline's selected interval; both set it explicitly.",
        )
    if not given_start:
        return None, None
    first = timeline.parse_frame(start, "start")
    last = timeline.parse_frame(end, "end")
    if first > last:
        raise _Error(
            f"start ({first}) must not be after end ({last}).",
            hint="Swap them; the interval is inclusive.",
        )
    return first, last


def resolve_selection(first, last, layer_count) -> tuple:
    """Validate the timeline selection read from the scene.

    layer_count is how many timeline layers are selected. Cascadeur's mocap works on the
    selected frames of the selected layers, so an empty layer selection is "no interval".
    """
    if not layer_count:
        raise _Error(
            "No timeline interval is selected, and start/end were not given.",
            hint=(
                "Select the frames to mocap on the timeline, or pass start and end. "
                "Use select_frames(start, end) to select an interval first."
            ),
        )
    if first is None or last is None:
        raise _Error(
            "Cascadeur did not report a selected frame interval.",
            hint="Select the frames on the timeline, or pass start and end.",
        )
    if first > last:
        raise _Error(
            f"The timeline selection is empty ({first} to {last}).",
            hint="Select at least one frame, or pass start and end.",
        )
    return int(first), int(last)


def parse_video_range(from_frame=None, to_frame=None):
    """Return the optional From/To clip range of the Load Video dialog as ints or None."""
    first = None if from_frame is None or from_frame == "" else timeline.parse_frame(from_frame, "from_frame")
    last = None if to_frame is None or to_frame == "" else timeline.parse_frame(to_frame, "to_frame")
    if first is not None and last is not None and first > last:
        raise _Error(
            f"from_frame ({first}) must not be after to_frame ({last}).",
            hint="The From/To fields trim the video; both are inclusive.",
        )
    return first, last


def clamp_video_range(nb_frames, first=None, last=None):
    """Clamp the requested From/To to 0..nb_frames-1 and return (first, last, clamped)."""
    try:
        count = int(nb_frames)
    except (TypeError, ValueError):
        count = 0
    if count <= 0:
        return first, last, False
    wanted_first = 0 if first is None else int(first)
    wanted_last = count - 1 if last is None else int(last)
    clamped_first = max(0, min(wanted_first, count - 1))
    clamped_last = max(clamped_first, min(wanted_last, count - 1))
    clamped = (clamped_first, clamped_last) != (wanted_first, wanted_last)
    return clamped_first, clamped_last, clamped


def dialog_text(dialog) -> str:
    """All visible text and button labels of a ui.dialogs() entry as one string."""
    texts = [str(item) for item in (dialog or {}).get("texts") or ()]
    texts.extend(
        str(button.get("text") or button.get("toolTip") or "") for button in (dialog or {}).get("buttons") or ()
    )
    return " ".join(part for part in texts if part).strip()


def dialog_role(text):
    """Classify a dialog/log text as one of the workflow signals, or None for unrelated text."""
    lowered = str(text or "").lower()
    if not lowered.strip():
        return None
    if FAILED_FRAMES_MARKER in lowered:
        return FAILED_FRAMES_ROLE
    if SUCCESS_MARKER in lowered:
        return SUCCESS_ROLE
    if CONFIRM_MARKER in lowered:
        return CONFIRM_ROLE
    if MODEL_MISSING_MARKER in lowered:
        return MODEL_MISSING_ROLE
    if CORRUPT_MARKER in lowered:
        return CORRUPT_ROLE
    if DOWNLOAD_MARKER in lowered:
        return DOWNLOAD_ROLE
    if SELECT_OBJECT_MARKER in lowered:
        return SELECT_OBJECT_ROLE
    if SELECT_FRAMES_MARKER in lowered:
        return SELECT_FRAMES_ROLE
    if TEXTURE_MARKER in lowered:
        return TEXTURE_ROLE
    if any(marker in lowered for marker in IMPORT_ERROR_MARKERS):
        return IMPORT_ERROR_ROLE
    if FAILED_MARKER in lowered:
        return FAILED_ROLE
    return None


def parse_failed_frames(text) -> list:
    """Frame numbers from "Frames containing unrecognized poses: 1, 3-5" (any separators)."""
    match = re.search(r"frames containing unrecognized poses\s*:?\s*\[?([^\]\n]*)", str(text or ""), re.IGNORECASE)
    if not match:
        return []
    frames = set()
    for item in re.finditer(r"(\d+)\s*[-\u2013]\s*(\d+)|(\d+)", match.group(1)):
        if item.group(1) is not None:
            first, last = int(item.group(1)), int(item.group(2))
            if first <= last and last - first <= 10000:
                frames.update(range(first, last + 1))
        elif item.group(3) is not None:
            frames.add(int(item.group(3)))
    return sorted(frames)


def shown_paths(texts) -> list:
    """Normalized paths (forward slashes, lower case) from the Load video dialog's texts."""
    return [str(text).replace("\\", "/").strip().casefold() for text in texts or ()]


def path_is_shown(texts, path) -> bool:
    """True when the dialog already displays path (Cascadeur shows forward slashes)."""
    wanted = str(path).replace("\\", "/").strip().casefold()
    return any(wanted in text for text in shown_paths(texts))


def video_frame_count(texts):
    """The Load video dialog's "frames: N" value, or None while it is not shown."""
    for text in texts or ():
        match = re.search(r"frames\s*:\s*(\d+)", str(text), re.IGNORECASE)
        if match:
            return int(match.group(1))
    return None


def mocap_state(texts):
    """(name, progress) for the MocapDialog's state text, or None for another dialog."""
    lowered = " ".join(str(text) for text in texts or ()).lower()
    for marker, name, progress in MOCAP_STATES:
        if marker in lowered:
            return name, progress
    return None


def import_progress(dialog):
    """Progress from the Import button's "Progress 37%" label, or None."""
    for button in (dialog or {}).get("buttons") or ():
        label = str(button.get("text") or "")
        match = re.search(r"(\d{1,3})\s*%", label)
        if match:
            return max(0.0, min(1.0, int(match.group(1)) / 100.0))
    return None


def _matches(character: dict, query: str) -> bool:
    """True when query names this character (name, root joint, info object or mesh)."""
    wanted = str(query).strip().casefold()
    if not wanted:
        return False
    names = [character.get("name"), character.get("root_joint"), character.get("info")]
    names.extend(character.get("meshes") or ())
    return any(str(name).casefold() == wanted for name in names if name)


def _partially_matches(character: dict, query: str) -> bool:
    wanted = str(query).strip().casefold()
    if not wanted:
        return False
    names = [character.get("name"), character.get("root_joint"), character.get("info")]
    names.extend(character.get("meshes") or ())
    return any(wanted in str(name).casefold() for name in names if name)


def choose_character(query, characters) -> dict:
    """Pick one character from rig.characters() output; raises ToolError listing the candidates.

    When query is None the scene must contain exactly one character (0 or several are errors,
    never a guess).
    """
    entries = [dict(entry) for entry in characters or ()]
    if not entries:
        raise _Error(
            "The scene has no character with a rig, so there is nothing to mocap onto.",
            hint=(
                "Open a scene with a rigged character (for example Cascadeur's Cascy.casc) and leave "
                "Rig mode, or use Quick Rigging first."
            ),
        )
    if query is None or str(query).strip() == "":
        if len(entries) == 1:
            return entries[0]
        names = [entry.get("name") for entry in entries]
        raise _Error(
            f"The scene has {len(entries)} characters; pass character= to choose one.",
            hint="Pass one of the character names from list_characters.",
            candidates=names,
        )
    exact = [entry for entry in entries if _matches(entry, query)]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        raise _Error(
            f"{query!r} matches {len(exact)} characters; use a unique name.",
            hint="Use the character name from list_characters (not a mesh shared by several rigs).",
            candidates=[entry.get("name") for entry in exact],
        )
    partial = [entry for entry in entries if _partially_matches(entry, query)]
    if len(partial) == 1:
        return partial[0]
    names = [entry.get("name") for entry in entries]
    if partial:
        raise _Error(
            f"{query!r} matches {len(partial)} characters; use a unique name.",
            hint="Pass one of the candidates.",
            candidates=[entry.get("name") for entry in partial],
        )
    raise _Error(
        f"Character not found: {query!r}.",
        hint="Call list_characters to see the scene's characters.",
        candidates=names,
    )


def new_object_ids(before_ids, after_ids) -> list:
    """Object ids present in after_ids but not in before_ids (order kept)."""
    before = {str(object_id) for object_id in before_ids}
    return [object_id for object_id in after_ids if str(object_id) not in before]


# ---------------------------------------------------------------------------
# Qt access (GUI thread only)
# ---------------------------------------------------------------------------


def _now() -> float:
    return time.monotonic()


def _schedule(callback) -> None:
    """Run callback on the next GUI event-loop turn (unit tests replace this)."""
    from PySide6 import QtCore

    QtCore.QTimer.singleShot(0, callback)


def _dialogs() -> list:
    """Visible dialogs and popups (ui.dialogs), or [] when the QML tree cannot be walked."""
    try:
        from .. import ui

        return list(ui.dialogs().get("dialogs") or [])
    except Exception:
        return []


def _click(ref) -> bool:
    try:
        from .. import ui

        ui.click(ref=ref)
        return True
    except Exception:
        return False


def _dialog_of_type(type_name):
    """The first dialog entry of one QML type (LoadVideoDialog / MocapDialog)."""
    for dialog in _dialogs():
        if dialog.get("kind") == "dialog" and str(dialog.get("type") or "") == type_name:
            return dialog
    return None


def _load_video_dialog():
    return _dialog_of_type(LOAD_DIALOG_TYPE)


def _mocap_dialog():
    return _dialog_of_type(MOCAP_DIALOG_TYPE)


def _button_label(button) -> str:
    return str(button.get("text") or button.get("toolTip") or "").strip()


def _buttons(dialog) -> list:
    return list((dialog or {}).get("buttons") or ())


def _choose_buttons(dialog) -> list:
    """The dialog's "Choose..." buttons in QML order (source first, frames folder second)."""
    return [button for button in _buttons(dialog) if "choose" in _button_label(button).casefold()]


def _click_label(dialog, labels):
    """Click the first button whose label equals or contains one of labels."""
    wanted = [label.casefold() for label in labels]
    buttons = _buttons(dialog)
    for button in buttons:
        label = _button_label(button)
        if label.casefold() in wanted and _click(button["ref"]):
            return label
    for button in buttons:
        label = _button_label(button)
        if any(want in label.casefold() for want in wanted) and _click(button["ref"]):
            return label
    return None


def _click_choose(dialog, index: int):
    """Click the index-th Choose... button; returns its label or None."""
    buttons = _choose_buttons(dialog)
    if index < 0 or index >= len(buttons):
        return None
    label = _button_label(buttons[index])
    return label if _click(buttons[index]["ref"]) else None


def _close_load_window(state=None) -> bool:
    """Close the Load video window this job opened, visible or hidden behind a native picker.

    Only the window recorded in state is closed: a Load video window the user opened stays
    untouched. The QML window has no Cancel button; its title-bar close is QWindow.close().
    Hidden windows are included because the window hides itself while a native picker is open.
    """
    if state is None:
        return False
    wanted = state.get("load_window_id")
    if wanted is None:
        return False
    closed = False
    for window in _quick_windows():
        if "load video" not in _window_title(window).lower():
            continue
        if _window_id(window) != wanted:
            continue
        try:
            # Callers check _load_window_pickers() first: destroying a window with a modal
            # picker on top crashes Cascadeur (2026.10.24), but with no picker left the window
            # is safe to close even while it is hidden.
            window.close()
            closed = True
        except Exception:
            pass
    return closed


def _log_size():
    try:
        return os.path.getsize(common.LOG_PATH)
    except OSError:
        return None


def _read_log(state) -> list:
    """New Cascadeur log lines since the previous read; keeps state["log"] for the result."""
    since = state.get("log_offset")
    try:
        lines = common.log_tail(since=since, lines=200)
    except Exception:
        lines = []
    state["log_offset"] = _log_size()
    if lines:
        state["log"].extend(str(line) for line in lines)
        state["log"] = state["log"][-40:]
    return lines


# ---------------------------------------------------------------------------
# Native Windows file/folder pickers (Cascadeur's Qt platform dialogs)
# ---------------------------------------------------------------------------


def _native_dialogs() -> list:
    """Visible #32770 dialogs of this process with the file-name (1148) or folder (1152) edit."""
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
    except Exception:
        return []
    try:
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user32.GetDlgItem.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.GetDlgItem.restype = wintypes.HWND
    except Exception:
        pass
    current_pid = os.getpid()
    found = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def callback(hwnd, lparam):
        try:
            if not user32.IsWindowVisible(hwnd):
                return True
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value != current_pid:
                return True
            class_name = ctypes.create_unicode_buffer(64)
            user32.GetClassNameW(hwnd, class_name, 64)
            if class_name.value != "#32770":
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            title = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, title, length + 1)
            for kind, control_id in (("file", NATIVE_FILE_EDIT), ("folder", NATIVE_FOLDER_EDIT)):
                edit = user32.GetDlgItem(hwnd, control_id)
                if edit:
                    found.append({"kind": kind, "hwnd": int(hwnd), "edit": int(edit), "title": title.value})
        except Exception:
            pass
        return True

    try:
        user32.EnumWindows(callback_type(callback), 0)
    except Exception:
        return found
    return found


def _native_of_kind(kind: str):
    for dialog in _native_dialogs():
        if dialog["kind"] == kind:
            return dialog
    return None


def _remember_native(state, native) -> None:
    """Remember the HWND of a native picker this job is driving, so cleanup only touches ours."""
    try:
        hwnd = int(native.get("hwnd") or 0)
    except (TypeError, ValueError):
        return
    if not hwnd:
        return
    owned = state.setdefault("native_hwnds", [])
    if hwnd not in owned:
        owned.append(hwnd)


def _set_native_text(dialog, text) -> bool:
    """Put text in a native picker's file-name/folder edit (no window activation needed)."""
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        user32.SetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPCWSTR]
        user32.SetWindowTextW.restype = wintypes.BOOL
        return bool(user32.SetWindowTextW(dialog["edit"], str(text)))
    except Exception:
        return False


def _post_native_button(dialog, control_id: int) -> bool:
    """Post a click to a native picker's button (1 = Open/Select, 2 = Cancel)."""
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        user32.GetDlgItem.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.GetDlgItem.restype = wintypes.HWND
        user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        user32.PostMessageW.restype = wintypes.BOOL
        button = user32.GetDlgItem(dialog["hwnd"], control_id)
        return bool(user32.PostMessageW(button, BM_CLICK, 0, 0)) if button else False
    except Exception:
        return False


def _click_native_ok(dialog) -> bool:
    return _post_native_button(dialog, 1)


def _click_native_cancel(dialog) -> bool:
    return _post_native_button(dialog, 2)


# ---------------------------------------------------------------------------
# Scene helpers (GUI thread)
# ---------------------------------------------------------------------------


def _characters() -> list:
    try:
        return list(rig.characters())
    except ToolError:
        raise
    except Exception as exc:  # noqa: BLE001 - report the API failure as a tool error
        raise _Error(
            f"Cascadeur could not list the scene's characters: {type(exc).__name__}: {exc}",
            hint="Check that a scene is open and that no modal dialog is blocking Cascadeur.",
        ) from None


def _character_object_id(character, model_viewer=None):
    """ObjectId of the character's info object (or root joint / mesh), or None."""
    if model_viewer is None:
        _app, _view_scene, scene = common.scenes()
        model_viewer = scene.model_viewer()
    names = [character.get("info"), character.get("root_joint")]
    names.extend(character.get("meshes") or ())
    for name in names:
        if not name:
            continue
        try:
            ids = [item for item in model_viewer.get_objects(str(name)) if not item.is_null()]
        except Exception:
            ids = []
        if ids:
            return ids[0]
    return None


def _timeline_selection(scene) -> tuple:
    """(first, last, selected layer count) of the timeline selection."""
    selector = scene.get_layers_selector()
    try:
        layers = list(selector.all_included_layer_ids())
    except Exception:
        layers = []
    try:
        interval = selector.selection().frames_interval()
        first, last = int(interval.first), int(interval.last)
    except Exception:
        first, last = None, None
    return first, last, len(layers)


def _selection_state(scene) -> dict:
    """Save the object selection, timeline selection and playhead (best effort)."""
    state = {"ids": None, "layers": None, "first": None, "last": None, "frame": None}
    try:
        selection = scene.selector().selected()
        state["ids"] = list(getattr(selection, "ordered_ids", None) or selection.ids)
    except Exception:
        pass
    try:
        selector = scene.get_layers_selector()
        state["layers"] = list(selector.all_included_layer_ids())
        interval = selector.selection().frames_interval()
        state["first"] = int(interval.first)
        state["last"] = int(interval.last)
    except Exception:
        pass
    try:
        state["frame"] = int(scene.get_current_frame(False))
    except Exception:
        pass
    return state


def _null_object_id():
    """csc.model.ObjectId.null(), or None outside Cascadeur (unit tests)."""
    try:
        import csc

        return csc.model.ObjectId.null()
    except Exception:
        return None


def _existing_model_ids(scene, object_ids) -> list:
    """Keep only ids that are model objects of this scene right now.

    A saved selection or a plane id can go stale (the plane was deleted, the scene was
    reloaded, or the id is a tool object such as an AutoPosing point). Selecting a stale
    ObjectId is what can crash Cascadeur, so everything that calls selector().select() is
    filtered through here first.
    """
    if not object_ids:
        return []
    try:
        current = {str(object_id) for object_id in scene.model_viewer().get_objects()}
    except Exception:
        return []
    return [object_id for object_id in object_ids if str(object_id) in current]


def _select(scene, object_ids) -> list:
    """Replace the object selection with the ids that still exist (T13's select contract)."""
    ids = _existing_model_ids(scene, list(object_ids or ()))
    pivot = ids[0] if ids else _null_object_id()
    scene.selector().select(set(ids), pivot)
    return ids


def _restore_selection(scene, saved) -> dict:
    """Put the caller's object selection, timeline selection and playhead back (best effort)."""
    try:
        if saved.get("ids") is not None:
            # Only objects that still exist: a saved tool object or the deleted reference plane
            # must never be handed to selector().select().
            _select(scene, saved["ids"])
        if saved.get("layers") is not None and saved.get("first") is not None:
            scene.get_layers_selector().set_full_selection_by_parts(
                list(saved["layers"]), int(saved["first"]), int(saved["last"])
            )
        if saved.get("frame") is not None and int(scene.get_current_frame(False)) != int(saved["frame"]):
            scene.set_current_frame(int(saved["frame"]))
    except Exception:
        return {"restored": False, "warning": "Cascadeur did not restore the previous selection: " + _last_line()}
    return {"restored": True}


def _object_ids(scene) -> list:
    """Every scene object id (cheap enough to diff on every poll tick)."""
    return list(scene.model_viewer().get_objects())


def _key_coverage(scene, object_ids, first: int, last: int) -> dict:
    """Keys of the character's layers inside first..last."""
    viewer = scene.layers_viewer()
    try:
        layer_ids = timeline.layer_ids_for_viewer(viewer, object_ids)
    except Exception:
        layer_ids = []
    frames = set()
    keyed_layers = 0
    for layer_id in layer_ids:
        try:
            layer = viewer.layer(layer_id)
        except Exception:
            continue
        try:
            keys = [frame for frame in timeline.key_frames(layer) if first <= int(frame) <= last]
        except Exception:
            keys = []
        if keys:
            keyed_layers += 1
            frames.update(keys)
    return {
        "keyframes": len(frames),
        "key_frames": sorted(frames),
        "keyed_layers": keyed_layers,
        "layers": len(layer_ids),
    }


def _character_controllers(character_name):
    """Controller ObjectIds of one character, or every controller when it cannot be resolved."""
    try:
        return [entry["id"] for entry in rig.controllers(character=character_name)]
    except Exception:
        return timeline.target_object_ids()


# ---------------------------------------------------------------------------
# Job state
# ---------------------------------------------------------------------------


def _active():
    return _ACTIVE


def _clear_active() -> None:
    global _ACTIVE
    _ACTIVE = None


# ---------------------------------------------------------------------------
# The scene tab this job belongs to
# ---------------------------------------------------------------------------


def _quick_windows() -> list:
    """Visible and hidden QQuickWindows (unit tests replace this)."""
    try:
        from .. import ui

        return list(ui.quick_windows(include_hidden=True))
    except Exception:
        return []


def _window_id(window):
    try:
        return int(window.winId())
    except Exception:
        return None


def _window_title(window) -> str:
    try:
        return str(window.title())
    except Exception:
        return ""


def _valid_qt(object_) -> bool:
    """shiboken validity when it is available, otherwise assume the wrapper is still alive."""
    try:
        import shiboken6

        return bool(shiboken6.isValid(object_))
    except Exception:
        return True


def _same_view_scene(first, second) -> bool:
    if first is second:
        return True
    try:
        import shiboken6

        return shiboken6.getCppPointer(first)[0] == shiboken6.getCppPointer(second)[0]
    except Exception:
        return False


def _scene_status(state):
    """("ok" | "switched" | "closed", reason) for the tab the job started on.

    The job must never touch another tab: every scene read and write is checked against this.
    """
    target = state.get("view_scene")
    if target is None or not _valid_qt(target):
        return "closed", "the scene tab this job started on was closed"
    try:
        _app, current, _domain = common.scenes()
    except Exception:
        return "switched", "Cascadeur has no current scene"
    if current is None or not _same_view_scene(target, current):
        return "switched", "another scene tab is current"
    return "ok", ""


def _scene_for_writes(state):
    """(domain scene, status) of the job's tab when it is safe to write there, else (None, status)."""
    status, _reason = _scene_status(state)
    if status != "ok":
        return None, status
    try:
        return state["view_scene"].domain_scene(), status
    except Exception:
        return None, "closed"


def _load_video_window():
    """The visible or hidden Load video window, or None."""
    for window in _quick_windows():
        if "load video" in _window_title(window).lower():
            return window
    return None


def _load_window_waiting(state) -> bool:
    """True when our Load video window exists but is hidden, i.e. its native picker is still up.

    Cascadeur hides the QML window while the modal file/folder picker is open; destroying the
    window in that state crashes the application (measured on 2026.10.24), so no cleanup may
    close it until it is visible again (or has gone away by itself).
    """
    wanted = state.get("load_window_id")
    if wanted is None:
        return False
    for window in _quick_windows():
        if _window_id(window) != wanted:
            continue
        try:
            return not bool(window.isVisible())
        except Exception:
            return False
    return False


def _remember_load_window(state) -> None:
    """Record which Load video window this job opened (never one that was already open)."""
    preexisting = state.get("preexisting_windows") or set()
    candidates = []
    for window in _quick_windows():
        if "load video" not in _window_title(window).lower():
            continue
        identifier = _window_id(window)
        if identifier is None or identifier in preexisting:
            continue
        candidates.append(identifier)
    if len(candidates) == 1:
        state["load_window_id"] = candidates[0]


def _schedule_later(milliseconds: int, callback) -> None:
    """Run callback after a delay on the GUI thread (unit tests replace this)."""
    from PySide6 import QtCore

    QtCore.QTimer.singleShot(int(milliseconds), callback)


# ---------------------------------------------------------------------------
# Load video dialog: open it, choose the file, choose the frames folder, import
# ---------------------------------------------------------------------------


def _open_import_dialog(state) -> None:
    """call_action for the reference-video import; runs on the next event-loop turn."""
    errors = []
    try:
        app = common.scenes()[0]
        manager = app.get_action_manager()
    except Exception:
        state["phase"] = "failed"
        state["error"] = "Cascadeur did not expose its action manager: " + _last_line()
        return
    for key in IMPORT_ACTIONS:
        try:
            manager.call_action(key)
        except Exception as exc:  # noqa: BLE001 - try the next candidate
            errors.append(f"{key}: {type(exc).__name__}: {exc}")
            continue
        state["import_action"] = key
        state["import_errors"] = errors
        return
    state["phase"] = "failed"
    state["import_errors"] = errors
    state["error"] = "Cascadeur did not open the reference-video dialog (" + "; ".join(errors) + ")."


def _phase_open_dialog(state, now: float) -> dict:
    """Wait for the Load video window, then press the source "Choose..." button."""
    if now - state["phase_started"] > DIALOG_TIMEOUT_S:
        return _abort(
            state,
            "Cascadeur did not open the Load video dialog.",
            hint=(
                "Check that File > Import > Bind video works by hand, and that no other dialog is blocking Cascadeur."
            ),
            action=state.get("import_action"),
            action_errors=state.get("import_errors"),
        )
    dialog = _load_video_dialog()
    if dialog is None:
        return {"done": False, "progress": 0.03}
    state["load_dialog_seen"] = True
    _remember_load_window(state)
    index = state.get("choose_index", 0)
    label = _click_choose(dialog, index)
    if label is None:
        return {"done": False, "progress": 0.04}
    state.setdefault("clicks", []).append(f"Choose[{index}]")
    state["phase"] = "file_dialog"
    state["phase_started"] = now
    return {"done": False, "progress": 0.05}


def _phase_file_dialog(state, now: float) -> dict:
    """Fill the native file picker with the .mp4 path and press Open."""
    native = _native_of_kind("file")
    if native is not None:
        state["native_seen"] = True
        _remember_native(state, native)
        if not _set_native_text(native, state["video"]):
            _click_native_cancel(native)
            return _abort(
                state,
                "Cascadeur's file picker did not accept the video path.",
                hint="Choose the .mp4 by hand once and report the dialog's controls.",
                native=native,
            )
        state["native_file"] = True
        _click_native_ok(native)
        state["native_clicked_at"] = now
        state["phase"] = "file_set"
        state["phase_started"] = now
        return {"done": False, "progress": 0.08}
    folder = _native_of_kind("folder")
    if folder is not None:
        # The frames folder button opened first; cancel it and try the other Choose button.
        _click_native_cancel(folder)
        state["choose_index"] = 1 - state.get("choose_index", 0)
        state["phase"] = "open_dialog"
        state["phase_started"] = now
        return {"done": False, "progress": 0.04}
    if now - state["phase_started"] > DIALOG_TIMEOUT_S:
        return _abort(
            state,
            "Cascadeur's file picker did not open after Choose...",
            hint="Pick the .mp4 by hand once and check that the picker is a normal Windows dialog.",
            dialog=state.get("dialog_texts"),
        )
    return {"done": False, "progress": 0.06}


def _phase_file_set(state, now: float) -> dict:
    """Wait until the dialog shows the video, then press the frames folder "Choose..."."""
    dialog = _load_video_dialog()
    if dialog is None:
        # The QML window hides while its native picker is up; give it time to come back.
        if now - state.get("native_clicked_at", 0.0) < DIALOG_TIMEOUT_S:
            return {"done": False, "progress": 0.09}
        return _abort(
            state,
            "Cascadeur's Load video dialog closed before the video was accepted.",
            hint="Retry; if it happens again, import the video by hand and check Cascadeur's log.",
            action=state.get("import_action"),
        )
    state["dialog_texts"] = [str(text)[:200] for text in dialog.get("texts") or ()]
    _remember_load_window(state)
    texts = dialog.get("texts") or ()
    frames = video_frame_count(texts)
    if path_is_shown(texts, state["video"]) and frames:
        state["video_frames"] = frames
        index = 1 if len(_choose_buttons(dialog)) > 1 else 0
        label = _click_choose(dialog, index)
        if label is None:
            return {"done": False, "progress": 0.10}
        state.setdefault("clicks", []).append(f"Choose[{index}]")
        state["phase"] = "folder_dialog"
        state["phase_started"] = now
        return {"done": False, "progress": 0.10}
    if now - state["phase_started"] > SOURCE_TIMEOUT_S:
        return _abort(
            state,
            "Cascadeur did not read the video: the dialog does not show its frames.",
            hint=(
                "Check that the .mp4 decodes in a player and that the file picker accepted it; "
                "the dialog's text is in the result."
            ),
            dialog=state.get("dialog_texts"),
        )
    return {"done": False, "progress": 0.09}


def _phase_folder_dialog(state, now: float) -> dict:
    """Fill the native folder picker with the writable frames folder and press OK."""
    native = _native_of_kind("folder")
    if native is not None:
        state["native_seen"] = True
        _remember_native(state, native)
        if not _set_native_text(native, state["frames_dir"]):
            _click_native_cancel(native)
            return _abort(
                state,
                "Cascadeur's folder picker did not accept the frames folder.",
                hint="Pick a folder by hand once and report the dialog's controls.",
                frames_dir=state["frames_dir"],
            )
        state["native_folder"] = True
        _click_native_ok(native)
        state["native_clicked_at"] = now
        state["phase"] = "folder_set"
        state["phase_started"] = now
        return {"done": False, "progress": 0.12}
    file_dialog = _native_of_kind("file")
    if file_dialog is not None:
        _click_native_cancel(file_dialog)
        state["choose_index"] = 1 - state.get("choose_index", 0)
        state["phase"] = "file_dialog"
        state["phase_started"] = now
        return {"done": False, "progress": 0.09}
    if now - state["phase_started"] > DIALOG_TIMEOUT_S:
        return _abort(
            state,
            "Cascadeur's folder picker did not open after Choose...",
            hint="Pick the frames folder by hand once and check that the picker is a normal Windows dialog.",
            dialog=state.get("dialog_texts"),
        )
    return {"done": False, "progress": 0.11}


def _phase_folder_set(state, now: float) -> dict:
    """Wait until the frames folder is shown, then press Import."""
    dialog = _load_video_dialog()
    if dialog is None:
        if now - state.get("native_clicked_at", 0.0) < DIALOG_TIMEOUT_S:
            return {"done": False, "progress": 0.12}
        return _abort(
            state,
            "Cascadeur's Load video dialog closed before the import started.",
            hint="Retry; if it happens again, import the video by hand and check Cascadeur's log.",
        )
    texts = dialog.get("texts") or ()
    state["dialog_texts"] = [str(text)[:200] for text in texts]
    if not path_is_shown(texts, state["frames_dir"]):
        if now - state["phase_started"] > SOURCE_TIMEOUT_S:
            return _abort(
                state,
                "Cascadeur did not take the frames folder.",
                hint="The import needs a writable frames folder; the dialog's text is in the result.",
                dialog=state.get("dialog_texts"),
            )
        return {"done": False, "progress": 0.13}
    if not state.get("trimmed"):
        state["trimmed"] = _set_video_range(state.get("from_frame"), state.get("to_frame"), state)
        state["phase_started"] = now
    label = _click_label(dialog, ("import",))
    if label is None:
        if now - state["phase_started"] > DIALOG_TIMEOUT_S:
            return _abort(
                state,
                "Cascadeur's Load video dialog has no Import button.",
                hint="Inspect the dialog by hand; its text is in the result.",
                dialog=state.get("dialog_texts"),
            )
        return {"done": False, "progress": 0.14}
    state.setdefault("clicks", []).append(label)
    state["import_clicked"] = now
    state["phase"] = "importing"
    state["phase_started"] = now
    return {"done": False, "progress": 0.16}


def _frame_fields(window_title: str = "Load video") -> list:
    """The Load video dialog's two plain-integer frame inputs (From above To).

    The duration fields show 00:00.0 and the source/frames path fields show a path, so only the
    From/To frame inputs are plain integers. The window filter keeps the main window's timeline
    frame fields out. Returns [] unless there are exactly two candidates.
    """
    try:
        from .. import ui

        tree = ui.tree(window=window_title, limit=400)
    except Exception:
        return []
    inputs = [
        element
        for element in tree.get("elements") or ()
        if element.get("input")
        and isinstance(element.get("rect"), list)
        and str(element.get("text") or "").strip().isdigit()
    ]
    inputs.sort(key=lambda element: (element["rect"][1], element["rect"][0]))
    return inputs if len(inputs) == 2 else []


def _set_video_range(first, last, state) -> bool:
    """Type the optional From/To frames into the dialog's frame fields (best effort).

    Enter is not pressed: the Import button is the dialog's default action and an Enter keypress
    could start the import while this function is still typing. Leaving the field drops focus,
    which commits the value (the QML handler runs on editingFinished).
    """
    if first is None and last is None:
        return True
    inputs = _frame_fields()
    if not inputs:
        state.setdefault("warnings", []).append(
            "from_frame/to_frame could not be typed into the Load video dialog (its two frame "
            "fields were not found); the whole clip was used."
        )
        return True
    try:
        from .. import ui
    except Exception:
        return False
    wanted = [frame for frame in (first, last) if frame is not None]
    try:
        for frame, element in ((first, inputs[0]), (last, inputs[1])):
            if frame is None:
                continue
            ui.type_text(
                text=str(int(frame)),
                ref=element["ref"],
                replace=True,
                submit=False,
                release_focus=True,
            )
    except Exception:
        state.setdefault("warnings", []).append(
            "from_frame/to_frame could not be typed into the Load video dialog; the whole clip was used."
        )
        return True
    values = [int(str(element.get("text") or "0").strip()) for element in _frame_fields()]
    if not set(wanted).issubset(set(values)):
        state.setdefault("warnings", []).append(
            f"from_frame/to_frame did not stick in the Load video dialog (typed {wanted}, fields read "
            f"{values}); Cascadeur may have used the whole clip."
        )
        return True
    state["range_typed"] = True
    return True


def _phase_importing(state, now: float) -> dict:
    """Wait for the plane to appear (or the dialog to report an error)."""
    for dialog in _dialogs():
        role = dialog_role(dialog_text(dialog))
        if role == IMPORT_ERROR_ROLE:
            _click_label(dialog, OK_LABELS)
            return _abort(
                state,
                "Cascadeur could not import the video: " + dialog_text(dialog)[:400],
                hint=("The frames folder must be writable and the .mp4 must decode. Cascadeur's text is in 'dialog'."),
                dialog=dialog_text(dialog)[:800],
            )
    dialog = _load_video_dialog()
    fraction = import_progress(dialog) if dialog is not None else None
    scene, _status = _scene_for_writes(state)
    if scene is None:
        return {"done": False, "progress": state.get("last_progress") or 0.2}
    plane = _plane_ready(state, scene)
    if plane is not None:
        return _after_import(state, plane)
    if dialog is None and state.get("import_clicked") and now - state["import_clicked"] < 2.0:
        return {"done": False, "progress": 0.4}
    if now - state["phase_started"] > IMPORT_TIMEOUT_S:
        return _abort(
            state,
            "Cascadeur did not finish importing the reference video.",
            hint="The video may still be decoding; check Cascadeur's window, then retry with a shorter clip.",
            dialog=state.get("dialog_texts"),
        )
    if fraction is not None:
        return {"done": False, "progress": 0.16 + 0.3 * fraction}
    return {"done": False, "progress": min(0.45, 0.16 + (now - state["phase_started"]) / IMPORT_TIMEOUT_S * 0.3)}


def _plane_ready(state, scene):
    """The new reference-video plane, or None until the same new object id is seen twice."""
    new_ids = new_object_ids(state["before_ids"], _object_ids(scene))
    if not new_ids:
        return None
    seen = state.get("plane_seen", 0) + 1
    state["plane_seen"] = seen
    if seen < 2:
        return None
    model_viewer = scene.model_viewer()
    rows = []
    for object_id in new_ids:
        try:
            name = model_viewer.get_object_name(object_id)
        except Exception:
            name = ""
        try:
            type_name = model_viewer.get_object_type_name(object_id)
        except Exception:
            type_name = ""
        rows.append({"id": object_id, "name": name, "type": type_name})
    rows.sort(key=lambda row: 0 if "mesh" in str(row["type"]).lower() else 1)
    state["new_objects"] = [{"name": row["name"], "type": row["type"], "id": str(row["id"])} for row in rows]
    return rows[0] if rows else None


def _after_import(state, plane) -> dict:
    """The plane exists: close the import window, select the interval and trigger the mocap."""
    _close_load_window(state)
    state["plane_id"] = plane["id"]
    state["plane_name"] = plane["name"]
    state["plane_type"] = plane["type"]
    if state.get("video_frames"):
        first = 0 if state.get("from_frame") is None else int(state["from_frame"])
        last = int(state["video_frames"]) - 1 if state.get("to_frame") is None else int(state["to_frame"])
        first, last, clamped = clamp_video_range(state["video_frames"], first, last)
        state["video_first"], state["video_last"] = first, last
        if clamped and not state.get("range_typed"):
            state.setdefault("warnings", []).append(
                f"The requested From/To was outside the video ({state['video_frames']} frames); "
                f"Cascadeur used {first}-{last}."
            )
    state["phase"] = "triggering"
    state["phase_started"] = _now()
    try:
        _schedule(lambda: _trigger(state))
    except Exception:
        return _abort(
            state,
            "Cascadeur could not queue the Mocap action: " + _last_line(),
            hint="Check get_log; the scene may have been closed.",
        )
    return {"done": False, "progress": 0.5}


# ---------------------------------------------------------------------------
# Mocap trigger and watchdog
# ---------------------------------------------------------------------------


def _target_ids(state) -> list:
    """Object ids for the current selection strategy (plane / character / both)."""
    order = TARGET_ORDERS[min(state.get("attempt", 0), len(TARGET_ORDERS) - 1)]
    ids = []
    for name in order:
        object_id = state.get("plane_id") if name == "plane" else state.get("character_id")
        if object_id is None:
            continue
        ids.append(object_id)
    return ids


def _trigger(state) -> None:
    """Select the target and call View.MocapTool (next event-loop turn)."""
    try:
        scene, status = _scene_for_writes(state)
        if scene is None:
            raise RuntimeError(f"the job's scene tab is not current ({status})")
        # The mocap writes key data into the character's layers; locked layers stay untouched.
        from .layers import ensure_unlocked

        locked = ensure_unlocked(_character_controllers(state.get("character")))
        if locked is not None:
            state["phase"] = "failed"
            state["error"] = locked.get("error") or "The character's layers are locked."
            state["error_hint"] = locked.get("hint")
            return
        timeline.select_interval(state["first"], state["last"])
        ids = _existing_model_ids(scene, _target_ids(state))
        if not ids:
            state["phase"] = "failed"
            state["error"] = (
                "The video plane (and the character) selected as the mocap source no longer exist in this tab."
            )
            state["error_hint"] = (
                "Retry; the reference plane is deleted after each run. get_scene_info lists the scene objects."
            )
            return
        _select(scene, ids)
        state["selected_names"] = timeline.object_names(ids)
        common.scenes()[0].get_action_manager().call_action(MOCAP_ACTION)
    except Exception:
        state["phase"] = "failed"
        state["error"] = "Cascadeur did not start the mocap tool: " + _last_line()
        return
    state["triggered"] = _now()
    state["phase"] = "waiting"


def _retry_with_next_target(state) -> dict:
    """After "please select an object with an animation reference", try the next selection."""
    attempt = state.get("attempt", 0) + 1
    state["attempt"] = attempt
    if attempt >= len(TARGET_ORDERS):
        return _abort(
            state,
            "Cascadeur asked for an object with an animation reference even with the video plane and "
            "the character selected.",
            hint=(
                "Run video_mocap with dry_run=true and check objects_selected in the result, or select "
                "the reference video plane by hand and press View > Mocap."
            ),
            selected=state.get("selected_names"),
        )
    state["retrigger"] = True
    state["phase"] = "waiting"
    state["phase_started"] = _now()
    return {"done": False, "progress": 0.3}


def _handle_role(state, role: str, text: str, dialog=None) -> dict:
    """React to one classified signal; returns a poll update or None to keep waiting."""
    if dialog is not None:
        state.setdefault("dialogs", []).append({"role": role, "text": text[:500]})
    if role == CONFIRM_ROLE:
        if state.get("confirmation_clicked"):
            return None
        state["confirmation_text"] = text[:800]
        state["confirmation_clicked"] = True
        if state.get("dry_run"):
            label = _click_label(dialog, CANCEL_LABELS) if dialog is not None else None
            state["confirmation_button"] = label
            return _finish(
                state,
                "cancelled",
                warning=(
                    "Dry run: the confirmation dialog was cancelled on purpose, so Cascadeur did not run "
                    "the mocap model and wrote no keys."
                ),
            )
        label = _click_label(dialog, PROCEED_LABELS) if dialog is not None else None
        state["confirmation_button"] = label
        if label is None:
            return _abort(
                state,
                "Cascadeur's mocap confirmation dialog has no Yes/Proceed button.",
                hint="Check the dialog by hand; its text and buttons are in the result.",
                dialog=text[:800],
            )
        state["run_started"] = _now()
        return {"done": False, "progress": 0.2}
    if role == SUCCESS_ROLE:
        if dialog is not None:
            _click_label(dialog, OK_LABELS)
        return _finish(state, "completed")
    if role == FAILED_FRAMES_ROLE:
        if dialog is not None:
            _click_label(dialog, OK_LABELS)
        state["failed_frames"] = parse_failed_frames(text)
        state["failed_frames_text"] = text[:800]
        return _finish(state, "partial")
    if role == SELECT_OBJECT_ROLE:
        if dialog is not None:
            _click_label(dialog, OK_LABELS)
        return _retry_with_next_target(state)
    if role == SELECT_FRAMES_ROLE:
        if dialog is not None:
            _click_label(dialog, OK_LABELS)
        return _abort(
            state,
            "The selected frames do not contain the animation reference: " + text[:400],
            hint=(
                "Cascadeur prints the reference's frame range in the dialog. Select frames inside it "
                "(an imported clip can be shorter than the timeline)."
            ),
            dialog=text[:800],
        )
    if role == MODEL_MISSING_ROLE:
        if dialog is not None:
            _click_label(dialog, OK_LABELS)
        return _abort(
            state,
            "Cascadeur reports that the mocap model is not installed.",
            hint=(
                "The first mocap run downloads about 825 MB from "
                f"{MODEL_URL}; run it once by hand (View > Mocap) with network access."
            ),
            model=model_file(),
        )
    if role in (CORRUPT_ROLE, DOWNLOAD_ROLE):
        if dialog is not None:
            _click_label(dialog, CANCEL_LABELS)
        return _abort(
            state,
            f"Cascadeur asked about the mocap model download ({role}).",
            hint="The model file is missing or corrupted; download it once by hand with network access.",
            dialog=text[:800],
        )
    if role in (FAILED_ROLE, TEXTURE_ROLE, IMPORT_ERROR_ROLE):
        if dialog is not None:
            _click_label(dialog, OK_LABELS)
        return _abort(
            state,
            f"Cascadeur reported a failure: {text[:400]}",
            hint="The dialog text is in the result; check the video (single person, full body) and the scene.",
            dialog=text[:800],
        )
    return None


def _watch(state, now: float) -> dict:
    """Poll the dialogs, log and Mocap progress window while the confirmation/run is pending."""
    polls = state["polls"]
    check_dialogs = polls <= 3 or polls % DIALOG_POLL_TICKS == 1
    dialogs = _dialogs() if check_dialogs else []
    for dialog in dialogs:
        if dialog.get("kind") != "dialog":
            continue
        text = dialog_text(dialog)
        role = dialog_role(text)
        if role is None:
            continue
        update = _handle_role(state, role, text, dialog)
        if update is not None:
            return update

    if polls <= 2 or polls % LOG_POLL_TICKS == 0:
        for line in _read_log(state):
            role = dialog_role(line)
            if role is None:
                continue
            if role in (SELECT_OBJECT_ROLE, SELECT_FRAMES_ROLE):
                update = _handle_role(state, role, line)
                if update is not None:
                    return update
            elif role in (FAILED_ROLE, TEXTURE_ROLE, IMPORT_ERROR_ROLE):
                if state.get("run_started") and now - state["run_started"] > 3.0:
                    return _abort(
                        state,
                        f"Cascadeur reported a failure in its log: {line.strip()[:300]}",
                        hint="The log line is in the result; get_log shows the surrounding lines.",
                        log=state.get("log", [])[-20:],
                    )

    mocap_dialog = _mocap_dialog() if state.get("mocap_seen") or check_dialogs else None
    if mocap_dialog is not None:
        state["mocap_seen"] = True
        state["mocap_closed_at"] = None
        found = mocap_state(mocap_dialog.get("texts") or ())
        if found is not None:
            state["mocap_state_name"], progress = found
            return {"done": False, "progress": progress}
        return {"done": False, "progress": 0.3}

    if state.get("mocap_seen"):
        if state.get("mocap_closed_at") is None:
            state["mocap_closed_at"] = now
        if now - state["mocap_closed_at"] > RESULT_GRACE_S:
            if state.get("failed_frames"):
                return _finish(state, "partial")
            state.setdefault("warnings", []).append(
                "Cascadeur's mocap progress window closed; no completion dialog was seen, so the key "
                "count is the evidence that the run landed."
            )
            return _finish(state, "completed")
        return {"done": False, "progress": 0.9}

    if state.get("confirmation_clicked") and state.get("run_started"):
        if now - state["run_started"] > CONFIRM_TIMEOUT_S:
            return _abort(
                state,
                "Cascadeur accepted the confirmation but never opened its mocap window.",
                hint=(
                    "Press View > Mocap by hand once to see what Cascadeur does; a model download "
                    "dialog or a licence prompt may be waiting off screen."
                ),
                log=state.get("log", [])[-20:],
            )
        return {"done": False, "progress": 0.25}

    if state.get("triggered") and now - state["triggered"] > CONFIRM_TIMEOUT_S:
        return _abort(
            state,
            "Cascadeur did not show the mocap confirmation after View.MocapTool.",
            hint=(
                "The dialog may be hidden behind Cascadeur, or the action needs a different selection. "
                "Run with dry_run=true and check objects_selected and dialogs in the result."
            ),
            selected=state.get("selected_names"),
            log=state.get("log", [])[-20:],
        )
    progress = 0.15 if state.get("triggered") else 0.05
    return {"done": False, "progress": progress}


def _phase_triggering(state, now: float) -> dict:
    if now - state["phase_started"] > DIALOG_TIMEOUT_S:
        return _abort(
            state,
            "Cascadeur did not react to View.MocapTool.",
            hint="Press View > Mocap by hand once to see what Cascadeur does for this scene.",
        )
    return {"done": False, "progress": 0.5}


def _phase_waiting(state, now: float) -> dict:
    if state.get("retrigger"):
        state["retrigger"] = False
        try:
            _schedule(lambda: _trigger(state))
        except Exception:
            return _abort(
                state,
                "Cascadeur could not queue the Mocap action again: " + _last_line(),
                hint="Check get_log.",
            )
        return {"done": False, "progress": 0.3}
    return _watch(state, now)


def _owned_native_pickers(state) -> list:
    """Native pickers this job opened that are still on screen."""
    owned = set(state.get("native_hwnds") or ())
    if not owned:
        return []
    return [item for item in _native_dialogs() if int(item.get("hwnd") or 0) in owned]


def _pickers_for_window(window) -> list:
    """Native pickers that belong to a Load video window (same title as the QML window).

    A picker opened before its HWND was recorded is still caught here, because Cascadeur gives
    the file/folder picker the Load video window's title.
    """
    if window is None:
        return []
    title = _window_title(window).strip().casefold()
    if not title:
        return []
    return [item for item in _native_dialogs() if str(item.get("title") or "").strip().casefold() == title]


def _load_window_pickers(state) -> list:
    """Pickers of this job's Load video window, recorded or found by title."""
    owned = _owned_native_pickers(state)
    seen = {int(item.get("hwnd") or 0) for item in owned}
    for item in _pickers_for_window(_load_window_of(state)):
        if int(item.get("hwnd") or 0) not in seen:
            owned.append(item)
    return owned


def _load_window_of(state):
    """Our Load video window object, when its winId was recorded."""
    wanted = state.get("load_window_id")
    if wanted is None:
        return None
    for window in _quick_windows():
        if _window_id(window) == wanted:
            return window
    return None


def _press_cancel(state) -> list:
    """Press Cancel/No in the windows this job opened; returns the labels clicked.

    Only the tool's own dialogs are answered: the Mocap progress window, its confirmation and
    the tool's Load video window. Unrelated dialogs are left alone. The Load video QQuickWindow
    is not closed while one of our native pickers is still up: destroying the window under an
    open modal picker crashes Cascadeur (measured), so the picker is cancelled first and the
    window is closed on a later tick once the picker is gone.
    """
    clicked = []
    for dialog in _dialogs():
        if dialog.get("kind") != "dialog":
            continue
        if str(dialog.get("type") or "") == MOCAP_DIALOG_TYPE:
            label = _click_label(dialog, CANCEL_LABELS)
            if label:
                clicked.append(label)
            continue
        role = dialog_role(dialog_text(dialog))
        if role is None:
            continue
        label = _click_label(dialog, CANCEL_LABELS) or _click_label(dialog, OK_LABELS)
        if label:
            clicked.append(label)
    pickers = _load_window_pickers(state)
    for native in pickers:
        _click_native_cancel(native)
        clicked.append("native cancel")
    # Do not destroy the Load video window while one of its modal pickers is still up: that
    # crashes Cascadeur. The picker was just cancelled, so the next cleanup closes the window.
    if not pickers and _close_load_window(state):
        clicked.append("close Load video")
    return clicked


def _phase_cancelling(state, now: float) -> dict:
    """Wait until Cascadeur's mocap window closes before deleting the plane or the frames.

    Deleting the reference video while Cascadeur's worker thread is still reading its frames can
    crash the application, so the timeout path presses Cancel and only then cleans up.
    """
    dialog = _mocap_dialog()
    pickers = _load_window_pickers(state)
    if dialog is not None or pickers:
        if now - state.get("cancelling_started", now) < CANCEL_WAIT_S:
            _press_cancel(state)
            return {"done": False, "progress": state.get("last_progress") or 0.05}
        warning = "Cascadeur's mocap window or file picker did not close after Cancel; the job cleaned up anyway."
    else:
        warning = None
    partial = _partial_frames(state)
    data = {"timed_out": True, "phase": "cancelling", "partial_key_frames": partial}
    if warning:
        data["warning"] = warning
    if partial:
        data["warning"] = (
            (data.get("warning", "") + " ") if data.get("warning") else ""
        ) + f"Cascadeur left keys on frames {partial} before it was cancelled."
    return _abort(
        state,
        state.get("cancel_reason") or "Video mocap was cancelled.",
        hint=(
            "The reference plane and the selection were cleaned up after Cascadeur's mocap window "
            "closed; pass a larger timeout for a long clip, or use a shorter video."
        ),
        **data,
    )


# ---------------------------------------------------------------------------
# Cleanup and results
# ---------------------------------------------------------------------------


def _delete_plane(state, scene=None) -> dict:
    """Delete the imported reference plane as one MCP undo step (unless keep_reference).

    Nothing is deleted when the job's tab is not the current one: the plane belongs to that tab
    and another tab must not be modified (or read) by mistake.
    """
    plane_id = state.get("plane_id")
    if plane_id is None:
        return {"deleted": False}
    if state.get("keep_reference"):
        return {"deleted": False, "kept": True}
    if scene is None:
        scene, status = _scene_for_writes(state)
        if scene is None:
            return {"deleted": False, "skipped": f"the job's scene tab is not current ({status})"}
    if not _existing_model_ids(scene, [plane_id]):
        return {"deleted": False, "missing": True}
    frame = 0
    try:
        frame = int(scene.get_current_frame(False))
    except Exception:
        pass

    def remove(model_editor, update_editor, scene_updater):
        model_editor.delete_objects({plane_id})
        scene_updater.generate_update()
        scene_updater.run_update(set(), frame)

    result = common.edit("Video mocap reference", remove)
    if result.get("ok"):
        return {"deleted": True, "undo": result.get("undo")}
    return {"deleted": False, "error": result.get("error"), "hint": result.get("hint")}


def _remove_frames_dir(state, plane_deleted=True) -> bool:
    """Remove the decoded frames folder this job created (never the user's own folder).

    keep_reference leaves the plane in the scene, and the plane's texture pages are these frame
    files, so they are kept with it. The same holds when the plane could not be deleted (another
    tab is current, or Cascadeur refused): deleting its frames would leave a broken plane.
    """
    if state.get("keep_reference") or not plane_deleted:
        return False
    path = state.get("frames_dir")
    if not path or not state.get("frames_created"):
        return False
    try:
        shutil.rmtree(path, ignore_errors=True)
        return not os.path.isdir(path)
    except Exception:
        return False


def _dismiss_our_dialogs(state) -> list:
    """Answer the dialogs this tool opened; returns the labels clicked."""
    clicked = []
    for dialog in _dialogs():
        if dialog.get("kind") != "dialog":
            continue
        role = dialog_role(dialog_text(dialog))
        if role is not None:
            label = _click_label(dialog, ("no", "cancel")) or _click_label(dialog, OK_LABELS)
            if label:
                clicked.append(label)
            continue
        if str(dialog.get("type") or "") == MOCAP_DIALOG_TYPE:
            label = _click_label(dialog, CANCEL_LABELS)
            if label:
                clicked.append(label)
    # Only the native pickers this job opened: their HWNDs were recorded when they were filled.
    pickers = _load_window_pickers(state)
    for native in pickers:
        _click_native_cancel(native)
        clicked.append("native cancel")
    # Do not destroy the Load video window while one of its modal pickers is still up: that
    # crashes Cascadeur. The picker was just cancelled, so the next cleanup closes the window.
    if not pickers and _close_load_window(state):
        clicked.append("close Load video")
    return clicked


def _cleanup(state, scene=None) -> dict:
    """Close our windows, delete the plane and restore the selection (never raises).

    Scene writes (deleting the plane, restoring the selection) only happen in the tab the job
    started on and only while that tab is current; otherwise they are reported as skipped.
    """
    report = {"dialogs": [], "plane": None, "restored": None, "frames": None, "scene": None}
    try:
        report["dialogs"] = _dismiss_our_dialogs(state)
    except Exception:
        pass
    target, status = _scene_for_writes(state)
    report["scene"] = status
    try:
        report["plane"] = _delete_plane(state, target)
    except Exception:
        report["plane"] = {"deleted": False, "error": _last_line()}
    plane_report = report["plane"] if isinstance(report["plane"], dict) else {}
    frames_removable = state.get("plane_id") is None or bool(plane_report.get("deleted"))
    try:
        report["frames"] = _remove_frames_dir(state, frames_removable)
    except Exception:
        report["frames"] = False
    if target is None:
        report["restored"] = {"restored": False, "skipped": "the job's scene tab is not current"}
        return report
    if scene is None:
        scene = target
    report["restored"] = _restore_selection(scene, state.get("selection") or {})
    return report


def _partial_frames(state) -> list:
    """Frames in the interval that have keys now but did not before the job started.

    Used after a cancel/timeout to report keys Cascadeur may already have written (T26 spec §5).
    """
    scene, _status = _scene_for_writes(state)
    if scene is None:
        return []
    try:
        object_ids = _character_controllers(state.get("character"))
        after = set(_key_coverage(scene, object_ids, state["first"], state["last"])["key_frames"])
    except Exception:
        return []
    before = set((state.get("coverage_before") or {}).get("key_frames") or ())
    return sorted(after - before)


def _result(state, outcome: str, extra=None) -> dict:
    """Build the success result from the finished job state."""
    scene, scene_status = _scene_for_writes(state)
    coverage = {"keyframes": 0, "key_frames": [], "keyed_layers": 0, "layers": 0}
    if scene is not None:
        try:
            object_ids = _character_controllers(state.get("character"))
            coverage = _key_coverage(scene, object_ids, state["first"], state["last"])
        except Exception:
            pass
    cleanup = _cleanup(state, scene)
    plane_report = cleanup.get("plane") if isinstance(cleanup.get("plane"), dict) else {}
    failed = sorted(set(state.get("failed_frames") or []))
    kept = bool(state.get("keep_reference"))
    undo_steps = [str(plane_report["undo"])] if plane_report.get("undo") else []
    data = {
        "character": state.get("character"),
        "frames": [state["first"], state["last"]],
        "from_frame": state.get("video_first"),
        "to_frame": state.get("video_last"),
        "keyframes": coverage["keyframes"],
        "key_frames": coverage["key_frames"][:200],
        "keyed_layers": coverage["keyed_layers"],
        "failed_frames": failed,
        "video": state["video"],
        "reference_plane": state.get("plane_name") if kept else None,
        "plane": (
            {"name": state.get("plane_name"), "type": state.get("plane_type"), "id": str(state.get("plane_id"))}
            if state.get("plane_id") is not None
            else None
        ),
        "elapsed_s": round(_now() - state["started"], 1),
        "outcome": outcome,
        "dry_run": bool(state.get("dry_run")),
        "objects_selected": state.get("selected_names"),
        "confirmation": state.get("confirmation_text"),
        "confirmation_button": state.get("confirmation_button"),
        "mocap_state": state.get("mocap_state_name"),
        "scene": scene_status,
        "import_action": state.get("import_action"),
        "import": {
            "dialog": state.get("dialog_texts"),
            "video_frames": state.get("video_frames"),
            "frames_dir": state.get("frames_dir"),
            "clicks": state.get("clicks") or [],
            "native_pickers": [name for name in ("native_file", "native_folder") if state.get(name)],
            "range_typed": bool(state.get("range_typed")),
        },
        "cleanup": cleanup,
        "undo": {
            "steps": undo_steps,
            "note": (
                "Cascadeur's mocap writes its own undo step for the baked keys; the reference plane "
                "import and delete are separate MCP: steps, so one Ctrl+Z does not revert the whole call."
            ),
        },
    }
    if state.get("failed_frames_text"):
        data["failed_frames_text"] = state["failed_frames_text"]
    if state.get("log"):
        data["log"] = state["log"][-15:]
    warnings = list(state.get("warnings") or [])
    if outcome == "partial":
        warnings.append(
            "Cascadeur processed the interval but could not recognise a pose on some frames; see failed_frames."
        )
    if extra and extra.get("warning"):
        warnings.append(str(extra["warning"]))
    if kept:
        warnings.append(
            f"keep_reference kept the video plane, so its decoded frames were kept too ({state.get('frames_dir')})."
        )
    if scene_status != "ok":
        warnings.append(
            "The job's scene tab was not current when it finished, so the reference plane and the "
            "selection were left as they were; switch to that tab to clean up."
        )
    if warnings:
        data["warning"] = " ".join(warnings)
    if not kept and state.get("plane_id") is not None and not plane_report.get("deleted") and scene_status == "ok":
        note = "The imported reference video plane could not be deleted; delete it by hand."
        data["warning"] = (data["warning"] + " " + note) if data.get("warning") else note
    return common.ok(**common.jsonable(data))


def _abort(state, error: str, hint: str, **data) -> dict:
    """Clean up after a failure and return the failure update jobs.py expects."""
    _clear_active()
    try:
        state["phase"] = "failed"
        cleanup = _cleanup(state)
        state["cleanup"] = cleanup
        data.setdefault("cleanup", cleanup)
        if state.get("log"):
            data.setdefault("log", state["log"][-20:])
        if state.get("confirmation_text"):
            data.setdefault("confirmation", state["confirmation_text"])
        if state.get("plane_name"):
            data.setdefault("plane", state.get("plane_name"))
        if state.get("dialog_texts"):
            data.setdefault("dialog", state.get("dialog_texts"))
    except Exception:
        pass
    return common.fail(error, hint, **common.jsonable(data))


def _finish(state, outcome: str, **extra) -> dict:
    """Finish the job with a success result (dry run / completed / partial)."""
    try:
        result = _result(state, outcome, extra)
    except Exception:
        result = common.fail(
            "The mocap job finished but its result could not be built: " + _last_line(),
            hint="Check get_log; the reference plane may still be in the scene.",
            traceback=traceback.format_exc(),
        )
    _clear_active()
    return {"done": True, "progress": 1.0, "result": result}


# ---------------------------------------------------------------------------
# Tool entry points
# ---------------------------------------------------------------------------


def _timeout_argument(timeout) -> float:
    if timeout is None or timeout == "":
        return DEFAULT_TIMEOUT_S
    try:
        value = float(timeout)
    except (TypeError, ValueError):
        raise _Error(
            f"timeout must be a number of seconds (got {timeout!r}).",
            hint="Use seconds, for example 600.",
        ) from None
    if value <= 0:
        raise _Error("timeout must be greater than 0 seconds.", hint="Use seconds, for example 600.")
    return value


@_tool("Check that a scene with one rigged character is open, then retry.")
def video_mocap(
    video_path,
    character=None,
    start=None,
    end=None,
    from_frame=None,
    to_frame=None,
    keep_reference=False,
    dry_run=False,
    timeout=DEFAULT_TIMEOUT_S,
):
    """Start the video-mocap background job (the T03 begin callable)."""
    global _ACTIVE
    if _active() is not None:
        return common.fail(
            "Another video-mocap job is still running.",
            hint="Wait for it with get_job, or cancel_job it, before starting another one.",
        )
    video = validate_video(video_path)
    model = check_model()
    # A Load video window that is already open is not ours: the user may have opened it, and
    # driving or closing it is not this job's business. Refuse instead of stacking a second one.
    leftover = _load_video_window()
    if leftover is not None:
        hint = "Close that window (its title-bar X or Cancel) and retry."
        if _pickers_for_window(leftover):
            hint = "Answer or cancel that Windows picker first, then close the Load video window and retry."
        return common.fail(
            "Cascadeur already has a Load video window open, so video_mocap did not start.",
            hint=hint,
        )
    if _mocap_dialog() is not None:
        return common.fail(
            "Cascadeur's Mocap window is already open, so video_mocap did not start.",
            hint="Press Cancel in that window (or wait for it to finish) and retry.",
        )
    timeout_s = _timeout_argument(timeout)
    keep = bool(keep_reference)
    dry = bool(dry_run)
    video_first, video_last = parse_video_range(from_frame, to_frame)
    explicit_first, explicit_last = parse_interval(start, end)

    characters = _characters()
    character_entry = choose_character(character, characters)
    _app, _view_scene, scene = common.scenes()
    if explicit_first is None:
        first, last, layers = _timeline_selection(scene)
        first, last = resolve_selection(first, last, layers)
    else:
        first, last = explicit_first, explicit_last
    # Cascadeur 2026.2.3 can crash when a timeline action runs on a one-frame animation (see
    # README's crash list), and the mocap selects and keys the interval, so require real keyed
    # frames before opening any dialog.
    checked = timeline.require_keyed_range(scene, first, last)
    if isinstance(checked, dict):
        return checked
    first, last = checked
    character_id = _character_object_id(character_entry, scene.model_viewer())
    if character_id is None:
        return common.fail(
            f"The character {character_entry.get('name')!r} has no scene object to select.",
            hint="Call list_characters and pass a character that has a rig in this scene.",
        )
    frames_dir = common.temp_dir("mocap-frames", time.strftime("%Y%m%d-%H%M%S"))
    state = {
        "started": _now(),
        "phase": "open_dialog",
        "phase_started": _now(),
        "deadline": _now() + timeout_s,
        "polls": 0,
        "video": video,
        "model": model,
        "character": character_entry.get("name"),
        "character_id": character_id,
        "first": first,
        "last": last,
        "from_frame": video_first,
        "to_frame": video_last,
        "keep_reference": keep,
        "dry_run": dry,
        "frames_dir": frames_dir,
        "frames_created": os.path.isdir(frames_dir),
        "attempt": 0,
        "choose_index": 0,
        # The job belongs to this tab. Every poll verifies it is still the current scene before
        # touching anything, so switching tabs never redirects the import, the mocap or cleanup.
        "view_scene": _view_scene,
        "preexisting_windows": {
            _window_id(window)
            for window in _quick_windows()
            if "load video" in _window_title(window).lower() and _window_id(window) is not None
        },
        "preexisting_native": {int(item.get("hwnd") or 0) for item in _native_dialogs() if int(item.get("hwnd") or 0)},
        "native_hwnds": [],
        "load_window_id": None,
        "coverage_before": _key_coverage(scene, _character_controllers(character_entry.get("name")), first, last),
        "selection": _selection_state(scene),
        "before_ids": _object_ids(scene),
        "new_objects": [],
        "log": [],
        "log_offset": _log_size(),
        "warnings": [],
        "dialogs": [],
        "failed_frames": [],
        "clicks": [],
        "last_progress": 0.0,
    }
    try:
        _select(scene, [character_id])
    except Exception:
        return common.fail(
            "Cascadeur did not select the character: " + _last_line(),
            hint="Check that no modal dialog is blocking Cascadeur.",
            traceback=traceback.format_exc(),
        )
    _ACTIVE = state
    try:
        _schedule(lambda: _open_import_dialog(state))
    except Exception:
        _clear_active()
        return common.fail(
            "Cascadeur could not queue the reference-video import: " + _last_line(),
            hint="Check get_log; the scene may have been closed.",
            traceback=traceback.format_exc(),
        )
    return common.ok(
        job="started",
        character=state["character"],
        frames=[first, last],
        video=video,
        frames_dir=frames_dir,
        dry_run=dry,
    )


@_tool("Check that a scene is open and that Cascadeur is responsive, then retry.")
def _poll(**kwargs):
    """QTimer poll: one workflow step per tick; returns {done, progress, result} for jobs.py."""
    state = _active()
    if state is None:
        return common.fail(
            "There is no video-mocap job to poll.",
            hint="Start video_mocap again; a server restart loses the job state.",
        )
    state["polls"] = state.get("polls", 0) + 1
    now = _now()
    try:
        status, reason = _scene_status(state)
        if status == "closed":
            return _abort(
                state,
                "The scene tab this job started on was closed, so the video-mocap job stopped.",
                hint="No other tab was touched. Start the job again on an open scene.",
                scene="closed",
                phase=state.get("phase"),
            )
        cancelling = state.get("phase") == "cancelling"
        if status == "switched" and not cancelling:
            # Never run an import or a mocap step against a different tab; wait for ours.
            state["paused"] = reason
            return {"done": False, "progress": state.get("last_progress") or 0.05}
        if status == "ok" and state.get("paused"):
            state.setdefault("warnings", []).append(
                "The job paused while another scene tab was current and resumed when its tab came back."
            )
            state["paused"] = None
        if now > state["deadline"] and not cancelling:
            # Press Cancel first and let the poll wait for Cascadeur's window to close; deleting
            # the plane while its worker thread still reads the frames can crash Cascadeur.
            state["cancel_reason"] = f"Video mocap did not finish within {state['deadline'] - state['started']:.0f}s."
            state["cancelling_started"] = now
            state["phase"] = "cancelling"
            _press_cancel(state)
            return {"done": False, "progress": state.get("last_progress") or 0.05}
        phase = state.get("phase")
        if phase == "cancelling":
            update = _phase_cancelling(state, now)
        elif phase == "open_dialog":
            update = _phase_open_dialog(state, now)
        elif phase == "file_dialog":
            update = _phase_file_dialog(state, now)
        elif phase == "file_set":
            update = _phase_file_set(state, now)
        elif phase == "folder_dialog":
            update = _phase_folder_dialog(state, now)
        elif phase == "folder_set":
            update = _phase_folder_set(state, now)
        elif phase == "importing":
            update = _phase_importing(state, now)
        elif phase == "triggering":
            update = _phase_triggering(state, now)
        elif phase == "waiting":
            update = _phase_waiting(state, now)
        elif phase == "failed":
            return _abort(
                state,
                state.get("error") or "The video-mocap job failed.",
                hint=state.get("error_hint") or "Check get_log; the scene may have been closed.",
            )
        else:
            return _abort(
                state,
                f"The video-mocap job lost track of its workflow (phase {phase!r}).",
                hint="Start video_mocap again.",
            )
        if isinstance(update, dict) and not update.get("done") and update.get("progress") is not None:
            state["last_progress"] = update["progress"]
        return update
    except Exception:
        return _abort(
            state,
            "The video-mocap job raised: " + _last_line(),
            hint="Check get_log; the reference video plane may still be in the scene.",
            traceback=traceback.format_exc(),
        )


def _finish_cancelled(state) -> None:
    """Clean up after cancel_job once Cascadeur's mocap window has closed (GUI thread).

    jobs.py stops the poll timer before it calls the cancel hook, so this runs from a QTimer
    chain: pressing Cancel and deleting the reference plane in the same tick would race
    Cascadeur's worker thread, which may still be reading the plane's frames.
    """
    if _mocap_dialog() is not None or _load_window_pickers(state):
        if _now() - state.get("cancel_requested_at", _now()) < CANCEL_WAIT_S:
            try:
                _schedule_later(250, lambda: _finish_cancelled(state))
            except Exception:
                pass
            return
    partial = _partial_frames(state)
    cleanup = _cleanup(state)
    report = {
        "partial_key_frames": partial,
        "scene": cleanup.get("scene"),
        "plane": cleanup.get("plane"),
        "frames": cleanup.get("frames"),
        "restored": cleanup.get("restored"),
        "keep_reference": bool(state.get("keep_reference")),
    }
    try:
        import sys

        setattr(sys, CANCEL_REPORT_ATTR, report)
    except Exception:
        pass
    try:
        print("[cascadeur-mcp-plus] video_mocap cancelled:", report)
    except Exception:
        pass


@_tool("Check get_job for the cancelled job's state.")
def _cancel(**kwargs) -> None:
    """cancel_job hook: press Cancel in Cascadeur's window, then clean up after it closes."""
    state = _active()
    if state is None:
        return
    _clear_active()
    state["cancel_requested_at"] = _now()
    try:
        _press_cancel(state)
    except Exception:
        pass
    try:
        _schedule_later(400, lambda: _finish_cancelled(state))
    except Exception:
        _finish_cancelled(state)


TOOLS = [
    {
        "name": "video_mocap",
        "description": (
            "Turn a reference video into character animation with Cascadeur's built-in video mocap "
            "(View.MocapTool), entirely on this machine. Use it after importing nothing by hand: the tool "
            "imports the .mp4 as a scene reference plane (File > Import > Bind video), points the dialog's "
            "frames folder at a writable %TEMP% directory, selects the character and the timeline frames, "
            "presses Mocap, answers the confirmation and deletes the plane afterwards. "
            "Frames are project frames, not video seconds: start/end default to the timeline selection and "
            "Cascadeur writes one keyframe per selected frame on the character, so the animation length "
            "equals end-start+1 after the run. from_frame/to_frame trim the video clip (typed into the Load "
            "video dialog's From/To frame fields; this path is not verified yet). It is a background job: it "
            "returns a job id immediately and get_job reports progress and the result (character, frames, "
            "key counts, failed_frames, elapsed_s). The mocap model (mocap.pt, about 825 MB) must be "
            "installed; if it is missing, the first run downloads it. keep_reference=true leaves the imported "
            "plane in the scene. dry_run=true stops at Cascadeur's confirmation dialog, cancels it and reports "
            "what it saw without running the model - use it to check the video import and the character setup. "
            "The job stays on the scene tab it started on: switching to another tab pauses it (nothing is read "
            "or written there) and closing that tab stops it. cancel_job or a timeout presses Cancel in "
            "Cascadeur's window and deletes the plane only after that window closes, so Cascadeur's worker "
            "thread is not reading the frames any more; keys written before the cancel are reported. "
            "Quality, duration and how Cascadeur treats existing keys on the selected frames are not verified "
            "yet (a real single-person clip is still needed); back up the animation if those frames already have "
            "keys. Cascadeur's baked "
            "keys are their own undo step, so one Ctrl+Z does not revert the import, the mocap and the plane "
            "deletion together."
        ),
        "inputSchema": schema(
            {
                "video_path": {
                    "type": "string",
                    "description": "Absolute path of the reference .mp4 (single person, full body, 3-5 s).",
                },
                "character": {
                    "type": "string",
                    "description": "Character name from list_characters (default: the scene's only character).",
                },
                "start": {
                    "type": ["integer", "string"],
                    "description": "First project frame to mocap (default: the timeline selection).",
                },
                "end": {
                    "type": ["integer", "string"],
                    "description": "Last project frame to mocap (default: the timeline selection).",
                },
                "from_frame": {
                    "type": ["integer", "string"],
                    "description": "First frame of the video clip to use (default: 0).",
                },
                "to_frame": {
                    "type": ["integer", "string"],
                    "description": "Last frame of the video clip to use (default: the last frame).",
                },
                "keep_reference": {
                    "type": "boolean",
                    "description": "Keep the imported reference video plane in the scene (default false).",
                },
                "dry_run": {
                    "type": "boolean",
                    "description": (
                        "Import the video and open Cascadeur's mocap confirmation, then cancel it instead of "
                        "running the model (default false). Verifies the workflow without waiting for mocap."
                    ),
                },
                "timeout": {
                    "type": "number",
                    "description": "Seconds before the job cancels itself (default 600).",
                },
            },
            ["video_path"],
        ),
        "requires": [
            "csc.Guid",
            "csc.app.ActionManager.call_action",
            "csc.app.Application.get_action_manager",
            "csc.app.SceneManager.scenes",
            "csc.app.get_application",
            "csc.domain.Scene.get_current_frame",
            "csc.domain.Scene.layers_viewer",
            "csc.domain.Scene.model_viewer",
            "csc.domain.Scene.modify_update",
            "csc.domain.Scene.set_current_frame",
            "csc.domain.SceneUpdater.run_update",
            "csc.math.OrthogonalTransform",
            "csc.math.Quaternion",
            "csc.math.Rotation",
            "csc.math.ScaledTransform",
            "csc.model.BehaviourViewer.get_behaviours",
            "csc.model.ModelViewer.get_objects",
            "csc.model.ObjectId",
            "csc.model.ObjectId.null",
            "action:View.Bind video",
            "action:View.MocapTool",
        ],
        "fn": video_mocap,
        "poll": _poll,
        "cancel": _cancel,
        "background": True,
        # begin() selects the character and an id handed to selector().select() right after a
        # scene loads (before its idle event) is what crashed Cascadeur on 2026.10.24. The tool
        # therefore starts on the scene-idle event, like the official script server's commands.
        "idle": True,
        "job_timeout": JOB_TIMEOUT_S,
    },
]
