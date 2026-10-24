"""render_video and render_image_sequence: Cascadeur's own timeline renderer (T32).

Cascadeur's RenderToFile tool can play the timeline into an MP4 or an image sequence with its own
encoder (avcodec/openh264 are bundled), so a video never depends on screenshots, on another window
or on an external ffmpeg. The export opens Cascadeur's own "Export video" window and reports its
progress there.

The tools are background jobs (T03): fn prepares the scene, then schedules the export on a later
GUI event-loop turn, poll reads the export window's progress every 100 ms and never sleeps, and
cancel presses its Stop button. The scheduling matters: calling RenderToFile's video export
directly from the dispatcher's QTimer job stack crashes Cascadeur 2026.2.3 (its playback loop runs
inside the call). The scene/camera/display are only restored after that call has returned. The
window's PlayToFileViewModel cannot be converted to Python, so progress, Stop and Ok come from the
window's visible texts/buttons, not from the view model.

The preparation and the cleanup both run on the GUI thread:

* the visible frame range is narrowed to the requested interval (and put back afterwards);
* the camera is fitted to the union of the whole motion with the helpers T33-fix adds to
  tools/contact_sheet.py (_collect_motion and plan_motion_camera), so a moving character stays in
  view; T31's camera_preset views and "current" are accepted too;
* the display is switched to Mesh mode without ghosts and trajectories, so the default output only
  shows the character's shaded mesh; the user's display settings are put back afterwards.

Nothing here needs Cascadeur at import time: csc, PySide6 and the UI helpers are imported inside the
functions that use them, so the pure geometry/path/MP4 helpers can be unit tested outside Cascadeur.
"""

from __future__ import annotations

import importlib
import os
import re
import shutil
import struct
import tempfile
import time
import traceback

from .. import camera, common, render, timeline
from . import display
from ..errors import ToolError
from ._schema import schema

DEFAULT_WIDTH = 1920
DEFAULT_HEIGHT = 1080
DEFAULT_CAMERA = "auto"
CAMERA_MODES = ("auto", "current", *camera.VIEWS)
VIDEO_EXTENSIONS = (".mp4",)
START_TIMEOUT = 30.0
STALL_TIMEOUT = 5.0
CLOSE_TIMEOUT = 5.0
VERIFY_DELAY = 0.5
DIALOG_SCAN_INTERVAL = 0.5
FILE_STABLE_SECONDS = 0.5
RETURN_TIMEOUT = 5.0
JOB_TIMEOUT = 7200.0

SESSION_KEY = "session"

# The job's begin/poll/cancel callbacks only share the tool arguments, so one render session keeps
# its bookkeeping here. Only one render can run at a time (Cascadeur has one export dialog).
_STATE = {SESSION_KEY: None}


class _BadArgument(ToolError):
    """A tool argument or the current state is wrong; carries the hint that belongs in the result."""

    def __init__(self, message: str, hint: str | None = None):
        super().__init__(message)
        self.hint = hint


# ---------------------------------------------------------------------------
# Pure helpers: arguments, ranges, file names (unit tested outside Cascadeur)
# ---------------------------------------------------------------------------


def camera_mode(value) -> str:
    """Normalize camera_preset: None and "" mean the automatic whole-motion camera."""
    if value is None:
        return DEFAULT_CAMERA
    mode = str(value).strip().lower()
    return mode or DEFAULT_CAMERA


def check_camera_mode(value) -> str:
    """Return the normalized camera mode or raise ToolError for an unknown name."""
    mode = camera_mode(value)
    if mode not in CAMERA_MODES:
        raise ToolError(f"Unknown camera_preset {value!r}; use one of: {', '.join(CAMERA_MODES)}.")
    return mode


def plan_range(first: int, last: int, start=None, end=None) -> tuple[int, int]:
    """Return the inclusive frame range to render and the source of that range.

    start/end override the animation's own bounds; when only one is given the other end comes
    from the animation. A reversed range is a tool error, not an empty video.
    """
    first, last = int(first), int(last)
    start = first if start is None else timeline.parse_frame(start, "start")
    end = last if end is None else timeline.parse_frame(end, "end")
    if start > end:
        raise ToolError(f"start ({start}) must not be after end ({end}).")
    source = "animation" if start == first and end == last else "requested range"
    return start, end, source


def video_path(path, overwrite=False) -> str:
    """Validate the MP4 output path, or return a new file under %TEMP%\\cascadeur-mcp-plus\\videos."""
    if not path:
        return render.default_path(common.temp_dir("videos"), ".mp4", prefix="video")
    target = common.output_path(path, overwrite=overwrite, extensions=VIDEO_EXTENSIONS)
    # Normalize the extension the same way T30 does for PNG: keep the file where the caller asked.
    stem, extension = os.path.splitext(target)
    return stem + ".mp4" if extension != ".mp4" else target


def sequence_folder(folder) -> str:
    """Validate or create the image-sequence output folder and return its absolute path.

    An existing folder must be empty: RenderToFile doesn't have an overwrite flag for sequences,
    and mixing a new render with old frames would make the result ambiguous. Without a folder the
    tool creates a unique one under %TEMP%.
    """
    if not folder:
        return tempfile.mkdtemp(prefix="sequence-", dir=common.temp_dir("sequences"))
    if not isinstance(folder, str) or not folder:
        raise ToolError("folder must be a non-empty absolute path.")
    target = os.path.abspath(os.path.normpath(folder))
    if not os.path.isabs(folder):
        raise ToolError(f"folder must be an absolute path (got {folder!r}); join it with a folder first.")
    if os.path.exists(target):
        if not os.path.isdir(target):
            raise ToolError(f"Output folder is a file, not a folder: {target!r}.")
        if os.listdir(target):
            raise ToolError(f"Output folder is not empty: {target!r}. Pass a new folder, or delete its contents first.")
    return target


# ---------------------------------------------------------------------------
# MP4 inspection (pure stdlib; used for the result and to spot a finished file)
# ---------------------------------------------------------------------------


def _boxes(data: bytes, start: int, end: int):
    """Yield (kind, payload_start, box_end) for the boxes between start and end."""
    position = int(start)
    end = min(int(end), len(data))
    while position + 8 <= end:
        size, kind = struct.unpack(">I4s", data[position : position + 8])
        header = 8
        if size == 1:
            if position + 16 > end:
                return
            size = struct.unpack(">Q", data[position + 8 : position + 16])[0]
            header = 16
        elif size == 0:
            size = end - position
        if size < header or position + size > end:
            return
        yield kind, position + header, position + size
        position += size


