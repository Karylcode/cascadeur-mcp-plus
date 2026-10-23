# Changelog

## 0.4.0 — 2026-10-23

- Portable: no hard-coded paths. `start_mcp_plus.py` finds the package from its own path, and the proxy
  passes that path and its port when it starts the server.
- One version and default port shared by the server and the proxy (`cascadeur_mcp_plus/version.py`).
- The server writes `tools_manifest.json` on start, so the proxy's offline tool list stays current.
- Proxy tests with fake servers; ruff configuration and formatting.

## 0.3.0

- `ui_drag`: drags as separate GUI-thread steps so AutoPosing updates live; can start from a scene object.
- `project_point`: scene object → window coordinates in the 3D viewport.
- `ui_drag` reports dialogs that interrupt it (e.g. the AutoPosing lock prompt).
- Default port moved to 48766; the proxy verifies a server's identity before sending it anything.

## 0.2.0

- Guard against Quick Rigging's *Add rig elements / Generate rig* outside Rig mode (Cascadeur crashes).
- `enter_rig_mode`, run on the scene idle event like the official script server.
- stdio proxy that always connects and starts the in-app server on demand.

## 0.1.0

- In-app MCP server: Python, scene info, screenshots via `PrintWindow`, Qt Quick UI inspection and input,
  dialogs, actions, log.
