"""The live-test gate: plain unittest discovery must not touch Cascadeur.

This test needs no Cascadeur. It proves LiveTestCase skips before any HTTP call when
CASCADEUR_LIVE is unset (the plain discovery every agent runs), and that only the
explicit value 1 reaches the server check that tests/live/run.py uses.

Run: python -m unittest discover -s tests -v
"""

from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tests.live import client, fixture  # noqa: E402


class LiveGatingTests(unittest.TestCase):
    def setUp(self) -> None:
        self._saved_env = os.environ.pop(fixture.LIVE_ENV, None)
        self._saved_health = client.health
        self._saved_call = client.call
        self.health_calls = []
        self.call_calls = []
        self._fail_http()

    def tearDown(self) -> None:
        client.health = self._saved_health
        client.call = self._saved_call
        os.environ.pop(fixture.LIVE_ENV, None)
        if self._saved_env is not None:
            os.environ[fixture.LIVE_ENV] = self._saved_env

    def _fail_http(self) -> None:
        def health(*args, **kwargs):
            self.health_calls.append((args, kwargs))
            raise AssertionError("client.health must not be called without CASCADEUR_LIVE=1")

        def call(*args, **kwargs):
            self.call_calls.append((args, kwargs))
            raise AssertionError("client.call must not be called without CASCADEUR_LIVE=1")

        client.health = health
        client.call = call

    def _run_probe(self, class_name: str) -> unittest.TestResult:
        class Probe(fixture.LiveTestCase):
            def test_live_body(self) -> None:
                raise AssertionError(f"{class_name} ran without the live-test gate")

        Probe.__name__ = class_name
        suite = unittest.TestLoader().loadTestsFromTestCase(Probe)
        result = unittest.TestResult()
        suite.run(result)
        return result

    def test_plain_unittest_skips_without_any_http(self) -> None:
        result = self._run_probe("ProbeWithoutLiveEnv")
        self.assertEqual(result.testsRun, 0)
        self.assertEqual(result.failures, [])
        self.assertEqual(result.errors, [])
        self.assertEqual(len(result.skipped), 1)
        reason = result.skipped[0][1]
        self.assertIn("tests/live/run.py", reason)
        self.assertIn(fixture.LIVE_ENV, reason)
        self.assertEqual(self.health_calls, [])
        self.assertEqual(self.call_calls, [])

    def test_gate_accepts_only_one(self) -> None:
        for value in ("", "0", "true", "yes"):
            os.environ[fixture.LIVE_ENV] = value
            result = self._run_probe("ProbeWith_" + (value or "empty"))
            self.assertEqual(result.testsRun, 0, value)
            self.assertEqual(result.failures, [], value)
            self.assertEqual(result.errors, [], value)
            self.assertEqual(len(result.skipped), 1, value)
            self.assertEqual(self.health_calls, [], value)
        self.assertEqual(self.call_calls, [])

    def test_enabled_live_reaches_the_server_check(self) -> None:
        os.environ[fixture.LIVE_ENV] = "1"
        calls = []

        def down(*args, **kwargs):
            calls.append((args, kwargs))
            return None

        client.health = down
        result = self._run_probe("ProbeWithLiveEnvButServerDown")
        self.assertEqual(result.testsRun, 0)
        self.assertEqual(result.failures, [])
        self.assertEqual(result.errors, [])
        self.assertEqual(len(result.skipped), 1)
        self.assertEqual(result.skipped[0][1], client.NOT_RUNNING)
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.call_calls, [])


if __name__ == "__main__":
    unittest.main()
