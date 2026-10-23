# Cascadeur MCP Plus

[![CI](https://github.com/Karylcode/cascadeur-mcp-plus/actions/workflows/ci.yml/badge.svg)](https://github.com/Karylcode/cascadeur-mcp-plus/actions/workflows/ci.yml)

[繁體中文](README.zh-TW.md)

An MCP server that lets AI agents (Claude Code, Codex, Cursor, …) drive [Cascadeur](https://cascadeur.com/):
run Python, inspect the scene, take screenshots, and operate the UI the way a person does — click, drag,
type, press shortcuts, answer dialogs. Think *BlenderMCP, for Cascadeur*.

Cascadeur 2026.2 ships a minimal script server with a single `run_script` tool. MCP Plus builds on it and
adds 17 more tools, most importantly full control of Cascadeur's Qt Quick interface.

> **Unofficial.** Not affiliated with or endorsed by Nekki. Cascadeur is a trademark of Nekki Limited.
> This project contains no Cascadeur code; it talks to Cascadeur only through its Python API and Qt.

## Tools

| Tool | What it does |
|---|---|
| `run_python` | Run Python in Cascadeur with `csc`, `app`, `scene`, `view_scene`, `ui` in scope; returns stdout and a value |
| `get_scene_info`, `get_object_info` | Scene summary (frames, objects, selection, rig behaviours, license flags), object behaviours |
| `screenshot` | Capture a window or a single UI element; can restore a minimized window first |
| `ui_tree` | On-screen elements (text, tooltip, actionId, type, rect, flags) with refs for the tools below |
| `ui_click`, `ui_hover` | Click / hover by ref, text query or window coordinates — real synthesized mouse input |
| `ui_drag` | Press, move in steps with the button held, release; start from a ref, query, x/y or a **scene object** |
| `ui_key`, `ui_type` | Shortcuts such as `ctrl+s` and text input (any Unicode) |
| `ui_set_property`, `ui_invoke` | Set a QML property or invoke a QML method/signal |
| `dialogs` | Visible dialogs and popups with their buttons |
| `list_actions`, `call_action` | Cascadeur action keys (e.g. `Commands.Rig mode.On`) and `ActionManager.call_action` |
| `project_point` | Window coordinates of scene objects in the 3D viewport |
| `enter_rig_mode` | Enter Rig mode the way a UI import does (required before Quick Rigging) |
| `get_log` | Tail of Cascadeur's log, where errors and Python tracebacks land |

## Requirements

- Windows 10/11 (screenshots and window handling use Win32)
- Cascadeur 2026.2 with its built-in MCP script server (tested with 2026.2.3)
- Python 3.11+ for the proxy (standard library only — nothing to `pip install`)
- An MCP client

## Install

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

## Use

1. Start Cascadeur and click **Scripts → MCP → Start script server** (each launch).
2. Ask your agent to work in Cascadeur. The first tool call starts MCP Plus inside Cascadeur
   automatically (about 10 seconds); later calls take well under a second.

You do not need to register Cascadeur's own `cascadeur` MCP entry; MCP Plus covers `run_script`
with `run_python`.

## How it works

```text
MCP client ──stdio──► proxy.py ──HTTP──► MCP Plus inside Cascadeur (127.0.0.1:48766)
                         │                       │ runs every call on Cascadeur's GUI thread
                         └─ if it is down ──► Cascadeur script server (127.0.0.1:8765) /run → start_mcp_plus.py
```

- Cascadeur's bundled PySide6 shares the application's Qt runtime, so the live QML item tree, its
  windows and Qt's input system are reachable from Python.
- Clicks, drags and keys are synthesized through Qt's window-system input path (QtTest), so they follow
  the same code path as a real user. Each drag step is its own GUI-thread job, so AutoPosing and other
  live updates run between moves.
- Screenshots use the Win32 `PrintWindow` API. Forcing an extra Qt Quick render pass from Python
  (`QQuickWindow.grabWindow`) crashed Cascadeur, whose viewport draws with its own OpenGL code.
- The proxy always connects (it serves a cached tool list while Cascadeur is closed) and checks a
  server's identity through `/health` before sending it anything.

## Safety

- Both servers listen on `127.0.0.1` only, but they run any Python they receive, like Cascadeur's own
  script server. Stop the script server when you are done.
- The proxy never forwards a call to a port held by another program: if `48766` or `8765` answers
  but is not the expected Cascadeur server, the call fails with an explanation instead.
- `ui_click` / `ui_invoke` refuse Quick Rigging's **Add rig elements / Generate rig** unless the scene is
  in Rig mode (exactly one `AnimationInfo`). In that state Cascadeur raises
  `Rig mode several animationInfo` and then crashes. Use `enter_rig_mode` first, or `force=true`.

## Tips and known limitations

- **Port 8765 taken** → *Start script server* fails with `WinError 10013`. Another local program owns the
  port; move it elsewhere (`netstat -ano | findstr :8765` shows which).
- **Minimized window** → nothing is rendered; call `screenshot` with `restore=true`.
- **Dragging a scripted pose** → Cascadeur may show "Pose in the current frame is different from the
  AutoPosing prediction…". `ui_drag` reports it under `dialogs`; answer *Yes* to keep the pose and drag again.
- **Viewport projection** assumes a perspective camera with a 30° vertical field of view (measured on
  2026.2.3). Pass `fov_deg` to `project_point` / `ui_drag` if your setup differs.
- Calls run on Cascadeur's GUI thread, so long operations freeze its UI until they finish.
- QML layouts can change between Cascadeur versions; prefer `actionId` or visible text over coordinates.

## Configuration

| Setting | Default | Where |
|---|---|---|
| `CASCADEUR_PLUS_URL` | `http://127.0.0.1:48766` | environment of the proxy; its port is passed to the in-app server when the proxy starts it |
| `CASCADEUR_OFFICIAL_URL` | `http://127.0.0.1:8765` | environment of the proxy |

Manual start or restart with fresh code, from Cascadeur's Python (script server or Python Console):

```python
p = r"C:\path\to\cascadeur-mcp-plus\start_mcp_plus.py"
exec(open(p, encoding="utf-8").read(), {"__file__": p})
```

## Development

```bash
python -m unittest discover -s tests -v   # proxy tests with fake servers, no Cascadeur needed
ruff check . && ruff format --check .
```

The in-app package (`cascadeur_mcp_plus/`) needs Cascadeur's Python and is tested live; `start_mcp_plus.py`
reloads it, so edits take effect on the next start without restarting Cascadeur.

## License

[MIT](LICENSE)
