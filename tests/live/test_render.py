"""Live tests for render_image (T30).

The tests decode the rendered PNGs themselves (stdlib zlib/struct) so they can prove the
picture actually contains the character and that different frames give different pictures.

Run with the live-test lock held and MCP Plus restarted from this worktree:

    python docs/tickets/bin/live.py lock T30
    python docs/tickets/bin/live.py restart C:/Users/User/Tools/cascadeur-mcp-plus-wt/T30
    python tests/live/run.py test_render.py
"""

from __future__ import annotations

import ast
import base64
import os
import struct
import tempfile
import time
import unittest
import zlib

try:
    from . import client
    from .fixture import LiveTestCase
except ImportError:
    import client
    from fixture import LiveTestCase

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
RENDERS = os.path.join(tempfile.gettempdir(), "cascadeur-mcp-plus", "renders")
HTTP_TIMEOUT = 300.0


# ---------------------------------------------------------------------------
# Minimal PNG reader: only what a rendered 8-bit RGB/RGBA PNG needs
# ---------------------------------------------------------------------------


def decode_png(data: bytes):
    """Return (width, height, channels, rows) for the PNG in data, rows without filter bytes."""
    if not data.startswith(PNG_SIGNATURE):
        raise AssertionError(f"render_image returned something that is not a PNG: {data[:16]!r}")
    position = len(PNG_SIGNATURE)
    header = None
    idat = b""
    while position + 8 <= len(data):
        length = struct.unpack(">I", data[position : position + 4])[0]
        kind = data[position + 4 : position + 8]
        payload = data[position + 8 : position + 8 + length]
        position += 12 + length
        if kind == b"IHDR":
            header = struct.unpack(">IIBBBBB", payload)
        elif kind == b"IDAT":
            idat += payload
        elif kind == b"IEND":
            break
    if header is None:
        raise AssertionError("the rendered PNG has no IHDR chunk")
    width, height, depth, color, _, _, interlace = header
    if depth != 8 or interlace != 0 or color not in (2, 6):
        raise AssertionError(f"unexpected PNG format: depth={depth} color={color} interlace={interlace}")
    channels = 3 if color == 2 else 4
    raw = zlib.decompress(idat)
    stride = width * channels
    rows = []
    previous = bytearray(stride)
    position = 0
    for _ in range(height):
        filter_type = raw[position]
        line = bytearray(raw[position + 1 : position + 1 + stride])
        position += 1 + stride
        if filter_type == 1:
            for i in range(channels, stride):
                line[i] = (line[i] + line[i - channels]) & 0xFF
        elif filter_type == 2:
            for i in range(stride):
                line[i] = (line[i] + previous[i]) & 0xFF
        elif filter_type == 3:
            for i in range(stride):
                left = line[i - channels] if i >= channels else 0
                line[i] = (line[i] + ((left + previous[i]) >> 1)) & 0xFF
        elif filter_type == 4:
            for i in range(stride):
                a = line[i - channels] if i >= channels else 0
                b = previous[i]
                c = previous[i - channels] if i >= channels else 0
                estimate = a + b - c
                distance_a = abs(estimate - a)
                distance_b = abs(estimate - b)
                distance_c = abs(estimate - c)
                if distance_a <= distance_b and distance_a <= distance_c:
                    predicted = a
                elif distance_b <= distance_c:
                    predicted = b
                else:
                    predicted = c
                line[i] = (line[i] + predicted) & 0xFF
        elif filter_type != 0:
            raise AssertionError(f"unknown PNG filter type {filter_type}")
        rows.append(line)
        previous = line
    return width, height, channels, rows


def mean_brightness(image, step: int = 4) -> float:
    _, _, channels, rows = image
    total = count = 0
    for line in rows[::step]:
        for index in range(0, len(line) - channels + 1, channels * step):
            total += line[index] + line[index + 1] + line[index + 2]
            count += 1
    return total / max(1, count) / 3


def distinct_colors(image, step: int = 4) -> int:
    _, _, channels, rows = image
    colors = set()
    for line in rows[::step]:
        for index in range(0, len(line) - channels + 1, channels * step):
            colors.add(bytes(line[index : index + 3]))
    return len(colors)


