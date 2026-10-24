**English** | [繁體中文](how-it-works.zh-TW.md) · [Documentation index](README.md)

# How it works

```text
MCP client ──stdio──► proxy.py ──HTTP──► MCP Plus inside Cascadeur (127.0.0.1:48766)
                         │                       │ runs calls on Cascadeur's GUI thread
                         └─ if it is down ──► Cascadeur script server (127.0.0.1:8765) /run → start_mcp_plus.py
```

## The proxy (`proxy.py`)

Your MCP client starts the proxy over stdio. It uses only the Python standard library and answers
`initialize` and `ping` itself, so it always connects, even while Cascadeur is closed.

- **`tools/list`**: if MCP Plus is up, the proxy forwards the request and saves the result to
  `tools_manifest.json`. Otherwise it serves that cached list.
- **`tools/call`**:
  1. Check through `/health` that MCP Plus answers on its port (see [Safety](safety.md)).
  2. If nothing answers, check the script server on 8765 the same way, then send it a short script
     (`POST /run`) that runs `start_mcp_plus.py` with the proxy's port. Wait up to 10 seconds for MCP Plus.
  3. Forward the call to MCP Plus (`POST /mcp`). If the connection is refused (for example, Cascadeur
     restarted), check or start MCP Plus again and retry once.
- Each request runs in its own thread, so a slow tool call never blocks pings or other calls.
- The proxy waits up to 120 seconds for a tool call, or `timeout` + 15 seconds for `run_python`.

## The in-app server (`cascadeur_mcp_plus/`)

- `start_mcp_plus.py` finds the package next to itself, reloads it in dependency order, and calls `start()`
  on Cascadeur's GUI thread. If a server is already running, it is stopped first.
- The server is an HTTP server on `127.0.0.1` (default port 48766) with `POST /mcp` and `GET /health`.
  On start it writes `tools_manifest.json`, so the proxy's offline tool list stays current.
- Cascadeur's bundled PySide6 shares the application's Qt runtime, so the live QML item tree, its windows
  and Qt's input system are reachable from Python.

## GUI-thread dispatch

Requests arrive on background HTTP threads. Tools that touch Cascadeur are put in a queue, and a `QTimer`
on the GUI thread runs them. The dispatcher is re-entrant: a job that opens a modal dialog does not block
the next job, which may be the one that closes it. Waits such as `settle_ms` happen on the HTTP thread,
so Cascadeur's event loop keeps running normally.

Exceptions:

- **`get_log`** only reads the log file, on the HTTP thread. It does not use the GUI thread.
- **`enter_rig_mode`** runs on Cascadeur's scene idle event, like the official script server.
- **`screenshot`** and **`ui_drag`** are split into several GUI-thread jobs, with the waits between them on
  the HTTP thread. `ui_drag` runs the press, each move and the release as separate jobs, so AutoPosing and
  other live updates run between moves.

A GUI-thread job times out after 60 seconds (`run_python`: its `timeout`; each step of `screenshot` and
`ui_drag`: 30 seconds). Because work runs on the GUI
thread, long operations freeze Cascadeur's UI until they finish.

## Input

Clicks, hovers, keys and drags are synthesized through Qt's window-system input path (QtTest), so they
follow the same code path as a real user. `ui_drag` can send `QMouseEvent`s directly instead
(`method="event"`). `ui_type` sends text-carrying key events to the window, and Qt Quick routes them to the
focused item; this works for any Unicode text.

## Screenshots

Screenshots use the Win32 `PrintWindow` API. Forcing an extra Qt Quick render pass from Python
(`QQuickWindow.grabWindow`) crashed Cascadeur, whose viewport draws with its own OpenGL code. A minimized
window renders nothing, so `screenshot` can restore it first (`restore=true`).

## Viewport projection

The Python API does not expose the viewport camera's field of view. `project_point` and `ui_drag` (with
`object`) use the main viewport camera's position and target and assume a perspective camera with a 30°
vertical field of view. That value was measured on Cascadeur 2026.2.3 by overlaying projected control
points on window captures (within about 1 px). Pass `fov_deg` if your setup differs.
