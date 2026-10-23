"""render_image: one picture of the 3D scene from Cascadeur's own renderer (T30).

The tool is a thin argument layer over cascadeur_mcp_plus/render.py: it validates the path,
switches the frame when asked, hands the render to render.render_to_file() (which queues the
render on the GUI thread and waits on this HTTP thread) and returns the file plus, when
return_image is true, a downscaled copy as image content like screenshot does.

T31 adds camera_preset: the preset camera (cascadeur_mcp_plus/camera.py) is applied after the
frame switch and the original camera is restored only once the render has finished writing its
file - restoring earlier could let the queued renderer capture the restored view.
"""

from __future__ import annotations

import os
import threading

from .. import camera, common, dispatch, render
from ..errors import ToolError
from ._schema import schema

_STATE = threading.local()


class _ImageFlag:
    """Truthy only for a call that returned image content.

    The registry's "image" field is static, but server._call_tool checks it after the handler has
    returned, so a tool that only sometimes returns an image can decide the answer per call. A
    request stays on one HTTP thread, so a thread-local flag cannot leak into another call.
    T30's ticket does not allow editing server.py, and T33's contact sheet will want the same.
    """

    def __bool__(self) -> bool:
        return bool(getattr(_STATE, "image", False))

    def __repr__(self) -> str:
        return "<image content when the call returns one>"


def _frame_number(frame):
    if frame is None:
        return None
    if isinstance(frame, bool) or not isinstance(frame, (int, float)) or int(frame) != frame:
        raise ToolError(f"frame must be a whole frame number (got {frame!r}).")
    return int(frame)


def _output_path(path, overwrite):
    if not path:
        return render.default_path(common.temp_dir("renders"))
    target = common.output_path(path, overwrite=overwrite, extensions=(".png",))
    # RenderToFile only accepts a lowercase ".png": with "shot.PNG" it writes "shot.PNG.png"
    # instead, which wait_for_file() would never see. Normalize so the file lands where the
    # caller asked (the check above already matched the extension case-insensitively).
    stem, extension = os.path.splitext(target)
    return stem + ".png" if extension != ".png" else target


class _MinimizedWindow(ToolError):
    """Cascadeur's window is minimized, and RenderToFile only draws while it is visible."""


def _scene_state():
    """GUI thread: the active view scene, its current frame and whether the window is minimized."""
    from .. import ui

    _, view_scene, scene = common.scenes()
    window = ui.resolve_window()[1]
    # ui._is_minimized is the same Win32 check screenshot and restore_window use; T30 may not
    # add a public alias to ui.py, so the handoff note asks for one when T42 rewrites the tools.
    return {
        "view_scene": view_scene,
        # clamp_animation=True (the default) would report the animation end for a cursor that
        # sits outside the animation range, and restoring that value would move the cursor.
        "frame": scene.get_current_frame(False),
        "minimized": bool(ui._is_minimized(window)),
    }


def _set_frame(view_scene, frame):
    """GUI thread: move the timeline and return the frame the cursor really sits on."""
    scene = view_scene.domain_scene()
    scene.set_current_frame(int(frame))
    return scene.get_current_frame(False)


def render_image(
    frame=None,
    width=render.DEFAULT_WIDTH,
    height=render.DEFAULT_HEIGHT,
    samples=None,
    path=None,
    overwrite=False,
    return_image=True,
    max_width=render.DEFAULT_WIDTH,
    camera_preset=None,
):
    """Render one image; runs on the HTTP thread because it waits for the file."""
    try:
        value = _render_image(frame, width, height, samples, path, overwrite, return_image, max_width, camera_preset)
    except _MinimizedWindow as e:
        _STATE.image = False
        return common.fail(
            str(e),
            hint="Restore the Cascadeur window first (screenshot with restore=true, or run_python "
            "ui.restore_window()), then call render_image again.",
        )
    except render.RenderTimeout as e:
        _STATE.image = False
        return common.fail(
            str(e),
            hint="The job stays queued, so the file named in the error may appear later with the wrong "
            "picture (for example after the window is restored). Delete it before retrying. If the window "
            "was minimized, restore it first with screenshot(restore=true) or run_python ui.restore_window().",
        )
    except ToolError as e:
        _STATE.image = False
        return common.fail(str(e))
    except Exception as e:  # keep the tool result format even for a bug or a Cascadeur timeout
        _STATE.image = False
        return common.fail(
            f"{type(e).__name__}: {e}",
            hint="Check get_log for the traceback and get_scene_info for the open scene.",
        )
    _STATE.image = isinstance(value, tuple)
    return value


def _camera_preset_name(camera_preset):
    if camera_preset is None:
        return None
    name = str(camera_preset).strip().lower()
    if name not in camera.VIEWS:
        raise ToolError(f"Unknown camera_preset {camera_preset!r}; use one of: {', '.join(camera.VIEWS)}.")
    return name


def _apply_camera_preset(view, aspect):
    """GUI thread: switch the viewport camera to a preset view of the scene's character."""
    return camera.apply_preset(view, target="character", aspect=aspect)