def _find_box(data: bytes, kind: bytes, start: int, end: int):
    for box_kind, payload, box_end in _boxes(data, start, end):
        if box_kind == kind:
            return payload, box_end
    return None


def _path_box(data: bytes, start: int, end: int, path):
    for kind in path:
        found = _find_box(data, kind, start, end)
        if found is None:
            return None
        start, end = found
    return start, end


def _mvhd(data: bytes, start: int, end: int):
    """(timescale, duration_ticks) from a movie header box."""
    payload = data[start:end]
    if len(payload) < 20:
        return None, None
    version = payload[0]
    if version == 1:
        if len(payload) < 32:
            return None, None
        timescale = struct.unpack(">I", payload[20:24])[0]
        duration = struct.unpack(">Q", payload[24:32])[0]
    else:
        timescale = struct.unpack(">I", payload[12:16])[0]
        duration = struct.unpack(">I", payload[16:20])[0]
    return timescale, duration


def _sample_count(data: bytes, start: int, end: int):
    """Number of samples in a sample table (stsz), or stts when stsz is missing."""
    stsz = _find_box(data, b"stsz", start, end)
    if stsz is not None:
        payload = data[stsz[0] : stsz[0] + 12]
        if len(payload) >= 12:
            return struct.unpack(">I", payload[8:12])[0]
    stts = _find_box(data, b"stts", start, end)
    if stts is not None:
        payload = data[stts[0] : stts[0] + 8]
        if len(payload) >= 8:
            entries = struct.unpack(">I", payload[4:8])[0]
            total = 0
            for index in range(entries):
                offset = stts[0] + 8 + index * 8
                if offset + 8 > len(data):
                    break
                total += struct.unpack(">I", data[offset : offset + 4])[0]
            return total
    return None


def mp4_info(path) -> dict:
    """Read format, duration and encoded frame count from an MP4 file.

    Supports a plain MP4 (moov at the start or the end) and falls back to the fragmented-MP4
    track fragments when no sample table is present. Raises ToolError when the file is not an MP4.
    """
    try:
        with open(path, "rb") as handle:
            data = handle.read()
    except OSError as e:
        raise ToolError(f"Could not read the rendered video {path!r}: {e}") from e
    top = list(_boxes(data, 0, len(data)))
    brands = [kind for kind, _start, _end in top]
    if b"ftyp" not in brands:
        raise ToolError(f"The rendered file {path!r} is not an MP4 (no ftyp box).")
    ftyp = _find_box(data, b"ftyp", 0, len(data))
    brand = data[ftyp[0] : ftyp[0] + 4].decode("ascii", "replace") if ftyp else None
    info = {"brand": brand, "duration_s": None, "timescale": None, "sample_count": None, "fps": None}
    moov = _find_box(data, b"moov", 0, len(data))
    if moov is not None:
        mvhd = _find_box(data, b"mvhd", moov[0], moov[1])
        if mvhd is not None:
            timescale, duration = _mvhd(data, *mvhd)
            info["timescale"] = timescale
            if timescale:
                info["duration_s"] = round(duration / float(timescale), 6)
        tracks = []
        for kind, payload, box_end in _boxes(data, moov[0], moov[1]):
            if kind != b"trak":
                continue
            mdia = _find_box(data, b"mdia", payload, box_end)
            if mdia is None:
                continue
            stbl = _path_box(data, mdia[0], mdia[1], (b"minf", b"stbl"))
            if stbl is None:
                continue
            handler = _find_box(data, b"hdlr", mdia[0], mdia[1])
            handler_type = (
                data[handler[0] + 8 : handler[0] + 12] if handler is not None and handler[0] + 12 <= len(data) else None
            )
            tracks.append((handler_type, stbl))
        # A video file may carry an audio track too; count only the video track when the handler
        # says which one it is.
        video_tracks = [stbl for handler_type, stbl in tracks if handler_type == b"vide"]
        chosen = video_tracks or [stbl for _handler_type, stbl in tracks]
        samples = 0
        seen = False
        for stbl in chosen:
            count = _sample_count(data, stbl[0], stbl[1])
            if count is not None:
                samples += count
                seen = True
        if seen:
            info["sample_count"] = samples
    if info["sample_count"] is None:
        samples = 0
        seen = False
        for kind, payload, box_end in top:
            if kind != b"moof":
                continue
            for traf_kind, traf_start, traf_end in _boxes(data, payload, box_end):
                if traf_kind != b"traf":
                    continue
                trun = _find_box(data, b"trun", traf_start, traf_end)
                if trun is None:
                    continue
                payload_bytes = data[trun[0] : trun[0] + 8]
                if len(payload_bytes) >= 8:
                    samples += struct.unpack(">I", payload_bytes[4:8])[0]
                    seen = True
        if seen:
            info["sample_count"] = samples
    if info["sample_count"] and info["duration_s"]:
        info["fps"] = round(info["sample_count"] / info["duration_s"], 3)
    return info


def video_complete(path, stable: float = FILE_STABLE_SECONDS, seen: dict | None = None) -> bool:
    """True when path looks like a finished MP4 whose size has stopped changing.

    seen is a small state dict the caller keeps between polls: {"size", "changed_at"}. A file is
    only accepted after its size stayed the same for stable seconds and it has an ftyp box and a
    moov/moof box, so a file that is still being written is never mistaken for a finished video.
    """
    try:
        size = os.path.getsize(path)
    except OSError:
        return False
    now = time.monotonic()
    if seen is not None:
        if seen.get("size") != size:
            seen.update({"size": size, "changed_at": now})
            return False
        if now - float(seen.get("changed_at", now)) < stable:
            return False
    try:
        with open(path, "rb") as handle:
            head = handle.read(1_048_576)
            handle.seek(max(0, size - 1_048_576))
            tail = handle.read()
    except OSError:
        return False
    if len(head) < 12 or head[4:8] != b"ftyp":
        return False
    return b"moov" in head or b"moov" in tail or b"moof" in tail


def _number(value, name: str = "value") -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ToolError(f"{name} must be a number (got {value!r}).") from None
    return number


# ---------------------------------------------------------------------------
# Export video window (GUI thread)
# ---------------------------------------------------------------------------

EXPORT_WINDOW_TITLE = "Export video"
_PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s*%")


