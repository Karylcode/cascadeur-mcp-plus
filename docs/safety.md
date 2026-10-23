# Safety

**English** | [繁體中文](safety.zh-TW.md) · [Documentation index](README.md)

## Both servers run any Python

Cascadeur's script server (`8765`) and MCP Plus (`48766`) listen on `127.0.0.1` only, but both run any
Python they receive. Stop the script server when you are done.

- Once MCP Plus is running, the proxy talks only to it. The proxy needs the script server again only to
  start MCP Plus.
- MCP Plus keeps running until Cascadeur closes.
- MCP Plus rejects `POST` requests whose `Origin` header is not `127.0.0.1`, `localhost` or `::1`.

## Server identity check

The proxy never forwards a call to a port held by another program. Before it sends anything, it asks the
port's `GET /health` who is answering:

- MCP Plus answers with `"server": "cascadeur-mcp-plus"`.
- Cascadeur's script server is recognized by its `idle_subscription` and `message_subscription` fields.
- Any other answer means another program.

If `48766` or `8765` answers but is not the expected Cascadeur server, the call fails with an explanation
instead, and nothing is sent there. Free the port or move the other program, then retry.

The check looks only at these fields; it does not compare versions. A successful check is reused for
60 seconds, and is repeated when a connection is refused.

## Quick Rigging guard

Quick Rigging's **Add rig elements** / **Generate rig** needs exactly one `AnimationInfo`, which exists only
in Rig mode. In any other state Cascadeur raises `Rig mode several animationInfo` and then crashes.

- `ui_click` and `ui_invoke` refuse an element whose text, tooltip or actionId matches *Add rig elements* or
  *Generate rig* (case-insensitive) unless the scene has exactly one `AnimationInfo`. For `ui_click` at
  `x`/`y`, the check uses the elements under that point.
- Call `enter_rig_mode` first.
- `ui_click` accepts `force=true` to skip the guard. `ui_invoke` has no `force`.
- Only these two tools are guarded. `call_action`, `ui_key`, `ui_set_property`, `ui_drag` and `run_python`
  are not checked.
