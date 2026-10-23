"""Base class and Cascadeur-side helpers for live tests.

A live test talks to the running MCP Plus server through client.py. The scene
bookkeeping runs inside Cascadeur in run_python, because a view scene object cannot
cross the HTTP boundary. A small module in Cascadeur's sys.modules (the registry)
holds the user's scene descriptors, the user's current scene/frame, and the count of
user scenes.

Test scenes are found by comparing scene descriptors with the user's baseline, not by
tab position: entering Rig mode can reload the scene and move the test tab in front of
the user's tab (observed on Cascadeur 2026.2.3).

Nothing here imports Cascadeur in the test process.

Teardown notes (learned the hard way on Cascadeur 2026.2.3): closing a scene tab and
then touching the scene manager in the same run_python call can crash Cascadeur in
Qt6Core (QObject signal activation after the tab's QML objects were destroyed), and
remove_application_scene() on a modified tab crashes it too. So close_test_scenes()
closes one tab per run_python call, returns immediately after close_scene(), then waits
and restores the user's scene in a separate call.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
import uuid
from string import Template

try:  # imported as live.fixture by unittest discovery
    from . import client
except ImportError:  # imported as fixture by run.py
    import client  # type: ignore[no-redef]


REGISTRY = "_cascadeur_mcp_plus_live"
_TOKEN = uuid.uuid4().hex
_SAMPLES_DIR: str | None = None


# ---------------------------------------------------------------------------
# In-Cascadeur helpers (executed by run_python on Cascadeur's GUI thread)
# ---------------------------------------------------------------------------

_INIT_CODE = Template(
    """
import json
import sys
import types


def describe(scene):
    return [scene.name(), scene.get_path_name()]


def keys(scenes):
    return sorted((scene.name(), scene.get_path_name()) for scene in scenes)


name = $registry
token = $token
reg = sys.modules.get(name)
sm = csc.app.get_application().get_scene_manager()
current = sm.current_scene()
if current is None:
    raise RuntimeError("Cascadeur has no open scene; open or create one before live tests")
scenes = sm.scenes()

if reg is not None and getattr(reg, "token", None) == token:
    problems = []
    if keys(scenes) != sorted(tuple(item) for item in reg.user_scenes):
        problems.append("open scenes")
    if describe(current) != list(reg.user_current):
        problems.append("current scene")
    if current.domain_scene().get_current_frame() != reg.user_frame:
        problems.append("current frame")
    if problems:
        raise RuntimeError("user scene state changed since the live tests started: " + ", ".join(problems))
else:
    if reg is not None:
        if keys(scenes) != sorted(tuple(item) for item in reg.user_scenes):
            raise RuntimeError(
                "a previous live test changed the user's scenes; close the leftover test tabs and restart "
                "MCP Plus before running live tests again"
            )
    reg = types.ModuleType(name)
    reg.token = token
    reg.sm = sm
    reg.dsm = csc.app.get_application().get_data_source_manager()
    reg.user_scenes = [list(item) for item in keys(scenes)]
    reg.user_count = len(scenes)
    reg.user_current = describe(current)
    reg.user_frame = current.domain_scene().get_current_frame()
    sys.modules[name] = reg

print("__RESULT__" + json.dumps({
    "scenes": [describe(s) for s in scenes],
    "current": describe(current),
    "frame": current.domain_scene().get_current_frame(),
}))
"""
)

_SAMPLES_CODE = """
import json
import os
import sys

candidates = []
if os.environ.get("CASCADEUR_DIR"):
    candidates.append(os.environ["CASCADEUR_DIR"])
csc_file = getattr(csc, "__file__", None)
if csc_file:
    path = os.path.dirname(os.path.abspath(csc_file))
    while True:
        candidates.append(path)
        parent = os.path.dirname(path)
        if parent == path:
            break
        path = parent
if getattr(sys, "executable", ""):
    candidates.append(os.path.dirname(sys.executable))
try:
    from PySide6 import QtCore

    candidates.append(QtCore.QCoreApplication.applicationDirPath())
except Exception:
    pass

for root in candidates:
    samples = os.path.join(root, "samples")
    if os.path.isdir(samples):
        print("__RESULT__" + json.dumps({"root": root, "samples": samples}))
        break
else:
    raise RuntimeError("could not find Cascadeur's samples directory; tried " + repr(candidates))
"""

_OPEN_SAMPLE_CODE = Template(
    """
import json
import sys

reg = sys.modules[$registry]
if reg.token != $token:
    raise RuntimeError("live-test session token changed; MCP Plus was restarted during the test run")
sm = reg.sm
dsm = reg.dsm

