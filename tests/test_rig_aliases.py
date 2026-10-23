"""Alias matching tests for cascadeur_mcp_plus/rig.py (T12).

The module is loaded from its file, so this test does not import the package __init__ (which
loads Cascadeur's server and PySide6). No Cascadeur is needed.

The main-point names below are the real names from Cascadeur's bundled samples
(Cascy.casc, UE4_Mannequin.casc, Roblox_R15_Blocky.casc); the Mixamo list uses that rig's
standard bone names (mixamorig:LeftHand style).

Run: python -m unittest discover -s tests -v
"""

from __future__ import annotations

import importlib.util
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RIG_PATH = os.path.join(ROOT, "cascadeur_mcp_plus", "rig.py")

_SPEC = importlib.util.spec_from_file_location("cascadeur_mcp_plus_rig_for_tests", RIG_PATH)
rig = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(rig)


# --- Real main-point names, sampled from the listed Cascadeur sample scenes -----------------

CASCY_MAIN_POINTS = [
    "pelvis_MainPoint",
    "thigh_MainPoint_r",
    "calf_MainPoint_r",
    "foot_MainPoint_r",
    "toe_MainPoint_r",
    "thigh_MainPoint_l",
    "calf_MainPoint_l",
    "foot_MainPoint_l",
    "toe_MainPoint_l",
    "stomach_MainPoint",
    "chest_MainPoint",
    "clavicle_MainPoint_r",
    "neck_MainPoint",
    "head_MainPoint",
    "clavicle_MainPoint_l",
    "arm_MainPoint_l",
    "forearm_MainPoint_l",
    "hand_MainPoint_l",
    "arm_MainPoint_r",
    "forearm_MainPoint_r",
    "hand_MainPoint_r",
]

UE4_MAIN_POINTS = [
    "pelvis_MainPoint",
    "thigh_MainPoint_r",
    "calf_MainPoint_r",
    "spine_01_MainPoint",
    "spine_02_MainPoint",
    "neck_01_MainPoint",
    "clavicle_MainPoint_l",
    "upperarm_MainPoint_l",
    "clavicle_MainPoint_r",
    "upperarm_MainPoint_r",
    "head_MainPoint",
    "lowerarm_MainPoint_r",
    "hand_MainPoint_r",
    "thigh_MainPoint_l",
    "foot_MainPoint_r",
    "ball_MainPoint_r",
    "lowerarm_MainPoint_l",
    "hand_MainPoint_l",
    "calf_MainPoint_l",
    "foot_MainPoint_l",
    "ball_MainPoint_l",
]

ROBLOX_R15_MAIN_POINTS = [
    "LowerTorso_MainPoint",
    "UpperTorso_MainPoint",
    "RightUpperArm_MainPoint",
    "RightLowerArm_MainPoint",
    "Head_MainPoint",
    "LeftUpperArm_MainPoint",
    "LeftUpperLeg_MainPoint",
    "LeftLowerArm_MainPoint",
    "LeftHand_MainPoint",
    "RightHand_MainPoint",
    "RightUpperLeg_MainPoint",
    "RightLowerLeg_MainPoint",
    "RightFoot_MainPoint",
    "LeftLowerLeg_MainPoint",
    "LeftFoot_MainPoint",
]

# The other Point objects in Cascy (15 AdditionalPoint, 5 DirectionPoint, 2 Self0Point).
# Cascadeur's StaticPointsTypes.get_main_points() includes several of these, but the interface
# and the aliases treat only *_MainPoint objects as main points.
CASCY_OTHER_POINTS = [
    "calf_AdditionalPoint_l",
    "calf_AdditionalPoint_r",
    "chest_AdditionalPoint",
    "clavicle_AdditionalPoint_l",
    "clavicle_AdditionalPoint_r",
    "forearm_AdditionalPoint_l",
    "forearm_AdditionalPoint_r",
    "hand_AdditionalPoint_l",
    "hand_AdditionalPoint_r",
    "head_AdditionalPoint",
    "neck_AdditionalPoint",
    "pelvis_AdditionalPoint",
    "stomach_AdditionalPoint",
    "toe_AdditionalPoint_l",
    "toe_AdditionalPoint_r",
    "hand_DirectionPoint_l",
    "hand_DirectionPoint_r",
    "head_DirectionPoint",
    "toe_DirectionPoint_l",
    "toe_DirectionPoint_r",
    "foot_Self0Point_l",
    "foot_Self0Point_r",
]

