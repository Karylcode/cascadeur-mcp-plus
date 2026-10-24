**English** | [繁體中文](README.zh-TW.md)

# Cascadeur MCP Plus

[![CI](https://github.com/Karylcode/cascadeur-mcp-plus/actions/workflows/ci.yml/badge.svg)](https://github.com/Karylcode/cascadeur-mcp-plus/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![Cascadeur 2026.2](https://img.shields.io/badge/Cascadeur-2026.2-orange.svg)](https://cascadeur.com/)

An MCP server that lets AI agents (Claude Code, Codex, Cursor and other stdio MCP clients) animate in
[Cascadeur](https://cascadeur.com/) with Cascadeur's own features: open and save scenes, set poses with numbers
and let AutoPosing move the body, key and interpolate the timeline, run AutoPhysics and cleanup, and render
videos and contact sheets with Cascadeur's own renderer. When no dedicated tool fits, it can still operate the UI
the way a person does — click, drag, type, press shortcuts, answer dialogs. Think *BlenderMCP, for Cascadeur*.
Cascadeur 2026.2 ships a minimal script server with a single `run_script` tool. MCP Plus builds on it with 105
tools: `run_python` covers `run_script`, and the other 104 are new. It has two parts: a stdio proxy that uses only
the Python standard library, and a server that runs inside Cascadeur on its bundled Python and PySide6.

> [!IMPORTANT]
> - **Unofficial.** Not affiliated with or endorsed by Nekki. Cascadeur is a trademark of Nekki Limited.
> - This project contains no Cascadeur code; it talks to Cascadeur only through its Python API and Qt.

## What it does

- **Scenes and files**: new, open, save, switch and close scenes; import models and FBX animations; export FBX or GLB (`open_scene`, `save_scene`, `import_model`, `import_animation`, `export_scene`).
- **Characters and rigs**: list characters and controllers, select objects, Quick Rig a bare skeleton, retarget an animation between rigs (`list_characters`, `select_objects`, `quick_rig`, `retarget_animation`).
- **Poses and AutoPosing**: read and write control points in centimetres and degrees and let AutoPosing pose the rest of the body; mirror, copy and paste poses (`get_pose`, `set_pose`, `mirror_pose`, `autoposing_enable`).
- **Timeline and keys**: animation range, keys, cycles, interpolation, IK/FK, tangents and easing (`set_animation_range`, `set_key`, `create_cycle`, `set_interpolation`, `set_ik_fk`).
- **Animation layers**: create, rename, hide, lock and move objects between layers (`create_layer`, `set_layer_locked`, `move_to_layer`).
- **Physics and AI**: AutoPhysics, foot-sliding and collision fixes, AI inbetweening, motion generation and experimental video mocap (`physics_enable`, `fix_foot_sliding`, `ai_inbetween`, `generate_motion`, `video_mocap`).
- **Review and output**: images, contact sheets, image sequences and MP4 videos from Cascadeur's own renderer and Export video window (`render_contact_sheet`, `render_video`).
- **Camera and display**: camera presets, framing, tracking, view modes, ghosts and trajectories (`camera_preset`, `frame_objects`, `track_object`, `set_view_mode`).
- **UI control, as a last resort**: screenshots, the live Qt Quick element tree, dialogs, and click, drag, type and keys like a person (`screenshot`, `ui_tree`, `ui_click`, `ui_drag`, `dialogs`).
- **Python, jobs and undo**: run Python with `csc`, `app` and `scene` in scope, run several calls in one `batch`, follow background jobs, undo and redo, trigger actions and read the log (`run_python`, `get_job`, `undo`, `call_action`, `get_log`).
- **Version check**: `get_status` lists the tools this Cascadeur build cannot use; calling one returns an explicit error instead of failing halfway.

## Requirements

- Windows 10/11
- Cascadeur 2026.2 with its built-in MCP script server (tested with 2026.2.3)
- Python 3.11+ for the proxy and the CLI (standard library only — nothing to `pip install`)
- An MCP client that runs stdio servers

Details: [Installation](docs/installation.md).

## Quick start

1. Get the code:

   ```bash
   git clone https://github.com/Karylcode/cascadeur-mcp-plus.git
   ```

2. Register the proxy with your MCP client. From the checkout, one command registers it with every client it
   detects (Claude Code, Codex CLI, Cursor, VS Code) and backs up each config file first; `--dry-run` shows the
   changes and writes nothing:

   ```bash
   cd C:\path\to\cascadeur-mcp-plus
   python -m cascadeur_mcp_plus_cli setup
   ```

   By hand, Claude Code:

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

3. Start Cascadeur and click **Scripts → MCP → Start script server** (each launch). To skip this, run
   `python -m cascadeur_mcp_plus_cli autostart enable` once from the checkout (one UAC prompt); Cascadeur then
   starts both servers itself.
4. Ask your agent to work in Cascadeur. The first tool call starts MCP Plus inside Cascadeur
   automatically (about 10 seconds). After that there is no startup delay.

Next: [Usage](docs/usage.md).

## Troubleshooting

- **Something does not connect**: run `python -m cascadeur_mcp_plus_cli doctor` from the checkout. It checks the Cascadeur install, ports 8765 and 48766, both servers and the registered clients.
- **`WinError 10013` on *Start script server***: another program owns port 8765; `netstat -ano | findstr :8765` shows which.
- **Renders refuse or come out empty**: Cascadeur is minimized. Restore it, for example with `screenshot` and `restore=true`.
- **"Unavailable in this Cascadeur version"**: call `get_status`; it lists the missing API or action for each tool.

More: [Troubleshooting](docs/troubleshooting.md).

## Documentation

- [Documentation index](docs/README.md): all pages, with what each one covers
- [Installation](docs/installation.md): requirements, setup, client configuration, autostart, environment variables
- [Usage](docs/usage.md): workflow, common tasks, output files, a complete example, prompt examples
- [Tools](docs/tools.md): all 105 tools with parameters and defaults
- [How it works](docs/how-it-works.md): proxy, in-app server, GUI-thread dispatch, background jobs, rendering
- [Safety](docs/safety.md): both servers run any Python they receive; read this
- [Behavior and limitations](docs/limitations.md): what the tools cannot do, and behaviour to expect
- [Troubleshooting](docs/troubleshooting.md): common problems and error messages
- [Development](docs/development.md): tests, linting, live tests, hot reload, source layout
- [Changelog](CHANGELOG.md): release notes (current version 0.5.0)

## License

MIT. See [LICENSE](LICENSE).
