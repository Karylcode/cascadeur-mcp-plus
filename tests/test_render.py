"""Tests for render.py: waiting for the renderer's file and reading the result (no Cascadeur).

Run: python -m unittest discover -s tests -v
"""

from __future__ import annotations

import base64
import importlib.util
import os
import struct
import sys
import tempfile
import threading
import time
import types
import unittest
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_render():
    """Load cascadeur_mcp_plus/render.py without importing the Cascadeur-only package __init__."""
    package = "cascadeur_mcp_plus"
    if package not in sys.modules:
        module = types.ModuleType(package)
        module.__path__ = [os.path.join(ROOT, package)]
        sys.modules[package] = module
    name = package + ".render"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, package, "render.py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


render = load_render()


def png_bytes(width: int = 4, height: int = 2, rgb=(10, 20, 30), complete: bool = True) -> bytes:
    """A small but valid 8-bit RGB PNG, optionally missing its IEND chunk."""

    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + kind
            + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    scanlines = b"".join(b"\x00" + bytes(rgb) * width for _ in range(height))
    body = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(scanlines))
    return body + chunk(b"IEND", b"") if complete else body


def write(path: str, data: bytes) -> str:
    with open(path, "wb") as handle:
        handle.write(data)
    return path


class ValidateSizeTests(unittest.TestCase):
    def test_accepts_pixel_sizes(self):
        self.assertEqual(render.validate_size(1280, 720), (1280, 720, None))
        self.assertEqual(render.validate_size(640, 360, 16), (640, 360, 16))
        # JSON numbers can arrive as floats; whole floats are fine.
        self.assertEqual(render.validate_size(320.0, 240.0), (320, 240, None))

    def test_rejects_unusable_values(self):
        for width, height in ((0, 720), (1280, 0), (-1, 720), (1280.5, 720), ("1280", 720), (True, 720)):
            with self.assertRaises(render.ToolError, msg=f"{width!r} {height!r}"):
                render.validate_size(width, height)
        with self.assertRaises(render.ToolError):
            render.validate_size(1280, 720, samples=0)
        with self.assertRaises(render.ToolError):
            render.validate_size(1280, 720, samples=1.5)
        with self.assertRaises(render.ToolError):
            render.validate_size(render.MAX_SIZE + 1, 720)

    def test_empty_size_is_rejected_not_defaulted(self):
        with self.assertRaises(render.ToolError):
            render.validate_size(None, 720)


class DefaultPathTests(unittest.TestCase):
    def test_unique_names_in_the_folder(self):
        with tempfile.TemporaryDirectory() as folder:
            first = render.default_path(folder)
            second = render.default_path(folder)
            self.assertNotEqual(first, second)
            self.assertEqual(os.path.dirname(first), folder)
            self.assertTrue(first.endswith(".png"))
            self.assertIn("render-", os.path.basename(first))


