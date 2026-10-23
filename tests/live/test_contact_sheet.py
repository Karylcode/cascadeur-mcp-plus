"""Live tests for render_contact_sheet (T33).

Each test opens Cascadeur's Backflip_animation sample in its own tab; the fixture closes it with
Don't Save and checks that the user's scenes, current scene and current frame are unchanged.
The tests decode the returned PNG themselves (stdlib zlib/struct) so they can prove the sheet has
the expected grid, that every cell carries its frame number and that the backflip's cells show
different poses. They also read the viewport camera through run_python to prove the camera is put
back.

Run with the live-test lock held and MCP Plus restarted from this worktree:

    python docs/tickets/bin/live.py lock T33
    python docs/tickets/bin/live.py restart C:/Users/User/Tools/cascadeur-mcp-plus-wt/T33
    python tests/live/run.py test_contact_sheet.py
"""

from __future__ import annotations

import ast
import base64
import os
import struct
import unittest
import zlib
from string import Template

try:
    from . import client
    from .fixture import LiveTestCase
except ImportError:  # executed from tests/live
    import client  # type: ignore[no-redef]
    from fixture import LiveTestCase  # type: ignore[no-redef]

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
HTTP_TIMEOUT = 600.0
GAP = 2

CAMERA_CODE = """
import numpy as np


def point(value):
    return [float(item) for item in np.asarray(value, dtype=float).reshape(-1)[:3]]


viewports = list(view_scene.viewports())
domain = None
for viewport in viewports:
    candidate = viewport.domain_viewport()
    try:
        if candidate.is_main():
            domain = candidate
            break
    except Exception:
        pass
if domain is None:
    domain = viewports[0].domain_viewport()
struct = domain.camera_struct()
result = {
    "position": point(struct.position),
    "target": point(struct.target),
    "type": str(struct.type),
}
"""

FRAME_CODE = """
result = {"raw": int(scene.get_current_frame(False)), "clamped": int(scene.get_current_frame())}
"""

SET_CAMERA_CODE = """
import numpy as np

position = $position
target = $target
type_name = $type_name
viewports = list(view_scene.viewports())
domain = None
for viewport in viewports:
    candidate = viewport.domain_viewport()
    try:
        if candidate.is_main():
            domain = candidate
            break
    except Exception:
        pass
if domain is None:
    domain = viewports[0].domain_viewport()
struct = domain.camera_struct()
struct.position = np.asarray(position, dtype=np.float32)
struct.target = np.asarray(target, dtype=np.float32)
if type_name:
    struct.type = getattr(csc.view.CameraType, type_name.rsplit(".", 1)[-1])
domain.set_camera_struct(struct)
result = {"position": [float(v) for v in position], "target": [float(v) for v in target], "type": type_name}
"""

# scene/viewport/viewports_controller.json inside Backflip_animation.casc (active viewport
# Viewport_RightTop). The viewport camera is view state, not part of the scene, so open_sample()
# does not restore the sample author's view; this test poses it explicitly.
SAMPLE_CAMERA = {
    "position": [313.457581, 543.294373, 1260.109863],
    "target": [-23.638443, 159.842499, -20.044579],
}


# ---------------------------------------------------------------------------
# Minimal PNG reader (the sheet is saved by Qt; filters 0-4 and RGB/RGBA)
# ---------------------------------------------------------------------------


def decode_png(data: bytes):
    """Return (width, height, channels, rows) for the PNG in data, rows without filter bytes."""
    if not data.startswith(PNG_SIGNATURE):
        raise AssertionError(f"render_contact_sheet returned something that is not a PNG: {data[:16]!r}")
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
        raise AssertionError("the composed PNG has no IHDR chunk")
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


def file_size(path: str) -> tuple[int, int]:
    with open(path, "rb") as handle:
        header = handle.read(24)
    if not header.startswith(PNG_SIGNATURE) or header[12:16] != b"IHDR":
        raise AssertionError(f"{path} is not a PNG")
    return struct.unpack(">II", header[16:24])


def cell_image(sheet, index: int, columns: int, cell_width: int, cell_height: int):
    """Crop one cell out of the decoded sheet, including its gap-free bytes."""
    _width, _height, channels, rows = sheet
    column = index % columns
    row = index // columns
    x = column * (cell_width + GAP)
    y = row * (cell_height + GAP)
    if x + cell_width > _width or y + cell_height > _height:
        raise AssertionError(f"cell {index} lies outside the {_width}x{_height} sheet")
    return (
        cell_width,
        cell_height,
        channels,
        [line[x * channels : (x + cell_width) * channels] for line in rows[y : y + cell_height]],
    )


def difference_ratio(first, second, threshold: int = 8, step: int = 3) -> float:
    """Fraction of sampled pixels that differ strongly between two images of the same size."""
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


def saturated_ratio(cell, threshold: int = 25, step: int = 2) -> float:
    """Fraction of sampled pixels with a clear colour cast.

    Cascadeur's sample characters are green/blue on a grey floor, so this proves a cell actually
    contains the character instead of only the background. The label's white/black box is not
    saturated, so it does not count.
    """
    _width, _height, channels, rows = cell
    coloured = total = 0
    for line in rows[::step]:
        for index in range(0, len(line) - channels + 1, channels * step):
            total += 1
            red, green, blue = line[index], line[index + 1], line[index + 2]
            if max(red, green, blue) - min(red, green, blue) > threshold:
                coloured += 1
    return coloured / max(1, total)


