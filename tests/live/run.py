"""Run the live tests against a running Cascadeur.

    python tests/live/run.py [pattern]

pattern is unittest's test-file pattern (default test_*.py).

This is the only supported entry point for live tests: it sets CASCADEUR_LIVE=1 before
unittest discovery, so LiveTestCase actually runs. Plain `python -m unittest discover -s
tests` (the command every agent runs before merging) leaves CASCADEUR_LIVE unset, skips
every live test, and makes no HTTP requests to Cascadeur. That keeps concurrent agents
from opening or closing scene tabs without the live-test lock.

The lock itself is a user workflow, not handled here:

    python docs/tickets/bin/live.py lock <ticket>
    python docs/tickets/bin/live.py restart <your worktree>
    python tests/live/run.py
    python docs/tickets/bin/live.py restart <main checkout>
    python docs/tickets/bin/live.py unlock <ticket>

Take the lock and restart as separate commands and check the lock command's exit code;
never chain restart after a failed lock.
"""

from __future__ import annotations

import os
import sys
import time
import unittest

try:  # imported as live.run by unittest discovery
    from . import client
    from .fixture import LIVE_ENV
except ImportError:  # executed as a script
    import client  # type: ignore[no-redef]
    from fixture import LIVE_ENV  # type: ignore[no-redef]


HERE = os.path.dirname(os.path.abspath(__file__))
TOP = os.path.dirname(HERE)


def main(argv: list[str]) -> int:
    pattern = argv[0] if argv else "test_*.py"
    # The gate: only this runner opts in. Must be set before discovery imports the tests.
    os.environ[LIVE_ENV] = "1"
    info = client.health()
    if info is None:
        print(client.NOT_RUNNING)
        return 2
    print(f"MCP Plus {info.get('version', '?')} at {client.PLUS_URL}")

    loader = unittest.TestLoader()
    suite = loader.discover(HERE, pattern=pattern, top_level_dir=TOP)
    count = suite.countTestCases()
    if count == 0:
        print(f"No live tests matched {pattern!r} under {HERE}.")
        return 2
    print(f"Running {count} live test(s)...\n")

    started = time.monotonic()
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    elapsed = time.monotonic() - started
    skipped = len(result.skipped)
    print(
        f"\nSummary: {result.testsRun} run, {len(result.failures)} failures, "
        f"{len(result.errors)} errors, {skipped} skipped in {elapsed:.1f}s"
    )
    if skipped:
        print("A skipped live test is not a pass; check the live-test prerequisites above.")
    return 0 if result.wasSuccessful() and not skipped else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
