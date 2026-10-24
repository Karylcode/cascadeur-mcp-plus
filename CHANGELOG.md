# Changelog

## 0.5.0 — 2026-10-24

The in-app server now has 105 tools (18 in 0.4.0). The package installs as a `cascadeur-mcp-plus` command.
This release is verified on Windows only.

### New tools

- Scenes: `new_scene`, `open_scene`, `save_scene` (overwrites only with `overwrite=true`), `list_open_scenes`,
  `switch_scene`, `close_scene` (discards unsaved changes only with `discard_changes=true`).
- Import and export: `import_model` (.fbx, .glb), `import_animation`, `list_fbx_takes`, `export_scene` (FBX or
  GLB).
- Characters and selection: `list_characters`, `list_controllers`, `select_objects`, `get_selection`,
  `clear_selection`.
- Timeline and keys: `get_timeline`, `set_frame`, `set_animation_range`, `select_frames`, `set_key`,
  `delete_key`, `list_keys`.
- AutoPosing: `get_autoposing_state`, `autoposing_enable`, `autoposing_update`, `lock_controllers`.
- Poses: `get_pose` and `set_pose` (positions in cm, rotations in degrees; AutoPosing is on by default).
- Interpolation and IK/FK: `set_interpolation`, `set_ik_fk`, `reset_tangents`, `reset_easing`.
- AutoPhysics: `get_physics_state`, `physics_enable`, `physics_freeze`, `physics_set_priority_frame`,
  `physics_show_fulcrums`, `physics_snap`.
- Motion cleanup: `fix_foot_sliding`, `fix_collisions`, `prepare_keys_by_fulcrums`.
- AI inbetweening: `ai_inbetween`, `generate_motion` (both need a Pro license).
- Cycles: `create_cycle`, `remove_cycles`.
- Mirror, copy and reset: `mirror_pose`, `copy_pose`, `paste_pose`, `reset_pose`.
- Animation layers: `list_layers`, `create_layer`, `create_layer_folder`, `delete_layer`, `set_layer_visible`,
  `set_layer_locked`, `rename_layer`, `move_to_layer`.
- Undo: `undo`, `redo`, `get_undo_history` (lists the `MCP:` steps from the Cascadeur log).
- Video mocap (experimental): `video_mocap` writes keyframes from a video of a person through Cascadeur's own
  mocap. Save the scene before calling it (see Known limitations).
- Retargeting: `retarget_animation` copies an animation onto the open target scene (needs Pro or Studio).
- Quick rigging: `list_rig_templates`, `quick_rig`.
- Rendering: `render_image` (PNG), `render_contact_sheet` (several frames on one PNG), `render_video` (MP4),
  `render_image_sequence` (PNG frames).
- Camera: `get_camera`, `set_camera`, `camera_preset` (front, back, left, right, top, three-quarter),
  `frame_objects`, `track_object`.
- Viewport display: `get_display_state`, `set_view_mode`, `set_ghosts`, `set_trajectories`,
  `set_viewport_layout`, `set_silhouette`, `set_surface`.
- Background jobs: `get_job`, `list_jobs`, `cancel_job`. Long tools such as `render_video`, `video_mocap` and
  `physics_snap` return a `job_id` at once; `get_job` returns the result.
- Batches: `batch` runs several tool calls in one request.
- Compatibility: `get_status` (see Compatibility checks).
- Python API search: `api_search` and `api_describe` look up Cascadeur's Python API stubs and examples.
- MCP resources: `cascadeur://scene`, `cascadeur://log`, `cascadeur://timeline`, `cascadeur://controllers`.

### Behavior changes

- Tools that write keys or controller values refuse a locked animation layer. Cascadeur's Python API does not
  enforce layer locks by itself.
- Most scene-changing tools make one undo step named `MCP: <tool>`. A few keep Cascadeur's own step names (see
  Known limitations).
- The stdio proxy waits for a tool call as long as its `timeout` argument asks, up to 630 seconds. It used to
  wait a fixed 120 seconds.
- `tools/call` checks the tool name first. A `KeyError` inside a tool now returns `isError` with the
  traceback.
- `tools/list` is ordered by module file name, so `screenshot` and `project_point` moved to the end.
- The `ui_*` tools, `screenshot` and `project_point` are described as last resorts. The server instructions
  point to `set_pose`, `set_key`, `get_pose`, `render_contact_sheet` and `render_video` first.

### Command line, install and autostart

- New `cascadeur-mcp-plus` command: `serve`, `setup`, `doctor`, `uninstall` and `autostart`. From a checkout,
  `python -I proxy.py` still works.
- `setup` registers the proxy with Claude Code, Codex CLI, Cursor and VS Code. `--dry-run` prints the exact
  changes, and each config file is backed up before it changes.
