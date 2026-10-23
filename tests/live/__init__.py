"""Live tests: run against a real Cascadeur through MCP Plus.

Live tests are opt-in: they run only when CASCADEUR_LIVE=1, which
tests/live/run.py sets after the caller takes the live-test lock. Plain unittest
discovery (what every agent runs before merging) skips them and sends no HTTP
requests to Cascadeur, so concurrent agents cannot open or close scene tabs.

See run.py for the command line and fixture.py for the test base class.
"""
