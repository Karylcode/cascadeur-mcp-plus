# Usage

**English** | [繁體中文](usage.zh-TW.md) · [Documentation index](README.md)

## Each session

1. Start Cascadeur and click **Scripts → MCP → Start script server**. Do this after each launch.
2. Ask your agent to work in Cascadeur. The first tool call starts MCP Plus inside Cascadeur automatically
   (about 10 seconds). After that there is no startup delay.

If the script server is not running, tool calls fail with a message that tells you to start it.

## Working tips

The proxy gives the agent these instructions:

- Inspect the UI with `ui_tree` or `screenshot` before clicking.
- Prefer `call_action` for elements that have an `actionId`.
- Check `get_log` after operations that may fail silently.

## Common tasks

**Inspect the scene.** `get_scene_info` summarizes frames, objects, selection, rig behaviours and license
flags. `get_object_info` describes objects by name.

**Run Python.** `run_python` runs code with `csc`, `app`, `scene`, `view_scene` and `ui` in scope.
It returns stdout and the value of a lone expression or of a variable named `result`.

**Operate the UI.** Call `ui_tree` or `dialogs` to get refs, then use them with `ui_click`, `ui_type`,
`ui_set_property` or `ui_invoke`. Refs reset on every `ui_tree` or `dialogs` call.

**Click what you see in a screenshot.** For a full-window capture, multiply image pixels by the returned
`window_units_per_pixel` to get the window coordinates that `ui_click` `x`/`y` use.

**Enter Rig mode and use Quick Rigging.**

1. Call `enter_rig_mode` (required before Quick Rigging). For a freshly imported model, Cascadeur then
   shows a "Rig mode helper" dialog that offers Quick Rigging.
2. Call `dialogs` and press its *Yes* button with `ui_click(ref=...)`.
3. Use Quick Rigging's **Add rig elements** / **Generate rig**. Outside Rig mode these clicks are refused;
   see [Safety](safety.md).

**Pose a character with AutoPosing.**

1. `project_point` shows where scene objects (for example a control point) are in the window, and whether
   they are on screen.
2. `ui_drag` with `object` set to that name and `dy=-80` drags it 80 px up. AutoPosing updates between steps.
3. Check the result with `screenshot`. If `ui_drag` returns `dialogs`, answer the dialog and drag again.

## Prompt examples

- "List the objects in the current Cascadeur scene, the frame range, and the selected controllers."
- "Switch the model I just imported to Rig mode, check the dialog, and open Quick Rigging."
- "Project the right-hand controller to window coordinates, drag it up 80 px so AutoPosing adjusts the arm,
  then show me a screenshot."
- "Run some Python in Cascadeur to print the current frame. If it fails, read the last 50 log lines."

## Start MCP Plus manually

The proxy starts MCP Plus for you. To start it by hand, or restart it with fresh code, run this in
Cascadeur's Python (through the script server or the Python Console):

```python
p = r"C:\path\to\cascadeur-mcp-plus\start_mcp_plus.py"
exec(open(p, encoding="utf-8").read(), {"__file__": p})
```

To use another port, see [Development](development.md).