def _render_image(frame, width, height, samples, path, overwrite, return_image, max_width, camera_preset):
    frame = _frame_number(frame)
    preset = _camera_preset_name(camera_preset)
    output = _output_path(path, overwrite)
    prepared = dispatch.call_on_gui(_scene_state, {}, 30.0)
    if prepared["minimized"]:
        raise _MinimizedWindow(
            "Cascadeur's window is minimized, and RenderToFile only renders while the window is visible "
            "(Cascadeur 2026.2.3 keeps the render queued until the window comes back)."
        )
    view_scene = prepared["view_scene"]
    previous = prepared["frame"]
    changed = frame is not None and frame != previous
    rendered = previous
    current = previous
    saved_camera = dispatch.call_on_gui(camera.snapshot, {}, 30.0) if preset else None
    applied = None
    restored = None
    error = None
    try:
        if changed:
            rendered = dispatch.call_on_gui(_set_frame, {"view_scene": view_scene, "frame": frame}, 30.0)
        if preset:
            # The render's aspect ratio, not the viewport's: a 16:9 render of a wider viewport
            # must still contain the whole character.
            applied = dispatch.call_on_gui(_apply_camera_preset, {"view": preset, "aspect": width / height}, 60.0)
        info = render.render_to_file(view_scene, output, width, height, samples)
    except Exception as e:  # the timeline and camera are restored below even when the render fails
        error = e
        info = None
    finally:
        if saved_camera is not None:
            try:
                restored = dispatch.call_on_gui(camera.restore, {"saved": saved_camera}, 30.0)
            except Exception as e:
                restored = None
                error = error or e
        if changed:
            try:
                current = dispatch.call_on_gui(_set_frame, {"view_scene": view_scene, "frame": previous}, 30.0)
            except Exception as e:
                current = None
                error = error or e
    if error is not None:
        raise error

    meta = {
        "path": output,
        "frame": rendered,
        "current_frame": current,
        "file_bytes": info["bytes"],
        "width": info["width"],
        "height": info["height"],
        "samples": info["samples"],
        "seconds": info["seconds"],
    }
    size = render.image_size(output)
    if size is not None:
        meta["file_size"] = [size[0], size[1]]
    if preset:
        meta["camera_preset"] = preset
        if applied is not None:
            meta["camera"] = applied["camera"]
        meta["camera_restored"] = restored is not None
        if restored is not None and any(
            abs(float(restored[key][index]) - float(saved_camera[key][index])) > 0.01
            for key in ("position", "target")
            for index in range(3)
        ):
            meta["camera_restored"] = False
            meta.setdefault(
                "note", "The camera could not be restored exactly; check get_camera before rendering again."
            )
    if frame is not None and rendered != frame:
        meta["requested_frame"] = frame
        meta["note"] = "Cascadeur clamped the frame to the animation range."
    if current is None:
        meta["note"] = "The timeline could not be switched back; check get_scene_info before rendering again."
    if not return_image:
        return common.ok(image_content=False, **meta)
    data, mime, image_meta = render.encode_image(output, max_width)
    meta.update(image_meta)
    meta["image_content"] = True
    meta["ok"] = True
    return data, mime, meta


TOOLS = [
    {
        "name": "render_image",
        "description": (
            "Render one image of the 3D scene with Cascadeur's own renderer (RenderToFile), so the picture does "
            "not depend on other windows covering Cascadeur. Use it to look at the scene or to grab a still from "
            "an animation. The file goes to %TEMP%\\cascadeur-mcp-plus\\renders unless path is given; path must "
            "end in .png (the extension is written in lowercase). Returns the image content (downscaled to "
            "max_width pixels) plus the file path, the rendered frame and the file's pixel size; with "
            "return_image=false only the JSON is returned. width and height are pixels and set the file's "
            "resolution. frame is a timeline frame number; the cursor's previous position is restored after the "
            "render, including a position outside the animation range. Cascadeur must not be minimized "
            "(RenderToFile only draws while the window is visible, so the tool fails with a hint instead of "
            "hanging). A timed-out render cannot be cancelled, so its temporary file may still appear later; the "
            "error names the file to delete before retrying. The camera is the current viewport's unless "
            "camera_preset is given: front, back, left, right, top or three_quarter frames the scene's "
            "character with the preset view (front follows the character's own facing) and the original camera "
            "is restored after the render."
        ),
        "inputSchema": schema(
            {
                "frame": {
                    "type": "integer",
                    "description": "Timeline frame to render (default: the current frame); the previous frame is restored.",
                },
                "width": {"type": "integer", "description": "Image width in pixels (default 1280)."},
                "height": {"type": "integer", "description": "Image height in pixels (default 720)."},
                "samples": {
                    "type": "integer",
                    "description": "Anti-aliasing samples per pixel; omitting it keeps Cascadeur's default.",
                },
                "path": {
                    "type": "string",
                    "description": "Absolute output path ending in .png (lowercase; default: a new file under %TEMP%).",
                },
                "overwrite": {"type": "boolean", "description": "Replace an existing file at path (default false)."},
                "return_image": {
                    "type": "boolean",
                    "description": "Also return the image content, not just the file path (default true).",
                },
                "max_width": {
                    "type": "integer",
                    "description": "Downscale the returned image content to this width in pixels (default 1280).",
                },
                "camera_preset": {
                    "type": "string",
                    "enum": list(camera.VIEWS),
                    "description": "Frame the character with a preset view (front, back, left, right, top, three_quarter) "
                    "before rendering; the original camera is restored afterwards.",
                },
            }
        ),
        "handler": render_image,
        "image": _ImageFlag(),
    },
]
