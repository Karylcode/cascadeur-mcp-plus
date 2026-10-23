"""Render the 3D scene to image files with Cascadeur's own renderer (T30).

RenderToFile draws the scene through Cascadeur's renderer, so the picture does not depend on
the window: a minimized or covered window gives the same image, and no window capture is
involved (QQuickWindow.grabWindow() crashes Cascadeur, see the hard limits in
docs/tickets/README.md).

Rendering is asynchronous: take_image() returns before the file is complete. Cascadeur's GUI
thread must never sleep, so the waiting lives in wait_for_file() and happens on the HTTP thread
that serves a tool call; render_to_file() does that split for the whole render (its GUI part
goes through dispatch.call_on_gui). T32 (video) and T33 (contact sheet) reuse these helpers, so
everything here is free of tool-specific argument handling.

Nothing in this module needs Cascadeur at import time: csc and PySide6 are imported inside the
functions that use them, which keeps the file testable from ordinary Python.
"""

from __future__ import annotations

import base64
import itertools
import os
import struct
import time

from .errors import ToolError

DEFAULT_WIDTH = 1280
DEFAULT_HEIGHT = 720
DEFAULT_TIMEOUT = 180.0
MAX_SIZE = 16384
MAX_SAMPLES = 4096
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_counter = itertools.count(1)


# ---------------------------------------------------------------------------
# Paths and arguments
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


def validate_size(width=DEFAULT_WIDTH, height=DEFAULT_HEIGHT, samples=None):
    """Return (width, height, samples) as ints, checking that the renderer can use them.

    samples stays None when it is None, which leaves Cascadeur's own default in place. Every
    bad value raises ToolError, so a tool can report it before anything is rendered.
    """
    width = _whole_number("width", width, 1, MAX_SIZE)
    height = _whole_number("height", height, 1, MAX_SIZE)
    if samples is not None:
        samples = _whole_number("samples", samples, 1, MAX_SAMPLES)
    return width, height, samples


def default_path(folder, extension: str = ".png", prefix: str = "render") -> str:
    """A unique image path inside folder, usable when the caller did not pass one."""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return os.path.join(folder, f"{prefix}-{stamp}-{os.getpid()}-{next(_counter)}{extension}")


# ---------------------------------------------------------------------------
# Starting a render (GUI thread)
# ---------------------------------------------------------------------------


def render_tool():
    """Cascadeur's RenderToFile tool (csc.tools.RenderToFile)."""
    import csc

    manager = csc.app.get_application().get_tools_manager()
    try:
        tool = manager.get_tool("RenderToFile")
    except Exception as e:  # pragma: no cover - only reachable inside Cascadeur
        raise ToolError(f"Cascadeur's RenderToFile tool is unavailable ({e}). Check the Cascadeur version.") from e
    if tool is None:  # pragma: no cover - only reachable inside Cascadeur
        raise ToolError("Cascadeur's RenderToFile tool is unavailable in this build; render_image cannot work.")
    return tool


def render_parameters(width=DEFAULT_WIDTH, height=DEFAULT_HEIGHT, samples=None):
    """csc.tools.RenderParameters with width/height in pixels and samples only when given."""
    import csc

    parameters = csc.tools.RenderParameters()
    width, height, samples = validate_size(width, height, samples)
    parameters.width = width
    parameters.height = height
    if samples is not None:
        parameters.samples = samples
    return parameters


def take_image(view_scene, path, width=DEFAULT_WIDTH, height=DEFAULT_HEIGHT, samples=None) -> None:
    """Ask RenderToFile for one image of view_scene in path; returns before the file is complete."""
    render_tool().take_image(view_scene, render_parameters(width, height, samples), path)


# ---------------------------------------------------------------------------
# Waiting for the file (HTTP thread)
# ---------------------------------------------------------------------------


def output_file(path) -> str:
    """Absolute output path whose folder exists, with any old file removed.

    The old file has to go: wait_for_file() would otherwise accept a complete image from an
    earlier render. The caller decides whether replacing is allowed (common.output_path).
    """
    path = os.path.abspath(path)
    folder = os.path.dirname(path)
    if not os.path.isdir(folder):
        raise ToolError(f"Output folder does not exist: {folder!r}. Create it first or pass an existing folder.")
    if os.path.isdir(path):
        raise ToolError(f"Output path is a folder, not a file: {path!r}.")
    if os.path.exists(path):
        try:
            os.remove(path)
        except OSError as e:
            raise ToolError(f"Could not replace the existing file {path!r}: {e}") from e
    return path


def image_complete(path) -> bool:
    """True when the file looks like a finished image (PNG IEND, JPEG end marker, or unknown)."""
    try:
        with open(path, "rb") as handle:
            head = handle.read(8)
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - 64))
            tail = handle.read()
    except OSError:
        return False
    if head.startswith(PNG_SIGNATURE):
        return b"IEND" in tail
    if head.startswith(b"\xff\xd8"):
        return tail.rstrip(b"\x00").endswith(b"\xff\xd9")
    return True