def _export_window():
    """Cascadeur's open Export video window, or None.

    The export is a separate top-level QQuickWindow. Its PlayToFileViewModel cannot be converted
    to Python ("Can't find converter for 'view::PlayToFileViewModel*'"), so the tool reads the
    progress page's percent text and its Stop/Ok buttons instead of the view model.
    """
    from .. import ui

    for window in ui.quick_windows():
        title = window.title().strip().lower()
        if title == EXPORT_WINDOW_TITLE or "export video" in title:
            return window
    return None


def _window_state(window) -> dict:
    """Texts, buttons and progress of the Export video window (GUI thread, no scene access)."""
    from .. import ui

    texts, buttons = [], []
    progress = None
    for item in ui._walk(window, False):
        info = ui._describe(item)
        text = info.get("text")
        if text:
            texts.append(str(text))
        if info.get("clickable") and text:
            buttons.append(str(text))
        if progress is None and "ProgressBar" in str(info.get("type", "")):
            try:
                value = float(item.property("value"))
            except (TypeError, ValueError):
                value = None
            if value is not None and 0.0 <= value <= 1.0:
                progress = value
    for text in texts:
        match = _PERCENT.search(text)
        if match:
            try:
                progress = max(0.0, min(1.0, float(match.group(1)) / 100.0))
            except ValueError:
                pass
            break
    lowered_texts = [text.lower() for text in texts]
    lowered_buttons = [button.lower() for button in buttons]
    return {
        "window": window,
        "texts": texts,
        "buttons": buttons,
        "progress": progress,
        "setup": any("start rendering" in button for button in lowered_buttons),
        "in_progress": any(button == "stop" for button in lowered_buttons)
        or any("rendering timeline" in text for text in lowered_texts),
        "done": any("render ended successfully" in text for text in lowered_texts)
        or (any(button == "ok" for button in lowered_buttons) and any("show in folder" in b for b in lowered_buttons)),
    }


def _dialog_state(session=None) -> dict | None:
    """The Export video window's state, or None when it is not open."""
    window = _export_window()
    if window is None:
        if session is not None:
            session["dialog"] = None
        return None
    try:
        return _window_state(window)
    except Exception:
        return None


def _click_window_button(window, labels) -> bool:
    """Click a button of the Export video window by its visible text (GUI thread)."""
    from .. import ui

    for label in labels:
        try:
            ui.click(query=label, window=window.title())
            return True
        except Exception:
            continue
    return False


def _close_dialog(session, stop: bool = False) -> bool:
    """Stop a running render (optional) and close the Export video window."""
    window = _export_window()
    if window is None:
        return True
    if stop:
        _click_window_button(window, ("Stop",))
    _click_window_button(window, ("Ok",))
    if _export_window() is not None:
        try:
            window.close()
        except Exception:
            pass
    if _export_window() is None:
        session["dialog"] = None
        return True
    return False


# ---------------------------------------------------------------------------
# Display and camera preparation/restoration (GUI thread)
# ---------------------------------------------------------------------------


def _display_snapshot() -> dict:
    state = display._read_state()
    return {
        "view_mode": state.get("view_mode"),
        "ghosts": state.get("ghosts"),
        "ghost_base_mode": state.get("ghost_base_mode"),
        "ghost_keyframes": state.get("ghost_keyframes"),
        "trajectory_mode": state.get("trajectory_mode"),
        "trajectory_active": state.get("trajectory_active"),
        "only_key_frames": state.get("only_key_frames"),
    }


def _apply_actions(actions) -> None:
    for action in actions:
        display._call_action(action)


def _ghost_actions(state: dict, base: str, keys: bool) -> list:
    """Actions that move the Ghost tool from state to (base, keys), mirroring set_ghosts."""
    actions = []
    current_keys = state.get("ghost_keyframes")
    current_base = state.get("ghost_base_mode")
    if current_keys is None or current_base is None:
        return actions
    if keys and not current_keys:
        actions.append(display.GHOST_ACTIONS["keyframes"])
    if not keys and current_keys:
        actions.append(display.GHOST_ACTIONS["keyframes"])
    if base != current_base:
        actions.append(display.GHOST_ACTIONS[base])
    return actions


def _trajectory_actions(state: dict, mode: str, only_keys) -> list:
    """Actions that move the Trajectory tool to mode/only_keys, mirroring set_trajectories."""
    actions = []
    active = state.get("trajectory_active")
    if active is None:
        return actions
    if mode == "off":
        if active:
            actions.append(display.TRAJECTORY_SWITCH)
    else:
        if not active:
            actions.append(display.TRAJECTORY_SWITCH)
        if state.get("trajectory_mode") != mode:
            actions.append(display.TRAJECTORY_ACTIONS[mode])
    if only_keys is not None and state.get("only_key_frames") != only_keys:
        actions.append(display.TRAJECTORY_ONLY_KEYS)
    return actions


def _apply_clean_display(saved: dict) -> dict:
    """Switch to Mesh, no ghosts, no trajectories; return what actually changed."""
    applied = {"view_mode": None, "ghosts": None, "trajectory": None, "warnings": []}
    current = dict(saved)
    if current.get("view_mode") is None:
        applied["warnings"].append("the view mode could not be read before rendering; it may stay on Mesh")
    if current.get("view_mode") != "mesh":
        display._set_view_mode_direct("mesh")
        applied["view_mode"] = "mesh"
    base = saved.get("ghost_base_mode")
    keys = saved.get("ghost_keyframes")
    if base is None or keys is None:
        applied["warnings"].append("the Ghost toolbar did not report its state, so ghosts were left unchanged")
    elif base != "none" or keys:
        _apply_actions(_ghost_actions(current, "none", False))
        applied["ghosts"] = "none"
    active = saved.get("trajectory_active")
    if active is None:
        applied["warnings"].append(
            "the Trajectory toolbar did not report its state, so trajectories were left unchanged"
        )
    elif active:
        _apply_actions(_trajectory_actions(current, "off", None))
        applied["trajectory"] = "off"
    return applied


