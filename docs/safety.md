**English** | [繁體中文](safety.zh-TW.md) · [Documentation index](README.md)

# Safety

This page lists what both servers can do, the checks that protect them, and the calls that crash Cascadeur.

## Both servers run any Python

Cascadeur's script server (port 8765) and MCP Plus (port 48766) listen on `127.0.0.1` only, but both run any Python they receive. `run_python` runs Python inside Cascadeur as well.

- Stop the script server when you are done.
- Once MCP Plus is running, the proxy talks only to it. The proxy needs the script server again only to start MCP Plus.
- MCP Plus keeps running until Cascadeur closes.
- MCP Plus rejects `POST` requests whose `Origin` header is not `127.0.0.1`, `localhost` or `::1`.
- [Autostart](installation.md#autostart) keeps both ports open without authentication. Disable it when you are not using the agent.

## Server identity check

The proxy never forwards a call to a port held by another program. Before it sends anything, it asks the port's `GET /health` who is answering:

- MCP Plus answers with `"server": "cascadeur-mcp-plus"`.
- Cascadeur's script server is recognized by its `idle_subscription` and `message_subscription` fields.
- Any other answer means another program.

If port 48766 or 8765 answers but is not the expected Cascadeur server, the call fails with an explanation, and nothing is sent to that port. Free the port or move the other program, then retry.

The check looks only at these fields; it does not compare versions. A successful check is reused for 60 seconds, and it is repeated when a connection is refused.

## Quick Rigging guard

Quick Rigging's **Add rig elements** and **Generate rig** need exactly one `AnimationInfo`, which exists only in Rig mode. In any other state Cascadeur raises `Rig mode several animationInfo` and then crashes.

- `ui_click` and `ui_invoke` refuse an element whose text, tooltip or actionId matches *Add rig elements* or *Generate rig* (case-insensitive), unless the scene has exactly one `AnimationInfo`. For `ui_click` at `x` and `y`, the check uses the elements under that point.
- Call `enter_rig_mode` first.
- `ui_click` accepts `force=true` to skip the guard. `ui_invoke` has no `force` parameter.
- Only these two tools are guarded. `call_action`, `ui_key`, `ui_set_property`, `ui_drag` and `run_python` are not checked.

## Calls that crash Cascadeur

- **Scenes.** Close a scene with `close_scene`. Do not close it from `run_python`, do not call `SceneManager.remove_application_scene`, and do not touch a scene in the same call that closed it. These crashed Cascadeur during testing.
- **Python APIs.** Avoid these in `run_python`. They crashed Cascadeur or raised an error during testing: `csc.layers.CyclesViewer`, `csc.layers.Cycle`, `CyclesEditor`, `Viewer.item()`, `Folder.children_ordered()`, and rotating a character with `modify_update`. Use `list_keys`, `list_layers` and `set_pose` instead.
- **Rig mode.** Call `enter_rig_mode` before Quick Rigging; see the guard above.
- **Video mocap.** `video_mocap` is experimental: cancelling a running mocap job crashed Cascadeur during testing. Save your scene before calling it.

## Layer locks

MCP Plus write tools respect animation layer locks. Cascadeur's Python API does not check them, so `run_python` can change objects on a locked layer.