def wait_for_file(path, timeout=DEFAULT_TIMEOUT, poll: float = 0.05, stable: float = 0.25) -> dict:
    """Wait until path holds a finished image and return {"path", "bytes", "seconds"}.

    The renderer may create the file first and fill it in afterwards, so the file only counts
    as finished when it is not empty, its size has not changed for a moment, and
    image_complete() finds the format's end marker. Raises ToolError after timeout.

    Call this on the HTTP thread, never on Cascadeur's GUI thread.
    """
    started = time.monotonic()
    deadline = started + max(0.1, float(timeout))
    last_size = None
    changed_at = started
    while True:
        try:
            size = os.path.getsize(path)
        except OSError:
            size = 0
        now = time.monotonic()
        if size != last_size:
            last_size = size
            changed_at = now
        elif size > 0 and now - changed_at >= stable and image_complete(path):
            return {"path": path, "bytes": size, "seconds": round(now - started, 3)}
        if now >= deadline:
            detail = f"{size} byte(s) written so far" if size else "no file was created"
            raise ToolError(
                f"Cascadeur did not finish rendering {path!r} within {float(timeout):.0f}s ({detail}). "
                "Check get_log for a renderer error, try a smaller width/height or fewer samples, "
                "and make sure the 3D viewport is showing the scene."
            )
        time.sleep(max(0.01, float(poll)))


def render_to_file(
    view_scene,
    path,
    width=DEFAULT_WIDTH,
    height=DEFAULT_HEIGHT,
    samples=None,
    timeout=DEFAULT_TIMEOUT,
) -> dict:
    """Render view_scene into path and wait until the file is complete.

    Runs on the thread that serves a tool call (a handler's HTTP thread): take_image() is queued
    on the GUI thread through dispatch.call_on_gui and this thread waits. output_file() checks
    the path and removes an old file first, so the waiting can never accept a stale image.
    Returns wait_for_file()'s details plus the requested width, height and samples; raises
    ToolError when the folder is missing or no finished file appears within timeout.
    """
    width, height, samples = validate_size(width, height, samples)
    path = output_file(path)

    # Imported here so the checks above (and this module's unit tests) never need Qt.
    from . import dispatch

    dispatch.call_on_gui(
        take_image,
        {"view_scene": view_scene, "path": path, "width": width, "height": height, "samples": samples},
        max(30.0, float(timeout)),
    )
    info = wait_for_file(path, timeout=timeout)
    info.update({"width": width, "height": height, "samples": samples})
    return info


# ---------------------------------------------------------------------------
# Reading the result
# ---------------------------------------------------------------------------


def image_size(path):
    """(width, height) from a PNG header, or None for other formats (they need Qt to decode)."""
    try:
        with open(path, "rb") as handle:
            header = handle.read(24)
    except OSError:
        return None
    if len(header) < 24 or not header.startswith(PNG_SIGNATURE) or header[12:16] != b"IHDR":
        return None
    return struct.unpack(">II", header[16:24])


def image_format(path):
    """The MIME type a rendered file actually holds, judged by its magic bytes."""
    try:
        with open(path, "rb") as handle:
            head = handle.read(2)
    except OSError:
        return None
    if head == PNG_SIGNATURE[:2]:
        return "image/png"
    if head == b"\xff\xd8":
        return "image/jpeg"
    return None


def encode_image(path, max_width=DEFAULT_WIDTH):
    """Return (base64_text, mime_type, meta) for a rendered image, downscaled to max_width.

    A PNG that already fits max_width is returned byte for byte, so no image decoding happens
    for the common 1280-wide render. Anything else (wider images, JPEG) is decoded, scaled and
    re-encoded through Qt; that work touches nothing but the file, so it is safe on the HTTP
    thread and never sleeps on the GUI thread.
    """
    size = image_size(path)
    if size is not None:
        limit = int(max_width) if max_width else 0
        if not limit or size[0] <= limit:
            with open(path, "rb") as handle:
                data = base64.b64encode(handle.read()).decode("ascii")
            return data, "image/png", {"image_size": [size[0], size[1]], "scaled": False}
    return _encode_with_qt(path, max_width, image_format(path))


def _encode_with_qt(path, max_width, mime):
    from PySide6 import QtCore, QtGui

    image = QtGui.QImage(path)
    if image.isNull():
        raise ToolError(f"Could not read the rendered image {path!r}; check get_log for a renderer error.")
    scaled = False
    limit = int(max_width) if max_width else 0
    if limit and image.width() > limit:
        image = image.scaledToWidth(limit, QtCore.Qt.SmoothTransformation)
        scaled = True
    buffer = QtCore.QBuffer()
    buffer.open(QtCore.QIODevice.WriteOnly)
    image.save(buffer, "PNG")
    data = base64.b64encode(bytes(buffer.data())).decode("ascii")
    meta = {"image_size": [image.width(), image.height()], "scaled": scaled}
    if mime:
        meta["source_mime"] = mime
    return data, "image/png", meta
