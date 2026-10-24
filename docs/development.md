**English** | [繁體中文](development.zh-TW.md) · [Documentation index](README.md)

# Development

## Unit tests

```bat
python -m unittest discover -s tests -v
```

Use Python 3.11 or newer. The unit tests do not start Cascadeur and do not connect to a running MCP Plus. Tests that need Cascadeur's own Python live under `tests/live/`.

## Live tests

Live tests run against a Cascadeur that is open with MCP Plus running:

```bat
python tests\live\run.py [pattern]
```

`pattern` is a unittest test-file pattern, `test_*.py` by default. `run.py` is the only supported entry point: it sets `CASCADEUR_LIVE=1` before discovery. Plain unittest discovery leaves that variable unset, skips every live test and sends no requests to Cascadeur. Live tests open and close scene tabs, so do not run them while someone else is using the same Cascadeur.

## Lint and format

Ruff is configured in `pyproject.toml`. Install the version that CI uses, then run both checks:

```bat
python -m pip install ruff==0.17.2
ruff check .
ruff format --check .
```

## CI

`.github/workflows/ci.yml` runs on pushes to `main`, on pull requests and by hand. It has two jobs:

- **Ruff**: the lint check and the format check.
- **Tests**: the unit tests on Windows, with Python 3.11 and 3.13.

## Manual start and hot reload

`start_mcp_plus.py` reloads the package in dependency order and restarts the server, so edits take effect on the next start without restarting Cascadeur. Run it from Cascadeur's Python (the script server or the Python Console):

```python
p = r"C:\path\to\cascadeur-mcp-plus\start_mcp_plus.py"
exec(open(p, encoding="utf-8").read(), {"__file__": p})
```

`MCP_PLUS_PORT` is a global in the dictionary passed to `exec`, not an environment variable. Add it to listen on another port:

```python
exec(open(p, encoding="utf-8").read(), {"__file__": p, "MCP_PLUS_PORT": 48767})
```

Point the proxy at the same port with `CASCADEUR_PLUS_URL` (see [Installation](installation.md#configuration)). When the proxy starts MCP Plus itself, it passes the port from that URL.

## Version and default port

`cascadeur_mcp_plus/version.py` holds `VERSION` and `DEFAULT_PORT` (48766) for the in-app server. The proxy reads `VERSION` from that file without importing the package. Its own default port, `DEFAULT_PORT` in `cascadeur_mcp_plus_cli/proxy.py`, repeats the value, so keep the two in sync. Release notes are in [CHANGELOG.md](../CHANGELOG.md).

## Tool manifest

The server writes `tools_manifest.json` on every start, and the proxy rewrites it after a live `tools/list`. The proxy serves this file while Cascadeur is closed.

## Adding and checking tools

Each tool is a dictionary in a module of `cascadeur_mcp_plus/tools/`. `load_tools()` in `tools/__init__.py` collects the modules in file-name order and rejects duplicate names. Every tool declares `requires`: the Cascadeur API paths (rooted at `csc`), `action:<id>` keys and `module:<script>` entries it needs. `get_status` reports the tools whose entries the running Cascadeur lacks.

`tools/check_readme_tools.py` checks the tool sections of `docs/tools.md` and `docs/tools.zh-TW.md`. Each registry tool must appear exactly once in each file, and both files must list the tools in the same order. Without `--offline` it also compares the list with the live `tools/list`, which needs Cascadeur with MCP Plus running:

```bat
python tools\check_readme_tools.py --offline
```

## Source layout

| Path | Role |
|---|---|
| `proxy.py` | Launcher for the stdio proxy (`python -I proxy.py`) |
| `start_mcp_plus.py` | Runs inside Cascadeur: reloads the package and starts or restarts the server |
| `tools_manifest.json` | Cached tool list for the proxy |
| `cascadeur_mcp_plus_cli/main.py` | Command-line tool: `setup`, `uninstall`, `doctor`, `autostart`, `serve` |
| `cascadeur_mcp_plus_cli/proxy.py` | Stdio proxy: `/health` checks, automatic start, forwarding, offline tool list |
| `cascadeur_mcp_plus_cli/clients.py` | MCP client config files: detection, backups and registration |
| `cascadeur_mcp_plus_cli/autostart.py`, `doctor.py` | Autostart handler, and the health report |
| `cascadeur_mcp_plus/__init__.py` | Exports `start`, `stop`, `status`, `VERSION` and `DEFAULT_PORT` |
| `cascadeur_mcp_plus/version.py` | Version and default port |
| `cascadeur_mcp_plus/server.py` | HTTP server (`/mcp`, `/health`), tool dispatch and the GUI-thread dispatcher |
| `cascadeur_mcp_plus/tools/` | Tool modules; each defines a `TOOLS` list |
| `cascadeur_mcp_plus/tools/__init__.py` | `load_tools()`, which collects and checks the registry |
| `cascadeur_mcp_plus/compat.py` | Startup check of each tool's `requires` |
| `cascadeur_mcp_plus/ui.py` | QML tree and refs, QtTest input, `PrintWindow` screenshots, dialogs, actions, viewport projection and drags |
| `cascadeur_mcp_plus/resources.py` | The four MCP resources |
| `cascadeur_mcp_plus/paths.py` | Cascadeur's data, log and installation folders |
| `tests/` | Unit tests; `tests/live/` holds the live tests and `run.py` |
| `tools/check_readme_tools.py` | Checks the tool sections of the documentation against the registry |
| `examples/` | `wave.md` and `wave.py`, a complete example |
| `pyproject.toml` | Project metadata and Ruff configuration (nothing to install) |
| `.github/workflows/ci.yml` | CI |
| `CHANGELOG.md` | Release notes |
