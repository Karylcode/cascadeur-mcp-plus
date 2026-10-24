"""Live tests for render_video and render_image_sequence (T32).

The tests use Backflip_animation.casc, the sample with a moving character, and keep every output
under %TEMP%/cascadeur-mcp-plus-tests/T32/. The MP4s are inspected with a small stdlib parser
(duration and encoded sample count), so the tests do not depend on ffmpeg.

Run with the live-test lock held and MCP Plus restarted from this worktree:

    python docs/tickets/bin/live.py lock T32
    python docs/tickets/bin/live.py restart C:/Users/User/Tools/cascadeur-mcp-plus-wt/T32
    python tests/live/run.py test_video.py
"""

from __future__ import annotations

import os
import struct
import tempfile
import time
import unittest

try:
    from . import client
    from .fixture import LiveTestCase
except ImportError:
    import client  # type: ignore[no-redef]
    from fixture import LiveTestCase  # type: ignore[no-redef]

SAMPLE = "Backflip_animation.casc"
HTTP_TIMEOUT = 600.0
DISPLAY_KEYS = (
    "view_mode",
    "ghosts",
    "ghost_keyframes",
    "trajectory_mode",
    "trajectory_active",
    "only_key_frames",
    "silhouette",
    "surface",
    "viewport_layout",
)


# ---------------------------------------------------------------------------
# MP4 box parsing (stdlib only)
# ---------------------------------------------------------------------------


def boxes(data, start, end):
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


def find_box(data, kind, start, end):
    for box_kind, payload, box_end in boxes(data, start, end):
        if box_kind == kind:
            return payload, box_end
    return None


def mp4_info(path: str) -> dict:
    with open(path, "rb") as handle:
        data = handle.read()
    top = list(boxes(data, 0, len(data)))
    kinds = [kind for kind, _, _ in top]
    if b"ftyp" not in kinds:
        raise AssertionError(f"{path} is not an MP4 (no ftyp box)")
    info = {"bytes": len(data), "duration_s": None, "timescale": None, "frames": None, "fps": None}
    moov = find_box(data, b"moov", 0, len(data))
    if moov is None:
        raise AssertionError(f"{path} has no moov box (the file is incomplete)")
    mvhd = find_box(data, b"mvhd", moov[0], moov[1])
    if mvhd is not None:
        payload = data[mvhd[0] : mvhd[1]]
        version = payload[0]
        if version == 1:
            timescale = struct.unpack(">I", payload[20:24])[0]
            duration = struct.unpack(">Q", payload[24:32])[0]
        else:
            timescale = struct.unpack(">I", payload[12:16])[0]
            duration = struct.unpack(">I", payload[16:20])[0]
        info["timescale"] = timescale
        info["duration_s"] = duration / float(timescale) if timescale else None
    tracks = []
    for kind, payload, box_end in boxes(data, moov[0], moov[1]):
        if kind != b"trak":
            continue
        mdia = find_box(data, b"mdia", payload, box_end)
        if mdia is None:
            continue
        minf = find_box(data, b"minf", mdia[0], mdia[1])
        stbl = find_box(data, b"stbl", minf[0], minf[1]) if minf else None
        if stbl is None:
            continue
        hdlr = find_box(data, b"hdlr", mdia[0], mdia[1])
        handler = data[hdlr[0] + 8 : hdlr[0] + 12] if hdlr else None
        tracks.append((handler, stbl))
    video_tracks = [stbl for handler, stbl in tracks if handler == b"vide"]
    chosen = video_tracks or [stbl for _handler, stbl in tracks]
    samples = 0
    seen = False
    for stbl in chosen:
        stsz = find_box(data, b"stsz", stbl[0], stbl[1])
        if stsz is not None:
            count = struct.unpack(">I", data[stsz[0] + 8 : stsz[0] + 12])[0]
            samples += count
            seen = True
            continue
        stts = find_box(data, b"stts", stbl[0], stbl[1])
        if stts is not None:
            entries = struct.unpack(">I", data[stts[0] + 4 : stts[0] + 8])[0]
            for index in range(entries):
                offset = stts[0] + 8 + index * 8
                samples += struct.unpack(">I", data[offset : offset + 4])[0]
            seen = True
    if seen:
        info["frames"] = samples
    if info["frames"] and info["duration_s"]:
        info["fps"] = samples / info["duration_s"]
    return info


def png_size(path: str) -> tuple[int, int]:
    with open(path, "rb") as handle:
        header = handle.read(24)
    if header[:8] != b"\x89PNG\r\n\x1a\n":
        raise AssertionError(f"{path} is not a PNG")
    return struct.unpack(">II", header[16:24])