def _restore_display(saved: dict) -> dict:
    """Put the view mode, ghosts and trajectories from a snapshot back; return the read-back state."""
    result = {"view_mode": False, "ghosts": False, "trajectory": False, "state": None}
    if not saved:
        return result
    try:
        current = display._read_state()
        if saved.get("view_mode") and current.get("view_mode") != saved["view_mode"]:
            display._set_view_mode_direct(saved["view_mode"])
        base = saved.get("ghost_base_mode")
        keys = saved.get("ghost_keyframes")
        if base is not None and keys is not None:
            _apply_actions(_ghost_actions(current, base, bool(keys)))
        mode = saved.get("trajectory_mode")
        active = saved.get("trajectory_active")
        target = "off" if active is False else mode
        if target is not None:
            _apply_actions(_trajectory_actions(current, target, saved.get("only_key_frames")))
        state = display._read_state()
    except Exception as e:  # report instead of hiding a display-restore failure
        result["error"] = f"{type(e).__name__}: {e}"
        return result
    result["state"] = state
    result["view_mode"] = saved.get("view_mode") in (None, state.get("view_mode"))
    result["ghosts"] = saved.get("ghosts") in (None, state.get("ghosts"))
    active = saved.get("trajectory_active")
    if active is False:
        result["trajectory"] = state.get("trajectory_active") is False
    elif saved.get("trajectory_mode"):
        result["trajectory"] = state.get("trajectory_active") is True and state.get("trajectory_mode") == saved.get(
            "trajectory_mode"
        )
    else:
        result["trajectory"] = True
    return result


def _motion_helpers():
    """T33-fix's whole-motion camera helpers; imported lazily so this module works without them."""
    module = importlib.import_module(f"{__package__}.contact_sheet")
    required = ("_collect_motion", "plan_motion_camera", "auto_view")
    missing = [name for name in required if not callable(getattr(module, name, None))]
    if missing:
        raise _BadArgument(
            "Rendering the whole motion needs ticket T33-fix's motion-camera helpers "
            f"({', '.join(missing)} are missing from tools/contact_sheet.py).",
            hint="Pass camera_preset='current' to use the current viewport camera, or update to a build with T33-fix.",
        )
    return module


def _apply_motion_camera(frames, mode, aspect) -> dict:
    """Fit one camera to every pose and write it (GUI thread); returns the view and camera."""
    helpers = _motion_helpers()
    motion = helpers._collect_motion(frames)
    if not motion.get("points"):
        raise _BadArgument(
            "No character control points were found in the rendered frames, so the camera cannot be fitted "
            "to the whole motion.",
            hint="Pass camera_preset='current' to keep the viewport camera, or open a rigged scene.",
        )
    view = helpers.auto_view(motion["axes"], motion["movement"]) if mode == "auto" else mode
    plan = helpers.plan_motion_camera(
        motion["points"],
        motion["axes"],
        view,
        fov_deg=motion["fov_deg"],
        aspect=aspect,
    )
    written = camera.write_camera(position=plan["position"], target=plan["target"], type_name="PERSPECTIVE")
    return {
        "view": view,
        "camera": written,
        "distance": plan.get("distance"),
        "fov_deg": plan.get("fov_deg"),
        "bbox": plan.get("bbox"),
    }


# ---------------------------------------------------------------------------
# Session preparation, restoration and polling (GUI thread)
# ---------------------------------------------------------------------------


def _scene_state() -> dict:
    """The active scene, its range/cursor/camera/display and whether the window is minimized."""
    from .. import ui

    _app, view_scene, scene = common.scenes()
    boundary = view_scene.animation_boundary()
    window = ui.resolve_window()[1]
    return {
        "view_scene": view_scene,
        "first_frame": int(boundary.first_frame),
        "last_frame": int(boundary.last_frame),
        "first_visible_frame": int(boundary.first_visible_frame),
        "last_visible_frame": int(boundary.last_visible_frame),
        # get_current_frame() clamps to the animation range; restore the cursor's real position.
        "frame": int(scene.get_current_frame(False)),
        "minimized": bool(ui._is_minimized(window)),
    }


def _set_visible_range(view_scene, start, end) -> tuple[int, int]:
    """Narrow the timeline's visible range and return what Cascadeur accepted."""
    boundary = view_scene.animation_boundary()
    boundary.first_visible_frame = int(start)
    boundary.last_visible_frame = int(end)
    return int(boundary.first_visible_frame), int(boundary.last_visible_frame)


def _restore_scene(session) -> dict:
    """Put the frame range, cursor and camera back; returns what was restored."""
    result = {"frame": None, "frame_restored": False, "range_restored": False, "camera_restored": None}
    view_scene = session.get("view_scene")
    if view_scene is None:
        return result
    saved = session["saved"]
    try:
        boundary = view_scene.animation_boundary()
        boundary.first_visible_frame = int(saved["first_visible_frame"])
        boundary.last_visible_frame = int(saved["last_visible_frame"])
        restored_range = (int(boundary.first_visible_frame), int(boundary.last_visible_frame))
        result["range_restored"] = restored_range == (saved["first_visible_frame"], saved["last_visible_frame"])
        scene = view_scene.domain_scene()
        scene.set_current_frame(int(saved["frame"]))
        result["frame"] = int(scene.get_current_frame(False))
        result["frame_restored"] = result["frame"] == saved["frame"]
    except Exception as e:
        result["error"] = f"{type(e).__name__}: {e}"
    saved_camera = session.get("saved_camera")
    if saved_camera is not None:
        try:
            camera.restore(saved_camera)
            result["camera_restored"] = True
        except Exception as e:
            result["camera_restored"] = False
            result["camera_error"] = f"{type(e).__name__}: {e}"
    return result


def _schedule(callback, delay_ms: int = 0) -> None:
    """Run callback on a later GUI event-loop turn.

    RenderToFile's video export runs Cascadeur's playback/render loop, so it must not be called
    from the dispatcher's QTimer job stack (that crashes Cascadeur 2026.2.3; T20 schedules the
    blocking AI action the same way).
    """
    from PySide6 import QtCore

    QtCore.QTimer.singleShot(int(delay_ms), callback)


def _run_export_start(session) -> None:
    """Fresh GUI event-loop turn: start Cascadeur's exporter (it may run its own loop)."""
    if session.get("cancelled") or _STATE.get(SESSION_KEY) is not session:
        session["render_returned"] = True
        return
    session["render_entered"] = True
    session["started_at"] = time.monotonic()
    session["phase"] = "starting"
    try:
        tool = render.render_tool()
        parameters = render.render_parameters(session["width"], session["height"], session["samples"])
        session["render_called"] = True
        if session["kind"] == "video":
            tool.play_to_video_file(session["view_scene"], parameters, session["temporary"])
        else:
            tool.play_to_images_sequence(session["view_scene"], parameters, session["temporary"])
    except Exception:
        session["start_error"] = traceback.format_exc()
    finally:
        session["render_returned"] = True