class OutputFileTests(unittest.TestCase):
    def test_missing_folder_is_reported(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(render.ToolError) as caught:
                render.output_file(os.path.join(folder, "missing", "image.png"))
            self.assertIn("Output folder does not exist", str(caught.exception))

    def test_existing_file_is_removed_so_the_wait_cannot_see_it(self):
        with tempfile.TemporaryDirectory() as folder:
            path = write(os.path.join(folder, "old.png"), png_bytes())
            self.assertEqual(render.output_file(path), os.path.normpath(path))
            self.assertFalse(os.path.exists(path))

    def test_folder_path_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            subfolder = os.path.join(folder, "subfolder")
            os.mkdir(subfolder)
            with self.assertRaises(render.ToolError) as caught:
                render.output_file(subfolder)
            self.assertIn("is a folder", str(caught.exception))


class ImageCompleteTests(unittest.TestCase):
    def test_complete_png_and_jpeg_are_complete(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertTrue(render.image_complete(write(os.path.join(folder, "a.png"), png_bytes())))
            self.assertTrue(render.image_complete(write(os.path.join(folder, "a.jpg"), b"\xff\xd8data\xff\xd9")))

    def test_unfinished_png_and_jpeg_are_not_complete(self):
        with tempfile.TemporaryDirectory() as folder:
            partial = write(os.path.join(folder, "partial.png"), png_bytes(complete=False))
            self.assertFalse(render.image_complete(partial))
            partial_jpeg = write(os.path.join(folder, "partial.jpg"), b"\xff\xd8data")
            self.assertFalse(render.image_complete(partial_jpeg))

    def test_missing_file_and_other_formats(self):
        self.assertFalse(render.image_complete(os.path.join(tempfile.gettempdir(), "does-not-exist-T30.png")))
        with tempfile.TemporaryDirectory() as folder:
            # An unknown format only has to be stable; the size check is what decides.
            self.assertTrue(render.image_complete(write(os.path.join(folder, "x.bin"), b"whatever")))


class WaitForFileTests(unittest.TestCase):
    def test_returns_after_the_file_is_written(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "later.png")
            data = png_bytes(8, 4)

            def writer():
                time.sleep(0.2)
                write(path, data)

            thread = threading.Thread(target=writer)
            thread.start()
            try:
                started = time.monotonic()
                info = render.wait_for_file(path, timeout=10.0, poll=0.02, stable=0.05)
                elapsed = time.monotonic() - started
            finally:
                thread.join()
            self.assertEqual(info["bytes"], len(data))
            self.assertEqual(info["path"], path)
            self.assertGreaterEqual(elapsed, 0.2)

    def test_does_not_accept_a_half_written_png(self):
        with tempfile.TemporaryDirectory() as folder:
            path = write(os.path.join(folder, "partial.png"), png_bytes(complete=False))
            with self.assertRaises(render.ToolError) as caught:
                render.wait_for_file(path, timeout=0.5, poll=0.02, stable=0.05)
            self.assertIn("did not finish rendering", str(caught.exception))

    def test_missing_file_times_out_with_a_hint(self):
        path = os.path.join(tempfile.gettempdir(), "cascadeur-mcp-plus-tests", "never-T30.png")
        with self.assertRaises(render.ToolError) as caught:
            render.wait_for_file(path, timeout=0.3, poll=0.02)
        message = str(caught.exception)
        self.assertIn("no file was created", message)
        self.assertIn("get_log", message)

    def test_a_directory_never_counts_as_a_finished_file(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(render.ToolError):
                render.wait_for_file(folder, timeout=0.2, poll=0.02)


class ImageReadingTests(unittest.TestCase):
    def test_png_size_and_format(self):
        with tempfile.TemporaryDirectory() as folder:
            path = write(os.path.join(folder, "sized.png"), png_bytes(640, 360))
            self.assertEqual(render.image_size(path), (640, 360))
            self.assertEqual(render.image_format(path), "image/png")

    def test_other_formats_have_no_png_size(self):
        with tempfile.TemporaryDirectory() as folder:
            jpeg = write(os.path.join(folder, "a.jpg"), b"\xff\xd8\xff\xd9")
            self.assertIsNone(render.image_size(jpeg))
            self.assertEqual(render.image_format(jpeg), "image/jpeg")
            self.assertIsNone(render.image_size(os.path.join(folder, "missing.png")))

    def test_small_png_is_returned_byte_for_byte(self):
        data = png_bytes(320, 180)
        with tempfile.TemporaryDirectory() as folder:
            path = write(os.path.join(folder, "small.png"), data)
            encoded, mime, meta = render.encode_image(path, max_width=1280)
            self.assertEqual(mime, "image/png")
            self.assertEqual(base64.b64decode(encoded), data)
            self.assertEqual(meta["image_size"], [320, 180])
            self.assertFalse(meta["scaled"])

    def test_zero_max_width_keeps_the_file_size(self):
        data = png_bytes(4096, 16)
        with tempfile.TemporaryDirectory() as folder:
            path = write(os.path.join(folder, "wide.png"), data)
            encoded, _, meta = render.encode_image(path, max_width=0)
            self.assertEqual(base64.b64decode(encoded), data)
            self.assertEqual(meta["image_size"], [4096, 16])


class RenderToFileChecksTests(unittest.TestCase):
    def test_bad_path_is_reported_before_anything_is_rendered(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(render.ToolError) as caught:
                render.render_to_file(None, os.path.join(folder, "missing", "image.png"))
            self.assertIn("Output folder does not exist", str(caught.exception))

    def test_bad_size_is_reported_before_anything_is_rendered(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(render.ToolError):
                render.render_to_file(None, os.path.join(folder, "image.png"), width=0)


if __name__ == "__main__":
    unittest.main()
