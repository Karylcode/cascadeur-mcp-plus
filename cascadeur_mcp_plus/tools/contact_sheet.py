"""render_contact_sheet: a grid of frames from one animation (T33).

A contact sheet lets an agent check a whole motion in one picture. Each cell is one frame
rendered with Cascadeur's own renderer (the T30 render helpers), the frame number is drawn in
the cell's corner, and the cells are composed in memory with QImage/QPainter. The composition
touches no window and no OpenGL surface, so it is safe on the HTTP thread.

The frames are rendered one at a time: render.render_to_file() queues take_image on Cascadeur's
GUI thread and waits on this HTTP thread, so the GUI thread never sleeps while a sheet is built.
The playhead and the camera are restored even when a render fails.

By default camera_preset="auto": before rendering, the character's control points are read at
every rendered frame on the GUI thread and T31's camera.py fits one fixed camera to the union
bounding box of the whole motion. Every cell therefore uses the same camera and the motion stays
in view even when the character travels. "current" keeps the viewport camera (the old
behaviour); front/back/left/right/top/three_quarter fit the same union from that view.
"""

from __future__ import annotations

import math
import os
import shutil
import tempfile
import threading
import time

from .. import common, dispatch, render, timeline
from ..errors import ToolError
from ._schema import schema

DEFAULT_COLUMNS = 4
DEFAULT_CELL_WIDTH = 320
DEFAULT_FRAMES = 8
MAX_FRAMES = 64
MAX_COLUMNS = 32
MIN_CELL_WIDTH = 16
MAX_CELL_WIDTH = 4096
MAX_SHEET_SIDE = 16384
MAX_SHEET_PIXELS = 40_000_000
CELL_GAP = 2
IMAGE_MAX_WIDTH = 0  # encode_image(0) returns the composed PNG byte for byte
CELL_ASPECT = 9.0 / 16.0
CAMERA_PRESETS = ("front", "back", "left", "right", "top", "three_quarter")
CAMERA_MODES = ("auto", "current", *CAMERA_PRESETS)
DEFAULT_CAMERA_PRESET = "auto"
CAMERA_MARGIN = 1.15

_STATE = threading.local()


class _ImageFlag:
    """Truthy only for a call that returned image content.

    server._call_tool decides whether a result is image content after the handler returns, so a
    tool that always returned a tuple would make failures crash the unpack. A request stays on
    one HTTP thread, so a thread-local flag cannot leak into another call. This is the same
    pattern as tools/render.py (T30).
    """

    def __bool__(self) -> bool:
        return bool(getattr(_STATE, "image", False))

    def __repr__(self) -> str:
        return "<image content when the call returns one>"


class _MinimizedWindow(ToolError):
    """Cascadeur's window is minimized, and RenderToFile only draws while it is visible."""


class _BadArgument(ToolError):
    """A tool argument is invalid; carries the hint that belongs in the result."""

    def __init__(self, message: str, hint: str):
        super().__init__(message)
        self.hint = hint


# ---------------------------------------------------------------------------
# Argument parsing and frame planning (pure Python)
# ---------------------------------------------------------------------------