- `uninstall` removes only what `setup` added.
- `doctor` checks the Cascadeur installation, both ports, the versions, the autostart handler and the detected
  clients. `--json` is available for scripts.
- `autostart status`, `enable` and `disable` (Windows only). `enable` installs one handler into Cascadeur's
  `events/application_started` folder after one UAC prompt. On the first scene idle, the handler starts
  Cascadeur's official script server (127.0.0.1:8765) and MCP Plus (127.0.0.1:48766). `disable` removes the
  handler and restores the file that `enable` replaced. Use `--dry-run` first.
- Security: while the handler is installed, both ports run any Python sent to them, with no authentication.
  Anything on this computer that can reach 127.0.0.1 can run code inside Cascadeur. If Cascadeur starts as
  administrator, both servers run with administrator rights. Run `autostart disable` when you do not need it.
  The one-time elevated step runs the Python that started `autostart enable`. Keep that Python in a folder
  that only administrators can write; if someone can replace it, they gain administrator rights.
- `enable` copies MCP Plus to `%LOCALAPPDATA%\cascadeur-mcp-plus\runtime\<version>\`. After an upgrade, run
  `autostart enable` again; `autostart status` reports when the copy and the installed package differ. `enable
  --runtime <checkout>` runs a source checkout in place, for development only.
- The wheel installs `cascadeur_mcp_plus_cli/` and `cascadeur_mcp_plus_runtime/`. It no longer installs
  top-level `proxy` or `start_mcp_plus` modules, and Cascadeur loads only the runtime folder.
- License metadata uses the SPDX string `MIT`. Building needs setuptools 77 or later.
- Cascadeur's log, install and data folders are found for each platform. Override them with `CASCADEUR_LOG`,
  `CASCADEUR_HOME` (or `CASCADEUR_DIR`) and `CASCADEUR_DATA`.

### Compatibility checks

- Each tool declares what it needs from Cascadeur: API paths, actions and script modules. MCP Plus checks them
  at startup and with `get_status(force=true)`.
- A tool that the installed Cascadeur lacks is marked "Unavailable in this Cascadeur version" in `tools/list`.
  Calling it returns the missing items.
- `get_status` reports the Cascadeur version, the tested version (2026.2.3), the unavailable tools, and the
  behaviors that depend on the version. The server instructions mention the version when it differs.
- The check confirms that names exist. It does not confirm behavior, and it cannot stop a call that crashes
  Cascadeur.

### Documentation and examples

- The README is now a landing page. The details are in `docs/`: Installation, Usage, Tools, How it works,
  Safety, Behavior and limitations, Troubleshooting and Development, each in English and Traditional Chinese.
- `tools/check_readme_tools.py` checks `docs/tools.md` and `docs/tools.zh-TW.md` against the tool registry and
  the live `tools/list`.
- `examples/wave.py` (standard library only) makes a 2-second wave on the Cascy sample and renders it to MP4
  and a contact sheet. `examples/wave.md` explains each step.

### Developer notes

- Run the unit tests with Python 3.11 or later: `python -m unittest discover -s tests`. Live tests need a
  running Cascadeur and run only through `tests/live/run.py`.
- A running server keeps the code it started with, so restart it to load changes. Starting the server from a
  checkout rewrites `tools_manifest.json`.
- A new tool must declare `requires`, or the server refuses to start.
- `live.py restart` can report success while the old server still runs. Check `cascadeur_mcp_plus.__file__`
  with `run_python`.

### Known limitations

- `set_pose` on frames that already have keys: AutoPosing pushes the write back, so `set_pose` falls back to a
  write without AutoPosing (`autoposing_applied: false`). That path takes three undo steps instead of two.
- `set_pose`: a second write to the same frame can be pulled back, so write each pose once. The main
  controller reports no rotation (`null`). Box world rotation is unreliable; use `space="local"`. Empty frames
  have no solver effect.
- AutoPosing lock prompts are recognized by their English text.
- `set_ik_fk` makes no MCP undo step (Ctrl+Z still works), and it also changes the end key of the interval;
  `set_interpolation` does not. Selecting `left_hand` changes the whole `Hand_L` layer (12 objects).
- Undo: IK/FK, cycle, cleanup, AutoLock, retarget and snap steps keep Cascadeur's own names.
  `get_undo_history` lists only `MCP:` steps, and because the Cascadeur log is global, steps from other scene
  tabs can appear. `set_animation_range` and camera changes are not undoable. `generate_motion` rewrites key
  values without an undo step, so back up first.
- Cascadeur clears every undo stack once its memory passes 6000 MB.
- Rendering: `render_image` writes PNG only. Renders need the Cascadeur window visible, and a minimized window
  is refused. A render that times out may still write its file. `samples` maps only LOW, MEDIUM and HIGH. A
  contact sheet holds at most 64 frames, and its automatic framing ignores the mesh. The `Frame: N` text
  cannot be turned off in exported video or contact sheets, and meshes appear as wireframes in the output.
  `render_video` stalls at 86% if a scene tab is switched during the export.
- `video_mocap` needs the mocap model (about 825 MB) in `%LOCALAPPDATA%\Nekki Limited\Cascadeur\resources\`.
  How it treats existing keys, `from_frame` and `to_frame`, and use without a Pro license are not checked.
- `video_mocap` crashed Cascadeur in testing: twice when a job was cancelled while the Mocap window was open,
  and once during a dry run. The root cause is not known. Save the scene before calling it. The live tests for
  these cases are skipped.
- `ai_inbetween` and `generate_motion` need a Pro license. The first `generate_motion` loads its model on
  Cascadeur's GUI thread for 20 to 26 seconds, and `get_scene_info` can time out during that time.
- `retarget_animation` needs Pro or Studio; Basic and Indie licenses are refused. Whether Paste needs a target
  range set first, the undo step count, and whether AutoPosing keys are copied are not checked.
- `import_animation` works only into a scene with the same skeleton, and BVH supports `target="all"` only. USD
  import and export return an error from Cascadeur, although `export_scene` lists `usd`.
- `quick_rig` needs exactly one AnimationInfo and refuses an existing rig unless `replace=true`. It makes
  several undo steps. A run that entered Rig mode by itself once made Cascadeur quit, so call `import_model`
  before `quick_rig`.
- Cleanup: `fix_collisions` ignores shallow floor contact (about 2.6 cm) and clears only deep penetration.
  Before-values need `prepare_keys_by_fulcrums` first; without it the result has only after-values. The
  cleanup tools use Cascadeur's default parameters.
- AutoPhysics: `physics_snap` turns Physics Assistant off, so call `physics_enable` again.
  `physics_show_fulcrums` is refused while the assistant is on. `physics_snap` reports at most 200 changed
  frames; `changed_frame_count` has the total.
- Cycles: `create_cycle` and `remove_cycles` do not check for overlaps, so remove existing cycles first. A
  cycle needs a keyed range of at least two frames. Cascadeur crashed on a one-frame animation.
- Poses: `copy_pose` keeps poses in memory, so they are lost on restart. `mirror_pose` mirrors the whole
  character only. A mirrored `paste_pose` skips box and joint rotations and lists them in `rotations_skipped`.
  `reset_pose` with `kind="default"` does not handle the untwist special case.
- Selection changes are not undo steps. The pivot is the first selected object.
- Scenes: tab indexes change in Rig mode, so address scenes by name or path. `open_scene` does not return a
  top-level `path`; use the path you passed.
- `batch` does not wait for background jobs; poll them with `get_job`.
- Controllers: `cascadeur://controllers` guesses each kind from its name and has no aliases.
  `list_controllers` has no `main_point` in Rig mode. `autoposing_locked` is always `null`.