def edge_ratio(image, offset: int = 6, threshold: int = 24, step: int = 3) -> float:
    """Fraction of sampled pixels that differ strongly from the pixel a few pixels to the right.

    A flat or smoothly shaded background has almost none; the character's silhouette and
    materials do, so this proves something was drawn without depending on exact colours.
    """
    _, _, channels, rows = image
    inside = total = 0
    for line in rows[::2]:
        limit = len(line) - (offset + 1) * channels
        for index in range(0, max(0, limit), channels * step):
            other = index + offset * channels
            total += 1
            if (
                max(
                    abs(line[index] - line[other]),
                    abs(line[index + 1] - line[other + 1]),
                    abs(line[index + 2] - line[other + 2]),
                )
                >= threshold
            ):
                inside += 1
    return inside / max(1, total)


def difference_ratio(first, second, threshold: int = 8, step: int = 4) -> float:
    """Fraction of sampled pixels that differ between two images of the same size."""
    if (first[0], first[1]) != (second[0], second[1]):
        raise AssertionError(f"images differ in size: {first[:2]} vs {second[:2]}")
    channels = first[2]
    different = total = 0
    for line_a, line_b in zip(first[3], second[3], strict=True):
        for index in range(0, len(line_a) - channels + 1, channels * step):
            total += 1
            if (
                max(
                    abs(line_a[index] - line_b[index]),
                    abs(line_a[index + 1] - line_b[index + 1]),
                    abs(line_a[index + 2] - line_b[index + 2]),
                )
                > threshold
            ):
                different += 1
    return different / max(1, total)


def file_size(path: str) -> tuple[int, int]:
    """(width, height) straight from the PNG header of a rendered file."""
    with open(path, "rb") as handle:
        header = handle.read(24)
    if not header.startswith(PNG_SIGNATURE) or header[12:16] != b"IHDR":
        raise AssertionError(f"{path} is not a PNG")
    return struct.unpack(">II", header[16:24])


def run_json(code: str):
    """Run code inside Cascadeur that sets `result` to a dict and return that dict."""
    answer = client.call("run_python", code=code)
    if not answer.get("ok"):
        raise AssertionError(f"run_python failed: {answer!r}")
    return ast.literal_eval(answer["value"])


def image_of(result: dict):
    """Decode the image content of a render_image result."""
    if "images" not in result:
        raise AssertionError(f"render_image returned no image content: {result!r}")
    return decode_png(base64.b64decode(result["images"][0]["data"]))


# ---------------------------------------------------------------------------
# Cascadeur-side helpers
# ---------------------------------------------------------------------------


MINIMIZE_CODE = """
window = ui.resolve_window()[1]
window.showMinimized()
result = True
"""

RESTORE_CODE = """
import ctypes

ui.restore_window()
result = True
"""

WINDOW_STATE_CODE = """
import ctypes

result = bool(ctypes.windll.user32.IsIconic(int(ui.resolve_window()[1].winId())))
"""


class CoverWindow:
    """An opaque topmost window that hides Cascadeur for the length of one render."""

    def __init__(self) -> None:
        import tkinter

        self._tkinter = tkinter
        self.root = tkinter.Tk()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        width = self.root.winfo_screenwidth()
        height = self.root.winfo_screenheight()
        self.root.geometry(f"{width}x{height}+0+0")
        self.root.configure(background="black")

    def show(self) -> None:
        self.root.deiconify()
        self.root.update()

    def close(self) -> None:
        try:
            self.root.destroy()
        except self._tkinter.TclError:
            pass


