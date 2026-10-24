"""Live checks for the version compatibility report (T54).

1. On the Cascadeur build this release was tested with, get_status reports no unavailable tool,
   every tool was checked, and every action key could be verified.
2. A temporary monkeypatch (csc.glb.process_export removed with delattr) is picked up by a forced
   audit: export_scene is marked unavailable in tools/list, refuses calls with the missing entry,
   and works again after the attribute is restored and the audit runs again.

The monkeypatch only touches a plain function of the csc.glb module (no scene, no tab, no UI), and
the restore is registered with addCleanup, so a failing assertion still puts it back.

Run with: python tests/live/run.py test_compat.py
"""

from __future__ import annotations

import os
import unittest

try:
    from . import client
    from .fixture import LiveTestCase, _run_json
except ImportError:
    import client  # type: ignore[no-redef]
    from fixture import LiveTestCase, _run_json  # type: ignore[no-redef]


TESTED_VERSION = "2026.2.3"
UNAVAILABLE_PREFIX = "Unavailable in this Cascadeur version:"
SAVED_MODULE = "_cascadeur_mcp_plus_t54_saved"

# Remove csc.glb.process_export, keeping the function in a module the restore step can read back.
_REMOVE_CODE = """
import json
import sys
import types

import csc

saved = types.ModuleType($saved)
saved.process_export = csc.glb.process_export
sys.modules[$saved] = saved
delattr(csc.glb, "process_export")
print("__RESULT__" + json.dumps({"removed": not hasattr(csc.glb, "process_export")}))
"""

_RESTORE_CODE = """
import json
import sys

import csc

saved = sys.modules.get($saved)
if saved is None or not hasattr(saved, "process_export"):
    raise RuntimeError("the saved csc.glb.process_export is gone; restart Cascadeur before using csc.glb")
if not hasattr(csc.glb, "process_export"):
    csc.glb.process_export = saved.process_export
del sys.modules[$saved]
print("__RESULT__" + json.dumps({"restored": hasattr(csc.glb, "process_export")}))
"""


def _unavailable_entries(status: dict) -> dict:
    return {item["tool"]: item["missing"] for item in status.get("unavailable", [])}


class CompatibilityLiveTests(LiveTestCase):
    def test_this_build_reports_nothing_unavailable(self) -> None:
        status = client.call("get_status", force=True)
        self.assertTrue(status["ok"], status)
        self.assertEqual(status["cascadeur_version"], TESTED_VERSION)
        self.assertTrue(status["version_tested"])
        self.assertEqual(status["unavailable"], [], "a tool is unavailable on the tested build")
        self.assertEqual(status["unavailable_count"], 0)
        self.assertEqual(status["actions"]["unverified"], [], "an action key could not be verified")
        self.assertGreater(status["requirements"], 500)

        tools = client.tools()
        self.assertEqual(status["tools"], len(tools), "every registered tool is checked")
        marked = [tool["name"] for tool in tools if tool["description"].startswith(UNAVAILABLE_PREFIX)]
        self.assertEqual(marked, [])

    def test_removed_api_is_refused_and_restored(self) -> None:
        self.addCleanup(self._restore_api)
        # A test scene, so an accidental export could never touch the user's scene.
        self.open_sample("Cascy.casc")
        removed = _run_json(_REMOVE_CODE.replace("$saved", repr(SAVED_MODULE)))
        self.assertTrue(removed["removed"])

        status = client.call("get_status", force=True)
        self.assertEqual(_unavailable_entries(status), {"export_scene": ["csc.glb.process_export"]})

        described = {tool["name"]: tool["description"] for tool in client.tools()}
        self.assertTrue(described["export_scene"].startswith(UNAVAILABLE_PREFIX), described["export_scene"])
        self.assertIn("csc.glb.process_export", described["export_scene"])
        self.assertFalse(described["import_model"].startswith(UNAVAILABLE_PREFIX))

        target = os.path.join(self.out_dir(), "compat_refused.glb")
        with self.assertRaises(client.LiveError) as caught:
            client.call("export_scene", path=target)
        message = str(caught.exception)
        self.assertIn(UNAVAILABLE_PREFIX, message)
        self.assertIn("csc.glb.process_export", message)
        self.assertFalse(os.path.exists(target), "a refused call must not write a file")

        restored = _run_json(_RESTORE_CODE.replace("$saved", repr(SAVED_MODULE)))
        self.assertTrue(restored["restored"])
        status = client.call("get_status", force=True)
        self.assertEqual(status["unavailable"], [], "the audit must clear once the API is back")
        described = {tool["name"]: tool["description"] for tool in client.tools()}
        self.assertFalse(described["export_scene"].startswith(UNAVAILABLE_PREFIX))

    def _restore_api(self) -> None:
        """Put csc.glb.process_export back even when an assertion above failed."""
        try:
            _run_json(
                "import sys\nimport json\nimport csc\n"
                f"saved = sys.modules.get({SAVED_MODULE!r})\n"
                "if saved is not None and not hasattr(csc.glb, 'process_export'):\n"
                "    csc.glb.process_export = saved.process_export\n"
                "print('__RESULT__' + json.dumps({'ok': True}))\n"
            )
        except AssertionError:
            pass


if __name__ == "__main__":
    unittest.main()
