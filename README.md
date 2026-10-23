**English** | [繁體中文](README.zh-TW.md)

# Cascadeur MCP Plus

[![CI](https://github.com/Karylcode/cascadeur-mcp-plus/actions/workflows/ci.yml/badge.svg)](https://github.com/Karylcode/cascadeur-mcp-plus/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![Cascadeur 2026.2](https://img.shields.io/badge/Cascadeur-2026.2-orange.svg)](https://cascadeur.com/)

An MCP server that lets AI agents (Claude Code, Codex, Cursor and other stdio MCP clients) drive
[Cascadeur](https://cascadeur.com/): run Python, inspect the scene, take screenshots, and operate the UI
the way a person does — click, drag, type, press shortcuts, answer dialogs. Think *BlenderMCP, for
Cascadeur*. Cascadeur 2026.2 ships a minimal script server with a single `run_script` tool. MCP Plus
builds on it and adds 17 more tools, most importantly full control of Cascadeur's Qt Quick interface.
It has two parts: a stdio proxy that uses only the Python standard library, and a server that runs
inside Cascadeur on its bundled Python and PySide6.

> [!IMPORTANT]
> - **Unofficial.** Not affiliated with or endorsed by Nekki. Cascadeur is a trademark of Nekki Limited.
> - This project contains no Cascadeur code; it talks to Cascadeur only through its Python API and Qt.

## What it does

- **Python**: run code inside Cascadeur with `csc`, `app`, `scene`, `view_scene` and `ui` in scope (`run_python`, which covers the official `run_script`).
- **Scene info**: frames, objects, selection, rig behaviours and license flags (`get_scene_info`, `get_object_info`).
- **UI inspection**: screenshots of a window or one element, the live Qt Quick element tree, and open dialogs with their buttons (`screenshot`, `ui_tree`, `dialogs`).
- **UI input**: click, hover, drag, press keys and shortcuts, type text, set QML properties and invoke QML methods or signals (`ui_click`, `ui_hover`, `ui_drag`, `ui_key`, `ui_type`, `ui_set_property`, `ui_invoke`).
- **Actions and log**: trigger Cascadeur actions by `actionId` (`list_actions`, `call_action`) and read the log for errors and Python tracebacks (`get_log`).
- **Rig mode**: `enter_rig_mode`, plus a guard that refuses Quick Rigging's *Add rig elements* / *Generate rig* outside Rig mode, where Cascadeur crashes.
- **AutoPosing**: `project_point` finds scene objects in the window, and `ui_drag` drags them with AutoPosing updating between steps.

## Requirements

- Windows 10/11
- Cascadeur 2026.2 with its built-in MCP script server (tested with 2026.2.3)
- Python 3.11+ for the proxy (standard library only — nothing to `pip install`)
- An MCP client that runs stdio servers

Details: [Installation](docs/installation.md).

## Quick start

1. Get the code:

   ```bash
   git clone https://github.com/Karylcode/cascadeur-mcp-plus.git
   ```

2. Register the proxy with your MCP client. Claude Code:

   ```bash
   claude mcp add --scope user cascadeur-plus -- python -I C:\path\to\cascadeur-mcp-plus\proxy.py
   ```

   Other clients (JSON config):

   ```json
   {
     "mcpServers": {
       "cascadeur-plus": {
         "command": "python",
         "args": ["-I", "C:\\path\\to\\cascadeur-mcp-plus\\proxy.py"]
       }
     }
   }
   ```

   Use the full path to `python.exe` if `python` is not on `PATH`.

3. Start Cascadeur and click **Scripts → MCP → Start script server** (each launch).
4. Ask your agent to work in Cascadeur. The first tool call starts MCP Plus inside Cascadeur
   automatically (about 10 seconds). After that there is no startup delay.

Next: [Usage](docs/usage.md).

## Troubleshooting

- **`WinError 10013` on *Start script server***: another program owns port 8765; `netstat -ano | findstr :8765` shows which.
- **Minimized window**: nothing is rendered; call `screenshot` with `restore=true`.
- **AutoPosing dialog while dragging a scripted pose**: `ui_drag` reports it under `dialogs`; answer *Yes* to keep the pose and drag again.

More: [Troubleshooting](docs/troubleshooting.md).

## Documentation

- [Documentation index](docs/README.md): all pages, with what each one covers
- [Installation](docs/installation.md): requirements, client setup, configuration
- [Usage](docs/usage.md): workflow, common tasks, prompt examples
- [Tools](docs/tools.md): all 18 tools with parameters and defaults
- [How it works](docs/how-it-works.md): proxy, in-app server, GUI-thread dispatch
- [Safety](docs/safety.md): both servers run any Python they receive; read this
- [Troubleshooting](docs/troubleshooting.md): known limitations and error messages
- [Development](docs/development.md): tests, linting, hot reload, source layout
- [Changelog](CHANGELOG.md): release notes (current version 0.4.0)

## License

MIT. See [LICENSE](LICENSE).
