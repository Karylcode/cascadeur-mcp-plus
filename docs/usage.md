**English** | [繁體中文](usage.zh-TW.md) · [Documentation index](README.md)

# Usage

This page covers how a session runs, the habits that work well, common tasks, where output files go, a complete example, and prompts to start with.

## Each session

1. Start Cascadeur and click **Scripts → MCP → Start script server**. Do this after each launch, unless [autostart](installation.md#autostart) starts it for you.
2. Ask your agent to work in Cascadeur. The first tool call starts MCP Plus inside Cascadeur automatically, which takes up to about 10 seconds. Later calls take well under a second.

If the script server is not running, tool calls fail with a message that tells you to start it. See [Troubleshooting](troubleshooting.md).

## Working tips

The proxy gives the agent these instructions:

- Inspect the UI with `ui_tree` or `screenshot` before clicking.
- Prefer `call_action` for elements that have an `actionId`.
- Check `get_log` after operations that may fail silently.

Use the dedicated tools first, such as `set_pose`, `set_key`, `set_interpolation` and the render tools. The interface tools (`ui_*`, `dialogs`, `screenshot`) are the last resort.

## Conventions

- **Units and frames.** Positions are in centimetres and rotations in degrees (Euler XYZ). Frames are whole numbers from 0.
- **Names.** Pass an object name or an alias such as `left_hand`, `right_foot`, `pelvis` or `head`. `list_controllers` lists them. An ambiguous name returns `candidates`; an unknown name returns a `closest` suggestion.
- **Results.** Most tools return `{"ok": true, ...}` on success. A failure is `{"ok": false, "error": "...", "hint": "..."}`; follow the hint. A few tools, such as `get_scene_info`, return their data without `ok`.
- **Undo.** Each scene-changing tool makes one undo step named `MCP: <tool>`. Undo it with `undo` or Ctrl+Z, and `get_undo_history` lists these steps. Some tools need more than one undo, and a few cannot be fully undone; see [Behavior and limitations](limitations.md).
- **Background jobs.** `render_video`, `render_image_sequence`, `physics_snap`, `fix_foot_sliding`, `fix_collisions`, `prepare_keys_by_fulcrums`, `ai_inbetween`, `generate_motion` and `video_mocap` return a `job_id` at once. Poll `get_job` (its `wait_s` waits up to 60 seconds) and stop a job with `cancel_job`. A failed or cancelled job still has `ok: true`, so check `status`. A job that was running when the server restarted reports `lost`.
- **Batch.** `batch` runs several calls in one request, with the same timeouts and errors as single calls. A batch cannot contain another batch.
- **Resources.** Four read-only MCP resources give the same data without a tool call: `cascadeur://scene` (like `get_scene_info`), `cascadeur://timeline`, `cascadeur://controllers` (up to 500 entries) and `cascadeur://log` (the last 200 lines).
- **Camera presets.** `render_contact_sheet` and `render_video` take `camera_preset`. `auto`, the default, fits one fixed camera to the whole motion; `current` keeps the viewport camera. The six views `front`, `back`, `left`, `right`, `top` and `three_quarter` fit the motion from that side. `render_image` takes the six views only.

## Common tasks

**Inspect the scene.** `get_scene_info` summarizes frames, objects, selection, rig behaviours and license flags. `get_object_info` describes objects by name. `list_keys` lists the keys of each controller in a frame range.

**Run Python.** `run_python` runs code with `csc`, `app`, `scene`, `view_scene` and `ui` in scope. It returns stdout and the value of a lone expression or of a variable named `result`. Use `api_search` and `api_describe` to check unfamiliar API names first.

**Pose with numbers.** `set_pose` sets positions or rotations of named controllers on a frame and keys them. With `autoposing: false` and `key: true`, each named controller lands where you set it. Read the result back with `get_pose`, check the motion with `render_contact_sheet`, and write the MP4 with `render_video`.

**Key and interpolate.** `set_animation_range` sets the first and last frame. `set_key` keys frames, `set_interpolation` sets the interval type (for example `bezier_clamped`), and `list_keys` shows the result.

**Build a rig.** `quick_rig` rigs a bare skeleton with Quick Rigging. It refuses a scene that already has a rig unless `replace=true`. To use Quick Rigging's dialog instead, call `enter_rig_mode` first. For a freshly imported model, Cascadeur then shows a "Rig mode helper" dialog: call `dialogs` and press its *Yes* button with `ui_click(ref=...)`. The **Add rig elements** and **Generate rig** buttons are refused unless the scene is in Rig mode; see [Safety](safety.md).

**Drag a controller (last resort).**

1. `project_point` shows where scene objects are in the window, and whether they are on screen.
2. `ui_drag` with `object` set to that name and `dy=-80` drags it 80 px up. AutoPosing updates between the steps.
3. Check the result with `screenshot`. If `ui_drag` returns `dialogs`, answer the dialog and drag again.

**Operate the interface (last resort).** Call `ui_tree` or `dialogs` to get refs, then use them with `ui_click`, `ui_type`, `ui_set_property` or `ui_invoke`. Refs reset on every `ui_tree` or `dialogs` call. For a full-window capture, multiply image pixels by the returned `window_units_per_pixel` to get the window coordinates that `ui_click` uses in `x` and `y`.

## Output files

| Output | Default location |
|---|---|
| `render_image` | `%TEMP%\cascadeur-mcp-plus\renders\` |
| `render_video` | `%TEMP%\cascadeur-mcp-plus\videos\` |
| `render_image_sequence` | `%TEMP%\cascadeur-mcp-plus\sequences\` |
| `render_contact_sheet` | `%TEMP%\cascadeur-mcp-plus\contact_sheets\` |
| `video_mocap` reference frames | `%TEMP%\cascadeur-mcp-plus\mocap-frames\<timestamp>\`, deleted after the run unless `keep_reference=true` |
| `examples/wave.py` | `%TEMP%\cascadeur-mcp-plus-tests\T56\` |

Files are replaced only with `overwrite=true`. A render or export that fails leaves the old file in place. `render_image` writes PNG files only.

## Complete example

Give an agent one sentence, such as *make a 2-second waving animation on Cascy and export an mp4*. The tool calls it makes, with their arguments, are in [examples/wave.md](../examples/wave.md). [examples/wave.py](../examples/wave.py) runs the same steps over HTTP against a running MCP Plus, using only the standard library. Run it from the checkout with Cascadeur open and MCP Plus running:

```bat
python examples\wave.py
```

It writes `wave.mp4` and `wave_contact.png` to `%TEMP%\cascadeur-mcp-plus-tests\T56\`. Use `--out` to choose another folder.

## Prompt examples

- "List the objects in the current Cascadeur scene, the frame range, and the selected controllers."
- "Make a 2-second waving animation on the character, check it with a contact sheet, and render an MP4."
- "Project the right-hand controller to window coordinates, drag it up 80 px so AutoPosing adjusts the arm, then show me a screenshot."
- "Run some Python in Cascadeur to print the current frame. If it fails, read the last 50 log lines."

## Start MCP Plus manually

The proxy starts MCP Plus for you. To start it by hand, or to restart it with fresh code, run this in Cascadeur's Python (through the script server or the Python Console):

```python
p = r"C:\path\to\cascadeur-mcp-plus\start_mcp_plus.py"
exec(open(p, encoding="utf-8").read(), {"__file__": p})
```

To listen on another port, see [Development](development.md#manual-start-and-hot-reload).
