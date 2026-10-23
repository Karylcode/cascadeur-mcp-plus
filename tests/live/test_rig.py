"""Live tests for list_characters, list_controllers and rig.resolve (T12).

Each test opens a bundled sample in its own tab; the fixture discards it in tearDown and
checks the user's scenes and current frame are unchanged. The alias checks cover the Cascadeur,
UE4 Mannequin and Roblox R15 naming in the samples; the Outliner is the reference for the
interface's main-point count. Mixamo-style names are covered by tests/test_rig_aliases.py.

Run with: python tests/live/run.py test_rig.py
"""

from __future__ import annotations

import json
import re
import time
import unittest

try:
    from . import client
    from .fixture import LiveTestCase
except ImportError:  # executed from tests/live
    import client  # type: ignore[no-redef]
    from fixture import LiveTestCase  # type: ignore[no-redef]


# sample -> alias -> the main control point the alias must resolve to
EXPECTED_ALIASES = {
    "Cascy.casc": {
        "left_hand": "hand_MainPoint_l",
        "right_hand": "hand_MainPoint_r",
        "right_foot": "foot_MainPoint_r",
        "pelvis": "pelvis_MainPoint",
        "head": "head_MainPoint",
    },
    "UE4_Mannequin.casc": {
        "left_hand": "hand_MainPoint_l",
        "right_hand": "hand_MainPoint_r",
        "right_foot": "foot_MainPoint_r",
        "pelvis": "pelvis_MainPoint",
        "head": "head_MainPoint",
    },
    "Roblox_R15_Blocky.casc": {
        "left_hand": "LeftHand_MainPoint",
        "right_hand": "RightHand_MainPoint",
        "right_foot": "RightFoot_MainPoint",
        "pelvis": "LowerTorso_MainPoint",
        "head": "Head_MainPoint",
    },
}

RESOLVE = """
import json

import cascadeur_mcp_plus.rig as rig

try:
    ids = rig.resolve({names}, character={character})
    model_viewer = scene.model_viewer()
    print("__RESULT__" + json.dumps({{"ok": True, "names": [model_viewer.get_object_name(item) for item in ids]}}))
except rig.RigError as exc:
    print(
        "__RESULT__"
        + json.dumps(
            {{
                "ok": False,
                "kind": type(exc).__name__,
                "error": str(exc),
                "hint": exc.hint,
                "candidates": exc.candidates,
            }}
        )
    )
"""


