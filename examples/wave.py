#!/usr/bin/env python3
"""Make a 2-second waving animation on Cascy and render it to an MP4 (T56 example).

This runs the steps in examples/wave.md against a running Cascadeur MCP Plus, over HTTP, using only
the standard library:

1. open_scene: the Cascy sample, in a new tab
2. get_timeline and set_animation_range: 2 seconds at the scene's fps; set_key keys the rest pose
3. set_pose on each pose frame: the right elbow and the right hand, written together with
   autoposing=false so both are placed exactly and keyed. The hand goes above the shoulder and out
   to the side, and swings side to side; the elbow comes from a two-bone solve with the rig's own
   upper-arm and forearm lengths. (AutoPosing=true was tried first: on a frame after a posed frame
   it reset the hand about 70 cm, so the example does not use it.)
4. set_pose on the last frame returns the arm to rest; set_interpolation makes the keys clamped Bezier
5. get_pose checks the hand height above the shoulder and the side-to-side swing
6. render_contact_sheet (a PNG grid of the mesh) and render_video (an MP4, a background job polled
   with get_job); both use the front camera, so the side-to-side swing is visible
7. close_scene closes the test tab with discard_changes, so the sample is never saved

The outputs go to %TEMP%\\cascadeur-mcp-plus-tests\\T56\\ (wave.mp4 and wave_contact.png) unless --out
is given. Cascadeur must be running with MCP Plus: Scripts > MCP > Start script server, or autostart.

    python examples/wave.py [--url http://127.0.0.1:48766] [--sample PATH] [--out DIR]

Progress goes to stderr; the summary is printed to stdout as JSON. The exit status is 0 only when
the MP4 and the contact sheet were written and the hand checks passed.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import tempfile
import time
import urllib.request

DEFAULT_URL = "http://127.0.0.1:48766"
DEFAULT_SAMPLE = r"C:\Program Files\Cascadeur\samples\Cascy.casc"
CLIP_SECONDS = 2.0
# Each pose: its position in the clip (0 = first frame, 1 = last frame) and its side-to-side swing,
# +1 for the outer end, -1 for the inner end, 0 for the middle.
POSES = ((0.10, 0), (0.20, 1), (0.30, -1), (0.40, 1), (0.50, -1), (0.60, 1), (0.70, -1), (0.80, 0))
# Sizes relative to the arm length (rest shoulder to rest hand): the hand goes up above the shoulder,
# out to the side, and swings a little either way.
LIFT = 0.55
OUT = 0.45
SWING = 0.16
END_STATES = ("done", "failed", "cancelled", "lost")


class ToolError(RuntimeError):
    """A tool call failed, or MCP Plus did not answer."""


class McpClient:
    """Minimal JSON-RPC client for the MCP Plus HTTP endpoint (POST /mcp)."""

    def __init__(self, url: str) -> None:
        self.url = url.rstrip("/")
        self._next_id = 0

    def health(self) -> dict:
        with urllib.request.urlopen(self.url + "/health", timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))

    def call(self, tool: str, arguments: dict | None = None, timeout: float = 240) -> dict:
        self._next_id += 1
        payload = {
            "jsonrpc": "2.0",
            "id": self._next_id,
            "method": "tools/call",
            "params": {"name": tool, "arguments": arguments or {}},
        }
        request = urllib.request.Request(
            self.url + "/mcp",
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                reply = json.loads(response.read().decode("utf-8"))
        except (OSError, ValueError) as error:
            raise ToolError(f"{tool}: no answer from {self.url} ({error})") from error
        if "error" in reply:
            raise ToolError(f"{tool}: JSON-RPC error {reply['error']}")
        result = reply.get("result") or {}
        data = None
        for item in result.get("content", []):
            if item.get("type") != "text":
                continue
            try:
                parsed = json.loads(item.get("text", ""))
            except ValueError:
                continue
            if isinstance(parsed, dict):
                data = parsed
                break
        if data is None:
            raise ToolError(f"{tool}: the answer has no JSON result")
        if result.get("isError") or data.get("ok") is False:
            raise ToolError(f"{tool} failed: {data.get('error', 'unknown error')} {data.get('hint', '')}".strip())
        return data


def log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def clip_frames(fps: float) -> tuple[int, list[int]]:
    """Last frame of a CLIP_SECONDS clip at this fps (the range is inclusive), and the pose frames."""
    last = round(CLIP_SECONDS * fps) - 1
    frames = [round(position * last) for position, _ in POSES]
    if frames[0] <= 0 or frames[-1] >= last or len(set(frames)) != len(frames):
        raise ToolError(f"a {CLIP_SECONDS:g}-second clip at {fps} fps is too short for the pose timing")
    return last, frames


def vector_sub(a: list[float], b: list[float]) -> list[float]:
    return [a[0] - b[0], a[1] - b[1], a[2] - b[2]]


def vector_add(a: list[float], b: list[float]) -> list[float]:
    return [a[0] + b[0], a[1] + b[1], a[2] + b[2]]


def vector_scale(a: list[float], factor: float) -> list[float]:
    return [a[0] * factor, a[1] * factor, a[2] * factor]


def dot(a: list[float], b: list[float]) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def rounded(a: list[float]) -> list[float]:
    return [round(value, 3) for value in a]


def elbow_between(
    shoulder: list[float], hand: list[float], upper: float, forearm: float, lateral: list[float]
) -> list[float]:
    """Elbow of a two-bone arm from the shoulder to the hand, bent down and outward."""
    reach = math.dist(shoulder, hand)
    direction = [(hand[i] - shoulder[i]) / reach for i in range(3)]
    along = (upper * upper - forearm * forearm + reach * reach) / (2 * reach)
    height = math.sqrt(max(0.0, upper * upper - along * along))
    bend = [0.6 * lateral[0], -1.0, 0.6 * lateral[2]]
    bend = vector_sub(bend, vector_scale(direction, dot(bend, direction)))
    length = math.sqrt(dot(bend, bend))
    bend = [value / length for value in bend]
    return [shoulder[i] + along * direction[i] + height * bend[i] for i in range(3)]


def position_of(client: McpClient, name: str, frame: int) -> list[float]:
    """World position in cm of one control point (an alias such as right_hand) at a frame."""
    data = client.call("get_pose", {"controllers": [name], "frame": frame})
    entries = data.get("controllers") or []
    if not entries or "position" not in entries[0]:
        raise ToolError(f"get_pose returned no position for {name} at frame {frame}")
    return [float(value) for value in entries[0]["position"]]


def largest(value, key: str) -> float | None:
    """The largest number stored under `key` anywhere in a nested JSON value, or None."""
    found = []
    if isinstance(value, dict):
        for name, item in value.items():
            if name == key and isinstance(item, (int, float)):
                found.append(float(item))
            else:
                nested = largest(item, key)
                if nested is not None:
                    found.append(nested)
    elif isinstance(value, list):
        for item in value:
            nested = largest(item, key)
            if nested is not None:
                found.append(nested)
    return max(found) if found else None


def wait_for_job(client: McpClient, job_id: str, limit_s: float) -> dict:
    """Poll get_job until the background job ends; return its result, or raise if it did not succeed."""
    deadline = time.monotonic() + limit_s
    while True:
        job = client.call("get_job", {"job_id": job_id, "wait_s": 30}, timeout=120)
        status = job.get("status")
        if status in END_STATES:
            break
        if time.monotonic() > deadline:
            client.call("cancel_job", {"job_id": job_id})
            raise ToolError(f"render_video job {job_id} did not finish within {limit_s:g} seconds; cancelled it")
        progress = job.get("progress")
        log(f"  render_video {status} {'' if progress is None else f'{progress:.0%}'}".rstrip())
    if status != "done":
        raise ToolError(f"render_video job {job_id} {status}: {job.get('error')}")
    return job.get("result") or {}


def make_wave(args: argparse.Namespace) -> dict:
    client = McpClient(args.url)
    try:
        health = client.health()
    except (OSError, ValueError) as error:
        raise ToolError(
            f"MCP Plus does not answer at {args.url} ({error}). Start Cascadeur and its script server "
            "(Scripts > MCP > Start script server), or enable autostart."
        ) from error
    if health.get("server") != "cascadeur-mcp-plus":
        raise ToolError(f"{args.url} is not Cascadeur MCP Plus: {health}")
    status = client.call("get_status")
    log(f"MCP Plus {health.get('version', '?')} with Cascadeur {status.get('cascadeur_version', '?')}")

    out_dir = args.out
    os.makedirs(out_dir, exist_ok=True)
    mp4_path = os.path.join(out_dir, "wave.mp4")
    sheet_path = os.path.join(out_dir, "wave_contact.png")
    timings: dict[str, float] = {}
    summary: dict = {"ok": False}
    scene = None
    started = time.monotonic()
    try:
        log(f"open_scene {args.sample}")
        began = time.monotonic()
        opened = client.call("open_scene", {"path": args.sample})
        scene = opened.get("path") or args.sample
        timings["open_scene"] = round(time.monotonic() - began, 2)

        timeline = client.call("get_timeline")
        fps = timeline.get("fps")
        if not fps:
            raise ToolError("Cascadeur reports no fps for this scene (get_timeline returned fps=null)")
        last, pose_frames = clip_frames(float(fps))
        client.call("set_animation_range", {"first": 0, "last": last})
        client.call("autoposing_enable", {"enabled": True})
        client.call("set_key", {"frames": [0]})
        log(f"range 0-{last} at {fps:g} fps ({(last + 1) / fps:g} s); pose frames {pose_frames}")

        shoulder = position_of(client, "right_shoulder", 0)
        other_shoulder = position_of(client, "left_shoulder", 0)
        hand_rest = position_of(client, "right_hand", 0)
        elbow_rest = position_of(client, "right_elbow", 0)
        arm = math.dist(shoulder, hand_rest)
        upper = math.dist(shoulder, elbow_rest)
        forearm = math.dist(elbow_rest, hand_rest)
        lateral = [shoulder[0] - other_shoulder[0], 0.0, shoulder[2] - other_shoulder[2]]
        width = math.hypot(lateral[0], lateral[2])
        if width < 1e-6 or not 30.0 <= arm <= 150.0 or min(upper, forearm) < 10.0:
            raise ToolError(f"unexpected rig: arm {arm:.1f} cm, upper {upper:.1f} cm, forearm {forearm:.1f} cm")
        lateral = [lateral[0] / width, 0.0, lateral[2] / width]
        centre = vector_add(vector_add(shoulder, vector_scale(lateral, OUT * arm)), [0.0, LIFT * arm, 0.0])

        began = time.monotonic()
        pose_errors = []
        for (_, swing), frame in zip(POSES, pose_frames, strict=True):
            hand = vector_add(centre, vector_scale(lateral, swing * SWING * arm))
            elbow = elbow_between(shoulder, hand, upper, forearm, lateral)
            reply = client.call(
                "set_pose",
                {
                    "values": {
                        "right_elbow": {"position": rounded(elbow)},
                        "right_hand": {"position": rounded(hand)},
                    },
                    "frame": frame,
                    "autoposing": False,
                    "key": True,
                },
            )
            pose_errors.append(largest(reply, "position_error_cm") or 0.0)
        reply = client.call(
            "set_pose",
            {
                "values": {
                    "right_elbow": {"position": rounded(elbow_rest)},
                    "right_hand": {"position": rounded(hand_rest)},
                },
                "frame": last,
                "autoposing": False,
                "key": True,
            },
        )
        pose_errors.append(largest(reply, "position_error_cm") or 0.0)
        client.call("set_interpolation", {"kind": "bezier_clamped", "start": 0, "end": last})
        timings["pose_and_key"] = round(time.monotonic() - began, 2)
        log(f"posed the right arm on frames {pose_frames} and back to rest on {last}")

        heights, along = [], []
        for frame in pose_frames:
            shoulder_now = position_of(client, "right_shoulder", frame)
            hand_now = position_of(client, "right_hand", frame)
            heights.append(hand_now[1] - shoulder_now[1])
            along.append(dot(vector_sub(hand_now, shoulder_now), lateral))
        swing_observed = max(along) - min(along)
        swing_expected = 2 * SWING * arm
        checks = {
            "hand_above_shoulder_min_cm": round(min(heights), 2),
            "hand_above_shoulder_required_cm": round(0.25 * arm, 2),
            "swing_observed_cm": round(swing_observed, 2),
            "swing_expected_cm": round(swing_expected, 2),
            "max_position_error_cm": round(max(pose_errors), 3),
        }
        if min(heights) < 0.25 * arm:
            raise ToolError(f"the hand is not raised enough above the shoulder: {checks}")
        if swing_observed < 0.5 * swing_expected:
            raise ToolError(f"the hand barely swings: {checks}")

        began = time.monotonic()
        previous_view = client.call("get_display_state").get("view_mode")
        client.call("set_view_mode", {"mode": "mesh"})
        try:
            sheet = client.call(
                "render_contact_sheet",
                {
                    "frames": [0, *pose_frames, last],
                    "columns": 5,
                    "cell_width": 384,
                    "camera_preset": "front",
                    "path": sheet_path,
                    "overwrite": True,
                },
                timeout=600,
            )
        finally:
            if previous_view:
                client.call("set_view_mode", {"mode": previous_view})
        timings["contact_sheet"] = round(time.monotonic() - began, 2)
        log(f"contact sheet: {sheet.get('path', sheet_path)}")

        began = time.monotonic()
        started_job = client.call(
            "render_video",
            {"path": mp4_path, "start": 0, "end": last, "camera_preset": "front", "overwrite": True},
            timeout=120,
        )
        video = wait_for_job(client, started_job["job_id"], limit_s=600)
        timings["render_video"] = round(time.monotonic() - began, 2)

        outputs = {}
        for label, path in (("mp4", mp4_path), ("contact_sheet", sheet_path)):
            if not os.path.isfile(path) or os.path.getsize(path) == 0:
                raise ToolError(f"{label} was not written to {path}")
            outputs[label] = {"path": path, "bytes": os.path.getsize(path)}
        summary = {
            "ok": True,
            "scene": scene,
            "fps": fps,
            "range": [0, last],
            "seconds": round((last + 1) / float(fps), 3),
            "pose_frames": pose_frames,
            "arm_cm": {"shoulder_to_hand": round(arm, 2), "upper_arm": round(upper, 2), "forearm": round(forearm, 2)},
            "checks": checks,
            "video": video,
            "outputs": outputs,
        }
        return summary
    finally:
        if scene is not None:
            try:
                began = time.monotonic()
                client.call("close_scene", {"scene": scene, "discard_changes": True})
                timings["close_scene"] = round(time.monotonic() - began, 2)
                time.sleep(0.5)
            except ToolError as error:
                log(f"close_scene: {error}")
        timings["total"] = round(time.monotonic() - started, 2)
        summary["timings_s"] = timings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Make a 2-second waving animation on Cascy and render it.")
    parser.add_argument("--url", default=DEFAULT_URL, help="MCP Plus address (default: %(default)s)")
    parser.add_argument("--sample", default=DEFAULT_SAMPLE, help="the Cascy sample to open (default: %(default)s)")
    parser.add_argument(
        "--out",
        default=os.path.join(os.environ.get("TEMP") or tempfile.gettempdir(), "cascadeur-mcp-plus-tests", "T56"),
        help="folder for wave.mp4 and wave_contact.png (default: %%TEMP%%\\cascadeur-mcp-plus-tests\\T56)",
    )
    args = parser.parse_args(argv)
    try:
        summary = make_wave(args)
    except ToolError as error:
        print(json.dumps({"ok": False, "error": str(error)}, indent=2, ensure_ascii=False))
        return 1
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