tab = sm.create_application_scene()
sm.set_current_scene(tab)
ok = False
error = None
try:
    ok = dsm.load_scene($sample)
except Exception as exc:
    error = repr(exc)
if not ok:
    raise RuntimeError("load_scene returned " + repr(ok) + " for " + $sample + ((": " + error) if error else ""))

scene = tab.domain_scene()
print("__RESULT__" + json.dumps({
    "name": tab.name(),
    "path": tab.get_path_name(),
    "frame": scene.get_current_frame(),
    "objects": len(scene.model_viewer().get_objects()),
    "scene_count": len(sm.scenes()),
}))
"""
)

_CLOSE_FIRST_CODE = Template(
    """
import json
import sys

reg = sys.modules[$registry]
if reg.token != $token:
    raise RuntimeError("live-test session token changed; MCP Plus was restarted during the test run")
sm = reg.sm
dsm = reg.dsm
scenes = sm.scenes()
remaining = {(item[0], item[1]): 0 for item in reg.user_scenes}
for item in reg.user_scenes:
    remaining[(item[0], item[1])] += 1
extra = []
for scene in scenes:
    key = (scene.name(), scene.get_path_name())
    if remaining.get(key, 0) > 0:
        remaining[key] -= 1
    else:
        extra.append(scene)

if not extra:
    result = {"closed": None}
else:
    tab = extra[0]
    result = {"closed": [tab.name(), tab.get_path_name()]}
    dsm.close_scene(tab)

# Nothing may touch the scene manager after close_scene(): the tab is being destroyed
# and a second call in the same script can crash Cascadeur.
print("__RESULT__" + json.dumps(result))
"""
)

_STATUS_CODE = Template(
    """
import json
import sys

reg = sys.modules[$registry]
if reg.token != $token:
    raise RuntimeError("live-test session token changed; MCP Plus was restarted during the test run")
sm = reg.sm
scenes = sm.scenes()
current = sm.current_scene()
remaining = {}
for item in reg.user_scenes:
    key = (item[0], item[1])
    remaining[key] = remaining.get(key, 0) + 1
extra = []
for scene in scenes:
    key = (scene.name(), scene.get_path_name())
    if remaining.get(key, 0) > 0:
        remaining[key] -= 1
    else:
        extra.append([key[0], key[1]])
print("__RESULT__" + json.dumps({
    "scenes": [[s.name(), s.get_path_name()] for s in scenes],
    "current": [current.name(), current.get_path_name()] if current is not None else None,
    "frame": current.domain_scene().get_current_frame() if current is not None else None,
    "extra": extra,
    "extra_count": len(extra),
    "user_scenes": list(reg.user_scenes),
    "user_current": list(reg.user_current),
    "user_frame": reg.user_frame,
}))
"""
)

_RESTORE_CODE = Template(
    """
import json
import sys

reg = sys.modules[$registry]
if reg.token != $token:
    raise RuntimeError("live-test session token changed; MCP Plus was restarted during the test run")
sm = reg.sm
scenes = sm.scenes()
remaining = {}
for item in reg.user_scenes:
    key = (item[0], item[1])
    remaining[key] = remaining.get(key, 0) + 1
for scene in scenes:
    key = (scene.name(), scene.get_path_name())
    if remaining.get(key, 0) <= 0:
        raise RuntimeError("cannot restore the user's scene while test scenes are still open")
    remaining[key] -= 1
target = None
for scene in scenes:
    if [scene.name(), scene.get_path_name()] == list(reg.user_current):
        target = scene
        break
if target is None:
    raise RuntimeError("the user's original scene is gone; close the remaining tabs manually")
sm.set_current_scene(target)
current = sm.current_scene()
print("__RESULT__" + json.dumps({
    "scenes": [[s.name(), s.get_path_name()] for s in sm.scenes()],
    "current": [current.name(), current.get_path_name()],
    "frame": current.domain_scene().get_current_frame(),
}))
"""
)


# ---------------------------------------------------------------------------
# Helpers shared by the tests
# ---------------------------------------------------------------------------


def _run(code: str, timeout: float = 60.0) -> dict:
    result = client.call("run_python", code=code, timeout=timeout, http_timeout=timeout + 30.0)
    if not isinstance(result, dict) or not result.get("ok"):
        detail = result.get("error") if isinstance(result, dict) else result
        raise AssertionError(f"run_python failed: {detail}")
    return result


def _run_json(code: str, timeout: float = 60.0):
    output = _run(code, timeout)
    for line in reversed(str(output.get("stdout", "")).splitlines()):
        if line.startswith("__RESULT__"):
            return json.loads(line[len("__RESULT__") :])
    raise AssertionError(f"run_python produced no __RESULT__ line: {output!r}")


def samples_dir() -> str:
    """Cascadeur's samples directory, derived inside Cascadeur from csc/sys.executable."""
    global _SAMPLES_DIR
    if _SAMPLES_DIR is None:
        _SAMPLES_DIR = _run_json(_SAMPLES_CODE)["samples"]
    return _SAMPLES_DIR