class RenderImageTests(LiveTestCase):
    def render(self, **arguments):
        return client.call("render_image", http_timeout=HTTP_TIMEOUT, **arguments)

    def current_frame(self) -> int:
        return client.call("get_scene_info")["frames"]["current"]

    def test_renders_the_character_and_writes_to_the_default_folder(self):
        self.open_sample("Cascy.casc")
        result = self.render(width=640, height=360, samples=8)
        image = image_of(result)
        meta = result["meta"]
        self.assertTrue(meta["ok"])
        self.assertEqual((image[0], image[1]), (640, 360))
        self.assertTrue(os.path.isfile(meta["path"]), meta["path"])
        self.assertTrue(
            os.path.normcase(os.path.dirname(meta["path"])) == os.path.normcase(RENDERS),
            f"default path {meta['path']!r} is not under {RENDERS!r}",
        )
        self.assertEqual(file_size(meta["path"]), (640, 360))
        self.assertGreater(mean_brightness(image), 4.0, "the render is (almost) black")
        self.assertGreater(distinct_colors(image), 64, "the render has almost no colours")
        edges = edge_ratio(image)
        self.assertGreater(edges, 0.005, f"the render has no visible character (edge ratio {edges:.4f})")

    def test_different_frames_render_different_poses_and_keep_the_current_frame(self):
        info = self.open_sample("Backflip_animation.casc")
        frames = client.call("get_scene_info")["frames"]
        first, last = frames["first"], frames["last"]
        self.assertGreater(last, first + 4, f"the sample has no animation: {frames!r}")
        middle = first + (last - first) // 2
        before = self.current_frame()
        standing = self.render(frame=first, width=320, height=240)
        flipped = self.render(frame=middle, width=320, height=240)
        after = self.current_frame()
        self.assertEqual(standing["meta"]["frame"], first)
        self.assertEqual(flipped["meta"]["frame"], middle)
        self.assertEqual(before, after, "render_image left the current frame changed")
        self.assertEqual(after, before)
        first_image = image_of(standing)
        second_image = image_of(flipped)
        self.assertGreater(difference_ratio(first_image, second_image), 0.005, "both frames rendered the same pose")
        self.assertEqual(file_size(standing["meta"]["path"]), (320, 240))
        self.assertGreater(info["objects"], 0)

    def test_a_cursor_outside_the_animation_range_is_kept(self):
        self.open_sample("Backflip_animation.casc")
        boundary = run_json(
            "boundary = view_scene.animation_boundary()\n"
            "result = {'first': boundary.first_frame, 'last': boundary.last_frame}"
        )
        outside = run_json(
            "boundary = view_scene.animation_boundary()\n"
            "scene.set_current_frame(boundary.last_frame + 5)\n"
            "result = {'target': boundary.last_frame + 5, 'raw': scene.get_current_frame(False), "
            "'clamped': scene.get_current_frame()}"
        )
        self.assertEqual(outside["raw"], outside["target"], "the cursor could not be placed outside the range")
        self.assertGreater(outside["target"], boundary["last"])
        self.assertEqual(outside["clamped"], boundary["last"], "a clamped read should stop at the last frame")

        plain = self.render(width=160, height=90, return_image=False)
        self.assertEqual(plain["frame"], outside["target"])
        self.assertEqual(plain["current_frame"], outside["target"])
        after = run_json("result = {'raw': scene.get_current_frame(False)}")
        self.assertEqual(after["raw"], outside["target"], "render_image moved a cursor that sat outside the range")

        inside = boundary["first"] + 1
        switched = self.render(frame=inside, width=160, height=90, return_image=False)
        self.assertEqual(switched["frame"], inside)
        restored = run_json("result = {'raw': scene.get_current_frame(False)}")
        self.assertEqual(restored["raw"], outside["target"], "the render did not restore the raw cursor")

    def test_uppercase_png_extension_is_written_as_png(self):
        # RenderToFile appends ".png" to an extension it does not know ("x.PNG" -> "x.PNG.png"),
        # so the tool normalizes the name; otherwise every render to that path would time out.
        self.open_sample("Cascy.casc")
        path = os.path.join(self.out_dir(), "uppercase.PNG")
        result = self.render(width=160, height=90, path=path, overwrite=True)
        written = result["meta"]["path"]
        self.assertTrue(written.endswith(".png"), written)
        self.assertTrue(os.path.isfile(written), written)
        self.assertFalse(os.path.exists(path + ".png"), "RenderToFile appended a second .png")
        self.assertEqual(file_size(written), (160, 90))

    def test_width_and_height_set_the_file_resolution(self):
        self.open_sample("Cascy.casc")
        small = os.path.join(self.out_dir(), "small-320x180.png")
        large = os.path.join(self.out_dir(), "large-800x450.png")
        self.render(width=320, height=180, path=small, overwrite=True)
        self.render(width=800, height=450, path=large, overwrite=True)
        self.assertEqual(file_size(small), (320, 180))
        self.assertEqual(file_size(large), (800, 450))

    def test_max_width_downscales_only_the_returned_image(self):
        self.open_sample("Cascy.casc")
        path = os.path.join(self.out_dir(), "scaled-800x450.png")
        result = self.render(width=800, height=450, path=path, overwrite=True, max_width=400)
        self.assertEqual(file_size(path), (800, 450), "the file keeps the requested resolution")
        self.assertEqual((result["meta"]["image_size"]), [400, 225])
        image = image_of(result)
        self.assertEqual((image[0], image[1]), (400, 225))

    def test_existing_file_is_refused_without_overwrite(self):
        self.open_sample("Cascy.casc")
        path = os.path.join(self.out_dir(), "overwrite-160x90.png")
        self.render(width=160, height=90, path=path, overwrite=True)
        with open(path, "rb") as handle:
            original = handle.read()
        with self.assertRaises(client.LiveError) as caught:
            self.render(width=160, height=90, path=path)
        self.assertIn("overwrite", str(caught.exception))
        with open(path, "rb") as handle:
            self.assertEqual(handle.read(), original, "the refused call changed the existing file")
        again = self.render(width=160, height=90, path=path, overwrite=True)
        self.assertTrue(again["meta"]["ok"])
        self.assertTrue(os.path.isfile(path))

    def test_return_image_false_returns_json_only(self):
        self.open_sample("Cascy.casc")
        result = self.render(width=160, height=90, return_image=False)
        self.assertNotIn("images", result, "return_image=false must not send image content")
        self.assertTrue(result["ok"])
        self.assertTrue(os.path.isfile(result["path"]))

    def test_minimized_window_is_refused_with_a_restore_hint(self):
        # Cascadeur 2026.2.3 queues the render and only draws once the window is visible again, so
        # the tool refuses a minimized window with a hint instead of waiting for the 180 s timeout.
        self.open_sample("Cascy.casc")
        self.assertTrue(client.call("run_python", code=MINIMIZE_CODE).get("ok"))
        time.sleep(1.0)
        state = client.call("run_python", code=WINDOW_STATE_CODE)
        self.assertEqual(state.get("value"), "True", f"the window did not minimize: {state!r}")
        started = time.monotonic()
        try:
            with self.assertRaises(client.LiveError) as caught:
                self.render(width=320, height=240)
        finally:
            self.assertTrue(client.call("run_python", code=RESTORE_CODE).get("ok"))
            time.sleep(1.5)
        elapsed = time.monotonic() - started
        message = str(caught.exception).lower()
        self.assertIn("minimized", message)
        self.assertIn("restore", message, "the failure must say how to fix it")
        self.assertLess(elapsed, 30.0, f"the minimized window must fail fast, took {elapsed:.1f}s")
        state = client.call("run_python", code=WINDOW_STATE_CODE)
        self.assertEqual(state.get("value"), "False", f"the window was not restored: {state!r}")
        after = self.render(width=320, height=240)
        self.assertIn("images", after, "rendering must work again once the window is restored")
        self.assertEqual((after["meta"]["file_size"]), [320, 240])

    def test_renders_while_another_window_covers_cascadeur(self):
        self.open_sample("Cascy.casc")
        normal = self.render(width=320, height=240)
        cover = CoverWindow()
        try:
            cover.show()
            time.sleep(1.0)
            covered = self.render(width=320, height=240)
        finally:
            cover.close()
        self.assertIn("images", covered, "rendering failed while another window covered Cascadeur")
        image = image_of(covered)
        self.assertEqual((image[0], image[1]), (320, 240))
        self.assertGreater(mean_brightness(image), 4.0, "the covered render is black")
        same = difference_ratio(image_of(normal), image)
        self.assertLess(same, 0.02, f"the covered render differs from the windowed one ({same:.3f} of pixels)")


if __name__ == "__main__":
    unittest.main()