class RigAliasTests(LiveTestCase):
    def _run_json(self, code: str, timeout: float = 90.0) -> dict:
        result = client.call("run_python", code=code, timeout=timeout, http_timeout=timeout + 30.0)
        self.assertTrue(isinstance(result, dict) and result.get("ok"), f"run_python failed: {result!r}")
        for line in reversed(str(result.get("stdout", "")).splitlines()):
            if line.startswith("__RESULT__"):
                return json.loads(line[len("__RESULT__") :])
        self.fail(f"run_python produced no __RESULT__ line: {result!r}")

    def _resolve(self, names, character=None) -> dict:
        # repr() so None becomes Python's None rather than JSON's null inside run_python.
        code = RESOLVE.format(names=json.dumps(list(names)), character=repr(character))
        return self._run_json(code)

    def _outliner_filter_ref(self) -> str:
        """The ref of the Outliner's "Filter by name" input (refs reset on every ui_tree)."""
        tree = client.call("ui_tree", query="Outliner", limit=200)
        for element in tree.get("elements", []):
            if element.get("placeholderText") == "Filter by name":
                return element["ref"]
        self.fail("Cascadeur's Outliner has no 'Filter by name' field")

    def _outliner_names(self, filter_text: str) -> list[str]:
        """The Outliner rows Cascadeur shows after filtering by name.

        The ref is used instead of a text query: with several panels open, a query can match
        the filter's label instead of its text input.
        """
        client.call(
            "ui_type",
            ref=self._outliner_filter_ref(),
            text=filter_text,
            replace=True,
            release_focus=True,
        )
        time.sleep(0.4)  # let the Outliner apply the filter before reading its rows
        try:
            tree = client.call("ui_tree", limit=5000)
        finally:
            client.call(
                "ui_type",
                ref=self._outliner_filter_ref(),
                text="",
                replace=True,
                release_focus=True,
            )
        names = []
        for element in tree.get("elements", []):
            text = element.get("text")
            if text:
                names.append(re.sub(r"<[^>]+>", "", text).strip())
        return [name for name in names if name]

    def test_aliases_resolve_on_three_rig_styles(self) -> None:
        for sample, expected in EXPECTED_ALIASES.items():
            with self.subTest(sample=sample):
                opened = self.open_sample(sample)
                self.assertEqual(opened["name"], sample)

                characters = client.call("list_characters")
                self.assertTrue(characters["ok"], characters)
                self.assertEqual(characters["count"], 1, characters)
                character = characters["characters"][0]
                if sample == "Cascy.casc":
                    self.assertIn("Cascy_mesh", character["meshes"], character)

                listed = client.call("list_controllers", character=character["name"])
                self.assertTrue(listed["ok"], listed)
                self.assertGreater(listed["count"], 0, listed)
                by_alias = {item["alias"]: item for item in listed["controllers"] if item["alias"]}

                for alias, name in expected.items():
                    self.assertIn(alias, by_alias, f"{sample}: alias {alias!r} is missing")
                    self.assertEqual(by_alias[alias]["name"], name, f"{sample}: alias {alias!r}")
                    self.assertEqual(by_alias[alias]["kind"], "main_point", f"{sample}: alias {alias!r} kind")

                resolved = self._resolve(expected)
                self.assertTrue(resolved.get("ok"), f"{sample}: {resolved}")
                self.assertEqual(resolved["names"], list(expected.values()), sample)

                positions = {alias: by_alias[alias]["position"] for alias in expected}
                for alias, position in positions.items():
                    self.assertIsInstance(position, list, f"{sample}: {alias} has no position")
                    self.assertEqual(len(position), 3, f"{sample}: {alias} position {position!r}")
                left = positions["left_hand"]
                right = positions["right_hand"]
                self.assertLess(
                    left[0] * right[0], 0, f"{sample}: left/right hand x are not opposite ({left}, {right})"
                )
                self.assertGreater(
                    positions["head"][1],
                    positions["pelvis"][1],
                    f"{sample}: head is not above the pelvis ({positions['head']}, {positions['pelvis']})",
                )

    def test_ambiguous_name_returns_candidates(self) -> None:
        self.open_sample("Cascy.casc")

        result = self._resolve(["hand"])
        self.assertFalse(result.get("ok"), result)
        self.assertEqual(result["kind"], "AmbiguousName", result)
        names = [item["name"] for item in result["candidates"]]
        self.assertIn("hand_MainPoint_l", names, result)
        self.assertIn("hand_MainPoint_r", names, result)
        self.assertGreaterEqual(len(names), 2, result)

        result = self._resolve(["foot"])
        self.assertFalse(result.get("ok"), result)
        self.assertEqual(result["kind"], "AmbiguousName", result)
        self.assertEqual(
            sorted(item["name"] for item in result["candidates"]),
            ["foot_MainPoint_l", "foot_MainPoint_r"],
            result,
        )

        result = self._resolve(["not_a_bone"])
        self.assertFalse(result.get("ok"), result)
        self.assertEqual(result["kind"], "UnknownName", result)
        self.assertIn("hint", result)

    def test_main_point_count_matches_cascadeur_interface(self) -> None:
        self.open_sample("Cascy.casc")
        listed = client.call("list_controllers", kinds=["main_point"])
        self.assertTrue(listed["ok"], listed)
        self.assertEqual(listed["count"], 21, [item["name"] for item in listed["controllers"]])
        names = {item["name"] for item in listed["controllers"]}
        self.assertEqual(len(names), 21)
        for name in names:
            self.assertIn("_MainPoint", name)

        # The Outliner filtered to "MainPoint" is the interface's list of these controls. It
        # shows exactly the 21 main control points (plus EdgeView edges, which are not
        # controllers and are ignored here).
        ui_names = self._outliner_names("MainPoint")
        ui_main = {
            name for name in ui_names if not name.startswith("EdgeView_") and re.search(r"_MainPoint(_[lr])?$", name)
        }
        self.assertEqual(
            ui_main,
            names,
            f"the Outliner shows {len(ui_main)} main points, list_controllers lists {len(names)}",
        )

    def test_autoposing_lock_is_not_faked(self) -> None:
        self.open_sample("Cascy.casc")
        listed = client.call("list_controllers", kinds=["main_point"])
        self.assertTrue(listed["ok"], listed)
        states = {item["name"]: item["autoposing_locked"] for item in listed["controllers"]}
        self.assertTrue(states)
        # Cascadeur 2026.2.3 has the AutoPosing lock actions but no Python query for the
        # per-point lock. The field must be null instead of reporting ToolFixation, which is a
        # different flag.
        self.assertEqual(set(states.values()), {None}, states)
        locked_action = client.call("ui_tree", query="Lock selected controllers")
        self.assertTrue(locked_action["elements"], "the AutoPosing lock action is missing from the interface")


if __name__ == "__main__":
    unittest.main()
