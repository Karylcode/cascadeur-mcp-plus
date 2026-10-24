**English** | [繁體中文](how-it-works.zh-TW.md) · [Documentation index](README.md)

# How it works

This page explains the parts of MCP Plus and how a call travels through them.

```text
MCP client ──stdio──► proxy.py ──HTTP──► MCP Plus inside Cascadeur (127.0.0.1:48766)
                         │                       │ calls run on Cascadeur's GUI thread
                         └─ if it is down ──► Cascadeur script server (127.0.0.1:8765) /run → start_mcp_plus.py
```

## The proxy

The MCP client starts the proxy over stdio. `proxy.py` in the checkout is a launcher for the implementation in `cascadeur_mcp_plus_cli/proxy.py`. The proxy uses only the Python standard library, and it answers `initialize` and `ping` itself, so it connects even while Cascadeur is closed.

- **`tools/list`**: if MCP Plus is up, the proxy forwards the request and saves the result to `tools_manifest.json`. Otherwise it serves that cached list.
- **`tools/call`**:
  1. It checks `/health` on the MCP Plus port, to confirm that the server answering is MCP Plus (see [Safety](safety.md)).
  2. If nothing answers, it checks the script server on port 8765 the same way, then sends it a short script (`POST /run`) that runs `start_mcp_plus.py`. It waits up to 10 seconds for MCP Plus to come up.
  3. It forwards the call to MCP Plus (`POST /mcp`). If the connection is refused, for example because Cascadeur restarted, it checks or starts MCP Plus again and retries once.
- Each request runs in its own thread, so a slow call never blocks pings or other calls.
- **Timeouts.** A tool with a numeric `timeout` argument gets that wait, capped at 600 seconds, plus a grace period for the response. Other tools get 210 seconds. That covers the 180-second internal wait of `render_image`.

## The in-app server (`cascadeur_mcp_plus/`)

- `start_mcp_plus.py` finds the package next to itself, reloads it in dependency order, and calls `start()` on Cascadeur's GUI thread. If a server is already running, it is stopped first.
- The server is an HTTP server on `127.0.0.1` (default port 48766) with `POST /mcp` and `GET /health`. On start it writes `tools_manifest.json`, so the proxy's offline tool list stays current.
- Cascadeur's bundled PySide6 shares the application's Qt runtime. The live QML item tree, its windows and Qt's input system are therefore reachable from Python.
- **Startup compatibility check.** Each tool lists in its `requires` what it needs from Cascadeur: API paths, actions or script modules. At startup MCP Plus checks them against the running Cascadeur. A tool with a missing entry stays in `tools/list`, its description starts with "Unavailable in this Cascadeur version:", and a call returns the missing entries instead of failing halfway. `get_status` runs the check again.

## GUI-thread dispatch

Requests arrive on background HTTP threads. Tools that touch Cascadeur are put in a queue, and a `QTimer` on the GUI thread runs them every 25 ms. The dispatcher is re-entrant: a job that opens a modal dialog does not block the next job, which may be the one that closes it. Waits such as `settle_ms` happen on the HTTP thread, so Cascadeur's event loop keeps running normally.

Exceptions:

- **`get_log`** only reads the log file, on the HTTP thread.
- **Scene idle tools** such as `enter_rig_mode`, `import_model`, `import_animation`, `quick_rig` and `video_mocap` run on Cascadeur's scene idle event, like the official script server. Rig mode and scene-graph edits need the scene idle event.
- **`screenshot`** and **`ui_drag`** are split into several GUI-thread jobs, with the waits between them on the HTTP thread. Each job waits up to 30 seconds. `ui_drag` runs the press, each move and the release as separate jobs, so AutoPosing and other live updates run between moves.
- **Background tools** start their work on the GUI thread and return at once with a `job_id`. The work itself is polled with `get_job`.

A normal GUI-thread job times out after 60 seconds (for `run_python`, after its `timeout`). Because the work runs on the GUI thread, a long operation freezes Cascadeur's UI until it finishes.

## Input

Clicks, hovers, keys and drags are synthesized through Qt's window-system input path (QtTest), so they follow the same path as real user input. `ui_drag` can send `QMouseEvent`s directly instead (`method="event"`). `ui_type` sends text-carrying key events to the window, and Qt Quick routes them to the focused item, so any Unicode text works. `ui_set_property` and `ui_invoke` bypass input handlers.

## Screenshots

`screenshot` uses the Win32 `PrintWindow` API. Forcing an extra Qt Quick render pass from Python (`QQuickWindow.grabWindow`) crashed Cascadeur, whose viewport draws with its own OpenGL code, so MCP Plus does not do it. A minimized window renders nothing, so `screenshot` can restore it first with `restore=true`.

## Rendering and output

- `render_image` uses Cascadeur's own renderer (`RenderToFile`). The window must be visible.
- `render_video` and `render_image_sequence` drive Cascadeur's official Export video window. Cascadeur must not be minimized, and no other export window may be open.
- `render_contact_sheet`, `render_video` and `render_image_sequence` render in Cascadeur's View mode with ghosts, trajectories and silhouette turned off. Controller points, joints, boxes and trajectories therefore stay out of the picture. Pass `keep_overlays=true` to render the viewport as it is.
- `render_contact_sheet` renders the chosen frames and joins them into one PNG grid, labelled with the frame numbers.

## Viewport projection

The Python API does not expose the viewport camera's field of view. `project_point` and `ui_drag` (with `object`) therefore use the main viewport camera's position and target, and assume a perspective camera with a 30° vertical field of view. That value was measured on Cascadeur 2026.2.3 by overlaying projected control points on window captures, within about 1 px. Pass `fov_deg` if your setup differs. Other camera types are refused.
