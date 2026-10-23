"""Live checks for retarget_animation (T28).

The main scenario mirrors T27's verified path: Backflip_animation.casc (58 joints, 124 frames)
retargeted onto UE4_Mannequin.casc (68 joints), with the source opened from an absolute path so
the tool's temporary-tab open/close is exercised too. Source and target are rendered at four
frames with render_image (T30) for visual comparison.

Every test opens its own sample tabs; tearDown discards them and checks the user's scenes, current
scene and current frame are unchanged. Run with the live-test lock held and MCP Plus restarted from
this worktree:

    python docs/tickets/bin/live.py lock T28
    python docs/tickets/bin/live.py restart C:/Users/User/Tools/cascadeur-mcp-plus-wt/T28
    python tests/live/run.py test_retarget.py
"""

from __future__ import annotations

import hashlib
import os
import tempfile
import unittest

try:
    from . import client
    from .fixture import LiveTestCase, samples_dir
except ImportError:  # executed from tests/live
    import client  # type: ignore[no-redef]
    from fixture import LiveTestCase, samples_dir  # type: ignore[no-redef]

TICKET_DIR = os.path.join(tempfile.gettempdir(), "cascadeur-mcp-plus-tests", "T28")
FRAMES = (0, 20, 60, 123)
RENDER_WIDTH = 640
RENDER_HEIGHT = 360


def _prepare_dir() -> str:
    os.makedirs(TICKET_DIR, exist_ok=True)
    return TICKET_DIR


def _digest(path: str) -> str:
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


class RetargetLiveTests(LiveTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.out = _prepare_dir()
        self.created: list[str] = []
        # Other live tickets sometimes leave their own helper windows open (T29's "Rig mode helper"
        # is one). Only dialogs that appear during this test belong to retarget_animation.
        self.baseline_dialogs = {self.dialog_key(dialog) for dialog in self.actionable_dialogs()}

    def tearDown(self) -> None:
        # Keep the rendered PNGs as the ticket's visual evidence; _prepare_dir clears them next run.
        super().tearDown()

    # -- helpers -----------------------------------------------------------

    def actionable_dialogs(self) -> list[dict]:
        dialogs = client.call("dialogs").get("dialogs", [])
        return [dialog for dialog in dialogs if dialog.get("kind") == "dialog" and dialog.get("buttons")]

    @staticmethod
    def dialog_key(dialog: dict) -> tuple:
        return (
            dialog.get("type"),
            tuple(dialog.get("texts") or []),
            tuple((button.get("text") or "").strip() for button in dialog.get("buttons") or []),
        )

    def render(self, scene: str, frame: int) -> str:
        path = os.path.join(self.out, f"{scene}_f{frame:03d}.png")
        if os.path.exists(path):
            os.remove(path)
        self.created.append(path)
        result = client.call(
            "render_image",
            frame=frame,
            width=RENDER_WIDTH,
            height=RENDER_HEIGHT,
            path=path,
            overwrite=True,
            return_image=False,
            http_timeout=330,
        )
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(result["frame"], frame, result)
        self.assertTrue(os.path.isfile(path), result)
        self.assertGreater(os.path.getsize(path), 1000, f"render is empty: {path}")
        with open(path, "rb") as handle:
            self.assertEqual(handle.read(8), b"\x89PNG\r\n\x1a\n", path)
        return path

    def scene_names(self) -> list[str]:
        listed = client.call("list_open_scenes")
        self.assertTrue(listed["ok"], listed)
        return [scene["name"] for scene in listed["scenes"]]

    def assert_no_dialogs(self) -> None:
        new = [dialog for dialog in self.actionable_dialogs() if self.dialog_key(dialog) not in self.baseline_dialogs]
        self.assertEqual(new, [], "retargeting left a new dialog open")

    def assert_tool_error(self, *fragments: str, **arguments) -> str:
        with self.assertRaises(client.LiveError) as caught:
            client.call("retarget_animation", **arguments)
        message = str(caught.exception)
        for fragment in fragments:
            self.assertIn(fragment, message)
        return message

    # -- tests -------------------------------------------------------------

    def test_backflip_to_ue4_mannequin_and_render_frames(self) -> None:
        target = self.open_sample("UE4_Mannequin.casc")
        self.assertEqual(target["name"], "UE4_Mannequin.casc")
        source_path = os.path.join(samples_dir(), "Backflip_animation.casc")
        self.assertTrue(os.path.isfile(source_path), source_path)
        before_scenes = self.scene_names()

        result = client.call(
            "retarget_animation",
            source=source_path,
            target="UE4_Mannequin.casc",
            http_timeout=330,
        )
        self.assertTrue(result.get("ok"), result)
        self.assertNotIn("hint", result, result)
        self.assertEqual(result["source"]["frames"], {"start": 0, "end": 123, "count": 124}, result)
        self.assertEqual(result["source"]["boundary"], {"first": 0, "last": 123}, result)
        self.assertEqual(result["source"]["joints"], 58, result)
        self.assertEqual(result["target"]["scene"], "UE4_Mannequin.casc", result)
        self.assertEqual(result["target"]["joints"], 68, result)
        self.assertEqual(result["target"]["before"]["animation_size"], 1, result)
        self.assertEqual(result["target"]["animation_size"], 124, result)
        self.assertEqual(result["target"]["last_key"], 123, result)
        self.assertEqual(result["target"]["boundary"], {"first": 0, "last": 123}, result)
        self.assertTrue(result["target"]["pose"]["changed_joints"], result)
        self.assertIn("pelvis", result["target"]["pose"]["joints"], result)

        # The absolute source path was opened in a temporary tab and closed again.
        self.assertEqual(self.scene_names(), before_scenes)
        self.assertNotIn("Backflip_animation.casc", self.scene_names())
        self.assert_no_dialogs()

        target_images = [self.render("target", frame) for frame in FRAMES]
        self.assertGreaterEqual(len({_digest(path) for path in target_images}), 3, target_images)

        # Open the source again just to render the same frames for a side-by-side comparison.
        self.open_sample("Backflip_animation.casc")
        source_images = [self.render("source", frame) for frame in FRAMES]
        self.assertGreaterEqual(len({_digest(path) for path in source_images}), 3, source_images)
        self.assert_no_dialogs()

    def test_source_without_animation_reports_no_frames(self) -> None:
        # Cascy.casc has a rig but no keys: Core::copy should reject the empty selection instead of
        # pasting a static pose. This is the T28 error path called out in the ticket.
        self.open_sample("Cascy.casc")
        self.open_sample("UE4_Mannequin.casc")
        message = self.assert_tool_error(
            "no frames selected",
            source="Cascy.casc",
            target="UE4_Mannequin.casc",
            http_timeout=330,
        )
        self.assertIn("no keyed animation", message)
        self.assertIn('"hint"', message)
        # The preflight rejected the static source before Copy/Paste, so the target is untouched.
        self.assertEqual(client.call("get_scene_info")["frames"]["animation_size"], 1)
        self.assert_no_dialogs()


if __name__ == "__main__":
    unittest.main()