def _begin_render(kind: str, path, folder, start, end, width, height, samples, camera_preset, overwrite) -> dict:
    """GUI thread: validate, prepare the scene and start the render (returns quickly)."""
    active = _STATE.get(SESSION_KEY)
    if active is not None:
        raise _BadArgument(
            "Another video render is already running in this Cascadeur.",
            hint="Wait for that job to finish, or call cancel_job on it before starting another render.",
        )
    mode = check_camera_mode(camera_preset)
    width, height, samples = render.validate_size(width, height, samples)
    if kind == "video":
        target = video_path(path, overwrite)
        folder_target = None
    else:
        target = None
        folder_target = sequence_folder(folder)
    range_start = None if start is None else timeline.parse_frame(start, "start")
    range_end = None if end is None else timeline.parse_frame(end, "end")
    if range_start is not None and range_end is not None and range_start > range_end:
        raise ToolError(f"start ({range_start}) must not be after end ({range_end}).")
    if mode != "current":
        _motion_helpers()  # fail before touching the scene when T33-fix is not in this checkout

    prepared = _scene_state()
    if prepared["minimized"]:
        raise _BadArgument(
            "Cascadeur's window is minimized, and RenderToFile only renders while the window is visible "
            "(Cascadeur 2026.2.3 keeps the render queued until the window comes back).",
            hint="Restore the Cascadeur window (screenshot with restore=true, or run_python ui.restore_window()), "
            "then call the tool again.",
        )
    # A dialog left over by an earlier session would make progress and completion ambiguous.
    stale = _dialog_state()
    if stale is not None:
        if stale["in_progress"]:
            raise _BadArgument(
                "Cascadeur is already rendering a video (an Export video window is running).",
                hint="Let that render finish or press its Stop button, then call the tool again.",
            )
        _close_dialog({"dialog": stale})

    first, last, source = plan_range(prepared["first_frame"], prepared["last_frame"], range_start, range_end)
    aspect = width / float(height)
    session = {
        "kind": kind,
        "path": target if kind == "video" else folder_target,
        "folder": folder_target,
        "temporary": None,
        "width": width,
        "height": height,
        "samples": samples,
        "camera_mode": mode,
        "view_scene": prepared["view_scene"],
        "saved": prepared,
        "saved_camera": None,
        "display": None,
        "display_applied": None,
        "started_at": time.monotonic(),
        "created_at": time.monotonic(),
        "phase": "prepared",
        "progress": 0.0,
        "cancelled": False,
        "dialog": None,
        "next_scan": 0.0,
        "seen": {},
        "saw_progress": False,
        "last_dialog_at": None,
        "render_entered": False,
        "render_called": False,
        "render_returned": False,
        "start_error": None,
        "start_clicked": False,
        "requested_range": [first, last],
        "range": [first, last],
        "requested_frames": last - first + 1,
        "source": source,
        "fps": timeline.scene_fps(prepared["view_scene"]),
        "moved": False,
    }
    started = False
    try:
        actual = _set_visible_range(prepared["view_scene"], first, last)
        session["range"] = list(actual)
        session["requested_frames"] = actual[1] - actual[0] + 1
        if mode != "current":
            session["saved_camera"] = camera.snapshot()
            session["camera"] = _apply_motion_camera(list(range(actual[0], actual[1] + 1)), mode, aspect)
        session["display"] = _display_snapshot()
        session["display_applied"] = _apply_clean_display(session["display"])
        if kind == "video":
            session["temporary"] = render.temp_output_path(target)
        else:
            # Render next to the destination so moving the frames is a same-volume rename at the end.
            parent = os.path.dirname(folder_target or common.temp_dir("sequences"))
            session["temporary"] = tempfile.mkdtemp(prefix=".mcp-render-", dir=parent)
        session["phase"] = "scheduled"
        _STATE[SESSION_KEY] = session
        started = True
        _schedule(lambda: _run_export_start(session))
        return common.ok(
            kind=kind,
            path=target if kind == "video" else folder_target,
            range=session["range"],
            width=width,
            height=height,
            samples=samples,
            camera_preset=(session.get("camera") or {}).get("view", mode),
            hint="call get_job with the job_id",
        )
    except Exception as e:
        session["error"] = f"{type(e).__name__}: {e}"
        if started:
            _STATE[SESSION_KEY] = session
            _abort_session(session, e)
        else:
            _cleanup_preparation(session)
        if isinstance(e, ToolError):
            raise
        raise ToolError(f"Could not start the render: {type(e).__name__}: {e}") from e


def _cleanup_preparation(session) -> None:
    """Undo the scene changes of a render that never started (GUI thread, no waiting)."""
    try:
        _close_dialog(session, stop=True)
    finally:
        try:
            _restore_scene(session)
        finally:
            if session.get("display"):
                _restore_display(session["display"])


def _video_file_ready(session) -> bool:
    temporary = session.get("temporary")
    if not temporary:
        return False
    return video_complete(temporary, seen=session.setdefault("seen", {}))


def _sequence_files(folder) -> list[str]:
    try:
        return sorted(
            os.path.join(folder, name) for name in os.listdir(folder) if os.path.isfile(os.path.join(folder, name))
        )
    except OSError:
        return []


def _sequence_complete(session) -> bool:
    """True once every requested frame exists and the folder has stopped changing for a moment."""
    files = _sequence_files(session["temporary"])
    count = len(files)
    seen = session.setdefault("sequence_seen", {})
    now = time.monotonic()
    if seen.get("count") != count:
        seen.update({"count": count, "changed_at": now})
        return False
    if count < session["requested_frames"]:
        return False
    return now - float(seen.get("changed_at", now)) >= FILE_STABLE_SECONDS


def _progress_value(session, state=None) -> float:
    if state is not None:
        return max(session.get("progress", 0.0), float(state["progress"]))
    if session["kind"] == "video":
        return session.get("progress", 0.0)
    total = max(1, int(session["requested_frames"]))
    return min(0.99, len(_sequence_files(session["temporary"])) / total)


def _render_finished(session) -> bool:
    if session["kind"] == "video":
        return _video_file_ready(session)
    return _sequence_complete(session)


def _fail_session(session, error, hint=None) -> dict:
    """Close the dialog, restore the scene and remove partial output; return a failure result."""
    cleanup = _abort_session(session, error)
    result = common.fail(error, hint=hint)
    result.update(cleanup)
    return result