- Layers: additive layers are not supported.
- Mixamo rig naming is covered by unit tests only.
- Do not call these from `run_python`: `csc.layers.CyclesViewer`, `Viewer.item()`,
  `Folder.children_ordered()`, `RenderToFile.play_to_video_file`, `RenderToFile.play_to_images_sequence` and
  `remove_application_scene`. Each one crashed Cascadeur, and the tools avoid them. Rotating a character with
  `modify_update` turns the controllers and the mesh in opposite directions, and a second `modify_update` in
  the same run raised "Cycle in data dependency graph".
- Compatibility: the check covers names only. The proxy's `initialize` text has no version reminder; only the
  in-app server adds one.
- Two planned tool-description cleanups were skipped: a shared instructions constant and parameter renames.
- Installed copies write `tools_manifest.json` and `resources_manifest.json` next to the installed package. In
  a read-only install they are not written, and the offline tool list stays empty until Cascadeur has started
  once.
- Autostart and mocap file-dialog automation are Windows only.
- An earlier wave run reported a 5.2 cm `position_error_cm`; its cause was not traced. The run for this
  release reported 0.0 cm.

### Not verified

- Only Windows 10 or 11 with Cascadeur 2026.2.3 was tested. The macOS and Linux code paths have never run on a
  real machine; their tests use simulated platforms.
- Video mocap quality on real footage was not checked.
- `video_mocap` live tests: the cancel-while-open test and the dry-run test are skipped because each crashed
  Cascadeur in testing. The other three tests pass.
- `autostart enable` and `autostart disable` were not run through UAC on the real Cascadeur folder; they were
  tested with a fake installation.
- The MP4 and the contact sheet from `examples/wave.py` were checked by their numbers, not by eye.
- The package is not on PyPI yet, so installing from an index with `uvx cascadeur-mcp-plus` is untested.

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
