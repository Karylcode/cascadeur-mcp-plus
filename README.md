# Cascadeur MCP Plus

[![CI](https://github.com/Karylcode/cascadeur-mcp-plus/actions/workflows/ci.yml/badge.svg)](https://github.com/Karylcode/cascadeur-mcp-plus/actions/workflows/ci.yml)

[繁體中文](README.zh-TW.md)

Cascadeur MCP Plus is an MCP server that runs inside [Cascadeur](https://cascadeur.com/), so an AI agent
(Claude Code, Codex CLI, Cursor, VS Code, …) can animate with Cascadeur's own features: set poses with numbers
and let AutoPosing move the body, key and interpolate the timeline, run AutoPhysics and cleanup, and render
the result with Cascadeur's own renderer. The agent does not have to drag bones with a mouse or stitch
screenshots together.

> **Unofficial.** Not affiliated with or endorsed by Nekki. Cascadeur is a trademark of Nekki Limited.
> This project contains no Cascadeur code; it talks to Cascadeur only through its Python API and Qt.

## Overview

- The server exposes 105 tools, grouped by topic in the Tools section. The stdio proxy adds two more for
  working with several Cascadeur windows (`list_instances` and `set_active_instance`).
- Built for Cascadeur 2026.2. Tested on 2026.2.3 on Windows. `get_status` lists the tools this Cascadeur build
  cannot use; calling one of them returns an explicit error instead of failing halfway.
- A typical job: open a scene, set the range, pose with `set_pose`, key and interpolate, check the motion with
  `render_contact_sheet`, then write the MP4 with `render_video`. A complete, runnable example is in
  `examples/wave.md`.

## Requirements

- Windows 10 or 11. macOS and Linux are not tested; see Platform support.
- Cascadeur 2026.2 with its built-in MCP script server (tested with 2026.2.3).
- Python 3.11 or newer for the proxy and the CLI. They use only the standard library, so there is nothing to
  `pip install`.
- [uv](https://docs.astral.sh/uv/), which provides `uvx`, for the one-line install. The manual install needs
  only Python.
- An MCP client: Claude Code, Codex CLI, Cursor, VS Code, or any client that reads a JSON `mcpServers` config.

## Install

### One line

From a checkout of this repository:

```bat
uvx --from C:\path\to\cascadeur-mcp-plus cascadeur-mcp-plus setup
```

`setup` registers the server, under the name `cascadeur-plus`, with every MCP client it detects (Claude Code,
Codex CLI, Cursor and VS Code). It backs up each config file before changing it. Options:

- `--dry-run` prints the exact changes and writes nothing.
- `--client claude-code` (or `codex`, `cursor`, `vscode`) changes one client only.
- `--command PATH` makes the clients run a different executable.

`cascadeur-mcp-plus uninstall` removes the registration again. `cascadeur-mcp-plus doctor` checks the Cascadeur
install, ports 8765 and 48766, the script server, the MCP Plus version and the detected clients.

The package is not published on PyPI yet, so the command above runs from the checkout. After publication the
install becomes `uvx cascadeur-mcp-plus setup`.

### Manual

Claude Code:

```bat
claude mcp add --scope user cascadeur-plus -- python -I C:\path\to\cascadeur-mcp-plus\proxy.py
```

Other clients, with a JSON config:

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

## Start Cascadeur

MCP Plus runs inside Cascadeur. The proxy starts it through Cascadeur's script server (port 8765) when it is not
running. Choose how the script server starts:

- **Autostart** (recommended for regular use): run `cascadeur-mcp-plus autostart enable` once, as described in
  Autostart. Cascadeur then starts both servers when it launches.
- **Manual:** after each launch, click **Scripts → MCP → Start script server** in Cascadeur. The first tool call
  starts MCP Plus automatically; later calls take well under a second.

## Autostart

`cascadeur-mcp-plus autostart` makes Cascadeur start the script server and MCP Plus when it launches.

- `autostart status` shows whether the handler is installed, which runtime copy it uses, and whether both servers
  answer. Add `--json` for scripts.
- `autostart enable` installs a handler in Cascadeur's `resources\scripts\python\events\application_started\`
  folder and copies MCP Plus to `%LOCALAPPDATA%\cascadeur-mcp-plus\runtime\<version>\`. Writing into the install
  folder needs administrator rights, so Windows shows one UAC prompt. `--dry-run` prints the changes first, and
  `--yes` skips the confirmation (the UAC prompt still appears).
- `autostart disable` removes the handler and restores the file it replaced.
- The runtime copy is a snapshot. Run `enable` again after you update the package, when `status` reports a runtime
  version that differs from the installed one, or when you want newer code. `--runtime PATH` runs MCP Plus from a
  checkout instead. It is for development: if that checkout moves, the handler fails and only writes to the log.
- `--method admin` (the default) is the only method available. `user` is not supported yet.

> **Security:** autostart opens two local ports without authentication: 8765 (Cascadeur's script server) and
> 48766 (MCP Plus). Any program on this computer can then run Python inside Cascadeur through them. Run
> `cascadeur-mcp-plus autostart disable` when you are not using the agent.

## Several Cascadeur windows

MCP Plus can run in up to 10 Cascadeur windows at once, on ports 48766 to 48775.

- The default instance (port 48766) starts through the script server, as above. Each other window needs MCP Plus
  started from its own Python Console: run `start_mcp_plus.py` from the checkout. The error for a closed instance
  gives the exact command.
- `list_instances` (a proxy tool, not in the tool tables) lists the instances that answer, with their port, process
  id, scene and version, and marks the active one.
- `set_active_instance` (also a proxy tool) chooses where calls without `instance` go. It takes a port, a process
  id, or part of a scene name.
- Every tool also accepts `instance` on a single call: a port (`48770`), `port:48770`, `pid:1234`, `default`, or
  part of a scene name or path. A name that matches several windows returns an error with the candidates. A call
  is never sent to a window that was only guessed.
- The selection belongs to one stdio proxy process, so two MCP clients do not share it.
- Only the default instance starts automatically, because port 8765 is shared between windows.

## Platform support

- **Windows 10 and 11: supported and tested** on Cascadeur 2026.2.3. Everything in this README was checked on
  Windows.
- **macOS and Linux: not tested.** The code has paths for them (data and install folders, client config locations,
  window capture), but nobody has run them on a real machine. The maintainer supports and verifies Windows only.
  The unit tests do not run Cascadeur on any platform.
- Windows only: autostart (UAC and writes to the install folder), the Win32 screenshot path, and the mocap dialog
  automation. On macOS or Linux, start MCP Plus from Cascadeur's Python Console with `start_mcp_plus.py`. Window
  capture there may be blocked (on Wayland, for example); `screenshot` then reports the error and points to
  `render_image`.

## Complete example

Give an agent one sentence: *make a 2-second waving animation on Cascy and export an mp4*. The tool calls it makes,
with their arguments, are in [examples/wave.md](examples/wave.md). [examples/wave.py](examples/wave.py) runs the
same steps over HTTP against a running MCP Plus, using only the standard library:

1. `open_scene` opens the Cascy sample in a new tab, and `get_timeline` reads the fps.
2. `set_animation_range` sets 2 seconds (frames 0 to 59 at 30 fps), and `set_key` keys the rest pose on frame 0.
3. `set_pose` writes the right elbow and hand on eight frames: the hand goes above the shoulder and out to the side,
   and swings side to side while the elbow bends. Each write uses `autoposing: false` and `key: true`, so both
   controllers land exactly where they are set.
4. `set_pose` returns the arm to rest on the last frame, and `set_interpolation` makes the keys clamped Bezier.
5. `get_pose` checks the hand height above the shoulder and the swing.
6. `render_contact_sheet` makes a PNG grid of the mesh from the front, and `render_video` writes the MP4 from the front
   as a background job, which the script polls with `get_job`.
7. `close_scene` closes the test tab with `discard_changes`, so the sample is never saved.

Run it with Cascadeur open and MCP Plus running:

```bat
python examples\wave.py
```

The script writes `wave.mp4` and `wave_contact.png` to `%TEMP%\cascadeur-mcp-plus-tests\T56\`. Use `--out` to choose
another folder.

## Tools

The server exposes these 105 tools (`tools/list`). The tables give a short summary; the full description and the
argument schema are what the agent sees. Each tool appears in exactly one row.

### Scene and files

| Tool | What it does |
|---|---|
| `new_scene` | Create an empty scene tab and make it current. |
| `open_scene` | Open a `.casc` file (absolute path) in a new tab; the current tab is never replaced. |
| `save_scene` | Save the current scene, or save it as a new `.casc` file when `path` is given. Replacing a file needs `overwrite=true`. |
| `list_open_scenes` | List the open scene tabs with index, name, path, whether each is current, and unsaved changes (null when Cascadeur does not report it). |
| `switch_scene` | Make a scene tab current by index or exact name. Prefer the name, because indexes change when Rig mode reorders tabs. |
| `close_scene` | Close one scene tab. A scene with unsaved changes stays open unless `discard_changes=true`. |
| `import_model` | Import an FBX or GLB model into the current scene and enter Rig mode. USD is not supported. |
| `import_animation` | Apply the animation in an FBX or BVH file to the character; `take` picks a take by name or index. |
| `list_fbx_takes` | List the animation take names inside an FBX file. |
| `export_scene` | Export the current scene to an FBX or GLB file at an absolute path. |
| `get_scene_info` | Summarize the scene: name, path, frames, object counts, selection, rig behaviours and license flags. |
| `get_object_info` | Describe objects found by exact or partial name: type, id, behaviours and their property names. |

### Characters and selection

| Tool | What it does |
|---|---|
| `list_characters` | List the characters with root joint, mesh name, control-point count and joint count. |
| `list_controllers` | List a character's control points with alias, kind, world position in cm and AutoPosing lock state. |
| `select_objects` | Select objects by name or alias, replacing, adding to or removing from the selection, as clicking them does. |
| `get_selection` | List the current selection with name, alias, kind, Cascadeur type, character and id. |
| `clear_selection` | Deselect everything in the active scene. |
| `enter_rig_mode` | Enter Rig mode with Cascadeur's own entry points. Quick Rigging needs it first. |
| `list_rig_templates` | List the Quick Rigging templates installed with Cascadeur. |
| `quick_rig` | Build a rig for a bare skeleton with Quick Rigging. It refuses a scene that already has a rig unless `replace=true`. |
| `retarget_animation` | Retarget animation from another open scene (by tab name) or a `.casc` file onto the current rig, between different skeletons. |

### Pose and keyframes

#### AutoPosing and poses

| Tool | What it does |
|---|---|
| `get_pose` | Read control-point positions (cm) and rotations (degrees, with quaternions) at a frame, without changing anything. |
| `set_pose` | Set positions, offsets or rotations with numbers, key them, and let AutoPosing move the rest of the body. |
| `mirror_pose` | Mirror the whole character's pose from left to right at a frame, with Cascadeur's own Mirror. |
| `copy_pose` | Copy a frame's pose into MCP Plus's pose store. The store is in memory and is lost on restart. |
| `paste_pose` | Paste a copied pose at a frame, optionally mirrored. |
| `reset_pose` | Reset controllers to the default T-pose or to the bind pose. |
| `get_autoposing_state` | Report whether AutoPosing is on. |
| `autoposing_enable` | Turn AutoPosing on or off. |
| `autoposing_update` | Recompute the current frame from the current AutoPosing locks, as one undo step. |
| `lock_controllers` | Lock controllers by name or alias for the frame, using Cascadeur's Autolock. Cascadeur's API cannot unlock them. |

#### Timeline and keys

| Tool | What it does |
|---|---|
| `get_timeline` | Report the current frame, the animation's first and last frame, its length and fps. Read-only. |
| `set_frame` | Move the timeline playhead to a frame. |
| `set_animation_range` | Set the animation's first and/or last frame. This is not undoable. |
| `set_key` | Add keys on frames for some controllers, or for the whole character when `controllers` is omitted. |
| `delete_key` | Remove keys on frames for some controllers, or for the whole character. |
| `list_keys` | List the key frames, the interpolation of each interval and the segments, per controller, in a frame range. |
| `select_frames` | Select a frame interval on the timeline, so the timeline actions that follow apply to it. |
| `create_cycle` | Turn a frame range into a timeline cycle with Cascadeur's own Create cycle action. |
| `remove_cycles` | Remove the timeline cycles that intersect a range, or all of them when no range is given. |

#### Interpolation and IK/FK

| Tool | What it does |
|---|---|
| `set_interpolation` | Set the interpolation of the intervals from `start` to `end`: `bezier`, `bezier_clamped`, `bezier_viscous`, `linear`, `step` or `fixed`. |
| `set_ik_fk` | Switch the intervals from `start` to `end` to IK, FK or GR (global FK). |
| `reset_tangents` | Clear the custom tangents of the keys in a range, restoring continuous tangents. |
| `reset_easing` | Reset the easing weights of the keys in a range to one third. |

#### Animation layers

| Tool | What it does |
|---|---|
| `list_layers` | List the animation layer tree with ids, paths, visible and locked flags, and object counts. |
| `create_layer` | Create an empty animation layer under a folder. |
| `create_layer_folder` | Create a folder in the layer tree. It starts with one empty layer, because Cascadeur 2026.2.3 rejects an empty folder. |
| `delete_layer` | Delete a layer or a folder. With `force=true` the objects are moved to the default layer first. |
| `set_layer_visible` | Show or hide an animation layer. |
| `set_layer_locked` | Lock or unlock an animation layer. Only MCP Plus write tools respect the lock. |
| `rename_layer` | Rename an animation layer or folder. |
| `move_to_layer` | Move control points to an animation layer. |

### Physics and AI

| Tool | What it does |
|---|---|
| `get_physics_state` | Report the AutoPhysics (Physics Assistant) state: enabled, frozen, fulcrum display, priority frames, frames and fps. |
| `physics_enable` | Turn the Physics Assistant (AutoPhysics) on or off. |
| `physics_snap` | Bake the AutoPhysics result into the animation, for all frames. Background job. |
| `physics_freeze` | Freeze or unfreeze the AutoPhysics result. |
| `physics_set_priority_frame` | Mark a frame whose pose the solver keeps while it solves the rest of the animation. |
| `physics_show_fulcrums` | Show or hide the fulcrum (contact) points of the character. |
| `fix_foot_sliding` | Remove foot sliding in a contact interval with Cascadeur's Fulcrum Motion Cleaning. Background job. |
| `fix_collisions` | Push colliding body parts apart with Cascadeur's Collision Penetration Cleaning. Background job. |
| `prepare_keys_by_fulcrums` | Prepare the keys of an interval from the fulcrum motion with Auto keys. Run it before cleaning. Background job. |
| `ai_inbetween` | Fill the frames between keys with Cascadeur's AI inbetweening. Background job. |
| `generate_motion` | Generate new motion for an interval with Cascadeur's motion generation. Background job. |
| `video_mocap` | Turn a reference video into character animation with Cascadeur's built-in video mocap. Background job. |

### Review and output

| Tool | What it does |
|---|---|
| `render_image` | Render the 3D view to a PNG with Cascadeur's own renderer. The default folder is under `%TEMP%`. |
| `render_contact_sheet` | Render several frames into one PNG grid, with the frame number in each cell, to check a motion. |
| `render_video` | Render the animation to an MP4 through Cascadeur's Export video window. Background job. |
| `render_image_sequence` | Render the animation to a folder of PNG frames through the same export window. Background job. |

### Camera and display

| Tool | What it does |
|---|---|
| `get_camera` | Return the viewport camera: position, target, type, distance and vertical field of view. |
| `set_camera` | Move the viewport camera by position and/or target, in cm. |
| `camera_preset` | Put the camera in a standard view of a character: `front`, `back`, `left`, `right`, `top` or `three_quarter`. |
| `frame_objects` | Move the camera so that the named objects are inside the view. |
| `track_object` | Make the camera follow an object or character. Without a name, it stops following. |
| `get_display_state` | Report the view mode, ghost mode, trajectory mode, viewport layout, silhouette and surface. |
| `set_view_mode` | Switch the viewport display mode: `mesh`, `joint`, `box_controller`, `point_controller`, `autoposing`, `rigging_view` or `view`. |
| `set_ghosts` | Set the ghost mode: `none`, `previous`, `next`, `neighbors`, `keyframes` or `selected_frames`. |
| `set_trajectories` | Set the trajectory mode: `off`, `all`, `edges`, `edges_and_fixed` or `parents`. `only_key_frames` limits it to keys. |
| `set_viewport_layout` | Switch the viewport layout to 1, 2 or 4 viewports. |
| `set_silhouette` | Turn the silhouette display on or off. |
| `set_surface` | Turn the surface display on or off. |

### UI control (last resort)

These tools drive the interface like a person would. Use the dedicated tools above first.

| Tool | What it does |
|---|---|
| `ui_tree` | Last resort: list the on-screen UI elements, with refs for the other interface tools. |
| `ui_click` | Last resort: click an element by ref or text, or at window coordinates. |
| `ui_drag` | Last resort: drag with the mouse, in steps, with the button held. |
| `ui_hover` | Last resort: hover over an element to show a tooltip or hover menu. |
| `ui_key` | Last resort: press keys or shortcuts such as `ctrl+s`. |
| `ui_type` | Last resort: type text into the focused element. |
| `ui_set_property` | Last resort: set a QML property on an element. |
| `ui_invoke` | Last resort: call a method or signal without arguments on an element. |
| `dialogs` | List the visible dialogs and popups, with their texts and buttons. |
| `screenshot` | Last resort: capture a Cascadeur window or one element. |
| `project_point` | Last resort: project scene objects to window coordinates, for `ui_click` and `ui_drag`. |

### Other

| Tool | What it does |
|---|---|
| `get_status` | Report the MCP Plus and Cascadeur versions, the tested version, and the tools this build cannot use. |
| `api_search` | Search Cascadeur's Python type stubs and official samples for classes, functions and docstrings. |
| `api_describe` | Show the stub signature and docstring of a `csc` class, function or method. |
| `run_python` | Run Python inside Cascadeur on its GUI thread, with `csc`, `app`, `scene`, `view_scene` and `ui` in scope. |
| `get_log` | Read the end of Cascadeur's log, where errors and Python tracebacks appear. |
| `batch` | Run several tool calls in one request. |
| `get_job` | Read the status, progress, result or error of a background job. |
| `list_jobs` | List recent background jobs, newest first. |
| `cancel_job` | Cancel a running background job. |
| `undo` | Undo the last change or changes with Cascadeur's own undo. |
| `redo` | Redo the last undone change or changes. |
| `get_undo_history` | List the recent `MCP:` undo steps, marked as applied or undone. |
| `list_actions` | List the Cascadeur action keys found in the live interface. |
| `call_action` | Trigger a Cascadeur action by key, the same as its toolbar button or menu item. Prefer it when an action exists. |

## Using the tools

- **Units and frames.** Positions are in centimetres and rotations in degrees (Euler XYZ). Frames are whole
  numbers from 0.
- **Names.** Pass a Cascadeur object name or an alias such as `left_hand`, `right_foot`, `pelvis` or `head`.
  `list_controllers` lists them. An ambiguous name returns `candidates`; an unknown name returns a `closest`
  suggestion.
- **Results.** A success is `{"ok": true, ...}`. A failure is `{"ok": false, "error": "...", "hint": "..."}`. Follow
  the hint.
- **Undo.** Each scene-changing tool makes one undo step named `MCP: <tool>`. Undo it with `undo` or Ctrl+Z.
  `get_undo_history` lists these steps. Some steps are longer; see Known limitations.
- **Background jobs.** `render_video`, `render_image_sequence`, `physics_snap`, `fix_foot_sliding`,
  `fix_collisions`, `prepare_keys_by_fulcrums`, `ai_inbetween`, `generate_motion` and `video_mocap` return a
  `job_id` at once. Poll `get_job` (its `wait_s` waits up to 60 seconds) and stop a job with `cancel_job`. Check
  `status`: `ok` is true for failed and cancelled jobs too. A job that was still running when the server restarted
  reports `lost`.
- **Pose with numbers.** Use `set_pose`, `set_key` and the render tools. The interface tools are the last resort.
- **Version differences.** A tool that needs an API or action this Cascadeur build lacks is listed by `get_status`,
  and calling it returns `ok: false` without running anything.
- **Rig styles.** Aliases work with Cascadeur's standard rig and with Mixamo, UE4 Mannequin and Roblox R15 naming.
  The Mixamo names were only unit tested.
- **Camera presets.** `render_contact_sheet` and `render_video` take `camera_preset`: `auto` (the default) fits one
  fixed camera to the whole motion, and `current` keeps the viewport camera. The six views (`front`, `back`, `left`,
  `right`, `top`, `three_quarter`) fit the motion from that side. `render_image` takes the six views only.
  `render_contact_sheet` uses `step` 1 when only `start` and `end` are given.
- **MCP resources.** Four read-only resources give the same data without a tool call: `cascadeur://scene` (as
  `get_scene_info`), `cascadeur://timeline`, `cascadeur://controllers` (up to 500 entries) and `cascadeur://log` (the
  last 200 lines).
- **Batch.** `batch` runs several calls in one request, with the same timeouts and errors as single calls. A batch
  cannot contain another batch.

## Output files

| Output | Default location |
|---|---|
| `render_image` | `%TEMP%\cascadeur-mcp-plus\renders\` |
| `render_video` | `%TEMP%\cascadeur-mcp-plus\videos\` |
| `render_image_sequence` | `%TEMP%\cascadeur-mcp-plus\sequences\` |
| `render_contact_sheet` | `%TEMP%\cascadeur-mcp-plus\contact_sheets\` |
| `video_mocap` reference frames | `%TEMP%\cascadeur-mcp-plus\mocap-frames\<timestamp>\`, deleted after the run unless `keep_reference=true` |
| `examples/wave.py` | `%TEMP%\cascadeur-mcp-plus-tests\T56\` |

Files are replaced only with `overwrite=true`. A render or export that fails leaves the old file in place. `render_image`
writes PNG files only.

## How it works

```text
MCP client ──stdio──► proxy.py ──HTTP──► MCP Plus inside Cascadeur (127.0.0.1:48766)
                         │                       │ calls run on Cascadeur's GUI thread
                         └─ if it is down ──► Cascadeur script server (127.0.0.1:8765) /run → start_mcp_plus.py
```

- The proxy is the program the MCP client starts. It forwards calls to MCP Plus, starts MCP Plus through the script
  server when needed, and serves the last known tool list while Cascadeur is closed. A tool with a numeric
  `timeout` argument gets that wait (capped at 600 seconds) plus a grace period; other tools get 210 seconds.
- MCP Plus uses the PySide6 bundled with Cascadeur, so it shares Cascadeur's Qt runtime. It reaches the live QML
  interface, its windows and Qt's input system from Python.
- Clicks, drags and key presses go through Qt's input path (QtTest), so they follow the path a real user's input
  takes. Each drag step is a separate GUI-thread job, so AutoPosing and other live updates run between moves.
- Renders use Cascadeur's own renderer (`RenderToFile`). Video uses Cascadeur's Export video window.
- Screenshots use the Win32 `PrintWindow` API. Forcing an extra Qt Quick render pass from Python
  (`QQuickWindow.grabWindow`) crashed Cascadeur in testing, so MCP Plus does not do it.
- The proxy checks each server's identity through `/health` before it sends a call to it, and it never forwards a
  call to a port held by another program.

## Safety

- Both servers listen on `127.0.0.1` only, but they run any Python they receive, like Cascadeur's own script
  server. Stop the script server when you are done, and disable autostart when you are not using the agent.
- Close scenes with `close_scene`. Do not close them from `run_python`, do not call
  `SceneManager.remove_application_scene`, and do not touch a scene in the same call that closed it. These crashed
  Cascadeur during testing.
- Layer locks are honoured by MCP Plus write tools only. Cascadeur's Python API does not check them, so
  `run_python` can change objects on a locked layer.
- Avoid these calls in `run_python`; they crashed Cascadeur or raised an error in testing: `csc.layers.CyclesViewer`,
  `csc.layers.Cycle`, `CyclesEditor`, `Viewer.item()`, `Folder.children_ordered()`, and rotating a character with
  `modify_update`. Use `list_keys`, `list_layers` and `set_pose` instead.
- `ui_click` and `ui_invoke` refuse Quick Rigging's **Add rig elements / Generate rig** unless the scene is in Rig
  mode (exactly one `AnimationInfo`). In that state Cascadeur raises `Rig mode several animationInfo` and then
  crashes. Call `enter_rig_mode` first.

## Known limitations

- **Tested on one setup.** Cascadeur 2026.2.3 on Windows. Other Cascadeur versions may behave differently. `get_status`
  lists the tools this build disables and the behaviour to re-check after an update. Behaviour that depends on the
  version: the video export window (`render_video`, `render_image_sequence`), the AutoPosing lock (`set_pose`,
  `lock_controllers`), animation layers, video mocap, cycles, AutoPhysics, AI interpolation (`ai_inbetween`,
  `generate_motion`), retargeting and the interface tools.
- **Undo is not complete for every tool.** Each scene-changing tool is one `MCP:` step, but `set_pose` with AutoPosing
  takes two undos: its own step, then Cascadeur's AutoPosing.Autolock. `set_animation_range` cannot be undone. IK/FK,
  retargeting, cleaning, cycles, mocap and Quick Rigging run Cascadeur's own commands, so one Ctrl+Z may not revert
  them completely, and `get_undo_history` does not list them. The history restarts when Cascadeur restarts or a
  scene loads.
- **Main control points have no rotation.** In 2026.2.3 they have positions only. Rotate boxes and joints instead,
  for example `hand_Box_l`.
- **AutoPosing can reset a later pose.** With `autoposing=true`, a `set_pose` on a frame that comes after another posed
  frame was reset in testing: the hand fell back about 70 cm from its target. Writing the controllers you need with
  `autoposing=false` placed them exactly; `examples/wave.md` shows it. On frames with no earlier AutoPosing data the
  body also moves little, so key the frames and check the numbers with `get_pose`.
- **A second `set_pose` on the same frame can undo part of the first.** Write all controllers of a frame in one call,
  or use `autoposing=false`.
- **Locks.** `lock_controllers` cannot unlock, because the 2026.2.3 API has no unlock. `list_open_scenes` reports
  `modified` as null, because 2026.2.3 has no public unsaved-changes flag.
- **Long operations freeze the interface.** Calls run on Cascadeur's GUI thread, so a long job blocks it. GUI-bound
  calls such as `get_scene_info` queue behind a running cleaning job. Poll `get_job` in short steps.
- **Video output.** `render_video` and `render_image_sequence` drive Cascadeur's official Export video window. Cascadeur
  must not be minimized, and no other export window may be open. The renderer draws its own `Frame: N` text in a
  corner, and mesh wireframe edges still show. Switching scene tabs during an export was seen to stall it at 86%.
  The contact sheet fits its camera to the control points, so hair or cloth outside them can be cut off.
- **AI and generated motion.** `generate_motion` overwrites posed keys and creates no undo step, so copy the
  animation first. Its first run in each Cascadeur process loads a model for about 20 to 26 seconds, and calls made
  in that time can time out. `ai_inbetween` needs a Pro license and at least two keys.
- **Physics.** `physics_snap` applies to all frames, and Physics Assistant turns off after a snap, so call
  `physics_enable` again before the next one. `physics_set_priority_frame` needs a frame inside the keyed range.
  Cleaning uses Cascadeur's default settings, and `prepare_keys_by_fulcrums` must run first. Judge a cleaning by the
  `same_point_frames` comparison, not by the total.
- **Retargeting and rigging.** `retarget_animation` needs a Pro or Studio license, works between humanoid rigs, and
  the source must have keys. `quick_rig` is for skeleton-only characters. Enter Rig mode with `import_model` first:
  in one live run, `quick_rig` crashed Cascadeur when it entered Rig mode itself.
- **Mocap.** `video_mocap` has no public Python API. It drives Cascadeur's Mocap dialog and needs the model file
  `mocap.pt` (about 825 MB), which is downloaded on first use. Its motion quality and non-Pro licenses are not
  verified, and it is Windows only.
- **Import and export.** USD is not supported. `import_model` takes FBX and GLB. `import_animation` needs matching
  joint names, so use `retarget_animation` between different skeletons. A failed import can leave the model in the
  scene; do not import it again.
- **Display settings are global.** The viewport display modes are interface state, not scene data. Closing a test
  scene does not restore them.
- **Interface tools.** These are the last resort. Coordinates and layouts change between Cascadeur versions.
- **Several windows.** For about 60 seconds after the default Cascadeur closes, calls can hang for up to 210 seconds
  instead of failing at once.
- **Rig naming.** The aliases were checked on Cascadeur's standard rig and on the Cascy, UE4 Mannequin and Roblox R15
  samples. Mixamo-style names were only unit tested.
- **Not on PyPI yet.** Install from a checkout, as described above.

## Configuration

| Setting | Default | Where |
|---|---|---|
| `CASCADEUR_PLUS_URL` | `http://127.0.0.1:48766` | Proxy environment. Sets the default instance. |
| `CASCADEUR_OFFICIAL_URL` | `http://127.0.0.1:8765` | Proxy environment. |
| `CASCADEUR_DIR` or `CASCADEUR_HOME` | detected | The Cascadeur install folder, used by `setup`, `autostart` and `doctor`. |
| `CASCADEUR_DATA` | the platform's Cascadeur data folder | Data folder, which also holds the mocap model. |
| `CASCADEUR_LOG` | `logs\cascadeur_log.log` in the data folder | The log that `get_log` reads. |
| `CLAUDE_CONFIG_DIR`, `CODEX_HOME` | client defaults | Where `setup` looks for client config files. |

To start or restart MCP Plus with fresh code, run this in Cascadeur's Python Console (or through the script server):

```python
p = r"C:\path\to\cascadeur-mcp-plus\start_mcp_plus.py"
exec(open(p, encoding="utf-8").read(), {"__file__": p})
```

## Troubleshooting

- Run `cascadeur-mcp-plus doctor`. It reports the install, the ports, the servers and the clients, and it exits with 1
  only for real problems.
- **Start script server fails with `WinError 10013`:** another program owns port 8765. Find it with
  `netstat -ano | findstr :8765` and move it.
- **Renders refuse or come out empty:** Cascadeur is minimized. Restore the window, for example with `screenshot`
  and `restore=true`, then try again.
- **"Unavailable in this Cascadeur version":** call `get_status`. It lists the missing API or action for each tool.
- **A call reaches an unexpected window:** call `list_instances` to see every running instance, then pass `instance`.
- **Tools are missing after an update:** restart the MCP client. Run `setup` again if the command path changed, and
  run `autostart enable` again after a package update.

## Development

```bash
python -m unittest discover -s tests -v   # unit tests; no Cascadeur needed
ruff check . && ruff format --check .
python tools/check_readme_tools.py        # README tool tables against the live server and the registry
```

`tools/check_readme_tools.py --offline` compares with the registry only. The in-app package (`cascadeur_mcp_plus/`)
needs Cascadeur's Python, so its live tests in `tests/live/` run inside Cascadeur. After you edit the in-app code,
run `start_mcp_plus.py` again to load it; Cascadeur itself does not need to restart.

A new tool declares `requires` in its registry entry: a `csc.` path, `action:<id>`, or `module:<script>[:function]`.
`get_status` then reports the tool when a Cascadeur build lacks one of them.

## License

[MIT](LICENSE)