def _delayed_close(delay_ms: int = 700) -> None:
    """Retry closing the Export Video dialog after Stop, once the QML page has had time to switch."""
    try:
        from PySide6.QtCore import QTimer
    except Exception:
        return

    def close():
        try:
            _close_dialog({"dialog": None, "next_scan": 0.0}, stop=False)
        except Exception:
            pass

    for delay in (delay_ms, delay_ms * 2):
        try:
            QTimer.singleShot(int(delay), close)
        except Exception:
            return


def _abort_session(session, error) -> dict:
    """Best-effort cleanup for a failed or cancelled render (GUI thread; every step bounded)."""
    _close_dialog(session, stop=True)
    restored = _restore_scene(session)
    display_result = _restore_display(session.get("display") or {}) if session.get("display") else {}
    temp = session.get("temporary")
    removed = False
    if temp:
        try:
            if session["kind"] == "video":
                if os.path.exists(temp):
                    os.remove(temp)
                    removed = True
            elif os.path.isdir(temp):
                shutil.rmtree(temp, ignore_errors=True)
                removed = not os.path.exists(temp)
        except OSError:
            removed = False
    _STATE[SESSION_KEY] = None
    _delayed_close()
    return {
        "frame_restored": restored.get("frame_restored"),
        "range_restored": restored.get("range_restored"),
        "camera_restored": restored.get("camera_restored"),
        "display_restored": (
            display_result.get("view_mode") is True
            and display_result.get("ghosts") is True
            and display_result.get("trajectory") is True
            if display_result
            else None
        ),
        "temporary_removed": removed,
        "error": error,
    }


def _move_file(source, target) -> tuple[bool, str | None]:
    """One non-blocking rename attempt (retried by later polls); returns (done, error)."""
    try:
        os.replace(source, target)
        return True, None
    except OSError as e:
        return False, str(e)


def _move_sequence(folder, target) -> tuple[bool, str | None]:
    """Move every rendered frame into the requested folder; undo a half-finished move on error."""
    moved = []
    try:
        os.makedirs(target, exist_ok=True)
        for path in _sequence_files(folder):
            destination = os.path.join(target, os.path.basename(path))
            os.replace(path, destination)
            moved.append(destination)
        return True, None
    except OSError as e:
        for path in moved:
            try:
                os.remove(path)
            except OSError:
                pass
        return False, str(e)


def _try_move(session) -> tuple[bool, str | None]:
    """Move the finished render into place once; marks session['moved'] on success."""
    if session.get("moved"):
        return True, None
    if session["kind"] == "video":
        moved, error = _move_file(session["temporary"], session["path"])
    else:
        moved, error = _move_sequence(session["temporary"], session["path"])
    if moved:
        session["moved"] = True
    return moved, error


def _finish_session(session) -> dict:
    """Validate the output, close the dialog, restore the scene and build the job result."""
    errors = []
    dialog_closed = _close_dialog(session, stop=False)
    if not dialog_closed:
        errors.append("the Export Video dialog did not close")
    restored = _restore_scene(session)
    if restored.get("frame_restored") is not True:
        errors.append("the timeline cursor could not be restored")
    if restored.get("range_restored") is not True:
        errors.append("the visible frame range could not be restored")
    if session.get("saved_camera") is not None and restored.get("camera_restored") is not True:
        errors.append("the camera could not be restored")
    display_result = _restore_display(session.get("display") or {})
    if session.get("display") and not (
        display_result.get("view_mode") is True
        and display_result.get("ghosts") is True
        and display_result.get("trajectory") is True
    ):
        errors.append("the display settings could not be restored")

    meta = {
        "kind": session["kind"],
        "path": session["path"],
        "requested_range": session["requested_range"],
        "range": session["range"],
        "range_source": session["source"],
        "width": session["width"],
        "height": session["height"],
        "samples": session["samples"],
        "fps": session["fps"],
        "camera_preset": session["camera_mode"],
        "camera_view": (session.get("camera") or {}).get("view"),
        "camera": (session.get("camera") or {}).get("camera"),
        "frame": session["saved"]["frame"],
        "current_frame": restored.get("frame"),
        "frame_restored": restored.get("frame_restored"),
        "range_restored": restored.get("range_restored"),
        "camera_restored": restored.get("camera_restored"),
        "display_applied": session.get("display_applied"),
        "display_before": session.get("display"),
        "display_after": display_result.get("state"),
        "display_restored": (
            display_result.get("view_mode") is True
            and display_result.get("ghosts") is True
            and display_result.get("trajectory") is True
        ),
        "dialog_closed": dialog_closed,
        "seconds": round(time.monotonic() - session["started_at"], 3),
        "clean_output": not (session.get("display_applied") or {}).get("warnings"),
    }
    if session["kind"] == "video":
        try:
            info = mp4_info(session["path"])
        except ToolError as e:
            info = {}
            errors.append(str(e))
        meta["file_bytes"] = os.path.getsize(session["path"]) if os.path.exists(session["path"]) else 0
        meta["encoded"] = info
        meta["frames"] = info.get("sample_count") or session["requested_frames"]
        meta["duration_s"] = info.get("duration_s")
        meta["encoded_fps"] = info.get("fps")
        meta["frame_count_verified"] = bool(info.get("sample_count"))
        meta["frame_count_matches"] = meta["frames"] == session["requested_frames"]
        if meta["file_bytes"] <= 0:
            errors.append("the rendered video is empty")
        if info and meta["frame_count_matches"] is False:
            errors.append(
                f"the encoded video has {meta['frames']} frames but {session['requested_frames']} were requested"
            )
        if info.get("duration_s") and session["fps"]:
            expected = (
                (session["range"][1] - session["range"][0] + 1) / float(session["fps"]) if session["fps"] else None
            )
            meta["expected_duration_s"] = round(expected, 3) if expected else None
    else:
        files = _sequence_files(session["path"])
        meta["frames"] = len(files)
        meta["file_bytes"] = sum(os.path.getsize(path) for path in files)
        meta["files"] = [os.path.basename(path) for path in files]
        meta["frame_count_matches"] = len(files) == session["requested_frames"]
        if not files:
            errors.append("the image sequence is empty")
        if len(files) != session["requested_frames"]:
            errors.append(
                f"the sequence has {len(files)} files but {session['requested_frames']} frames were requested"
            )
    if errors:
        meta["warnings"] = errors
        meta.setdefault("note", "; ".join(errors))
    else:
        meta["ok"] = True
    _STATE[SESSION_KEY] = None
    return meta


def _start_error(session) -> str | None:
    """The last line of an exception raised by the scheduled export start, if any."""
    text = session.get("start_error")
    if not text:
        return None
    lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
    return lines[-1] if lines else "the exporter raised an exception"


