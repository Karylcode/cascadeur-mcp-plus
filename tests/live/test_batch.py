"""Live tests for T41's batch tool.

Most tests open their own Cascy.casc tab with the fixture; the first test changes one
Transform value and selects that joint in its test tab, and tearDown discards the tab
without saving (no test closes a scene itself). The failure cases use get_object_info with
a name that cannot exist, and the budget case stops before any call runs, so they are
read-only.

Run with: python tests/live/run.py test_batch.py
"""

from __future__ import annotations

import ast
import json
import unittest

try:
    from . import client
    from .fixture import LiveTestCase
except ImportError:
    import client  # type: ignore[no-redef]
    from fixture import LiveTestCase  # type: ignore[no-redef]


def raw_call(tool: str, **arguments) -> dict:
    """Return a tool's JSON payload even when the server marks the result isError.

    client.call raises LiveError for isError results, which would hide the partial results
    that batch deliberately returns after a failed call.
    """
    response = client._rpc("tools/call", {"name": tool, "arguments": arguments})
    result = response.get("result") or {}
    text = " ".join(item.get("text", "") for item in result.get("content", []) if item.get("type") == "text").strip()
    return json.loads(text)


class BatchLiveTests(LiveTestCase):
    def test_scene_info_run_python_scene_info(self):
        opened = self.open_sample("Cascy.casc")
        self.assertEqual(opened["name"], "Cascy.casc")
        before = client.call("get_scene_info")

        result = client.call(
            "batch",
            calls=[
                {"tool": "get_scene_info", "arguments": {}},
                {
                    "tool": "run_python",
                    "arguments": {
                        "code": """
mv = scene.model_viewer()
bv = mv.behaviour_viewer()
dv = mv.data_viewer()
frame = scene.get_current_frame()
owner = mv.get_objects("toe_r")[0]
transform = bv.get_behaviour_by_name(owner, "Transform")
position_id = bv.get_behaviour_data(transform, "global_position")
origin = dv.get_data_value(position_id, frame)

def change(model_editor, update_editor, scene_updater):
    model_editor.data_editor().set_data_value(position_id, frame, origin + [1.0, 0.0, 0.0])
    scene_updater.run_update({position_id}, frame)

changed = scene.modify_update("T41 batch modification", change)
moved = dv.get_data_value(position_id, frame)
scene.selector().select({owner})
result = {
    "changed": bool(changed),
    "before_x": float(origin[0]),
    "after_x": float(moved[0]),
    "selected": len(scene.selector().selected().ids),
    "selected_name": mv.get_object_name(owner),
}
""",
                        "timeout": 30,
                    },
                },
                {"tool": "get_scene_info", "arguments": {}},
            ],
        )
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["count"], 3)
        self.assertEqual([item["index"] for item in result["results"]], [0, 1, 2])
        self.assertEqual(
            [item["tool"] for item in result["results"]],
            ["get_scene_info", "run_python", "get_scene_info"],
        )
        self.assertEqual([item["ok"] for item in result["results"]], [True, True, True])

        first, second, third = result["results"]
        self.assertEqual(first["result"]["scene"]["name"], "Cascy.casc")
        self.assertEqual(first["result"]["objects"]["count"], before["objects"]["count"])

        self.assertTrue(second["result"]["ok"], second["result"])
        change = ast.literal_eval(second["result"]["value"])
        self.assertTrue(change["changed"], change)
        self.assertNotEqual(change["after_x"], change["before_x"])
        self.assertEqual(change["selected"], 1)
        self.assertEqual(change["selected_name"], "toe_r")

        self.assertEqual(third["result"]["scene"]["name"], "Cascy.casc")
        self.assertEqual(third["result"]["objects"]["count"], before["objects"]["count"])
        self.assertEqual(third["result"]["selection"]["count"], 1)
        self.assertEqual(third["result"]["selection"]["names"], ["toe_r"])

    def test_stop_on_error_true_stops_at_the_failed_call(self):
        self.open_sample("Cascy.casc")
        payload = raw_call(
            "batch",
            calls=[
                {"tool": "get_scene_info", "arguments": {}},
                {"tool": "get_object_info", "arguments": {"name": "T41-no-such-object"}},
                {"tool": "run_python", "arguments": {"code": "result = 'must not run'", "timeout": 30}},
            ],
        )
        self.assertFalse(payload["ok"], payload)
        self.assertEqual(payload["stopped_at"], 1)
        self.assertEqual([item["index"] for item in payload["results"]], [0, 1])
        self.assertTrue(payload["results"][0]["ok"])
        self.assertFalse(payload["results"][1]["ok"])
        self.assertIn("T41-no-such-object", payload["results"][1]["result"]["error"])
        self.assertIn("call 1", payload["error"])
        self.assertIn("get_object_info", payload["error"])
        self.assertIn("results[1]", payload["hint"])

    def test_stop_on_error_false_runs_the_later_calls(self):
        self.open_sample("Cascy.casc")
        payload = raw_call(
            "batch",
            stop_on_error=False,
            calls=[
                {"tool": "get_scene_info", "arguments": {}},
                {"tool": "get_object_info", "arguments": {"name": "T41-no-such-object"}},
                {"tool": "run_python", "arguments": {"code": "result = 'later call ran'", "timeout": 30}},
            ],
        )
        self.assertFalse(payload["ok"], payload)
        self.assertEqual(payload["failed"], [1])
        self.assertEqual(len(payload["results"]), 3)
        self.assertEqual([item["ok"] for item in payload["results"]], [True, False, True])
        self.assertIn("1 of 3", payload["error"])
        self.assertEqual(payload["results"][0]["result"]["scene"]["name"], "Cascy.casc")
        self.assertFalse(payload["results"][1]["ok"])
        self.assertTrue(payload["results"][2]["result"]["ok"])
        self.assertEqual(payload["results"][2]["result"]["value"], "'later call ran'")

    def test_timeout_stops_before_a_call_that_cannot_fit(self):
        # 1 s of budget cannot cover get_scene_info's 60 s default, so the call must not run.
        payload = raw_call(
            "batch",
            timeout=1,
            calls=[
                {"tool": "get_scene_info", "arguments": {}},
            ],
        )
        self.assertFalse(payload["ok"], payload)
        self.assertEqual(payload["stopped_at"], 0)
        self.assertEqual(payload["results"], [])
        self.assertEqual(payload["not_run"], [{"index": 0, "tool": "get_scene_info"}])
        self.assertIn("did not start call 0", payload["error"])
        self.assertIn("batch timeout", payload["error"])
        self.assertIn("not_run", payload["hint"])

    def test_nested_batch_is_rejected(self):
        payload = raw_call(
            "batch",
            calls=[
                {"tool": "batch", "arguments": {"calls": []}},
            ],
        )
        self.assertFalse(payload["ok"], payload)
        self.assertIn("calls[0]", payload["error"])
        self.assertIn("nested", payload["error"])
        self.assertNotIn("results", payload)


if __name__ == "__main__":
    unittest.main()
