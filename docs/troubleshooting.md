**English** | [繁體中文](troubleshooting.zh-TW.md) · [Documentation index](README.md)

# Troubleshooting

Start with `python -m cascadeur_mcp_plus_cli doctor` from the checkout. It reports the Cascadeur installation, the ports, the servers and the MCP clients, and it exits with 1 only for real problems. For Python tracebacks and other errors inside Cascadeur, call `get_log` or read the file named by `CASCADEUR_LOG`.

## Common problems

- **Start script server fails with `WinError 10013`.** Another program owns port 8765. Find it with `netstat -ano | findstr :8765` and move it.
- **Renders refuse, or come out empty.** Cascadeur is minimized. Restore the window with `screenshot` and `restore=true`, then try again.
- **Black screenshot.** `screenshot` adds a warning. The window may be covered by a fullscreen app, or it may not be rendering yet.
- **"Unavailable in this Cascadeur version".** Call `get_status`. It lists the missing API or action for each tool.
- **A dragged pose shows a prompt.** Cascadeur may show "Pose in the current frame is different from the AutoPosing prediction". `ui_drag` reports it under `dialogs`. Answer *Yes* to keep the pose, and drag again.
- **Viewport projection is off.** `project_point` and `ui_drag` assume a perspective camera with a 30° vertical field of view, measured on 2026.2.3. Pass `fov_deg` if your setup differs. Other camera types are refused.
- **Long operations freeze Cascadeur.** Calls run on Cascadeur's GUI thread, so a long operation freezes its UI until it finishes. Poll `get_job` at short intervals instead of waiting in one long call.
- **Interface tools stop finding elements.** QML layouts can change between Cascadeur versions. Prefer an `actionId` or visible text over coordinates.
- **An action is missing from `list_actions`.** It reads the live QML tree, so actions of tools that have never been opened may be missing.
- **Unknown ref.** Refs reset on every `ui_tree` or `dialogs` call. Call `ui_tree` again.
- **A controller name is not found.** Use `list_controllers` to see the names and aliases of a character. An unknown name returns a `closest` suggestion.
- **`video_mocap` is refused.** The model file `mocap.pt` is not installed yet. See [Behavior and limitations](limitations.md#retargeting-rigging-and-mocap).
- **Tools are missing after an update.** Restart the MCP client. Run `setup` again if the command path changed, and run `autostart enable` again after a package update.

## Error messages

| Message starts with | What to do |
|---|---|
| `Cascadeur MCP Plus is not reachable and could not be started` | Open Cascadeur, click **Scripts → MCP → Start script server**, then retry. |
| `… is used by another program, not …` | Another program holds port 48766 or 8765, and nothing was sent to it. Free the port or move the program, then retry. See [Safety](safety.md). |
| `MCP Plus start script ran but the server did not come up` | Check Cascadeur's log file. |
| `Cascadeur rejected the MCP Plus start script` | The script server returned an error. The message includes its reply. |
| `No response from Cascadeur within …` | Cascadeur may be busy or frozen. |
| `Cascadeur did not finish within …` | A GUI-thread job timed out, and the GUI may be busy. For `run_python`, raise `timeout` (at most 600). |
| `Lost connection to Cascadeur MCP Plus` | Cascadeur may have closed or crashed. |
| `Refused to click …` | The Quick Rigging guard. Call `enter_rig_mode` first. See [Safety](safety.md). |
| `Cascadeur's mocap model is not installed` | See [Behavior and limitations](limitations.md#retargeting-rigging-and-mocap). |