def _click_export_start(session) -> None:
    """Fresh GUI turn: press the setup page's Start button (some builds wait for it)."""
    window = _export_window()
    if window is None:
        return
    _click_window_button(window, ("Start rendering",))
    session["start_clicked_at"] = time.monotonic()


def _poll_session(session) -> dict:
    """One poll tick (GUI thread, never sleeps); returns {done, progress, result} or a failure."""
    now = time.monotonic()
    phase = session.get("phase")
    if phase == "verify":
        return _poll_verify(session, now)
    if phase == "waiting_return":
        return _poll_waiting_return(session, now)
    error = _start_error(session)
    if error:
        return _fail_session(
            session,
            f"Cascadeur's exporter raised before it started: {error}",
            hint="Check get_log for the traceback; the scene, camera and display were restored.",
        )
    if phase == "scheduled" and not session.get("render_entered"):
        if now - session["created_at"] > START_TIMEOUT:
            return _fail_session(
                session,
                "The scheduled video export did not start within 30 s.",
                hint="Cascadeur may be busy; check get_log and retry with a smaller width/height.",
            )
        return {"done": False, "progress": 0.0}

    state = _dialog_state(session)
    if state is not None:
        session["last_dialog_at"] = now
        if state["done"]:
            return _begin_finishing(session)
        if state["in_progress"]:
            session["saw_progress"] = True
            session["phase"] = "rendering"
            if state.get("progress") is not None:
                session["progress"] = max(session.get("progress", 0.0), float(state["progress"]))
            session["last_progress_at"] = now
            return {"done": False, "progress": session.get("progress", 0.0)}
        if state["setup"]:
            # Some builds only open the dialog and wait for Start. The click runs in a fresh
            # event-loop turn for the same reason the export call itself is scheduled.
            waited = now - session.get("started_at", now)
            if session.get("render_called") and not session.get("start_clicked") and waited > 1.0:
                session["start_clicked"] = True
                _schedule(lambda: _click_export_start(session))
            if session.get("saw_progress") or _render_finished(session):
                return _begin_finishing(session)
            if waited > START_TIMEOUT:
                return _fail_session(
                    session,
                    "The Export video window opened but the render did not start (it stayed on the setup page).",
                    hint="Check get_log; try a smaller width/height or a shorter range.",
                )
            return {"done": False, "progress": 0.0}
        return {"done": False, "progress": session.get("progress", 0.0)}

    if _render_finished(session):
        return _begin_finishing(session)
    last = session.get("last_dialog_at")
    if session.get("saw_progress"):
        if last is not None and now - last <= STALL_TIMEOUT:
            return {"done": False, "progress": session.get("progress", 0.0)}
        return _fail_session(
            session,
            "The Export video window disappeared before the render finished; the output is missing or incomplete.",
            hint="Check get_log for the renderer's error and retry with a smaller width/height or a shorter range.",
        )
    if now - session["started_at"] > START_TIMEOUT:
        return _fail_session(
            session,
            "Cascadeur did not start rendering the video within 30 s (no Export video window, no output file).",
            hint="Check that the 3D viewport shows the scene and that get_log has no renderer error, then retry.",
        )
    return {"done": False, "progress": 0.0}


def _begin_finishing(session) -> dict:
    """The exporter finished: close its window, then wait for the blocking call to return."""
    now = time.monotonic()
    _close_dialog(session, stop=False)
    if not session.get("render_returned"):
        # play_to_video_file runs Cascadeur's loop synchronously; restoring the scene while that
        # loop is still unwinding is what the hard limits warn about.
        session["phase"] = "waiting_return"
        session["return_deadline"] = now + RETURN_TIMEOUT
        return {"done": False, "progress": 1.0}
    return _restore_after_render(session)


def _poll_waiting_return(session, now: float) -> dict:
    """Wait for the export call to return, closing its window if it reappears, then restore."""
    if _dialog_state(session) is not None:
        _close_dialog(session, stop=False)
    if session.get("render_returned") or now >= float(session.get("return_deadline", 0.0)):
        return _restore_after_render(session)
    return {"done": False, "progress": 1.0}


def _restore_after_render(session) -> dict:
    """Restore the scene/camera/display once the exporter is done; go to the verify phase."""
    session["phase"] = "verify"
    session["verify_at"] = time.monotonic() + VERIFY_DELAY
    session["move_deadline"] = time.monotonic() + 10.0
    session["close_deadline"] = time.monotonic() + CLOSE_TIMEOUT
    session["next_close"] = 0.0
    session["restored"] = _restore_scene(session)
    session["display_result"] = _restore_display(session.get("display") or {}) if session.get("display") else {}
    return {"done": False, "progress": 1.0}


def _poll_verify(session, now: float) -> dict:
    """After the render stops: close any window, move the output, then build the result."""
    if now < float(session.get("verify_at", 0.0)):
        return {"done": False, "progress": 1.0}
    if now >= float(session.get("next_close", 0.0)):
        session["next_close"] = now + DIALOG_SCAN_INTERVAL
        if _dialog_state(session) is not None:
            _close_dialog(session, stop=False)
    if _dialog_state(session) is not None:
        if now < float(session.get("close_deadline", 0.0)):
            return {"done": False, "progress": 1.0}
    if not session.get("moved"):
        moved, error = _try_move(session)
        if not moved:
            if now < float(session.get("move_deadline", 0.0)):
                return {"done": False, "progress": 1.0}
            return _fail_session(
                session,
                f"The render finished but its output could not be moved into place: {error}",
                hint="Close any program holding the file and retry.",
            )
    meta = _finish_session(session)
    if meta.get("ok"):
        return {"done": True, "progress": 1.0, "result": meta}
    return common.fail(
        meta.get("note") or "The render finished but its result could not be verified.",
        hint="The file may still be usable; check the output folder and get_log.",
    )


# ---------------------------------------------------------------------------
# Tool callbacks (fn = begin, poll, cancel; all on the GUI thread)
# ---------------------------------------------------------------------------


def _tool_failure(error: Exception) -> dict:
    return common.fail(str(error), getattr(error, "hint", None))


def render_video(
    path=None,
    start=None,
    end=None,
    width=DEFAULT_WIDTH,
    height=DEFAULT_HEIGHT,
    samples=None,
    camera_preset=None,
    overwrite=False,
):
    """Begin a background MP4 render (GUI thread; the render itself is asynchronous)."""
    try:
        return _begin_render("video", path, None, start, end, width, height, samples, camera_preset, overwrite)
    except ToolError as e:
        return _tool_failure(e)


