# Troubleshooting

**English** | [繁體中文](troubleshooting.zh-TW.md) · [Documentation index](README.md)

## Tips and known limitations

- **Port 8765 taken** → *Start script server* fails with `WinError 10013`. Another local program owns the
  port; move it elsewhere (`netstat -ano | findstr :8765` shows which).
- **Minimized window** → nothing is rendered, and `screenshot` returns an error. Call `screenshot` with
  `restore=true`.
- **Black screenshot** → `screenshot` adds a warning. The window may be covered by a fullscreen app, or not
  rendering yet.
- **Dragging a scripted pose** → Cascadeur may show "Pose in the current frame is different from the
  AutoPosing prediction…". `ui_drag` reports it under `dialogs`; answer *Yes* to keep the pose and drag again.
- **Viewport projection** assumes a perspective camera with a 30° vertical field of view (measured on
  2026.2.3). Pass `fov_deg` to `project_point` / `ui_drag` if your setup differs. Other camera types are refused.
- **Long operations** → calls run on Cascadeur's GUI thread, so long operations freeze its UI until they finish.
- **Cascadeur updates** → QML layouts can change between Cascadeur versions; prefer `actionId` or visible
  text over coordinates.
- **Missing actions** → `list_actions` reads the live QML tree, so actions of tools that have never been
  opened may be missing.
- **Unknown ref** → refs reset on every `ui_tree` or `dialogs` call. Call `ui_tree` again.

## Error messages

| Message starts with | What to do |
|---|---|
| `Cascadeur MCP Plus is not reachable and could not be started` | Open Cascadeur, click **Scripts → MCP → Start script server**, then retry. |
| `… is used by another program, not …` | Another program holds `48766` or `8765`; nothing was sent to it. Free the port or move the program, then retry. See [Safety](safety.md). |
| `MCP Plus start script ran but the server did not come up` | Check Cascadeur's log file (path under `get_log` in [Tools](tools.md)). |
| `Cascadeur rejected the MCP Plus start script` | The script server returned an error; the message includes its reply. |
| `No response from Cascadeur within …` | Cascadeur may be busy or frozen. |
| `Cascadeur did not finish within …` | A GUI-thread job timed out; the GUI may be busy. For `run_python`, raise `timeout` (at most 600). |
| `Lost connection to Cascadeur MCP Plus` | Cascadeur may have closed or crashed. |
| `Refused to click …` | The Quick Rigging guard. Call `enter_rig_mode` first. See [Safety](safety.md). |