def label_present(cell) -> bool:
    """True when the cell's top-left corner contains the dark box and bright digits of a label.

    The label box is nearly opaque, so a rendered scene cannot accidentally supply both a large
    dark area and bright glyph pixels in exactly that corner.
    """
    width, height, channels, rows = cell
    bright = dark = 0
    for line in rows[4 : min(height, 40)]:
        for pixel in range(4, min(width, 80)):
            index = pixel * channels
            red, green, blue = line[index], line[index + 1], line[index + 2]
            if red >= 200 and green >= 200 and blue >= 200:
                bright += 1
            elif red <= 80 and green <= 80 and blue <= 80:
                dark += 1
    return bright >= 5 and dark >= 50


def even_frames(first: int, last: int, count: int = 8) -> list[int]:
    """The default frame selection, repeated here so the test does not need Cascadeur imports."""
    if count <= 1 or last <= first:
        return [first]
    span = last - first
    return sorted({int(round(first + span * index / (count - 1))) for index in range(count)})


def run_json(code: str):
    """Run code inside Cascadeur that sets result to a dict and return that dict."""
    answer = client.call("run_python", code=code)
    if not answer.get("ok"):
        raise AssertionError(f"run_python failed: {answer!r}")
    return ast.literal_eval(answer["value"])


class ContactSheetLiveTests(LiveTestCase):
    def setUp(self) -> None:
        # The viewport camera is view state, not part of the scene (T31's live tests found the
        # same); save the user's camera and put it back after the test tab is closed.
        self.user_camera = self.camera()
        self.addCleanup(self.restore_user_camera)

    def restore_user_camera(self) -> None:
        camera = getattr(self, "user_camera", None)
        if camera:
            self.set_camera(camera["position"], camera["target"], camera.get("type"))

    def sheet(self, **arguments):
        return client.call("render_contact_sheet", http_timeout=HTTP_TIMEOUT, **arguments)

    def camera(self) -> dict:
        return run_json(CAMERA_CODE)

    def frame(self) -> dict:
        return run_json(FRAME_CODE)

    def set_camera(self, position, target, type_name=None) -> dict:
        code = Template(SET_CAMERA_CODE).substitute(
            position=repr([float(value) for value in position]),
            target=repr([float(value) for value in target]),
            type_name=repr(type_name),
        )
        return run_json(code)

    def pose_sample_camera(self) -> dict:
        """Use the camera the Backflip sample was saved with (open_sample does not restore it)."""
        return self.set_camera(SAMPLE_CAMERA["position"], SAMPLE_CAMERA["target"], "PERSPECTIVE")

    def assert_camera_equal(self, after: dict, before: dict) -> None:
        self.assertEqual(after["type"], before["type"], "the camera type changed")
        for key in ("position", "target"):
            for index, (a, b) in enumerate(zip(after[key], before[key], strict=True)):
                self.assertAlmostEqual(a, b, places=3, msg=f"camera {key}[{index}] changed")

    def test_default_sheet_shows_the_backflip_in_eight_labeled_cells(self):
        self.open_sample("Backflip_animation.casc")
        self.pose_sample_camera()
        timeline = client.call("get_timeline")
        first, last = timeline["first_frame"], timeline["last_frame"]
        self.assertGreater(last, first + 50, f"the sample has no long animation: {timeline!r}")
        before_frame = self.frame()
        before_camera = self.camera()
        target = os.path.join(self.out_dir(), "backflip-default.png")

        result = self.sheet(path=target, overwrite=True)
        meta = result["meta"]
        self.assertTrue(meta["ok"], meta)
        expected = even_frames(first, last)
        self.assertEqual(meta["frames"], expected, meta)
        self.assertEqual(meta["count"], 8)
        self.assertEqual(meta["columns"], 4)
        self.assertEqual(meta["rows"], 2)
        self.assertEqual(meta["selection"], "even sample")
        self.assertEqual(meta["file_size"], [4 * 320 + 3 * GAP, 2 * 180 + GAP])
        self.assertEqual(file_size(target), (4 * 320 + 3 * GAP, 2 * 180 + GAP))

        image = decode_png(base64.b64decode(result["images"][0]["data"]))
        self.assertEqual(image[:2], (4 * 320 + 3 * GAP, 2 * 180 + GAP))
        cells = [cell_image(image, index, 4, 320, 180) for index in range(8)]
        for index, cell in enumerate(cells):
            self.assertTrue(label_present(cell), f"cell {index} (frame {expected[index]}) has no frame number")
            coloured = saturated_ratio(cell)
            self.assertGreater(
                coloured, 0.0005, f"cell {index} (frame {expected[index]}) shows no character ({coloured:.4f})"
            )
        first_move = difference_ratio(cells[0], cells[4])
        self.assertGreater(first_move, 0.01, f"the first and middle cells look the same ({first_move:.3f})")
        last_move = difference_ratio(cells[0], cells[7])
        self.assertGreater(last_move, 0.01, f"the first and last cells look the same ({last_move:.3f})")

        self.assertEqual(self.frame()["raw"], before_frame["raw"], "the playhead was not restored")
        self.assert_camera_equal(self.camera(), before_camera)

    def test_frames_argument_produces_three_cells(self):
        self.open_sample("Backflip_animation.casc")
        self.pose_sample_camera()
        before_camera = self.camera()
        target = os.path.join(self.out_dir(), "three-cells.png")
        result = self.sheet(frames=[0, 10, 20], path=target, overwrite=True)
        meta = result["meta"]
        self.assertTrue(meta["ok"], meta)
        self.assertEqual(meta["frames"], [0, 10, 20])
        self.assertEqual(meta["count"], 3)
        self.assertEqual(meta["columns"], 3, "the unused fourth column must be dropped")
        self.assertEqual(meta["rows"], 1)
        self.assertEqual(meta["file_size"], [3 * 320 + 2 * GAP, 180])
        self.assertEqual(file_size(target), (3 * 320 + 2 * GAP, 180))

        image = decode_png(base64.b64decode(result["images"][0]["data"]))
        cells = [cell_image(image, index, 3, 320, 180) for index in range(3)]
        for index, cell in enumerate(cells):
            self.assertTrue(label_present(cell), f"cell {index} has no frame number")
            coloured = saturated_ratio(cell)
            self.assertGreater(coloured, 0.0005, f"cell {index} shows no character ({coloured:.4f})")
        moved = max(
            difference_ratio(cells[0], cells[1]),
            difference_ratio(cells[0], cells[2]),
            difference_ratio(cells[1], cells[2]),
        )
        self.assertGreater(moved, 0.003, f"frames 0, 10 and 20 look the same ({moved:.3f})")
        self.assert_camera_equal(self.camera(), before_camera)

    def test_playhead_outside_the_range_and_camera_are_restored(self):
        self.open_sample("Backflip_animation.casc")
        self.pose_sample_camera()
        timeline = client.call("get_timeline")
        first, last = timeline["first_frame"], timeline["last_frame"]
        outside = last + 5
        client.call("set_frame", frame=outside)
        before = self.frame()
        self.assertEqual(before["raw"], outside, "the cursor could not be placed outside the range")
        self.assertEqual(before["clamped"], last)
        before_camera = self.camera()
        target = os.path.join(self.out_dir(), "restore.png")

        meta = self.sheet(frames=[first, first + 10], cell_width=160, path=target, overwrite=True)["meta"]
        self.assertTrue(meta["ok"], meta)
        self.assertEqual(meta["current_frame"], outside, meta)
        self.assertTrue(meta["frame_restored"])
        self.assertTrue(meta["camera_restored"])
        after = self.frame()
        self.assertEqual(after["raw"], outside, "the raw playhead outside the animation was moved")
        self.assert_camera_equal(self.camera(), before_camera)

    def test_camera_preset_bridge(self):
        """T31 may or may not be merged: either apply its preset or report the missing build."""
        self.open_sample("Cascy.casc")
        before_camera = self.camera()
        target = os.path.join(self.out_dir(), "camera-preset.png")
        names = {tool["name"] for tool in client.tools()}
        if "camera_preset" not in names:
            with self.assertRaises(client.LiveError) as caught:
                self.sheet(frames=[0], cell_width=160, camera_preset="front", path=target)
            self.assertIn("T31", str(caught.exception))
            return
        meta = self.sheet(frames=[0], cell_width=160, camera_preset="front", path=target, overwrite=True)["meta"]
        self.assertTrue(meta["ok"], meta)
        self.assertEqual(meta["camera_preset"], "front")
        self.assertTrue(meta["camera_restored"], meta)
        self.assert_camera_equal(self.camera(), before_camera)

    def test_existing_sheet_is_refused_without_overwrite(self):
        self.open_sample("Backflip_animation.casc")
        target = os.path.join(self.out_dir(), "overwrite.png")
        self.sheet(frames=[0], cell_width=160, path=target, overwrite=True)
        with open(target, "rb") as handle:
            original = handle.read()
        with self.assertRaises(client.LiveError) as caught:
            self.sheet(frames=[0], cell_width=160, path=target)
        self.assertIn("overwrite", str(caught.exception))
        with open(target, "rb") as handle:
            self.assertEqual(handle.read(), original, "the refused call changed the existing sheet")
        again = self.sheet(frames=[0], cell_width=160, path=target, overwrite=True)
        self.assertTrue(again["meta"]["ok"])

    def test_bad_arguments_are_rejected(self):
        cases = [
            {"frames": []},
            {"columns": 0},
            {"cell_width": 8},
            {"path": "relative.png"},
            {"frames": [0], "start": 0},
            {"step": 0, "end": 20},
        ]
        for arguments in cases:
            with self.subTest(arguments=arguments):
                with self.assertRaises(client.LiveError):
                    self.sheet(**arguments)


if __name__ == "__main__":
    unittest.main()