def render_image_sequence(
    folder=None,
    start=None,
    end=None,
    width=DEFAULT_WIDTH,
    height=DEFAULT_HEIGHT,
    samples=None,
    camera_preset=None,
):
    """Begin a background image-sequence render (GUI thread)."""
    try:
        return _begin_render("image_sequence", None, folder, start, end, width, height, samples, camera_preset, False)
    except ToolError as e:
        return _tool_failure(e)


def poll(**kwargs):
    """QTimer callback: read progress, finish once, or fail with cleanup; never sleeps."""
    del kwargs
    session = _STATE.get(SESSION_KEY)
    if session is None:
        return common.fail(
            "The render session is gone (the server may have restarted).",
            hint="list_jobs shows the job as lost; start the render again.",
        )
    try:
        return _poll_session(session)
    except Exception as e:  # noqa: BLE001 - a poll exception must still restore the scene
        return _fail_session(
            session,
            f"{type(e).__name__}: {e}",
            hint="Check get_log for the traceback and get_scene_info for the open scene.",
        )


def cancel(**kwargs):
    """cancel_job: press Stop, close the window and restore the scene once the exporter stopped."""
    del kwargs
    session = _STATE.get(SESSION_KEY)
    if session is None:
        return None
    session["cancelled"] = True
    window = _export_window()
    if window is not None:
        _click_window_button(window, ("Stop",))
        _click_window_button(window, ("Ok",))
    _schedule(lambda: _cancel_cleanup(session, 0), 300)
    return None


def _cancel_cleanup(session, attempt: int) -> None:
    """Delayed cleanup after cancel_job: let a blocking export call return before restoring."""
    if _STATE.get(SESSION_KEY) is not session:
        return
    if _export_window() is not None:
        _close_dialog(session, stop=True)
    if not session.get("render_returned") and attempt < 10:
        _schedule(lambda: _cancel_cleanup(session, attempt + 1), 500)
        return
    _abort_session(session, "The render was cancelled.")


TOOLS = [
    {
        "name": "render_video",
        "description": (
            "Render the animation timeline to an MP4 with Cascadeur's own encoder (RenderToFile), as a "
            "background job: returns a job_id immediately and get_job reports progress and the result. Use it "
            "to produce a video of a motion without screenshotting frames or an external ffmpeg. The whole "
            "animation is rendered unless start/end narrow the range. By default the camera is fitted to the "
            "union of the whole motion so a moving character stays in view (T33-fix), the display switches to "
            "Mesh without ghosts or trajectories so the output shows only the character's shaded mesh, and the "
            "range, camera and display settings are restored afterwards. camera_preset also accepts current "
            "(keep the viewport camera) or T31's front/back/left/right/top/three_quarter views. path must be "
            "absolute and end in .mp4; an existing file is only replaced with overwrite=true. width/height are "
            "pixels, samples is Cascadeur's anti-aliasing sample count (omit for its default). The render dialog's "
            "progress is reported and cancel_job presses its Stop button. Cascadeur must not be minimized. The "
            "result lists the file path, byte size, encoded frame count, duration, fps and what was restored."
        ),
        "inputSchema": schema(
            {
                "path": {
                    "type": "string",
                    "description": "Absolute output path ending in .mp4 (default: a new file under %TEMP%).",
                },
                "start": {
                    "type": ["integer", "string"],
                    "description": "First frame of the interval (default: the animation's first frame).",
                },
                "end": {
                    "type": ["integer", "string"],
                    "description": "Last frame of the interval, inclusive (default: the animation's last frame).",
                },
                "width": {"type": "integer", "description": "Video width in pixels (default 1920)."},
                "height": {"type": "integer", "description": "Video height in pixels (default 1080)."},
                "samples": {
                    "type": "integer",
                    "description": "Anti-aliasing samples per pixel; omitting it keeps Cascadeur's default.",
                },
                "camera_preset": {
                    "type": "string",
                    "enum": list(CAMERA_MODES),
                    "description": "auto (default) fits the whole motion; current keeps the viewport camera; "
                    "front/back/left/right/top/three_quarter fit the whole motion from that view.",
                },
                "overwrite": {"type": "boolean", "description": "Replace an existing file at path (default false)."},
            }
        ),
        "fn": render_video,
        "poll": poll,
        "cancel": cancel,
        "background": True,
        "job_timeout": JOB_TIMEOUT,
    },
    {
        "name": "render_image_sequence",
        "description": (
            "Render the animation timeline to a folder of image files with Cascadeur's own renderer "
            "(RenderToFile), as a background job. Use it when a video is not needed or when the frames should be "
            "processed later. The whole animation is rendered unless start/end narrow the range; the same "
            "whole-motion camera, clean Mesh display and restoration as render_video apply. folder must be an "
            "absolute path to a missing or empty folder (a new folder under %TEMP% is used by default); an "
            "existing folder with files is refused so old and new frames cannot mix. width/height are pixels, "
            "samples is the anti-aliasing sample count. Returns a job_id; get_job reports progress and the "
            "resulting folder, file count and byte size. Cancel with cancel_job (it presses the dialog's Stop)."
        ),
        "inputSchema": schema(
            {
                "folder": {
                    "type": "string",
                    "description": "Absolute destination folder (missing or empty; default: a new folder under %TEMP%).",
                },
                "start": {
                    "type": ["integer", "string"],
                    "description": "First frame of the interval (default: the animation's first frame).",
                },
                "end": {
                    "type": ["integer", "string"],
                    "description": "Last frame of the interval, inclusive (default: the animation's last frame).",
                },
                "width": {"type": "integer", "description": "Image width in pixels (default 1920)."},
                "height": {"type": "integer", "description": "Image height in pixels (default 1080)."},
                "samples": {
                    "type": "integer",
                    "description": "Anti-aliasing samples per pixel; omitting it keeps Cascadeur's default.",
                },
                "camera_preset": {
                    "type": "string",
                    "enum": list(CAMERA_MODES),
                    "description": "auto (default) fits the whole motion; current keeps the viewport camera; "
                    "front/back/left/right/top/three_quarter fit the whole motion from that view.",
                },
            }
        ),
        "fn": render_image_sequence,
        "poll": poll,
        "cancel": cancel,
        "background": True,
        "job_timeout": JOB_TIMEOUT,
    },
]