MIXAMO_MAIN_POINTS = [
    "mixamorig:Hips_MainPoint",
    "mixamorig:LeftUpLeg_MainPoint",
    "mixamorig:LeftLeg_MainPoint",
    "mixamorig:LeftFoot_MainPoint",
    "mixamorig:LeftToeBase_MainPoint",
    "mixamorig:RightUpLeg_MainPoint",
    "mixamorig:RightLeg_MainPoint",
    "mixamorig:RightFoot_MainPoint",
    "mixamorig:RightToeBase_MainPoint",
    "mixamorig:Spine_MainPoint",
    "mixamorig:Spine1_MainPoint",
    "mixamorig:Spine2_MainPoint",
    "mixamorig:Neck_MainPoint",
    "mixamorig:Head_MainPoint",
    "mixamorig:LeftShoulder_MainPoint",
    "mixamorig:LeftArm_MainPoint",
    "mixamorig:LeftForeArm_MainPoint",
    "mixamorig:LeftHand_MainPoint",
    "mixamorig:RightShoulder_MainPoint",
    "mixamorig:RightArm_MainPoint",
    "mixamorig:RightForeArm_MainPoint",
    "mixamorig:RightHand_MainPoint",
]


def controllers_for(names, kinds=None):
    """Controller dicts like rig.controllers returns, for pure matching tests."""
    aliases = rig.alias_map(names)
    result = []
    for name in names:
        kind = (kinds or {}).get(name, "main_point")
        result.append({"name": name, "kind": kind, "alias": aliases.get(name), "type": "Point"})
    return result


class NameNormalisationTests(unittest.TestCase):
    def test_cascadeur_style_name(self) -> None:
        self.assertEqual(rig.normalize_name("hand_MainPoint_l"), (("hand",), "l"))
        self.assertEqual(rig.normalize_name("thigh_MainPoint_r"), (("thigh",), "r"))
        self.assertEqual(rig.normalize_name("pelvis_MainPoint"), (("pelvis",), None))

    def test_namespace_prefix_is_ignored(self) -> None:
        for name in ("mixamorig:LeftHand_MainPoint", "_1:LeftHand_MainPoint", "Armature|LeftHand_MainPoint"):
            self.assertEqual(rig.normalize_name(name), (("hand",), "l"), name)

    def test_separators_and_case(self) -> None:
        for name in ("LeftHand_MainPoint", "left hand main point", "hand.L", "hand_l_MainPoint"):
            self.assertEqual(rig.normalize_name(name), (("hand",), "l"), name)

    def test_markers_and_side_are_removed(self) -> None:
        self.assertEqual(rig.normalize_name("head_DirectionPoint"), (("head",), None))
        self.assertEqual(rig.normalize_name("hand_AdditionalPoint_l"), (("hand",), "l"))
        self.assertEqual(rig.normalize_name("toe_Self0Point_r"), (("toe",), "r"))
        self.assertEqual(rig.normalize_name("calf_LimbDir_l"), (("calf",), "l"))

    def test_camel_case_compounds(self) -> None:
        self.assertEqual(rig.normalize_name("LeftUpperArm_MainPoint"), (("upper", "arm"), "l"))
        self.assertEqual(rig.normalize_name("LowerTorso_MainPoint"), (("lower", "torso"), None))
        self.assertEqual(rig.normalize_name("spine_02_MainPoint"), (("spine", "02"), None))