# ---------------------------------------------------------------------------
# Cascadeur-side helpers and the test case
# ---------------------------------------------------------------------------


def display_state() -> dict:
    return client.call("get_display_state")


def camera_state() -> dict:
    camera = client.call("get_camera")["camera"]
    return {"position": camera["position"], "target": camera["target"]}


def same_point(first, second, tolerance: float = 0.02) -> bool:
    return all(abs(float(a) - float(b)) <= tolerance for a, b in zip(first, second, strict=True))


def export_dialogs() -> list[dict]:
    """Any open Export video window (a separate window, so check its title too)."""
    found = []
    for dialog in client.call("dialogs").get("dialogs", []):
        text = " ".join(dialog.get("texts") or []).lower()
        buttons = {(button.get("text") or "").strip().lower() for button in dialog.get("buttons", [])}
        title = (dialog.get("title") or "").strip().lower()
        if (
            title == "export video"
            or "rendering timeline" in text
            or "render ended successfully" in text
            or {"stop", "show in folder..."} & buttons
        ):
            found.append(dialog)
    return found


class VideoLiveTests(LiveTestCase):
    def out_dir(self) -> str:
        path = os.path.join(tempfile.gettempdir(), "cascadeur-mcp-plus-tests", "T32")
        os.makedirs(path, exist_ok=True)
        return path

    def setUp(self) -> None:
        super().setUp()
        self.calls = 0

    def tearDown(self) -> None:
        # A render dialog would make closing the test scene unsafe, so stop jobs first.
        try:
            self._cancel_leftover_jobs()
        finally:
            super().tearDown()

    def _cancel_leftover_jobs(self) -> None:
        try:
            for job in client.call("list_jobs", limit=100)["jobs"]:
                if job["status"] == "running" and job["kind"] in ("render_video", "render_image_sequence"):
                    client.call("cancel_job", job_id=job["job_id"])
        except Exception:
            return
        for _ in range(30):
            if not export_dialogs():
                return
            time.sleep(0.2)

    def start_job(self, tool: str, **arguments) -> str:
        result = client.call(tool, http_timeout=HTTP_TIMEOUT, **arguments)
        self.assertTrue(result.get("ok"), result)
        job_id = result["job_id"]
        self.assertRegex(job_id, r"^job-\d+$")
        return job_id

    def wait_job(self, job_id: str, timeout: float = 420.0) -> tuple[dict, list[float]]:
        """Poll get_job until it finishes; return the final snapshot and the progress samples."""
        deadline = time.monotonic() + timeout
        progress = []
        last = None
        while time.monotonic() < deadline:
            last = client.call("get_job", job_id=job_id)
            if last.get("progress") is not None:
                progress.append(float(last["progress"]))
            if last.get("done"):
                return last, progress
            self.calls += 1
            if self.calls % 20 == 0:
                print(f"  {job_id} {last['status']} progress={last.get('progress')} ({last['elapsed_s']:.0f}s)")
            time.sleep(0.5)
        self.fail(f"job {job_id} did not finish in {timeout:.0f}s: {last!r}")

    def timeline(self) -> dict:
        return client.call("get_timeline")

    def assert_restored(self, timeline_before: dict, display_before: dict, camera_before: dict, meta: dict) -> None:
        timeline_after = self.timeline()
        self.assertEqual(timeline_after["frame"], timeline_before["frame"], "the current frame changed")
        self.assertEqual(
            (timeline_after["first_visible_frame"], timeline_after["last_visible_frame"]),
            (timeline_before["first_visible_frame"], timeline_before["last_visible_frame"]),
            "the visible frame range changed",
        )
        if meta and "frame_restored" in meta:
            self.assertTrue(meta.get("frame_restored"), meta)
            self.assertTrue(meta.get("range_restored"), meta)
            if meta.get("camera_restored") is not None:
                self.assertTrue(meta["camera_restored"], meta)
            self.assertTrue(meta.get("display_restored"), meta)
            self.assertTrue(meta.get("dialog_closed"), meta)
        camera_after = camera_state()
        self.assertTrue(same_point(camera_after["position"], camera_before["position"]), (camera_after, camera_before))
        self.assertTrue(same_point(camera_after["target"], camera_before["target"]), (camera_after, camera_before))
        display_after = display_state()
        for key in DISPLAY_KEYS:
            self.assertEqual(display_after.get(key), display_before.get(key), f"display {key} changed")
        self.assertEqual(export_dialogs(), [], "an Export Video dialog is still open")

    # ------------------------------------------------------------------
    # The required default Backflip render
    # ------------------------------------------------------------------

    def test_1_default_backflip_mp4(self):
        opened = self.open_sample(SAMPLE)
        self.assertEqual(opened["name"], SAMPLE)
        timeline = self.timeline()
        before = {
            "frame": timeline["frame"],
            "display": display_state(),
            "camera": camera_state(),
            "timeline": timeline,
        }
        path = os.path.join(self.out_dir(), "backflip-default.mp4")
        if os.path.exists(path):
            os.remove(path)
        started = time.monotonic()
        job_id = self.start_job("render_video", path=path)
        snapshot, progress = self.wait_job(job_id, timeout=600.0)
        elapsed = time.monotonic() - started
        self.assertEqual(snapshot["status"], "done", snapshot)
        meta = snapshot["result"]
        self.assertTrue(meta["ok"], meta)
        self.assertTrue(os.path.isfile(path), path)
        info = mp4_info(path)
        expected_frames = timeline["last_frame"] - timeline["first_frame"] + 1
        print(
            f"default Backflip: {info['frames']} frames, {info['duration_s']:.3f}s @ {info['fps']:.2f} fps, "
            f"{info['bytes'] / 1024:.0f} KiB in {elapsed:.1f}s"
        )
        self.assertEqual(info["frames"], expected_frames, f"the video does not cover the whole animation: {info}")
        # Cascadeur's movie duration leaves out the last frame's own duration, so frames/duration
        # lands a little above the scene fps (30.24 for 124 frames at 30 fps).
        self.assertAlmostEqual(info["fps"], timeline["fps"], delta=0.5)
        self.assertAlmostEqual(info["duration_s"], expected_frames / timeline["fps"], delta=0.2)
        self.assertEqual([meta["range"][0], meta["range"][1]], [timeline["first_frame"], timeline["last_frame"]])
        self.assertEqual(meta["frames"], expected_frames)
        self.assertTrue(meta["frame_count_matches"], meta)
        self.assertIn(meta["camera_view"], ("front", "back", "left", "right"))
        self.assertEqual(meta["camera_preset"], "auto")
        self.assertEqual(meta["display_applied"]["view_mode"], "view")
        self.assertEqual(meta["display_applied"]["ghosts"], "none")
        self.assertFalse(meta["display_applied"]["trajectory_active"])
        self.assertFalse(meta["display_applied"]["silhouette"])
        self.assertTrue(progress, "get_job reported no progress while the video rendered")
        self.assertTrue(
            any(0.0 < value < 1.0 for value in progress), f"no intermediate progress was reported: {progress}"
        )
        self.assert_restored(before["timeline"], before["display"], before["camera"], meta)
        self.assertGreater(meta["file_bytes"], 1024)

    # ------------------------------------------------------------------
    # A short range, dirty display and overwrite
    # ------------------------------------------------------------------

    def test_2_short_range_restores_scene_and_cleans_the_display(self):
        self.open_sample(SAMPLE)
        # Start from a deliberately dirty display so the tool has to change and restore it.
        self.assertTrue(client.call("set_view_mode", mode="point_controller")["ok"])
        self.assertTrue(client.call("set_ghosts", mode="previous")["ok"])
        self.assertTrue(client.call("set_trajectories", mode="all")["ok"])
        timeline = self.timeline()
        before = {
            "frame": timeline["frame"],
            "display": display_state(),
            "camera": camera_state(),
            "timeline": timeline,
        }
        path = os.path.join(self.out_dir(), "backflip-range.mp4")
        if os.path.exists(path):
            os.remove(path)

        # An existing file is refused without overwrite.
        with open(path, "wb") as handle:
            handle.write(b"old")
        with self.assertRaises(client.LiveError) as refused:
            client.call("render_video", path=path, start=10, end=21, width=320, height=180)
        self.assertIn("overwrite", str(refused.exception).lower())
        with open(path, "rb") as handle:
            self.assertEqual(handle.read(), b"old", "the refused call changed the existing file")

        job_id = self.start_job(
            "render_video", path=path, start=10, end=21, width=320, height=180, samples=2, overwrite=True
        )
        snapshot, _progress = self.wait_job(job_id, timeout=240.0)
        self.assertEqual(snapshot["status"], "done", snapshot)
        meta = snapshot["result"]
        self.assertTrue(meta["ok"], meta)
        self.assertEqual(meta["range"], [10, 21])
        self.assertEqual(meta["requested_range"], [10, 21])
        self.assertEqual(meta["frames"], 12)
        info = mp4_info(path)
        self.assertEqual(info["frames"], 12, f"the video does not contain the requested range: {info}")
        self.assertAlmostEqual(info["duration_s"], 12 / timeline["fps"], delta=0.15)
        self.assertEqual(meta["display_applied"]["view_mode"], "view")
        self.assertEqual(meta["display_applied"]["ghosts"], "none")
        self.assertFalse(meta["display_applied"]["trajectory_active"])
        self.assertFalse(meta["display_applied"]["silhouette"])
        self.assertTrue(meta["clean_output"], meta)
        self.assert_restored(before["timeline"], before["display"], before["camera"], meta)
        # The dirty display must really be back, not just reported.
        after = display_state()
        self.assertEqual(after["view_mode"], before["display"]["view_mode"])
        self.assertEqual(after["ghosts"], before["display"]["ghosts"])
        self.assertEqual(after["trajectory_mode"], before["display"]["trajectory_mode"])

    # ------------------------------------------------------------------
    # Progress and cancel
    # ------------------------------------------------------------------

    def test_3_progress_and_cancel(self):
        self.open_sample(SAMPLE)
        timeline = self.timeline()
        before = {
            "frame": timeline["frame"],
            "display": display_state(),
            "camera": camera_state(),
            "timeline": timeline,
        }
        path = os.path.join(self.out_dir(), "cancelled.mp4")
        if os.path.exists(path):
            os.remove(path)
        job_id = self.start_job("render_video", path=path, width=1280, height=720, samples=8)
        deadline = time.monotonic() + 120.0
        seen = []
        while time.monotonic() < deadline:
            snapshot = client.call("get_job", job_id=job_id)
            progress = snapshot.get("progress")
            if progress is not None:
                seen.append(float(progress))
            if snapshot.get("status") != "running":
                self.fail(f"the render finished before it could be cancelled: {snapshot!r}")
            if progress is not None and 0.05 < progress < 0.95:
                break
            time.sleep(0.25)
        else:
            self.fail(f"no intermediate progress was reported before the cancel test timed out: {seen!r}")
        print(f"cancel at progress {seen[-1]:.3f} after {len(seen)} samples")
        cancelled = client.call("cancel_job", job_id=job_id)
        self.assertTrue(cancelled["ok"], cancelled)
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertTrue(cancelled["done"])
        time.sleep(2.0)
        self.assertEqual(export_dialogs(), [], "cancel_job left the Export Video dialog open")
        self.assertFalse(os.path.exists(path), "cancel_job left the requested video file behind")
        leftovers = [name for name in os.listdir(self.out_dir()) if name.startswith("cancelled.rendering-")]
        self.assertEqual(leftovers, [], "cancel_job left a temporary render file behind")
        self.assert_restored(before["timeline"], before["display"], before["camera"], cancelled)
        # The next render must still start (the session was cleaned up).
        again = self.start_job("render_video", path=path, start=0, end=2, width=160, height=90)
        snapshot, _ = self.wait_job(again, timeout=120.0)
        self.assertEqual(snapshot["status"], "done", snapshot)
        self.assertTrue(snapshot["result"]["ok"], snapshot)

    # ------------------------------------------------------------------
    # Image sequence
    # ------------------------------------------------------------------

    def test_4_image_sequence(self):
        self.open_sample(SAMPLE)
        timeline = self.timeline()
        before = {
            "frame": timeline["frame"],
            "display": display_state(),
            "camera": camera_state(),
            "timeline": timeline,
        }
        folder = os.path.join(self.out_dir(), "sequence")
        if os.path.isdir(folder):
            for name in os.listdir(folder):
                os.remove(os.path.join(folder, name))
        job_id = self.start_job("render_image_sequence", folder=folder, start=0, end=3, width=160, height=90, samples=2)
        snapshot, _progress = self.wait_job(job_id, timeout=180.0)
        self.assertEqual(snapshot["status"], "done", snapshot)
        meta = snapshot["result"]
        self.assertTrue(meta["ok"], meta)
        self.assertEqual(meta["frames"], 4, meta)
        self.assertEqual(meta["requested_range"], [0, 3])
        files = sorted(os.listdir(folder))
        self.assertEqual(len(files), 4, files)
        self.assertTrue(all(name.lower().endswith((".png", ".jpg")) for name in files), files)
        for name in files:
            self.assertGreater(os.path.getsize(os.path.join(folder, name)), 100)
        sizes = [png_size(os.path.join(folder, name)) for name in files if name.lower().endswith(".png")]
        if sizes:
            self.assertEqual(set(sizes), {(160, 90)}, sizes)
        self.assert_restored(before["timeline"], before["display"], before["camera"], meta)


if __name__ == "__main__":
    unittest.main()