def _whole_number(name: str, value, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ToolError(f"{name} must be a whole number (got {value!r}).")
    number = int(value)
    if number != value:
        raise ToolError(f"{name} must be a whole number (got {value!r}).")
    if not minimum <= number <= maximum:
        raise ToolError(f"{name} must be between {minimum} and {maximum} (got {number}).")
    return number


def cell_height_for(cell_width: int) -> int:
    """Height of one 16:9 cell for the requested width (at least one pixel)."""
    return max(1, round(int(cell_width) * CELL_ASPECT))


def sheet_size(count: int, columns: int, cell_width: int, cell_height: int) -> tuple[int, int]:
    """Pixel size of the composed sheet for count cells, gaps included."""
    rows = max(1, math.ceil(int(count) / int(columns)))
    width = int(columns) * int(cell_width) + (int(columns) - 1) * CELL_GAP
    height = rows * int(cell_height) + (rows - 1) * CELL_GAP
    return width, height


def even_frames(first: int, last: int, count: int = DEFAULT_FRAMES) -> list[int]:
    """count frames spread evenly from first to last, both ends included.

    Rounding can land on the same frame twice when the animation is shorter than the requested
    count (for example first=0, last=3 with count=8); duplicates are dropped, so the result may
    be shorter than count.
    """
    first, last = int(first), int(last)
    if count <= 1 or last <= first:
        return [first]
    span = last - first
    values = sorted({round(first + span * index / (count - 1)) for index in range(count)})
    return [int(value) for value in values]


def plan_frames(frames=None, start=None, end=None, step=None, first=0, last=0) -> tuple[list[int], str]:
    """Return (frames to render, how they were chosen).

    frames is a frame, a list of frames or a comma-separated string (T14's parser); it wins and
    may not be combined with start/end/step. Otherwise start/end default to the animation's
    bounds and step defaults to 1; with no start/end/step at all, DEFAULT_FRAMES frames are
    sampled evenly across the animation. Every result is sorted, unique and at most MAX_FRAMES
    long.
    """
    if frames is not None:
        if start is not None or end is not None or step is not None:
            raise _BadArgument(
                "Pass either frames or start/end/step, not both.",
                hint="Use frames=[0, 10, 20], or start/end/step for an interval.",
            )
        chosen = timeline.parse_frames(frames)
        source = "frames"
    else:
        first, last = int(first), int(last)
        if start is None and end is None and step is None:
            chosen = even_frames(first, last)
            source = "even sample"
        else:
            start = first if start is None else timeline.parse_frame(start, "start")
            end = last if end is None else timeline.parse_frame(end, "end")
            if start > end:
                raise _BadArgument(
                    f"start ({start}) must not be after end ({end}).",
                    hint="Swap start and end, or leave start/end/step out to sample the whole animation.",
                )
            step = 1 if step is None else timeline.parse_frame(step, "step")
            if step < 1:
                raise _BadArgument("step must be 1 or more.", hint="Pass step=1 for every frame, or a larger step.")
            chosen = list(range(start, end + 1, step))
            source = "start/end/step"
    if len(chosen) > MAX_FRAMES:
        raise _BadArgument(
            f"{len(chosen)} frames is too many for one contact sheet (the limit is {MAX_FRAMES}).",
            hint="Pass a step, a shorter interval, or an explicit frames list with fewer frames.",
        )
    if not chosen:
        raise _BadArgument("No frames were selected.", hint="Pass frames, or a start/end interval.")
    return chosen, source


def _output_path(path, overwrite) -> str:
    if not path:
        return render.default_path(common.temp_dir("contact_sheets"), prefix="contact-sheet")
    return common.output_path(path, overwrite=overwrite, extensions=(".png",))


# ---------------------------------------------------------------------------
# Auto framing (pure geometry, uses T31's camera.py)
# ---------------------------------------------------------------------------


def camera_mode(value) -> str:
    """Normalize the camera_preset argument; None and "" mean the default auto framing."""
    if value is None:
        return DEFAULT_CAMERA_PRESET
    mode = str(value).strip().lower()
    return mode or DEFAULT_CAMERA_PRESET


def auto_view(axes, movement=(0.0, 0.0, 0.0)) -> str:
    """Choose a view perpendicular to the character's movement.

    A backflip travels along the character's forward axis, so a side view ("right") shows the
    rotation; sideways travel uses the front view instead. No movement keeps the side view.
    """
    forward = axes["forward"]
    right = axes["right"]
    along_forward = sum(float(movement[index]) * float(forward[index]) for index in range(3))
    along_right = sum(float(movement[index]) * float(right[index]) for index in range(3))
    return "right" if abs(along_forward) >= abs(along_right) else "front"


def union_bbox(points) -> dict:
    """Axis-aligned bounding box of every pose, as {min, max, center} lists of floats."""
    from .. import camera

    if not points:
        raise ToolError("No character points were collected, so the contact sheet camera cannot be fitted.")
    low, high = camera.bbox(points)
    center = camera.bbox_center(points)
    return {"min": list(low), "max": list(high), "center": list(center)}


def plan_motion_camera(points, axes, view, fov_deg=30.0, aspect=16.0 / 9.0, margin=CAMERA_MARGIN) -> dict:
    """One fixed camera that fits the union of all points from the given preset view.

    camera.preset_camera() computes the distance with camera.fit_distance(), so the returned
    position keeps the union bounding box (plus margin) inside the rendered frame.
    """
    from .. import camera

    bounds = union_bbox(points)
    plan = camera.preset_camera(
        view,
        bounds["center"],
        axes,
        points,
        fov_deg=fov_deg,
        aspect=aspect,
        margin=margin,
    )
    return {
        "view": view,
        "position": list(plan["position"]),
        "target": list(plan["target"]),
        "distance": float(plan["distance"]),
        "fov_deg": float(fov_deg),
        "aspect": float(aspect),
        "bbox": bounds,
    }


# ---------------------------------------------------------------------------
# Camera and playhead (GUI thread)
# ---------------------------------------------------------------------------


def _main_viewport(view_scene):
    """The main 3D viewport's domain viewport, or the first one when none reports is_main()."""
    viewports = list(view_scene.viewports())
    if not viewports:
        return None
    for viewport in viewports:
        domain = viewport.domain_viewport()
        try:
            if domain.is_main():
                return domain
        except Exception:
            continue
    return viewports[0].domain_viewport()


def _camera_point(value) -> list[float]:
    """Flatten a camera position/target (numpy (3,), (3, 1) or a list) to three floats."""
    reshape = getattr(value, "reshape", None)
    if callable(reshape):
        value = reshape(-1)
    try:
        values = [float(item) for item in value]
    except TypeError:
        raise ToolError(f"Cascadeur returned a camera value that is not a 3D point: {value!r}.") from None
    if len(values) < 3:
        raise ToolError(f"Cascadeur returned a camera value with fewer than three coordinates: {value!r}.")
    return values[:3]


def _camera_snapshot(view_scene):
    """The camera values needed to put the main viewport's camera back later."""
    domain = _main_viewport(view_scene)
    if domain is None:
        return None
    struct = domain.camera_struct()
    return {
        "position": _camera_point(struct.position),
        "target": _camera_point(struct.target),
        "type": struct.type,
    }


def _camera_write(view_scene, saved) -> bool:
    """Write a snapshot back to the main viewport; False when there is no camera to write."""
    domain = _main_viewport(view_scene)
    if domain is None or not saved:
        return False
    import numpy as np

    struct = domain.camera_struct()
    struct.position = np.asarray(saved["position"], dtype=np.float32)
    struct.target = np.asarray(saved["target"], dtype=np.float32)
    struct.type = saved["type"]
    domain.set_camera_struct(struct)
    return True


def _prepare():
    """GUI thread: the active scene, its animation bounds, the raw cursor and the camera."""
    from .. import ui

    _app, view_scene, scene = common.scenes()
    boundary = view_scene.animation_boundary()
    window = ui.resolve_window()[1]
    return {
        "view_scene": view_scene,
        "first_frame": int(boundary.first_frame),
        "last_frame": int(boundary.last_frame),
        # get_current_frame() clamps to the animation range; a cursor outside it has to be
        # restored with its real value (T30-fix).
        "frame": int(scene.get_current_frame(False)),
        "camera": _camera_snapshot(view_scene),
        "minimized": bool(ui._is_minimized(window)),
    }


def _set_frame(view_scene, frame):
    """GUI thread: move the playhead and return the frame the cursor really sits on."""
    scene = view_scene.domain_scene()
    scene.set_current_frame(int(frame))
    return int(scene.get_current_frame(False))


def _restore(view_scene, frame, camera):
    """GUI thread: put the playhead and the camera back; return what was restored."""
    scene = view_scene.domain_scene()
    scene.set_current_frame(int(frame))
    return {
        "frame": int(scene.get_current_frame(False)),
        "camera": _camera_write(view_scene, camera),
    }


def _camera_preset_available() -> bool:
    """Whether T31's camera.py helpers needed for auto framing can be imported."""
    try:
        from .. import camera
    except ImportError:
        return False
    required = ("bbox", "bbox_center", "character_axes", "preset_camera", "write_camera", "viewport_fov")
    return all(callable(getattr(camera, name, None)) for name in required)


def _collect_motion(frames):
    """GUI thread: character positions at every rendered frame, with the playhead restored.

    Returns the union points of every pose, the axes of the most upright sampled pose (for
    camera.character_axes), the pelvis movement from the first to the last frame, and the
    viewport FOV. Multiple characters are all included in the union; axes and movement come
    from the character with the most controllers in each frame.
    """
    from .. import camera, rig

    _app, _view_scene, scene = common.scenes()
    previous = int(scene.get_current_frame(False))
    batches = []
    try:
        for frame in frames:
            scene.set_current_frame(int(frame))
            entries = [entry for entry in rig.controllers() if entry.get("position")]
            if not entries:
                continue
            groups = {}
            for entry in entries:
                key = entry.get("character") or ""
                group = groups.setdefault(key, {"points": [], "aliases": {}})
                position = [float(value) for value in entry["position"]]
                group["points"].append(position)
                alias = entry.get("alias")
                if alias and alias not in group["aliases"]:
                    group["aliases"][alias] = position
            largest = max(groups.values(), key=lambda group: len(group["points"]))
            batches.append(
                {
                    "frame": int(frame),
                    "points": [point for group in groups.values() for point in group["points"]],
                    "character_points": largest["points"],
                    "aliases": largest["aliases"],
                }
            )
    finally:
        scene.set_current_frame(previous)

    if not batches:
        return {
            "points": [],
            "axes": None,
            "movement": [0.0, 0.0, 0.0],
            "fov_deg": float(camera.viewport_fov()),
            "frames": [],
            "first_frame": None,
            "last_frame": None,
        }

    points = [point for batch in batches for point in batch["points"]]
    best_axes = None
    best_up = None
    for batch in batches:
        try:
            axes = camera.character_axes(batch["aliases"])
        except ValueError:
            continue
        up = float(axes["up"][1])
        if best_up is None or up > best_up:
            best_axes, best_up = axes, up
    if best_axes is None:  # pragma: no cover - character_axes falls back to the world axes
        best_axes = camera.character_axes({})

    first = batches[0]
    last = batches[-1]
    first_point = first["aliases"].get("pelvis") or camera.bbox_center(first["character_points"])
    last_point = last["aliases"].get("pelvis") or camera.bbox_center(last["character_points"])
    movement = [float(last_point[index]) - float(first_point[index]) for index in range(3)]
    return {
        "points": points,
        "axes": best_axes,
        "movement": movement,
        "fov_deg": float(camera.viewport_fov()),
        "frames": [batch["frame"] for batch in batches],
        "first_frame": batches[0]["frame"],
        "last_frame": batches[-1]["frame"],
    }


def _prepare_camera(frames, view, cell_width, cell_height):
    """GUI thread: fit one camera to every pose and write it; the playhead is restored."""
    from .. import camera

    motion = _collect_motion(frames)
    if not motion["points"]:
        raise ToolError(
            "No character control points were found in the rendered frames, so the automatic camera "
            "cannot be fitted. Pass camera_preset='current' to use the current viewport camera."
        )
    chosen_view = auto_view(motion["axes"], motion["movement"]) if view == "auto" else view
    aspect = float(cell_width) / float(cell_height)
    plan = plan_motion_camera(motion["points"], motion["axes"], chosen_view, motion["fov_deg"], aspect)
    written = camera.write_camera(position=plan["position"], target=plan["target"], type_name="PERSPECTIVE")
    config = {
        "position": [float(value) for value in written["position"]],
        "target": [float(value) for value in written["target"]],
        "type": str(written.get("type")),
        "distance": plan["distance"],
        "fov_deg": plan["fov_deg"],
        "aspect": plan["aspect"],
        "bbox": plan["bbox"],
    }
    return {"view": chosen_view, "plan": plan, "camera": config, "frames": motion["frames"]}


# ---------------------------------------------------------------------------
# Composition (HTTP thread; QImage only, never a window)
# ---------------------------------------------------------------------------


def _draw_label(painter, x: int, y: int, frame: int, cell_width: int) -> None:
    """Draw the frame number on a dark rounded box in the cell's top-left corner."""
    from PySide6 import QtCore, QtGui

    font = QtGui.QFont()
    font.setBold(True)
    font.setPixelSize(max(11, min(cell_width // 18, 48)))
    painter.setFont(font)
    metrics = QtGui.QFontMetrics(font)
    text = str(frame)
    padding = max(3, font.pixelSize() // 3)
    width = metrics.horizontalAdvance(text) + 2 * padding
    height = metrics.height() + 2 * padding
    rect = QtCore.QRect(x + 4, y + 4, width, height)
    painter.setPen(QtCore.Qt.NoPen)
    # Nearly opaque, so the number stays readable on light and dark renders alike.
    painter.setBrush(QtGui.QColor(16, 16, 16, 220))
    painter.drawRoundedRect(rect, 4, 4)
    painter.setPen(QtGui.QColor(255, 255, 255, 245))
    painter.drawText(rect, QtCore.Qt.AlignCenter, text)


def _compose_sheet(cell_files, frames, columns, cell_width, cell_height, output) -> dict:
    """Draw the rendered cells into one PNG on output and return its size."""
    from PySide6 import QtCore, QtGui

    width, height = sheet_size(len(cell_files), columns, cell_width, cell_height)
    sheet = QtGui.QImage(width, height, QtGui.QImage.Format_RGB32)
    sheet.fill(QtGui.QColor(24, 24, 24))
    painter = QtGui.QPainter(sheet)
    try:
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        painter.setRenderHint(QtGui.QPainter.TextAntialiasing)
        for index, (path, frame) in enumerate(zip(cell_files, frames, strict=True)):
            image = QtGui.QImage(path)
            if image.isNull():
                raise ToolError(f"Could not read the rendered cell {path!r}; check get_log for a renderer error.")
            if image.width() != cell_width or image.height() != cell_height:
                image = image.scaled(
                    cell_width,
                    cell_height,
                    QtCore.Qt.KeepAspectRatio,
                    QtCore.Qt.SmoothTransformation,
                )
            column = index % columns
            row = index // columns
            x = column * (cell_width + CELL_GAP)
            y = row * (cell_height + CELL_GAP)
            painter.drawImage(x, y, image)
            _draw_label(painter, x, y, frame, cell_width)
    finally:
        painter.end()
    if not sheet.save(output, "PNG"):
        raise ToolError(f"Could not write the contact sheet to {output!r}; check that the folder is writable.")
    return {"width": width, "height": height}


# ---------------------------------------------------------------------------
# The tool handler (HTTP thread)
# ---------------------------------------------------------------------------


def render_contact_sheet(
    start=None,
    end=None,
    step=None,
    frames=None,
    columns=DEFAULT_COLUMNS,
    cell_width=DEFAULT_CELL_WIDTH,
    camera_preset=DEFAULT_CAMERA_PRESET,
    path=None,
    overwrite=False,
):
    """Render a grid of frames; runs on the HTTP thread because the renders wait for files."""
    try:
        value = _render_contact_sheet(start, end, step, frames, columns, cell_width, camera_preset, path, overwrite)
    except _MinimizedWindow as e:
        _STATE.image = False
        return common.fail(
            str(e),
            hint="Restore the Cascadeur window first (screenshot with restore=true, or run_python "
            "ui.restore_window()), then call render_contact_sheet again.",
        )
    except render.RenderTimeout as e:
        _STATE.image = False
        return common.fail(
            str(e),
            hint="The queued render may still write a cell file later; the temporary cell folder is "
            "deleted, so retry after checking the window is visible and get_log has no renderer error.",
        )
    except ToolError as e:
        _STATE.image = False
        return common.fail(str(e), getattr(e, "hint", None))
    except Exception as e:  # keep the tool result format even for a bug or a Cascadeur timeout
        _STATE.image = False
        return common.fail(
            f"{type(e).__name__}: {e}",
            hint="Check get_log for the traceback and get_scene_info for the open scene.",
        )
    _STATE.image = isinstance(value, tuple)
    return value


def _render_contact_sheet(start, end, step, frames, columns, cell_width, camera_preset, path, overwrite):
    started = time.monotonic()
    columns = _whole_number("columns", columns, 1, MAX_COLUMNS)
    cell_width = _whole_number("cell_width", cell_width, MIN_CELL_WIDTH, MAX_CELL_WIDTH)
    cell_height = cell_height_for(cell_width)
    target = _output_path(path, overwrite)
    mode = camera_mode(camera_preset)
    if mode not in CAMERA_MODES:
        raise _BadArgument(
            f"Unknown camera_preset {camera_preset!r}; use auto, current, {', '.join(CAMERA_PRESETS)}.",
            hint="Use auto (default) to fit the whole motion, current for the viewport camera, or one of T31's views.",
        )
    if mode != "current" and not _camera_preset_available():
        raise _BadArgument(
            "Automatic camera framing and view presets need ticket T31's camera.py, which this checkout does not have.",
            hint="Pass camera_preset='current' to use the current camera, or run a build that includes T31's camera tools.",
        )

    prepared = dispatch.call_on_gui(_prepare, {}, 30.0)
    if prepared["minimized"]:
        raise _MinimizedWindow(
            "Cascadeur's window is minimized, and RenderToFile only renders while the window is visible "
            "(Cascadeur 2026.2.3 keeps the render queued until the window comes back)."
        )
    view_scene = prepared["view_scene"]
    previous = prepared["frame"]
    camera = prepared["camera"]
    chosen, source = plan_frames(frames, start, end, step, prepared["first_frame"], prepared["last_frame"])
    # Never leave a trailing empty column: a 3-frame sheet has 3 columns even when the default
    # asked for 4.
    layout_columns = min(columns, len(chosen))
    sheet_width, sheet_height = sheet_size(len(chosen), layout_columns, cell_width, cell_height)
    if sheet_width > MAX_SHEET_SIDE or sheet_height > MAX_SHEET_SIDE or sheet_width * sheet_height > MAX_SHEET_PIXELS:
        raise _BadArgument(
            f"The sheet would be {sheet_width}x{sheet_height} pixels, which is too large to compose in memory.",
            hint="Use a smaller cell_width, fewer columns or fewer frames.",
        )

    cells_dir = tempfile.mkdtemp(prefix="cells-", dir=common.temp_dir("contact_sheets"))
    cell_files = []
    rendered_frames = []
    output_tmp = render.temp_output_path(target)
    error = None
    restored = None
    camera_info = None
    try:
        if mode != "current":
            camera_info = dispatch.call_on_gui(
                _prepare_camera,
                {"frames": chosen, "view": mode, "cell_width": cell_width, "cell_height": cell_height},
                120.0,
            )
        for index, frame in enumerate(chosen):
            actual = dispatch.call_on_gui(_set_frame, {"view_scene": view_scene, "frame": frame}, 30.0)
            cell = os.path.join(cells_dir, f"cell-{index:03d}-{actual}.png")
            render.render_to_file(view_scene, cell, cell_width, cell_height)
            cell_files.append(cell)
            rendered_frames.append(actual)
        _compose_sheet(cell_files, rendered_frames, layout_columns, cell_width, cell_height, output_tmp)
        render.move_file(output_tmp, target)
    except Exception as e:  # the playhead and camera are restored in finally
        error = e
    finally:
        try:
            restored = dispatch.call_on_gui(
                _restore,
                {"view_scene": view_scene, "frame": previous, "camera": camera},
                30.0,
            )
        except Exception as e:
            error = error or e
        shutil.rmtree(cells_dir, ignore_errors=True)
        if error is not None and os.path.exists(output_tmp):
            try:
                os.remove(output_tmp)
            except OSError:
                pass
    if error is not None:
        raise error

    rows = max(1, math.ceil(len(rendered_frames) / layout_columns))
    file_size = render.image_size(target)
    data, mime, image_meta = render.encode_image(target, IMAGE_MAX_WIDTH)
    meta = {
        "path": target,
        "frames": rendered_frames,
        "requested_frames": chosen,
        "count": len(rendered_frames),
        "selection": source,
        "columns": layout_columns,
        "rows": rows,
        "cell_width": cell_width,
        "cell_height": cell_height,
        "file_bytes": os.path.getsize(target),
        "current_frame": restored["frame"] if restored else previous,
        "frame_restored": bool(restored and restored["frame"] == previous),
        "camera_restored": camera is None or bool(restored and restored["camera"]),
        "camera_preset": mode,
        "camera_view": camera_info["view"] if camera_info else None,
        "camera": camera_info["camera"] if camera_info else None,
        "camera_frames": camera_info["frames"] if camera_info else [],
        "seconds": round(time.monotonic() - started, 3),
        "image_content": True,
    }
    if file_size is not None:
        meta["file_size"] = list(file_size)
    meta.update(image_meta)
    if rendered_frames != chosen:
        meta["note"] = "Cascadeur clamped at least one frame to the animation range."
    meta["ok"] = True
    return data, mime, meta


TOOLS = [
    {
        "name": "render_contact_sheet",
        "description": (
            "Render a contact sheet: a grid of frames from the current animation, one rendered cell per frame "
            "with the frame number in the cell's corner. Use it to check a whole motion in one picture before "
            "encoding it; render_video makes the final MP4, and screenshot is only for the interface. Pick frames "
            "with frames=[0, 10, 20] (a list, one frame or a comma-separated "
            "string) or with start/end/step; with none of them eight frames are sampled evenly from the whole "
            "animation. columns sets the cells per row (default 4; fewer frames mean fewer columns) and cell_width "
            "the width of one 16:9 cell in pixels (default 320). By default camera_preset='auto' reads the "
            "character's control points at every rendered frame and fits ONE fixed camera to the union of all "
            "poses (a side view when the motion is forward/backward), so the whole motion stays in view even when "
            "the character travels. camera_preset also accepts current (the viewport camera, the old behaviour) or "
            "front/back/left/right/top/three_quarter, which fit the same union from that view. Every cell uses the "
            "same camera. The PNG goes to %TEMP%\\cascadeur-mcp-plus\\contact_sheets unless path is given "
            "(absolute, ending in .png); an existing file is only replaced with overwrite=true. Returns the image "
            "content plus the file path, the rendered frames, the camera used, the sheet's pixel size and timing. "
            "The playhead and camera are restored after the call, including a playhead outside the animation "
            "range. Cascadeur must not be minimized (RenderToFile only draws while the window is visible). At most "
            "64 frames fit in one sheet."
        ),
        "inputSchema": schema(
            {
                "start": {
                    "type": ["integer", "string"],
                    "description": "First frame of the interval (default: the animation's first frame).",
                },
                "end": {
                    "type": ["integer", "string"],
                    "description": "Last frame of the interval, inclusive (default: the animation's last frame).",
                },
                "step": {
                    "type": ["integer", "string"],
                    "description": "Frame step for start/end (default 1); leave start/end/step out to sample 8 evenly.",
                },
                "frames": {
                    "type": ["integer", "array", "string"],
                    "items": {"type": "integer"},
                    "description": "Exact frames to render, for example [0, 10, 20]; cannot be combined with start/end/step.",
                },
                "columns": {
                    "type": "integer",
                    "description": "Cells per row (default 4; reduced when there are fewer frames).",
                },
                "cell_width": {
                    "type": "integer",
                    "description": "Width of one cell in pixels (default 320); the 16:9 height follows from it.",
                },
                "camera_preset": {
                    "type": "string",
                    "default": "auto",
                    "description": "auto (default) fits the whole motion from a side view; current keeps the viewport "
                    "camera; front/back/left/right/top/three_quarter fit the same motion from that view.",
                },
                "path": {
                    "type": "string",
                    "description": "Absolute output path ending in .png (default: a new file under %TEMP%).",
                },
                "overwrite": {"type": "boolean", "description": "Replace an existing file at path (default false)."},
            }
        ),
        "requires": [
            "csc.app.Application.get_setting_manager",
            "csc.app.Application.get_tools_manager",
            "csc.app.ToolsManager.get_tool",
            "csc.app.get_application",
            "csc.domain.Scene.get_current_frame",
            "csc.domain.Scene.model_viewer",
            "csc.domain.Scene.set_current_frame",
            "csc.model.BehaviourViewer.get_behaviours",
            "csc.model.ModelViewer.get_objects",
            "csc.tools.RenderParameters",
            "csc.tools.RenderToFile.take_image",
            "csc.view.CameraType",
            "csc.view.Viewport.domain_viewport",
        ],
        "handler": render_contact_sheet,
        "image": _ImageFlag(),
    },
]