class AliasMapTests(unittest.TestCase):
    def assert_alias(self, mapping, alias, name) -> None:
        owners = [candidate for candidate, value in mapping.items() if value == alias]
        self.assertEqual(owners, [name], f"alias {alias!r} should resolve to {name!r}")

    def test_cascadeur_standard_rig(self) -> None:
        mapping = rig.alias_map(CASCY_MAIN_POINTS)
        expected = {
            "pelvis": "pelvis_MainPoint",
            "chest": "chest_MainPoint",
            "head": "head_MainPoint",
            "neck": "neck_MainPoint",
            "left_hand": "hand_MainPoint_l",
            "right_hand": "hand_MainPoint_r",
            "left_elbow": "forearm_MainPoint_l",
            "right_elbow": "forearm_MainPoint_r",
            "left_shoulder": "clavicle_MainPoint_l",
            "right_shoulder": "clavicle_MainPoint_r",
            "left_foot": "foot_MainPoint_l",
            "right_foot": "foot_MainPoint_r",
            "left_knee": "calf_MainPoint_l",
            "right_knee": "calf_MainPoint_r",
            "left_toe": "toe_MainPoint_l",
            "right_toe": "toe_MainPoint_r",
        }
        for alias, name in expected.items():
            self.assert_alias(mapping, alias, name)
        self.assertNotIn("center_of_mass", mapping.values())

    def test_ue4_mannequin_rig(self) -> None:
        mapping = rig.alias_map(UE4_MAIN_POINTS)
        expected = {
            "pelvis": "pelvis_MainPoint",
            "chest": "spine_02_MainPoint",
            "head": "head_MainPoint",
            "neck": "neck_01_MainPoint",
            "left_hand": "hand_MainPoint_l",
            "right_hand": "hand_MainPoint_r",
            "left_elbow": "lowerarm_MainPoint_l",
            "right_elbow": "lowerarm_MainPoint_r",
            "left_shoulder": "clavicle_MainPoint_l",
            "right_shoulder": "clavicle_MainPoint_r",
            "left_foot": "foot_MainPoint_l",
            "right_foot": "foot_MainPoint_r",
            "left_knee": "calf_MainPoint_l",
            "right_knee": "calf_MainPoint_r",
            "left_toe": "ball_MainPoint_l",
            "right_toe": "ball_MainPoint_r",
        }
        for alias, name in expected.items():
            self.assert_alias(mapping, alias, name)

    def test_roblox_r15_rig(self) -> None:
        mapping = rig.alias_map(ROBLOX_R15_MAIN_POINTS)
        expected = {
            "pelvis": "LowerTorso_MainPoint",
            "chest": "UpperTorso_MainPoint",
            "head": "Head_MainPoint",
            "left_hand": "LeftHand_MainPoint",
            "right_hand": "RightHand_MainPoint",
            "left_elbow": "LeftLowerArm_MainPoint",
            "right_elbow": "RightLowerArm_MainPoint",
            "left_shoulder": "LeftUpperArm_MainPoint",
            "right_shoulder": "RightUpperArm_MainPoint",
            "left_foot": "LeftFoot_MainPoint",
            "right_foot": "RightFoot_MainPoint",
            "left_knee": "LeftLowerLeg_MainPoint",
            "right_knee": "RightLowerLeg_MainPoint",
        }
        for alias, name in expected.items():
            self.assert_alias(mapping, alias, name)
        # R15 has no neck or toe control points; do not invent them.
        self.assertNotIn("neck", mapping.values())
        self.assertNotIn("left_toe", mapping.values())
        self.assertNotIn("right_toe", mapping.values())

    def test_mixamo_rig(self) -> None:
        mapping = rig.alias_map(MIXAMO_MAIN_POINTS)
        expected = {
            "pelvis": "mixamorig:Hips_MainPoint",
            "chest": "mixamorig:Spine2_MainPoint",
            "head": "mixamorig:Head_MainPoint",
            "neck": "mixamorig:Neck_MainPoint",
            "left_hand": "mixamorig:LeftHand_MainPoint",
            "right_hand": "mixamorig:RightHand_MainPoint",
            "left_elbow": "mixamorig:LeftForeArm_MainPoint",
            "right_elbow": "mixamorig:RightForeArm_MainPoint",
            "left_shoulder": "mixamorig:LeftShoulder_MainPoint",
            "right_shoulder": "mixamorig:RightShoulder_MainPoint",
            "left_foot": "mixamorig:LeftFoot_MainPoint",
            "right_foot": "mixamorig:RightFoot_MainPoint",
            "left_knee": "mixamorig:LeftLeg_MainPoint",
            "right_knee": "mixamorig:RightLeg_MainPoint",
            "left_toe": "mixamorig:LeftToeBase_MainPoint",
            "right_toe": "mixamorig:RightToeBase_MainPoint",
        }
        for alias, name in expected.items():
            self.assert_alias(mapping, alias, name)

    def test_each_required_alias_has_an_owner_in_a_full_rig(self) -> None:
        for fixture in (CASCY_MAIN_POINTS, UE4_MAIN_POINTS, MIXAMO_MAIN_POINTS):
            mapping = rig.alias_map(fixture)
            missing = set(rig.ALIASES) - {"center_of_mass"} - set(mapping.values())
            self.assertEqual(missing, set(), fixture[0])

    def test_highest_spine_is_chest(self) -> None:
        mapping = rig.alias_map(["spine_01_MainPoint", "spine_02_MainPoint"])
        self.assertEqual(mapping, {"spine_02_MainPoint": "chest"})
        mapping = rig.alias_map(["spine_01_MainPoint", "spine_03_MainPoint"])
        self.assertEqual(mapping, {"spine_03_MainPoint": "chest"})

    def test_tie_is_not_guessed(self) -> None:
        # Roblox R6 has a single Torso, which is equally pelvis-like and chest-like.
        self.assertEqual(rig.alias_map(["Torso_MainPoint"]), {})

    def test_other_point_types_do_not_steal_main_point_aliases(self) -> None:
        # Live probe finding: the sample scene has 43 Point objects (21 MainPoint, 15
        # AdditionalPoint, 5 DirectionPoint, 2 Self0Point) and get_main_points() returns all
        # 43 of some of them; only the MainPoint objects may own the body aliases.
        every_point = CASCY_MAIN_POINTS + CASCY_OTHER_POINTS
        self.assertEqual(len(every_point), 43)
        self.assertEqual(rig.alias_map(every_point), rig.alias_map(CASCY_MAIN_POINTS))
        self.assertEqual(len(rig.alias_map(every_point)), 16)

    def test_finger_points_get_no_body_alias(self) -> None:
        for name in ("f_thumb1_MainPoint_l", "f_index1_MainPoint_r", "f_pinky3_MainPoint_l"):
            self.assertEqual(rig.alias_map([name]), {}, name)

    def test_thigh_and_upper_leg_are_not_knees(self) -> None:
        mapping = rig.alias_map(["thigh_MainPoint_l", "LeftUpLeg_MainPoint", "LeftUpperLeg_MainPoint"])
        self.assertEqual(mapping, {})


class MatchingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.controllers = controllers_for(CASCY_MAIN_POINTS)
        self.controllers += [
            {"name": "hand_l", "kind": "joint", "alias": None, "type": "Joint"},
            {"name": "hand_r", "kind": "joint", "alias": None, "type": "Joint"},
            {"name": "pelvis", "kind": "joint", "alias": None, "type": "Joint"},
            {"name": "hand_Box_l", "kind": "box", "alias": None, "type": "Box"},
            {"name": "hand_DirectionPoint_l", "kind": "direction", "alias": None, "type": "Point"},
        ]

    def names(self, query):
        return [item["name"] for item in rig.match_controllers(query, self.controllers)]

    def test_alias_beats_the_identically_named_joint(self) -> None:
        self.assertEqual(self.names("pelvis"), ["pelvis_MainPoint"])
        self.assertEqual(self.names("left_hand"), ["hand_MainPoint_l"])
        self.assertEqual(self.names("right_foot"), ["foot_MainPoint_r"])

    def test_exact_object_name(self) -> None:
        self.assertEqual(self.names("hand_MainPoint_l"), ["hand_MainPoint_l"])
        self.assertEqual(self.names("hand_Box_l"), ["hand_Box_l"])

    def test_side_spellings(self) -> None:
        for query in ("LeftHand", "left_hand", "hand_l", "hand.L", "left hand", "hand_l_MainPoint"):
            self.assertEqual(self.names(query), ["hand_MainPoint_l"], query)

    def test_partial_name_prefers_main_points(self) -> None:
        self.assertEqual(sorted(self.names("hand")), ["hand_MainPoint_l", "hand_MainPoint_r"])
        self.assertEqual(sorted(self.names("foot")), ["foot_MainPoint_l", "foot_MainPoint_r"])

    def test_unknown_name_returns_no_match(self) -> None:
        self.assertEqual(self.names("tail_tip"), [])
        self.assertEqual(rig.suggest_names("pelvus", [item["name"] for item in self.controllers]), ["pelvis"])

    def test_center_of_mass_alias(self) -> None:
        controllers = self.controllers + [
            {"name": "Center of mass", "kind": "other", "alias": "center_of_mass", "type": "Center of Mass"}
        ]
        for query in ("center_of_mass", "center of mass", "CenterOfMass", "com"):
            self.assertEqual(self.names_for(query, controllers), ["Center of mass"], query)

    @staticmethod
    def names_for(query, controllers):
        return [item["name"] for item in rig.match_controllers(query, controllers)]


class KindTests(unittest.TestCase):
    def test_kinds_from_real_names(self) -> None:
        cases = {
            ("pelvis_MainPoint", "Point"): "main_point",
            ("hand_MainPoint_l", "Point"): "main_point",
            ("mixamorig:LeftHand_MainPoint", "Point"): "main_point",
            ("hand_DirectionPoint_l", "Point"): "direction",
            ("hand_AdditionalPoint_l", "Point"): "other",
            ("foot_Self0Point_r", "Point"): "other",
            ("calf_LimbDir_l", "Limb Direction"): "direction",
            ("hand_Box_l", "Box"): "box",
            ("pelvis", "Joint"): "joint",
            ("Center of mass", "Center of Mass"): "other",
        }
        for (name, type_name), expected in cases.items():
            self.assertEqual(rig.name_kind(name, type_name), expected, name)

    def test_alias_query(self) -> None:
        self.assertEqual(rig.alias_query("left_hand"), "left_hand")
        self.assertEqual(rig.alias_query("Left Hand"), "left_hand")
        self.assertEqual(rig.alias_query("pelvis"), "pelvis")
        self.assertEqual(rig.alias_query("hand"), None)
        self.assertEqual(rig.alias_query("center_of_mass"), "center_of_mass")
        self.assertEqual(rig.alias_query("hand_MainPoint_l"), None)


if __name__ == "__main__":
    unittest.main()
