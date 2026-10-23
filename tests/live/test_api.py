"""Live checks for api_search and api_describe (T04).

Read-only: no sample scene is opened, so the fixture only records and restores the user's
scene list, current scene and current frame.

Run with: python tests/live/run.py test_api.py
"""

from __future__ import annotations

import time
import unittest

try:
    from . import client
    from .fixture import LiveTestCase
except ImportError:  # executed from tests/live
    import client  # type: ignore[no-redef]
    from fixture import LiveTestCase  # type: ignore[no-redef]


class ApiToolsTests(LiveTestCase):
    def test_search_play_to_video_file(self) -> None:
        result = client.call("api_search", query="play_to_video_file")
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["results"][0]["symbol"], "csc.tools.RenderToFile.play_to_video_file")
        self.assertEqual(result["results"][0]["source"], "stubs")

    def test_search_set_data_value_finds_stub_and_sample(self) -> None:
        result = client.call("api_search", query="set_data_value", limit=50)
        self.assertTrue(result["ok"], result)
        symbols = [item["symbol"] for item in result["results"]]
        self.assertIn("csc.model.DataEditor.set_data_value", symbols)
        self.assertTrue(
            any("move_joints.py" in item["file"] for item in result["results"]),
            "samples/move_joints.py usage was not found",
        )

    def test_describe_class_fields_and_runtime(self) -> None:
        result = client.call("api_describe", symbol="csc.view.SphericalCameraStruct")
        self.assertTrue(result["ok"], result)
        self.assertEqual([field["name"] for field in result["fields"]], ["position", "target", "type"])
        self.assertIn("class SphericalCameraStruct:", result["source"])
        runtime = result["runtime"]
        self.assertTrue(runtime["available"], runtime)
        self.assertIn("missing_from_stub", runtime)

    def test_describe_accepts_short_name(self) -> None:
        result = client.call("api_describe", symbol="RenderToFile")
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["symbol"], "csc.tools.RenderToFile")

    def test_queries_after_index_build_are_fast(self) -> None:
        client.call("api_search", query="render")  # build and cache the index
        samples = []
        for _ in range(5):
            started = time.perf_counter()
            result = client.call("api_search", query="camera", limit=5)
            samples.append(time.perf_counter() - started)
            self.assertTrue(result["ok"], result)
        self.assertLess(max(samples), 1.0, f"api_search took {samples!r} for warm calls")


if __name__ == "__main__":
    unittest.main()
