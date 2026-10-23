# Development

**English** | [繁體中文](development.zh-TW.md) · [Documentation index](README.md)

## Tests

```bash
python -m unittest discover -s tests -v
```

The tests cover only `proxy.py`, using fake local servers; no Cascadeur is needed. The in-app package
(`cascadeur_mcp_plus/`) needs Cascadeur's Python and is tested live.

## Lint and format

Ruff is configured in `pyproject.toml`. Install the version CI uses, then run each check:

```bash
pip install ruff==0.17.2
ruff check .
ruff format --check .
```

## CI

`.github/workflows/ci.yml` runs on pushes to `main`, on pull requests, and by hand:

- **Ruff**: lint and format check on Ubuntu with Python 3.13.
- **Tests**: the unit tests on Windows with Python 3.11 and 3.13.

## Manual start and hot reload

`start_mcp_plus.py` reloads the package in dependency order and restarts the server, so edits take effect
on the next start without restarting Cascadeur. Run it from Cascadeur's Python (script server or Python Console):

```python
p = r"C:\path\to\cascadeur-mcp-plus\start_mcp_plus.py"
exec(open(p, encoding="utf-8").read(), {"__file__": p})
```

`MCP_PLUS_PORT` is a global passed in that dict, not an environment variable. Add it to listen on another port:

```python
exec(open(p, encoding="utf-8").read(), {"__file__": p, "MCP_PLUS_PORT": 48767})
```

Point the proxy at the same port with `CASCADEUR_PLUS_URL` (see [Installation](installation.md)).
When the proxy starts MCP Plus itself, it passes `MCP_PLUS_PORT` from that URL.

## Version and default port

`cascadeur_mcp_plus/version.py` holds `VERSION` (currently 0.4.0) and `DEFAULT_PORT` (48766) for both the
server and the proxy. The proxy reads `VERSION` from the file without importing the package. Its default
`CASCADEUR_PLUS_URL` in `proxy.py` repeats the port, so keep the two in sync. Release notes are in
[CHANGELOG.md](../CHANGELOG.md).

## Tool manifest

The server writes `tools_manifest.json` on every start, and the proxy rewrites it after a live `tools/list`.
The proxy serves this file while Cascadeur is closed.

## Source layout

| Path | Role |
|---|---|
| `proxy.py` | stdio MCP proxy: `/health` checks, automatic start, forwarding, offline tool list |
| `start_mcp_plus.py` | Runs inside Cascadeur: reloads the package and starts or restarts the server |
| `tools_manifest.json` | Cached tool list for the proxy |
| `cascadeur_mcp_plus/__init__.py` | Exports `start`, `stop`, `status`, `VERSION`, `DEFAULT_PORT` |
| `cascadeur_mcp_plus/version.py` | Version and default port |
| `cascadeur_mcp_plus/server.py` | HTTP server (`/mcp`, `/health`), tool definitions, `QTimer` GUI-thread dispatcher |
| `cascadeur_mcp_plus/ui.py` | QML tree and refs, QtTest input, `PrintWindow` screenshots, dialogs, actions, viewport projection and drags |
| `cascadeur_mcp_plus/scene_tools.py` | `run_python`, scene and object info, `enter_rig_mode`, `get_log` |
| `tests/test_proxy.py` | Proxy tests with fake servers |
| `pyproject.toml` | Project metadata and Ruff configuration (nothing to install) |
| `.github/workflows/ci.yml` | CI: Ruff and tests |
| `CHANGELOG.md` | Release notes |