class LiveTestCase(unittest.TestCase):
    """Base class: skips when MCP Plus is down, records and restores the user's scenes.

    Subclasses open sample scenes with open_sample(); tearDown closes every tab the
    tests opened without saving and then asserts that the user's scene list, current
    scene, and current frame are unchanged.
    """

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        if not client.available():
            raise unittest.SkipTest(client.NOT_RUNNING)
        cls.baseline = _run_json(_INIT_CODE.substitute(registry=repr(REGISTRY), token=repr(_TOKEN)))

    def tearDown(self) -> None:
        try:
            self._close_test_scenes()
        finally:
            _run_json(
                _RESTORE_CODE.substitute(registry=repr(REGISTRY), token=repr(_TOKEN)),
                timeout=30.0,
            )
        self._assert_user_state()

    def open_sample(self, name: str = "Cascy.casc") -> dict:
        """Open samples/<name> in a new tab and return name/path/frame/objects."""
        if os.path.basename(name) != name:
            raise ValueError(f"open_sample takes a file name, not a path: {name!r}")
        sample = os.path.join(samples_dir(), name)
        info = _run_json(
            _OPEN_SAMPLE_CODE.substitute(registry=repr(REGISTRY), token=repr(_TOKEN), sample=repr(sample)),
            timeout=120.0,
        )
        # Let Cascadeur finish its deferred scene setup (AutoPosing, trajectory interval)
        # before a test starts changing or closing the tab.
        time.sleep(1.0)
        return info

    def out_dir(self) -> str:
        """%TEMP%\\cascadeur-mcp-plus-tests\\<test class>\\, created if needed."""
        path = os.path.join(tempfile.gettempdir(), "cascadeur-mcp-plus-tests", type(self).__name__)
        os.makedirs(path, exist_ok=True)
        return path

    def dismiss_dialogs(
        self,
        preferred: tuple[str, ...] = ("don't save", "no", "cancel", "close", "ok"),
        timeout: float = 1.0,
    ) -> list[str]:
        """Click the first safe button in any open dialog; returns the labels clicked.

        Button labels are matched case-insensitively. Refuse to press Save/Save as.../Yes
        by default, so a test can clear a prompt without writing to disk or changing the
        user's files.
        """
        deadline = time.monotonic() + timeout
        while True:
            dialogs = client.call("dialogs").get("dialogs", [])
            if not dialogs:
                return []
            for dialog in dialogs:
                for button in dialog.get("buttons", []):
                    label = (button.get("text") or "").strip()
                    if label.lower() in preferred:
                        client.call("ui_click", ref=button["ref"])
                        return [label]
            if time.monotonic() >= deadline:
                return []
            time.sleep(0.2)

    # -- internals ----------------------------------------------------------

    def _status(self) -> dict:
        return _run_json(_STATUS_CODE.substitute(registry=repr(REGISTRY), token=repr(_TOKEN)))

    def _close_test_scenes(self) -> None:
        time.sleep(0.3)  # let the last UI call settle before touching the tab
        deadline = time.monotonic() + 60.0
        while True:
            status = self._status()
            if status["extra_count"] == 0:
                return
            before = status["extra_count"]
            _run_json(_CLOSE_FIRST_CODE.substitute(registry=repr(REGISTRY), token=repr(_TOKEN)))
            time.sleep(0.5)
            # A modified test scene shows "Do you want to save changes...?"; discard it.
            self.dismiss_dialogs(preferred=("don't save", "dont save", "discard"), timeout=3.0)
            time.sleep(0.3)
            if not self._wait_for(lambda count=before: self._status()["extra_count"] < count, timeout=10.0):
                raise AssertionError(f"test scene did not close; still open: {self._status()['extra']!r}")
            if time.monotonic() > deadline:
                raise AssertionError("timed out closing test scenes")

    def _assert_user_state(self) -> None:
        status = self._status()
        self.assertEqual(sorted(status["scenes"]), sorted(self.baseline["scenes"]), "the user's open scenes changed")
        self.assertEqual(status["current"], self.baseline["current"], "the user's current scene changed")
        self.assertEqual(status["frame"], self.baseline["frame"], "the user's current frame changed")

    @staticmethod
    def _wait_for(predicate, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(0.1)
        return bool(predicate())
