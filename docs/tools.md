**English** | [繁體中文](tools.zh-TW.md) · [Documentation index](README.md)

# Tools

MCP Plus has 105 tools. They are grouped by topic below. Each tool has a one-sentence summary, a table of parameters with their defaults and notes, and a description of what it returns.

- **Scene and files:** `new_scene`, `open_scene`, `save_scene`, `list_open_scenes`, `switch_scene`, `close_scene`, `import_model`, `import_animation`, `list_fbx_takes`, `export_scene`, `get_scene_info`, `get_object_info`
- **Characters and selection:** `list_characters`, `list_controllers`, `select_objects`, `get_selection`, `clear_selection`, `enter_rig_mode`, `list_rig_templates`, `quick_rig`, `retarget_animation`
- **Pose and keyframes:**
  - **AutoPosing and poses:** `get_pose`, `set_pose`, `mirror_pose`, `copy_pose`, `paste_pose`, `reset_pose`, `get_autoposing_state`, `autoposing_enable`, `autoposing_update`, `lock_controllers`
  - **Timeline and keys:** `get_timeline`, `set_frame`, `set_animation_range`, `set_key`, `delete_key`, `list_keys`, `select_frames`, `create_cycle`, `remove_cycles`
  - **Interpolation and IK/FK:** `set_interpolation`, `set_ik_fk`, `reset_tangents`, `reset_easing`
  - **Animation layers:** `list_layers`, `create_layer`, `create_layer_folder`, `delete_layer`, `set_layer_visible`, `set_layer_locked`, `rename_layer`, `move_to_layer`
- **Physics and AI:** `get_physics_state`, `physics_enable`, `physics_snap`, `physics_freeze`, `physics_set_priority_frame`, `physics_show_fulcrums`, `fix_foot_sliding`, `fix_collisions`, `prepare_keys_by_fulcrums`, `ai_inbetween`, `generate_motion`, `video_mocap`
- **Review and output:** `render_image`, `render_contact_sheet`, `render_video`, `render_image_sequence`
- **Camera and display:** `get_camera`, `set_camera`, `camera_preset`, `frame_objects`, `track_object`, `get_display_state`, `set_view_mode`, `set_ghosts`, `set_trajectories`, `set_viewport_layout`, `set_silhouette`, `set_surface`
- **UI control (last resort):** `ui_tree`, `ui_click`, `ui_drag`, `ui_hover`, `ui_key`, `ui_type`, `ui_set_property`, `ui_invoke`, `dialogs`, `screenshot`, `project_point`
- **Other:** `get_status`, `api_search`, `api_describe`, `run_python`, `get_log`, `batch`, `get_job`, `list_jobs`, `cancel_job`, `undo`, `redo`, `get_undo_history`, `list_actions`, `call_action`

Each result is a JSON object. Most tools return `"ok": true` on success. A failure returns `"ok": false` with an `error` message and usually a `hint`; follow the hint. Some tools, such as `get_scene_info`, return their data without `ok`. A call that cannot run at all, such as an unknown tool name, an argument of the wrong type or a timeout, comes back as a tool error with a message.

## Common parameters

| Parameter | Meaning |
|---|---|
| `window` | Window index or title substring. Default: the focused window, else the main Cascadeur window. `ui_tree` lists all windows when it is omitted. |
| `ref` | Element ref from the latest `ui_tree` (such as `0:12`) or `dialogs` (such as `d0:3`) result. Refs reset on every `ui_tree` or `dialogs` call. |
| `query` | Text, tooltip, actionId, objectName or placeholder text of an on-screen element. Exact matches rank first, then clickable and enabled elements. |
| `settle_ms` | Milliseconds to wait for the UI after the action, at most 5000. Default 150; `ui_drag` uses 300. Listed in the schemas of `ui_click`, `ui_drag`, `ui_hover`, `ui_key`, `ui_type` and `call_action`. |
| `start`, `end`, `frame`, `frames` | Whole frame numbers from 0. A numeric string such as `"120"` is accepted too. |
| `controllers` | Controller names or aliases, as a list or as one string. `list_controllers` lists them. |

Positions are in centimetres and rotations in degrees (Euler XYZ). Object names are Cascadeur object names or aliases such as `left_hand`, `right_foot`, `pelvis` or `head`.

## Scene and files

### `new_scene`

Opens a new, empty scene tab and makes it the current scene. Use it to start from scratch without replacing any open scene.

No parameters.

Returns `scene` (the new tab: `index`, `name`, `path`, `current`, `modified`) and `open_scenes`, the number of open tabs.

### `open_scene`

Opens a `.casc` file in a new scene tab and makes it the current scene. It never loads into the current tab, so an open scene is not replaced.

| Parameter | Default | Notes |
|---|---|---|
| `path` | required | Absolute path to an existing `.casc` file. A relative path, another extension or a missing file is refused. |

Returns `scene` (the new tab, with the same fields as in `new_scene`) and `open_scenes`. If Cascadeur cannot load the file, the call returns an error, the previous tab becomes current again and the empty tab is closed.

### `save_scene`

Saves the current scene back to its own file, or to a new `.casc` file when `path` is given. Replacing an existing file, including the scene's own file, needs `overwrite=true`.

| Parameter | Default | Notes |
|---|---|---|
| `path` | the scene's own file | Absolute `.casc` path in an existing folder. Omit it to save back to the scene's own file; a scene that was never saved has no file and needs a `path`. |
| `overwrite` | `false` | Must be `true` to replace an existing file. Saving back to the scene's own file always needs it. |

Returns `saved_to` (the path written), `overwritten` (whether a file already existed there) and `scene` (the tab: `index`, `name`, `path`, `current`, `modified`).

### `list_open_scenes`

Lists every open scene tab. Use it to choose the index or name for `switch_scene` and `close_scene`.

No parameters.

Returns `scenes` (one entry per tab, in tab order: `index`, `name`, `path`, `current`, `modified`), `count` and `current` (the index of the current tab, `-1` if none). `modified` is `true` or `false` when Cascadeur exposes an unsaved-changes flag and `null` otherwise.

### `switch_scene`

Makes an open scene tab the current scene. Call `list_open_scenes` first: an index is only valid for the tab order at that moment.

| Parameter | Default | Notes |
|---|---|---|
| `scene` | required | An integer tab index from `list_open_scenes`, or a string: the exact scene name or the scene's file path. Refused: an index out of range, an unknown name, a name shared by several tabs (use the index instead) and a boolean. |

Returns `scene` (the tab: `index`, `name`, `path`, `current`, `modified`) and `was_current`, which is `true` when that tab was already current.

### `close_scene`

Closes one scene tab and leaves every other tab alone. Use it when a temporary or test tab is no longer needed. A scene with unsaved changes stays open unless `discard_changes` is `true`; the scene is never saved.

| Parameter | Default | Notes |
|---|---|---|
| `scene` | required | Tab index (integer), or exact scene name or file path (string); the same rules as in `switch_scene`. |
| `discard_changes` | `false` | `true` closes a scene that has unsaved changes and throws the changes away. `false` leaves such a scene open and returns an error. |

Returns `closed` (the closed tab: `index`, `name`, `path`, `current`, `modified`), `discarded` (`true` when Cascadeur's closing prompt appeared and was answered with *Don't Save*) and `prompt` (the prompt's details, `null` when no prompt appeared).

### `import_model`

Imports an FBX or GLB model into the current scene and enters Rig mode, the way Cascadeur's File > Import does. Use it to load a character before Quick Rigging: a bare Python import skips the `RigInfo` step, and Quick Rigging crashes without it. USD files are refused because Cascadeur's Python API has no USD importer.

| Parameter | Default | Notes |
|---|---|---|
| `path` | required | Absolute path to an existing `.fbx` or `.glb` file. A `.gltf` file goes to the GLB importer, but it has not been tested. Other extensions, including USD, are refused. |
| `enter_rig_mode` | `true` | Enter Rig mode after the import, only when the scene has no `RigInfo`. `false` skips that step. |
| `timeout` | `60` | Seconds to wait for the import and the Rig mode step; capped at `600`. |

Returns `path`, `format` (`fbx` or `glb`), `objects_added`, `joints` and `meshes` (counts), `animation_info` and `rig_info` (behaviour counts), `rig_mode` (`true` when the scene has exactly one `AnimationInfo`) and `undo_steps`. This is not one undo step: Cascadeur's loader records its own undo command and entering Rig mode records another, so `undo_steps` (1 or 2) is how many undos revert the whole call. When the "Rig mode helper" dialog opens, `next` says how to answer it. `rig_error` carries a Rig mode problem without failing the call. If the call fails after the model was imported, the error has `imported: true`: the model is already in the scene, so do not import it again.

### `import_animation`

Applies the animation stored in an FBX or BVH file to the current scene's character. Use `list_fbx_takes` first to choose a take, and `retarget_animation` instead when the target skeleton has different joint names. Cascadeur's BVH command replaces the keyed animation on the layers it writes, so use FBX for a partial import.

| Parameter | Default | Notes |
|---|---|---|
| `path` | required | Absolute path to an existing `.fbx` or `.bvh` file. Other extensions, including USD, are refused. |
| `take` | the file's default take | FBX only; ignored for BVH. A take name (case-insensitive) or a 0-based index (integer) from `list_fbx_takes`. An unknown name or an index out of range is refused. |
| `target` | `"all"` | `all` (every joint), `selected_objects` (only the selected objects) or `selected_frames` (the selected timeline frames). BVH supports `all` only. |
| `timeout` | `60` | Seconds to wait for the import; capped at `600`. |

Returns `path`, `format`, `target`, `take` (the take name used, `null` for the default), `before` and `after` (each with the animation's `first_frame`, `last_frame`, `size` and `last_key`), `changed` (whether the sampled joint poses or the last key changed) and `undo_steps` (1: one native undo command). When `changed` is `false`, `hint` says to compare the file's joint names with the scene's. Cascadeur reports a refused paste only in its log, so a logged failure such as `Import animation: failed` or `No BVH joint names matched` comes back as `ok: false` with `marker` and the log lines in `log`.

### `list_fbx_takes`

Lists the animation take names inside an FBX file, in file order. Use it before `import_animation` to pass an exact take name. Takes exist only in FBX files; GLB holds a single animation.

| Parameter | Default | Notes |
|---|---|---|
| `path` | required | Absolute path to an existing `.fbx` file. Other extensions are refused. |
| `timeout` | `60` | Seconds to wait; capped at `600`. |

Returns `path`, `takes` (the take names in file order) and `count`.

### `export_scene`

Exports the current scene to an FBX or GLB file. An existing target is moved aside before the export and put back if the export fails, so a failed export is never reported as a success with the old file's size. USD export has no Python API in Cascadeur 2026.2.3 and is refused.

| Parameter | Default | Notes |
|---|---|---|
| `path` | required | Absolute output path ending in `.fbx` or `.glb`, in an existing folder. |
| `format` | from the file extension | `fbx`, `glb` or `usd`; it must match the extension. `usd` is always refused. |
| `objects` | `"all"` | `all` (every object) or `selected` (only the selected objects). |
| `frames` | `"all"` | `all` (the whole animation) or `selected` (only the selected timeline frames). |
| `overwrite` | `false` | `true` replaces an existing file; otherwise an existing file is refused. |
| `timeout` | `60` | Seconds to wait for the export; capped at `600`. |

Returns `path`, `format`, `bytes` (the size of the written file), `objects` and `frames` (the options used) and `method` (the Cascadeur exporter that ran). It returns an error before writing anything when Cascadeur's export license is not available.

### `get_scene_info`

Summarizes the current scene. Use it first to learn what is open, then `list_controllers` for a character's control points or `get_timeline` for the frame range.

| Parameter | Default | Notes |
|---|---|---|
| `max_objects` | `80` | Maximum number of objects listed in `objects.sample`. |

Returns `scene` (`name`, `path`, `open_scenes`, `gravity_per_frame`), `frames` (`current`, `first`, `last`, `animation_size`), `objects` (`count`, `by_type` and `sample`, a list of `name` and `type`), `selection` (`count` and up to 50 `names`), `behaviours` (the number of `RigInfo`, `AnimationInfo`, `QrtInfo`, `Joint`, `MeshObject` and `AutoPosing` behaviours; a type that this Cascadeur version does not define is left out) and `license` (the flags `pro_features` and `export`).

### `get_object_info`

Describes objects by name: type, id, and behaviours with their property names. Use it for objects that `list_controllers` does not cover (a mesh, camera or helper), or to inspect behaviours before `run_python`.

| Parameter | Default | Notes |
|---|---|---|
| `name` | required | Cascadeur object name. An exact name is tried first; if nothing matches, a case-insensitive partial name. Aliases are not resolved. |
| `max_properties` | `40` | Maximum number of property names listed per behaviour. |

Returns `objects` (at most 5; each with `name`, `type`, `id` and `behaviours`, a list of `name` and `properties`) and `matches`, the number of matching objects.

## Characters and selection

### `list_characters`

Lists the scene's characters: the roots that have a `RigInfo` behaviour or, when the scene has none, an `AnimationInfo` behaviour. Use it first to learn which character or rig the scene contains, then pass a returned name (or a root joint or mesh name) as the `character` argument of the tools that take one. Read-only.

No parameters.

Returns `count` and `characters`. Each character has `name`, `mode` (`rig` or `animation`), `root_joint`, `info` (the name of its `RigInfo` or `AnimationInfo` object), `meshes` (mesh names), `controllers` (the number of control points) and `joints` (the number of joints). When the scene has no character, `hint` says how to get one.

### `list_controllers`

Lists a character's control points (main points, direction controllers, boxes, joints and other rig objects) with alias, kind, world position and AutoPosing lock state. Use it to find the control point to move instead of guessing internal names such as `mixamorig:LeftHand_MainPoint`. Most tools that take controllers accept an alias such as `left_hand` in place of the internal name.

| Parameter | Default | Notes |
|---|---|---|
| `character` | all characters | A name from `list_characters`; a root joint or mesh name also works. Matching is case-insensitive, exact first, then partial. An unknown or ambiguous name is refused with candidates. |
| `kinds` | all kinds | Only these kinds: `main_point`, `direction`, `box`, `joint`, `other`. A list, or one kind as a string. An unknown kind is refused. |

Returns `count`, `controllers` and `characters` (the names of the characters in the result). Each controller has `name`, `alias`, `kind`, `type` (Cascadeur's object type: `Point`, `Box`, `Limb Direction`, `Joint` or `Center of Mass`), `position`, `autoposing_locked` and `character`. `position` is `[x, y, z]` in world coordinates, in centimetres (`cm`), at the current frame. `alias` is `pelvis`, `chest`, `head`, `neck`, `center_of_mass`, or `left_` or `right_` followed by `hand`, `elbow`, `shoulder`, `foot`, `knee` or `toe`; it is `null` for objects without one. `autoposing_locked` is always `null` because the Python API of Cascadeur 2026.2.3 does not expose the lock.

### `select_objects`

Selects scene objects (control points, joints, boxes, meshes, ...) by name or alias, as clicking them in the viewport does. Use it for tools that act on the object selection, such as AutoPosing locking or camera framing; timeline actions take their range from `select_frames`, and most other tools select the controllers they need from their own arguments. Selection changes do not create undo steps.

| Parameter | Default | Notes |
|---|---|---|
| `names` | required | Object names, aliases or partial names, for example `["left_hand", "right_hand"]`; a single string also works. Each name must match exactly one object: an ambiguous name is refused with `candidates`, an unknown one with the closest names. |
| `mode` | `"replace"` | `replace` (replace the selection), `add` (add to it) or `remove` (deselect the named objects). |

Returns `mode`, `requested` (the names after empty entries and duplicates are dropped), `count` (the number of objects selected afterwards), `names` and `selection`. Each `selection` entry has `name`, `alias`, `kind`, `type`, `character` and `id`. When Cascadeur's selection does not match the requested set, `hint` says so.

### `get_selection`

Lists the current selection with each object's name, alias, kind, Cascadeur type, character and id. Use it to check what an action will act on, or to see the result of `select_objects`. Read-only.

No parameters.

Returns `count`, `names` and `selection`. Each entry has `name`, `alias`, `kind` (`main_point`, `direction`, `box`, `joint` or `other`; `null` for objects that are not rig controllers, such as meshes), `type`, `character` and `id`. Tool objects such as AutoPosing points have no model name: they are listed with `tool_object: true` and a `name` of the form `<Type id>`.

### `clear_selection`

Deselects every object in the active scene, as clicking empty space in the viewport does. Use it before building a new selection, or to stop an action from acting on an old one. Selection changes do not create undo steps.

No parameters.

Returns `count` (0 on success), `names` and `selection` (both empty on success). If part of the selection remains, `hint` says to check that no dialog is blocking Cascadeur.

### `enter_rig_mode`

Enters Cascadeur's Rig mode through its own `rig_mode.on` entry points. Use it after `import_model` or before Quick Rigging: generating rig elements outside Rig mode crashes Cascadeur. It runs on the scene idle event, like the official script server.

| Parameter | Default | Notes |
|---|---|---|
| `color` | `[0, 0.5, 0]` | Rig color as a list of RGB numbers, each from 0 to 1. |

Returns `rig_mode`, `changed`, `path` and `animation_info`. A scene with exactly one `AnimationInfo` is already in Rig mode: nothing changes and there is no `path`. More than one `AnimationInfo` is an error; resolve it by hand before rigging. Otherwise `path` is `run_raw` when a `RigInfo` exists, or `run_after_import` for a freshly imported model, which creates the `RigInfo`, enters Rig mode and opens the "Rig mode helper" dialog; `next` then says to press its *Yes* button with `dialogs` and `ui_click`.

### `list_rig_templates`

Lists the Quick Rigging templates installed with Cascadeur (`resources/autorig_templates`, `*.qrigcasc` and the older `*.qrt`). Use it before `quick_rig` when the automatic template match is wrong or ambiguous.

No parameters.

Returns `directory` (the folder that was read), `count` and `templates`. Each template has `name`, `file`, `path` (absolute), `format`, `readable`, `joints` (the number of named joints), `bones`, `sections` (the body sections) and `error` (`null` when the template is readable).

### `quick_rig`

Creates a Cascadeur rig (rig elements, control points and, optionally, AutoPosing) for a bare skeleton with Quick Rigging. It first makes sure the scene is in Rig mode, because generating rig elements outside Rig mode crashes Cascadeur 2026.2.3, then loads a template, adds the rig elements, presses **Generate rig** and closes Quick Rigging. Use it after `import_model` on a skeleton that has no rig.

| Parameter | Default | Notes |
|---|---|---|
| `template` | closest match | Template name, file name or absolute `.qrigcasc` (or `.qrt`) path; see `list_rig_templates`. Omitted: the template whose joint names match the scene best, provided at least 5 of its joints, and at least 60% of them, exist in the scene. An unknown or ambiguous name, no close enough match, or a tie between templates returns an error with `candidates`. |
| `mirror_plane` | Quick Rigging's current setting | `XY`, `XZ`, `YZ`, `Undef`, or an integer from 0 to 3 (XY=0, XZ=1, YZ=2, Undef=3). Other values are refused. |
| `create_autoposing` | `true` | Create AutoPosing together with the rig. |
| `replace` | `false` | `true` rebuilds a scene that already has a `RigInfo` or prototype rig elements. `false` refuses such a scene, so a half-made rig is never finalized silently. |
| `enter_rig_mode` | `true` | Enter Rig mode when the scene is not in it. `false` requires the scene to be in Rig mode already (exactly one `AnimationInfo`); otherwise the call is refused. |
| `timeout` | `60` | Seconds to wait for Quick Rigging; capped at `600`. |

Returns `template` (`name`, `path`, `auto_selected`, the joint counts `matched` and `template_joints`, and `candidates`), `rig` (the new character: `name`, `root_joint`, `controllers`, `joints`, `meshes`), `controllers` (`total`, `main_points`, `by_kind`, `names`), `autoposing` (`requested`, `created`), `rig_mode` (`entered`, `path`, `animation_info`), `rig_info`, `animation_info`, `template_applied`, `warnings`, `undo_commands` and `undo_steps`. Success means the template was applied and the scene has exactly one `RigInfo` and no `AnimationInfo`. This is not one undo step: Rig mode, rig-element creation and Rig mode off each record a Cascadeur command, which `undo_commands` lists and `undo_steps` counts. An error raised while Quick Rigging runs carries `stage` (where the call stopped), `warnings`, `undo_commands` and `log`; invalid arguments, a scene without joints and a template that cannot be chosen are refused before anything runs.

### `retarget_animation`

Copies animation from one scene to another with Cascadeur's Edit > Retargeting Copy and Paste actions, for characters with different rigs. Use it when a take was made for another skeleton and `import_animation` cannot map its joints. It needs a Pro or Studio license, two different scene tabs that each have exactly one `RigInfo`, and the same model type in both.

| Parameter | Default | Notes |
|---|---|---|
| `source` | required | Name of an open scene tab, or an absolute `.casc` path; a path that is not open is opened in a temporary tab and closed afterwards. `.fbx`, `.glb`, `.gltf`, `.bvh` and USD files are refused: import the animation into a scene with the same skeleton first. |
| `target` | current scene | Name of an open scene tab, or the absolute `.casc` path of an open scene. It must already be open and differ from `source`; the pasted keys stay in that tab and are not saved to disk. |
| `start` | source's first frame | First source frame to copy (inclusive), a whole frame number. |
| `end` | source's last frame | Last source frame to copy (inclusive), a whole frame number. `start` must not be after `end`, and both must lie inside the source's animation range. |

Returns `source` and `target`, each with `scene` (the tab name), `path`, `frames` (`start`, `end`, `count`), `boundary`, `rig_info` and `animation_info`. `target` also has `before` (its frame range, `animation_size` and `last_key` before the paste), `animation_size` and `last_key` after the paste, and `pose`, whose `changed_joints` and `all_changed` compare sampled pelvis, hand and foot joints before and after. `seconds` is the elapsed time. If no sampled joint changed, `hint` suggests checking the result with `render_image`. Before pasting, the tool selects the same `start` to `end` frames in the target and extends the target's last frame to `end` if it ends earlier. A source with no animation in a multi-frame range is refused before anything is copied. If the temporary source tab cannot be closed after a successful paste, the call returns an error with `retargeted: true`. Cascadeur's native Copy and Paste record their own undo commands, not an `MCP:` step.
## AutoPosing and poses

### `get_pose`

Reads the current pose as numbers: for each controller its position in cm and its rotation as Euler XYZ degrees plus a quaternion, at a frame, in world or local space. Use it before `set_pose` to see the current values, or after it to check that a change stuck. Read-only: it never changes the scene, the selection or the playhead.

| Parameter | Default | Notes |
|---|---|---|
| `controllers` | all main control points | Names or aliases; a list or one string. |
| `frame` | current frame | A whole number from 0. Any frame can be read; the playhead does not move. |
| `space` | `"world"` | `world` reads the global position and rotation; `local` reads the local ones. |

Returns `frame`, `current_frame`, `space`, `count` and `controllers`. Each controller has `name`, `alias`, `kind`, `character`, `position` (cm), `rotation` (Euler XYZ degrees) and `quaternion`. In Cascadeur 2026.2.3 main control points have no rotation data, so for them `rotation` and `quaternion` are `null` and `rotation_available` is `false`; boxes (such as `hand_Box_l`) and joints do have rotations. A controller with no values at the frame yet reports the closest readable frame in `frame_used`.

### `set_pose`

Sets the pose with numbers instead of dragging controllers: it writes control-point positions (cm) and rotations (degrees) at a frame, keys them and, by default, lets AutoPosing pose the rest of the body. Use it for every pose change; do not drag controllers with `ui_drag`. Moving the pelvis also moves the feet through the rig hierarchy before AutoPosing runs, even when the feet are AutoPosing-locked.

| Parameter | Default | Notes |
|---|---|---|
| `values` | required | Object that maps a controller name or alias to `position` `[x, y, z]` (absolute, cm), `offset` `[dx, dy, dz]` (cm, added to the value at that frame) and/or `rotation` `[rx, ry, rz]` (degrees, Euler XYZ), all in `space`. Give `position` or `offset`, not both; `rotation` can go with either. In Cascadeur 2026.2.3 main control points take no rotation; rotate boxes and joints instead. Must not be empty. |
| `frame` | current frame | A whole number from 0. The playhead does not move. |
| `autoposing` | `true` | `true` locks the frame with `AutoPosing.Autolock`, writes only the given controllers, then runs the AutoPosing update so the body follows; AutoPosing is switched on first if it is off. `false` writes only the given values and leaves every other control point alone. |
| `key` | `true` | `true` keys the written controllers at the frame. `false` changes existing keys only, so the frame must already have data. |
| `space` | `"world"` | `world` or `local`. |
| `tolerance_cm` | `1.0` | Greater than 0. Largest miss in cm allowed for each requested position before the call falls back to writing without AutoPosing; rotations are held to 1.0 degree. |
| `answer` | `"yes"` | Answer for Cascadeur's "Adjust AutoPosing lock state?" prompt: `yes` (Autolock: locks the points whose pose differs from the AutoPosing prediction), `no` (keep the current locks) or `cancel` (close the prompt without changing locks). |

Returns `frame`, `space`, `keyed`, `keys`, `controllers`, `adjusted`, `autoposing`, `autoposing_applied`, `fallback`, `extended_to` and `undo_steps`. Each entry of `controllers` has the `requested` values, the `before` and `after` values, and `position_error_cm` and `rotation_error_deg` against the request; `adjusted` lists the other main points that AutoPosing moved, with `moved_cm`; `extended_to` is the frame the layers were extended to when the scene had no data at `frame` yet. If a controller misses by more than `tolerance_cm` (or 1 degree) after the AutoPosing update, the same call writes all the requested values again without AutoPosing: `autoposing_applied` is then `false`, `warning` is set, each entry also has `position_error_cm_before_fallback` and `rotation_error_deg_before_fallback`, and the rest of the body does not follow on that frame. If a controller still misses, `ok` is `false` and `missed` lists it. The write, the keys and the AutoPosing update are one undo step, `MCP: set_pose`; Cascadeur's own Autolock step cannot be merged into it, so a full undo takes two undos (three after a fallback) and `undo_steps` lists them, newest first. Refused when a controller is on a locked layer.

### `mirror_pose`

Mirrors the character's pose across its mirror plane at a frame, so a raised left arm becomes a raised right arm. It uses Cascadeur's own MirrorTool, so boxes and joints rotate correctly, and it always mirrors the whole character: `controllers` only chooses which mirrored controllers the result reports. One call mirrors one character.

| Parameter | Default | Notes |
|---|---|---|
| `frame` | current frame | A whole number from 0. The frame must already have data; key it first with `set_pose` or `set_key`. The playhead is not moved. |
| `controllers` | all main control points | Names or aliases; a list or one string. Only chooses what the result reports. In a scene with several characters, pass controllers of one character and call once per character. |

Returns `frame`, `character`, `scope`, `plane` (the world-space plane the mirror used), `controllers`, `unpaired`, `max_symmetry_error_cm` and `undo`. Each entry of `controllers` has `name`, `mirror_of`, `before_position`, `after_position`, `expected_mirror_position` (cm) and `symmetry_error_cm`. The undo step is Cascadeur's native `Mirror` step, not an `MCP:` step; one undo restores the pose. Refused when the frame has no data, when a controller is on a locked layer, or when the scene has several characters and the controllers do not belong to exactly one of them.

### `copy_pose`

Copies the pose at a frame into MCP Plus's own pose store, so `paste_pose` can write it at another frame. Positions are stored in cm (world) and rotations in degrees (local); the copies live in MCP Plus's memory and are lost when the server restarts. Read-only: it never changes the pose, the keys or the playhead.

| Parameter | Default | Notes |
|---|---|---|
| `frame` | required | A whole number from 0. The frame must already have data; key it first. |
| `controllers` | all main control points | Names or aliases; a list or one string. |

Returns `name` (a new copy: `pose_1`, `pose_2` and so on), `frame`, `count`, `controllers`, `planes` and `stored`. Each entry of `controllers` has `name`, `alias`, `kind`, `character`, `position`, `rotation` and `quaternion`; main control points have no rotation, so their `rotation` is `null`. `planes` holds the mirror plane of each character at the copied frame, which `paste_pose` with `mirrored` set to `true` reflects around; `stored` is the number of copies held.

### `paste_pose`

Pastes a pose copied with `copy_pose` at another frame, as one undo step. Positions are written in world space (cm) and rotations in local space (degrees). Stored names are matched exactly (full object names, not aliases), so the scene needs the same controller names as the one the pose was copied from.

| Parameter | Default | Notes |
|---|---|---|
| `frame` | required | A whole number from 0. |
| `name` | most recent copy | The name `copy_pose` returned, for example `pose_1`. |
| `mirrored` | `false` | `true` mirrors the stored positions across the mirror plane stored with the copy. Stored box and joint rotations are not mirrored; use `mirror_pose` on the live rig for those. |
| `key` | `true` | `true` keys the pasted controllers at the frame. `false` changes existing keys only, so the frame must already have data. |

Returns `name`, `source_frame`, `frame`, `mirrored`, `keyed`, `controllers`, `count`, `skipped`, `rotations_skipped`, `passes`, `extended_to`, `keys` and `undo` (`MCP: paste_pose`). Each entry of `controllers` has `name`, `source`, `requested_position`, `after_position` and `position_error_cm`, plus the rotation fields when a rotation was pasted. Boxes and joints are written in a second pass after the main points (`passes` is then 2); `skipped` lists controllers with no mirror counterpart or no stored position, and `extended_to` is the frame the layers were extended to when the scene had no data at `frame` yet. Fails when no pose has been copied yet or `name` is unknown; refused when a target is on a locked layer.

### `reset_pose`

Resets controllers to the rig's default T-pose, or to the bind pose of the skinned meshes, at a frame, as one undo step. The default T-pose is usually not the pose the scene was saved in.

| Parameter | Default | Notes |
|---|---|---|
| `kind` | `"default"` | `default` writes the rig's stored T-pose (main control points get their T-pose world position, boxes and joints their local transforms, like Cascadeur's Go to T-pose command). `bind` writes joint transforms from the skinned meshes' inverse bind matrices, like Go to Bind Pose. |
| `controllers` | depends on `kind` | Names or aliases; a list or one string. When omitted, `default` resets every main control point, box and joint, and `bind` resets every joint. Controllers with no T-pose or bind data are left unchanged and listed in `skipped`. |
| `frame` | current frame | A whole number from 0. The frame is keyed, and made writable when needed. The playhead is not moved. |

Returns `kind`, `frame`, `count`, `controllers`, `skipped`, `rotation_error_max_deg`, `extended_to`, `keys` and `undo` (`MCP: reset_pose`). Each entry of `controllers` has `name`, `kind`, `after_position`, `expected_position` and `position_error_cm` (cm), plus `rotation_error_deg` when the pose stores a rotation. Boxes and joints are written in a second pass after the main points. Refused when a controller is on a locked layer.

### `get_autoposing_state`

Reports whether AutoPosing is on in the current scene, read from the AutoPosing toolbar button in Cascadeur's interface. Use it before `set_pose` or `autoposing_update`. Read-only.

No parameters.

Returns `enabled`, `known`, `action`, `frame`, `sources` and `note`, plus `locked_controllers` (always `null`) and `locked_known` (always `false`): Cascadeur 2026.2.3 has no Python query for the locked set, so check a lock by its effect on positions. Fails when the switch cannot be read from the interface, for example when no rigged scene is open.

### `autoposing_enable`

Turns AutoPosing on or off. It reads the switch first and only changes it when it differs, so calling it twice has the same result. It also answers Cascadeur's "Adjust AutoPosing lock state?" prompt, so no dialog is left open.

| Parameter | Default | Notes |
|---|---|---|
| `enabled` | `true` | `true` turns AutoPosing on, `false` turns it off. |
| `answer` | `"yes"` | Answer for Cascadeur's "Adjust AutoPosing lock state?" prompt: `yes` (Autolock: locks the points whose pose differs from the AutoPosing prediction), `no` (keep the current locks) or `cancel` (close the prompt without changing locks). |

Returns `requested`, `enabled` (the state afterwards), `before`, `changed` and `dialog` (what happened to the prompt; `null` when none appeared). When the switch already has the requested state, `changed` is `false` and no action is triggered.

### `autoposing_update`

Recomputes the pose of the current frame from the current AutoPosing locks (Cascadeur's "Update AutoPosing" operation), as one undo step. Only AutoPosing-locked points keep the positions a script wrote; unlocked points go back to AutoPosing's prediction, so lock the frame first (see `lock_controllers`). It also answers the "Adjust AutoPosing lock state?" prompt, so no dialog is left open.

| Parameter | Default | Notes |
|---|---|---|
| `answer` | `"yes"` | Answer for Cascadeur's "Adjust AutoPosing lock state?" prompt: `yes` (Autolock: locks the points whose pose differs from the AutoPosing prediction), `no` (keep the current locks) or `cancel` (close the prompt without changing locks). |

Returns `frame`, `undo` (`MCP: autoposing_update`), `enabled` (whether AutoPosing is on afterwards), `enabled_before` and `dialog`. When AutoPosing is off the pose may not change, so call `autoposing_enable` first. Refused while any control point of a character is on a locked layer.

### `lock_controllers`

Locks AutoPosing controllers for the current frame, so AutoPosing keeps their position when other points move. It selects the named controllers, runs Cascadeur's `AutoPosing.Autolock` command and restores the selection; Cascadeur chooses the full lock set of the frame, so more points than the named ones can be locked. It also answers the "Adjust AutoPosing lock state?" prompt.

| Parameter | Default | Notes |
|---|---|---|
| `names` | required | Controller names or aliases, as a list. |
| `locked` | `true` | `true` locks. `false` is refused: Cascadeur 2026.2.3 has no unlock action for model control points. |
| `answer` | `"yes"` | Answer for Cascadeur's "Adjust AutoPosing lock state?" prompt: `yes` (Autolock: locks the points whose pose differs from the AutoPosing prediction), `no` (keep the current locks) or `cancel` (close the prompt without changing locks). |

Returns `locked`, `action`, `count`, `controllers` (each with `name` and `id`), `scope`, `undo` (`AutoPosing.Autolock`), `readback` and `dialog`. `readback` is always `null` because the lock state cannot be read back.

## Timeline and keys

### `get_timeline`

Returns the timeline state of the current scene: the playhead, the animation range, the visible range, the length and the frame rate. Use it before switching frames or changing the animation range. Read-only.

No parameters.

Returns `frame` (the real playhead position, even outside the animation range), `first_frame` and `last_frame` (the animation range), `first_visible_frame` and `last_visible_frame`, `length` (in frames), `fps` and `seconds`. `fps` is `null` when this Cascadeur version does not expose it, and `seconds` is only added when `fps` is known.

### `set_frame`

Moves the timeline playhead to a frame. Use it before an action that works on the current frame. A frame outside the animation range is still reached, but keys there are not visible on the timeline.

| Parameter | Default | Notes |
|---|---|---|
| `frame` | required | A whole number from 0. |

Returns `frame` (where the playhead is afterwards) and `requested`. When the frame is outside the animation range, a hint suggests widening the range with `set_animation_range`.

### `set_animation_range`

Sets the animation's first and/or last frame, which widens or shortens the timeline. Use it before keying frames beyond the current animation range. The range is a scene setting, not model data: undo does not revert it, so call the tool again with the old values to restore it.

| Parameter | Default | Notes |
|---|---|---|
| `first` | — | A whole number from 0. Omit to keep the current first frame. At least one of `first` and `last` is required. |
| `last` | — | A whole number from 0. Omit to keep the current last frame. Must not be less than the first frame. |

Returns `requested_first`, `requested_last`, `first_frame`, `last_frame`, `first_visible_frame`, `last_visible_frame`, `frame` (the playhead, which can move when the range shrinks) and `undoable` (always `false`). The range is saved with the scene; when Cascadeur adjusts the requested range, a hint says so.

### `set_key`

Adds keyframes on the given frames for the given controllers, or for the whole character when `controllers` is omitted. Use it to pin a pose before interpolation, AI inbetweening or cleanup. The whole call is one undo step, `MCP: set_key`.

| Parameter | Default | Notes |
|---|---|---|
| `frames` | required | Frame numbers (a whole number or a list of them). Accepts `"0,10,20"`. |
| `controllers` | all controllers | Names, aliases or partial names; a list or one string. |

Returns `frames`, `controllers` (the matched names), `layers` (how many animation layers the matched controllers are on), `keys_added`, `keys_already` (keys that already existed) and `undo`. A frame outside the animation range is keyed but not visible on the timeline, and the result then has a `warning`. Refused when a controller is on a locked layer.

### `delete_key`

Removes the keyframes on the given frames from the given controllers, or from the whole character when `controllers` is omitted. Use it to clear keys before re-keying, to remove a bad key or to shorten an animation. The whole call is one undo step, `MCP: delete_key`.

| Parameter | Default | Notes |
|---|---|---|
| `frames` | required | Frame numbers (a whole number or a list of them). Accepts `"0,10,20"`. |
| `controllers` | all controllers | Names, aliases or partial names; a list or one string. |

Returns `frames`, `controllers` (the matched names), `layers`, `keys_removed`, `frames_without_key` and `undo`. `frames_without_key` counts the layer and frame pairs that had no key, not whole frames. Refused when a controller is on a locked layer.

### `list_keys`

Lists the keyframes of controllers in a frame range, with the interpolation type of the interval each key starts. Use it to find where keys are before editing, or to confirm that an interpolation action changed a range. Read-only.

| Parameter | Default | Notes |
|---|---|---|
| `controllers` | all controllers | Names or aliases; a list or one string. When omitted, only controllers that have keys in the range are reported; name controllers to list them even when they have none. |
| `start` | animation start | A whole number from 0. First frame to inspect. |
| `end` | animation end | A whole number from 0. Last frame to inspect; must not be less than `start`. |

Returns `start`, `end`, `frame_count`, `key_frames` (the union of all key frames), `count` and `controllers`. Each entry of `controllers` covers one animation layer: `name` (the first controller on it), `names` (the matched controllers on it), `keys`, `interpolation` (the type at each key frame) and `segments` (runs of frames that share one interpolation, each with `first`, `last`, `key` and `interpolation`). Interpolation types are `bezier`, `linear`, `step`, `fixed`, `clamped_bezier`, `low_amplitude_bezier`, `none` and `ai`. When `controllers` is omitted the result also has `scanned_layers` and `without_keys`; `without_layer` lists named controllers that have no layer.

### `select_frames`

Selects a frame interval on the timeline, so Cascadeur's `Timeline.* on selected interval` actions (interpolation, IK/FK, cycles) apply to it. Use it before running such an action, for example with `call_action`. It does not change keyframes.

| Parameter | Default | Notes |
|---|---|---|
| `start` | required | A whole number from 0. First frame of the interval. |
| `end` | required | Last frame of the interval (`start` or more). |
| `controllers` | all layers | Names or aliases; a list or one string. Restricts the selection to their layers. |

Returns `requested_first`, `requested_last`, `first` and `last` (the interval the selector reports), `layers` (how many layers are selected) and, when `controllers` was given, `controllers`. When Cascadeur reports a different interval than requested, a hint says so.

### `create_cycle`

Turns the frame range `start` to `end` into a timeline cycle (loop) with Cascadeur's own `Timeline.Create cycle` action. Use it after the pose keys are done and before rendering a looping animation. The range must lie inside the animation range and the keyed frames, and the animation needs at least two keyed frames; a one-frame animation is refused because Cascadeur can crash.

| Parameter | Default | Notes |
|---|---|---|
| `start` | required | A whole number from 0. First frame of the cycle's active range. |
| `end` | required | Last frame of the cycle's active range; more than `start`, because a cycle needs at least two frames. |
| `offset` | `"none"` | `none` (plain loop, repeats the same pose), `position` (keeps moving forward, for example a walk cycle) or `position_rotation` (keeps position and rotation offsets). |

Returns `start`, `end`, `offset`, `action` (the Cascadeur action that ran) and `selection` (the confirmed timeline selection). Refused before anything is selected when any layer that holds objects is locked. The previous timeline selection is restored afterwards as a second native command, so one undo reverts that restore and the next reverts the cycle; `undo` is `null`. Cycle structures cannot be read back safely on this build, so verify by comparing a frame inside the range with the same frame one cycle later.

### `remove_cycles`

Removes the timeline cycles that intersect `start` to `end` with Cascadeur's own `Timeline.Remove cycles` action, or every cycle when both are omitted. Use it to replace a loop or to stop an animation after the last repetition. It does not delete keyframes.

| Parameter | Default | Notes |
|---|---|---|
| `start` | animation start | A whole number from 0. First frame of the range. With only `start`, cycles are removed from that frame to the end. |
| `end` | last keyed frame | A whole number from 0. Last frame of the range. With only `end`, cycles are removed up to that frame. |

Returns `start` and `end` (the range used), `action`, `selection` (the confirmed timeline selection) and `undo` (`null`). Refused before anything is selected when the animation has fewer than two keyed frames, when the range lies outside the animation range or the keyed frames, or when any layer that holds objects is locked. The cycle removal and the restore of the previous timeline selection are two native undo steps.

## Interpolation and IK/FK

### `set_interpolation`

Sets the interpolation type of the timeline intervals from `start` to `end`. Use it after keying poses to change how the animation moves between them. It writes Cascadeur's timeline data directly, so the object selection, the timeline selection and the playhead do not change.

| Parameter | Default | Notes |
|---|---|---|
| `kind` | required | `bezier`, `bezier_clamped`, `bezier_viscous`, `linear`, `step` or `fixed`. |
| `start` | required | A whole number from 0. First frame of the range. |
| `end` | required | Last frame of the range (`start` or more). Every interval that overlaps the range is changed except the one that starts on `end`, so 0 to 20 changes the interval that starts at 0. With `start` equal to `end`, only the interval that starts there changes. |
| `controllers` | all controllers | Names or aliases; a list or one string. |

Returns `kind`, `start`, `end`, `controllers`, `layer_count`, `layers` (per layer, the interpolation read back from each selected interval), `sections` (the intervals in the range), `sections_changed`, `readback` (a count per interpolation type) and `undo` (`MCP: set_interpolation`). The read-back names the clamped and viscous types `clamped_bezier` and `low_amplitude_bezier`. When no key overlaps the range nothing changes and a hint suggests adding keys with `set_key`. Refused when a controller is on a locked layer.

### `set_ik_fk`

Switches the IK/FK type of the timeline intervals from `start` to `end`: `ik`, `fk` or `gr` (global FK). It selects the interval, runs Cascadeur's own "IK type on selected interval" action and restores the object and timeline selection. Unlike `set_interpolation`, the interval that starts on `end` is changed too.

| Parameter | Default | Notes |
|---|---|---|
| `mode` | required | `ik`, `fk` or `gr` (global FK). |
| `start` | required | A whole number from 0. First frame of the range. |
| `end` | required | Last frame of the range (`start` or more). |
| `controllers` | all controllers | Names or aliases; a list or one string. |

Returns `mode`, `action`, `start`, `end`, `controllers`, `layer_count`, `layers` (per layer, the interval mode and key mode read back), `sections`, `sections_changed`, `readback` and `undo_source` (`"Cascadeur action"`: Cascadeur names the undo step itself, not `MCP: set_ik_fk`). When no key overlaps the range the action is not called: `action_called` is `false` and a hint suggests adding keys. Refused when the animation has fewer than two keyed frames, when the range lies outside the animation range or the keyed frames, or when a controller is on a locked layer.

### `reset_tangents`

Clears the custom tangents of the timeline keys from `start` to `end` and restores continuous tangents (Cascadeur's "Clear custom tangents on selected interval"). Use it after editing tangents to remove overshoot or a hand-made curve. It writes the key data directly, so the object selection, the timeline selection and the playhead do not change.

| Parameter | Default | Notes |
|---|---|---|
| `start` | required | A whole number from 0. First frame of the range. |
| `end` | required | Last frame of the range (`start` or more). The key that starts on `end` is left alone, as in `set_interpolation`. |
| `controllers` | all controllers | Names or aliases; a list or one string. |

Returns `start`, `end`, `controllers`, `layer_count`, `layers` (per layer, the tangents read back), `sections`, `sections_changed`, `readback`, `reset` (`"continuous"`) and `undo` (`MCP: reset_tangents`). Refused when a controller is on a locked layer.

### `reset_easing`

Resets the easing weights of the timeline keys from `start` to `end` to their neutral value, one third (Cascadeur's "Reset easing weights on selected interval"). Use it after easing edits to make the motion between keys symmetric again. It writes the key data directly, so the object selection, the timeline selection and the playhead do not change.

| Parameter | Default | Notes |
|---|---|---|
| `start` | required | A whole number from 0. First frame of the range. |
| `end` | required | Last frame of the range (`start` or more). The key that starts on `end` is left alone, as in `set_interpolation`. |
| `controllers` | all controllers | Names or aliases; a list or one string. |

Returns `start`, `end`, `controllers`, `layer_count`, `layers` (per layer, the left and right weight velocities read back), `sections`, `sections_changed`, `readback`, `weight` (one third) and `undo` (`MCP: reset_easing`). Refused when a controller is on a locked layer.

## Animation layers

### `list_layers`

Lists the scene's animation layer tree: every folder and layer with its name, id, path, visibility, lock state and object count. Use it before moving control points or changing a layer, because layer names are not unique: pass the returned `path` or `id` when two items share a name. Most layer tools find an item by id, full path, exact name or unique partial name, ignoring case, and refuse a name that matches several items, listing the matching paths in `candidates`.

No parameters.

Returns `root` (`id` and `name`), `counts` (`layers` and `folders`) and `tree`. Each node has `kind` (`layer` or `folder`), `name`, `id`, `path` (such as `parent/child`) and `objects`. Layers also have `visible` and `locked`; folders have `children` and the object count of their whole subtree, and their `visible` and `locked` are `null`.

### `create_layer`

Creates an empty animation layer under a folder. Use it to build a separate track before `move_to_layer`, so a group of control points can be hidden, locked or edited together. The call is one undo step.

| Parameter | Default | Notes |
|---|---|---|
| `name` | required | Name of the new layer. Must not be empty or contain `/`, and must be unique among its siblings (case-insensitive). |
| `parent` | root folder | Folder name, path (`parent/child`) or id from `list_layers`. |

Returns `layer` (with `id`, `name` and `path`) and `undo`.

### `create_layer_folder`

Creates a folder in the animation layer tree. Cascadeur 2026.2.3 rejects a folder with no layers, so the new folder opens with one empty `Track0` layer. The call is one undo step.

| Parameter | Default | Notes |
|---|---|---|
| `name` | required | Name of the new folder. Must not be empty or contain `/`, and must be unique among its siblings (case-insensitive). |
| `parent` | root folder | Folder name, path (`parent/child`) or id from `list_layers`. |

Returns `folder` (with `id`, `name` and `path`), `default_layer` (the `Track0` layer) and `undo`.

### `delete_layer`

Deletes a layer, or a folder with everything under it. A layer that holds objects and a folder that is not empty are refused unless `force` is `true`; with `force`, the objects first move to the scene's default layer (or the first other layer). The whole call is one undo step.

| Parameter | Default | Notes |
|---|---|---|
| `name` | required | Layer or folder name, path (`parent/child`) or id. It must be an exact name, path or id; a partial name is not matched. |
| `force` | `false` | `true` deletes a layer that holds objects, or a folder with children, removing everything under a folder bottom-up (layers before the folders that hold them). |

Returns `deleted` (with `name`, `path`, `id` and `kind`), `objects` (how many objects its layers held), `undo` and, when objects were moved, `objects_moved_to` (the layer they went to). Refused when a layer being deleted is locked, when the objects would move to a locked layer, or when no other layer exists to take them.

### `set_layer_visible`

Shows or hides an animation layer. Hiding a layer hides its objects' animation in the viewport; the stored animation is untouched. The call is one undo step.

| Parameter | Default | Notes |
|---|---|---|
| `name` | required | Layer name, path (`parent/child`) or id. A folder is not accepted. |
| `visible` | required | `true` shows the layer, `false` hides it. |

Returns `layer` (with its new state), `visible` and `undo`. When the layer already has that state nothing changes and `changed` is `false`.

### `set_layer_locked`

Locks or unlocks an animation layer. A locked layer is protected from MCP Plus write tools such as `set_pose`, `set_key` and `set_interpolation`: they refuse to edit objects on it and return an error that names the layer, because Cascadeur's Python API does not enforce the lock. Unlock with `locked=false` before posing or keying. The call is one undo step.

| Parameter | Default | Notes |
|---|---|---|
| `name` | required | Layer name, path (`parent/child`) or id. A folder is not accepted. |
| `locked` | required | `true` locks the layer, `false` unlocks it. |

Returns `layer` (with its new state), `locked` and `undo`. When the layer already has that state nothing changes and `changed` is `false`.

### `rename_layer`

Renames an animation layer or folder. The new name must be unique among its siblings. The call is one undo step.

| Parameter | Default | Notes |
|---|---|---|
| `name` | required | Current layer or folder name, path or id. |
| `new_name` | required | New name. Must not be empty or contain `/`, and must be unique among its siblings (case-insensitive). |

Returns `item` (the layer or folder with its new `name` and `path`) and `undo`. When the new name equals the current one, nothing changes and `changed` is `false`.

### `move_to_layer`

Moves control points to an animation layer. Use it to group a character's controls so the layer can be hidden or locked as one unit. The call is one undo step.

| Parameter | Default | Notes |
|---|---|---|
| `controllers` | required | Control point names, aliases or partial names; a list or one string. |
| `layer` | required | Target layer name, path (`parent/child`) or id. A folder is not a layer. |

Returns `layer`, `moved` (the names of the objects that moved), `already_in_layer` and `undo`; when every controller is already there, nothing changes and `changed` is `false`. Refused when a controller's current layer or the target layer is locked.
## Physics and AI

### `get_physics_state`

Reads the Physics Assistant (AutoPhysics) state of the current scene and changes nothing. Use it before changing physics
settings or snapping.

No parameters.

Returns `enabled`, `frozen`, `show_fulcrums` (fulcrum points shown) and `only_key_fulcrums` (only key fulcrums used),
`priority_frames` (a list of frame numbers), `current_frame`, `first_frame`, `last_frame` and `fps`, and the gravity per
frame as `gravity_per_frame` and `gravity_cm_per_frame2` (cm/frame², 980 / fps²). A value that Cascadeur does not report
is `null`.

### `physics_enable`

Turns Cascadeur's Physics Assistant (AutoPhysics) on or off. Use it to start the physical solve before `physics_snap`, or
to stop it. It reads the state first and does nothing when it already matches, so repeating the call is safe.

| Parameter | Default | Notes |
|---|---|---|
| `enabled` | required | `true` turns the Physics Assistant on, `false` turns it off. Must be a boolean. |

Returns `changed` (`false` when the state already matched) and `enabled` (the state afterwards).

### `physics_snap`

Bakes the AutoPhysics result into the animation (Snap to Auto Physics) as a background job. Use it after `physics_enable`
has started the solve. The job answers Cascadeur's frame-choice dialog with All Frames and waits until the pose stops
changing.

No parameters.

Returns a `job_id` at once, without waiting for the bake; `get_job` polls it for progress and the result, and `cancel_job`
stops it. The finished job reports `frames` (the keyed animation range, as `first` and `last`), `changed_first`,
`changed_last`, `changed_frame_count`, `changed_frames` (at most 200), `max_delta_cm`, `max_rotation_delta_deg` and
`objects` (the control points that moved); only the pelvis, head, hands and feet are compared. `commands` lists the
Cascadeur commands the snap ran; they are Cascadeur's own undo steps, not `MCP:` steps. The call is refused at once, with
no job, when the Physics Assistant is off, when the keyed animation is shorter than two frames, when a layer is locked or
when another snap is running. The job times out after 300 seconds.

### `physics_freeze`

Freezes or unfreezes the AutoPhysics result. A frozen result keeps the solved physics from following further animation
edits; unfreeze it to solve again. It reads the state first and does nothing when it already matches.

| Parameter | Default | Notes |
|---|---|---|
| `enabled` | required | `true` freezes the physics result, `false` unfreezes it. Must be a boolean. |

Returns `changed` and `frozen` (the state afterwards). It is refused while the Physics Assistant is off.

### `physics_set_priority_frame`

Marks a frame as an AutoPhysics priority frame: the solver keeps that frame's pose while it solves the rest of the
animation. Use it to pin a landing pose or a contact. It moves the playhead to the frame, runs Cascadeur's Set priority
frame action and puts the playhead back.

| Parameter | Default | Notes |
|---|---|---|
| `frame` | required | Frame number: a whole number from 0, or a numeric string such as `"12"`. It must lie inside the keyed animation (at least two frames of it); the frame itself need not carry a key. |

Returns `changed`, `frame`, `priority` (`true` when the frame is now a priority frame), `priority_frames` (the full list)
and `playhead_restored` (the frame the playhead went back to). A frame that already is a priority frame is left alone
(`changed` is `false`); the tool cannot clear one. It is refused while the Physics Assistant is off or when the layer is
locked.

### `physics_show_fulcrums`

Shows or hides Cascadeur's Fulcrum Visualizer, which draws all fulcrum (contact) points of the character so you can see
which points touch the ground or another surface. Cascadeur cannot show it while the Physics Assistant is on, so call
`physics_enable` with `enabled=false` first. It reads the state first and does nothing when it already matches.

| Parameter | Default | Notes |
|---|---|---|
| `enabled` | required | `true` shows all fulcrum points, `false` hides them. Must be a boolean. |

Returns `changed` and `show_fulcrums` (the state afterwards). `enabled=true` is refused while the Physics Assistant is on.

### `fix_foot_sliding`

Removes foot sliding in a contact interval with Cascadeur's Fulcrum Motion Cleaning (`View.FixFoot`) as a background job.
Use it after keying a walk or run and before rendering. It measures the summed horizontal movement (cm) of the fulcrum
points that stay in contact between two consecutive frames, before and after the cleaning.

| Parameter | Default | Notes |
|---|---|---|
| `start` | animation's first frame | First frame of the interval: a whole number from 0, or a numeric string such as `"12"`. |
| `end` | last keyed frame | Last frame of the interval, inclusive. The interval must lie inside the keyed animation and be at least 3 frames long. |
| `controllers` | whole character | Controller names or aliases; a list or one string. An empty list is refused. |

Returns a `job_id` at once; `get_job` polls it for progress and the result, and `cancel_job` stops it. The finished job
reports `frames`, `controllers`, `before` and `after` (with `slide_total_cm` and `slide_max_cm`) and `change`
(`slide_reduction_cm` and `slide_reduction_percent`). When Cascadeur has no cached contacts for the range yet, `before` has
no contact data and `measurement_hint` suggests running `prepare_keys_by_fulcrums` on the same range first. The cleaning is
Cascadeur's own undo command (listed in `commands`), not an `MCP:` step. The call is refused at once when a layer is
locked (unlock it with `set_layer_locked`) or when another cleanup is running. The job times out after 300 seconds.

### `fix_collisions`

Pushes colliding bodies apart with Cascadeur's Collision Penetration Cleaning (`View.FixCollisions`) as a background job.
Use it on an interval where a limb penetrates the body or the floor. It measures the total and the maximum penetration
depth (cm), before and after the cleaning.

| Parameter | Default | Notes |
|---|---|---|
| `start` | animation's first frame | First frame of the interval: a whole number from 0, or a numeric string such as `"12"`. |
| `end` | last keyed frame | Last frame of the interval, inclusive. The interval must lie inside the keyed animation and be at least 3 frames long. |
| `controllers` | whole character | Controller names or aliases; a list or one string. An empty list is refused. |

Returns a `job_id` at once; `get_job` polls it for progress and the result, and `cancel_job` stops it. The finished job
reports `frames`, `controllers`, `before` and `after` (with `penetration_total_cm` and `penetration_max_cm`) and `change`
(`penetration_reduction_cm` and `penetration_reduction_percent`). As with `fix_foot_sliding`, `before` has no collision data when
Cascadeur has no cached collision points for the range yet. The cleaning is Cascadeur's own undo command (listed in
`commands`), not an `MCP:` step. The call is refused at once when a layer is locked or when another cleanup is running.
The job times out after 300 seconds.

### `prepare_keys_by_fulcrums`

Prepares the keyframes of an interval from the fulcrum motion with Cascadeur's Auto keys (`View.AutoInterpolation_Keys`)
as a background job. Use it after cleaning contacts, so the cleaned motion is carried by fewer, better placed keys instead
of dense frames. It always works on the whole character.

| Parameter | Default | Notes |
|---|---|---|
| `start` | animation's first frame | First frame of the interval: a whole number from 0, or a numeric string such as `"12"`. |
| `end` | last keyed frame | Last frame of the interval, inclusive. The interval must lie inside the keyed animation and be at least 2 frames long. |

Returns a `job_id` at once; `get_job` polls it for progress and the result, and `cancel_job` stops it. The finished job
reports `frames`, `before` and `after` (key summaries with `key_count`, `layers_with_keys`, `key_frames` and
`interpolation`, the number of keys per interpolation type) and `change` (`keys_before`, `keys_after` and `key_delta`).
Auto keys is Cascadeur's own undo command (listed in `commands`), not an `MCP:` step. The call is refused at once when a
layer is locked or when another cleanup is running. The job times out after 300 seconds.

### `ai_inbetween`

Fills the frames between key poses with Cascadeur's AI inbetweening (the timeline action `Timeline.AI.AI on selected
interval`) as a background job. Key the poses around the interval with `set_key` first: the keyed poses stay, and
Cascadeur synthesizes the motion between them. The AI features need a Pro license.

| Parameter | Default | Notes |
|---|---|---|
| `start` | required | First frame of the interval: a whole number from 0, or a numeric string such as `"12"`. |
| `end` | required | Last frame of the interval; must be greater than `start`. |
| `controllers` | whole character | Controller names or aliases, such as `left_hand`; a list or one string. |

Returns a `job_id` at once; `get_job` polls it for progress and the result, and `cancel_job` stops it. The finished job
reports `first_frame`, `last_frame`, `changed_first`, `changed_last`, `layers_changed`, `ai_layers` (how many layers got AI
interpolation) with `ai_layer_names`, `keys_added`, `keys_removed`, `interpolation` (keys per interpolation type) and
`seconds`. The previous selection and playhead are restored when the job finishes. The call is refused at once when the
license has no AI features, when a selected layer is locked or when another AI job is running. It is also refused when the
keys do not fit: every selected layer needs a key at or before `start` and one at or after `end`, at least one key must lie
inside the interval, and no two neighbouring keys may be farther apart than Cascadeur's `InbetweeningTool/MaxIntervalLength`
setting. The job times out after 1800 seconds.

### `generate_motion`

Generates a new motion for the interval with Cascadeur's Run Motion Generation (`View.Inbetweening_RunMotionGeneration`)
as a background job. Use it when the keyed poses are only a rough idea and Cascadeur's motion generator should replace the
animation between the keys. Unlike `ai_inbetween`, it rewrites the keyed values and can set the interval to fixed
interpolation, so copy the animation first if the keyed poses must survive.

| Parameter | Default | Notes |
|---|---|---|
| `start` | required | First frame of the interval: a whole number from 0, or a numeric string such as `"12"`. |
| `end` | required | Last frame of the interval; must be greater than `start`. |
| `controllers` | whole character | Controller names or aliases, such as `left_hand`; a list or one string. |

Returns a `job_id` at once; `get_job` polls it for progress and the result, and `cancel_job` stops it. The finished job
reports `first_frame`, `last_frame`, `changed_first`, `changed_last`, `layers_changed` with `changed_layer_names`,
`keys_added`, `keys_removed` and `seconds`. Cascadeur 2026.2.3 creates no undo step for the generated motion. The first run
in a Cascadeur session loads its Torch model (about 20 s), and Cascadeur's GUI may not respond during that load, although
the `job_id` is still returned at once; later runs are fast. The previous selection and playhead are restored when the job
finishes. The call is refused at once under the same conditions as `ai_inbetween` (Pro license, keys around the interval,
locked layers, another AI job running). The job times out after 1800 seconds.

### `video_mocap`

> [!WARNING]
> **Experimental.** In testing on Cascadeur 2026.2.3 this tool crashed Cascadeur. Save your scene before
> calling it. See [Behavior and limitations](limitations.md#retargeting-rigging-and-mocap).

Turns a reference video into character animation with Cascadeur's built-in video mocap (`View.MocapTool`), entirely on
this machine, as a background job. The tool imports the `.mp4` as a scene reference plane, points the Load video dialog's
frames folder at a writable `%TEMP%` directory, selects the character and the timeline frames, presses Mocap, answers the
confirmation and deletes the plane afterwards. Cascadeur writes one keyframe per selected frame on the character.

| Parameter | Default | Notes |
|---|---|---|
| `video_path` | required | Absolute path of the reference `.mp4` (single person, full body, 3–5 s). The file must exist and not be empty. |
| `character` | the scene's only character | Character name from `list_characters` (a root joint or mesh name also matches). Required when the scene has more than one character. |
| `start` | timeline selection | First project frame to mocap (a project frame, not a video second). Pass it together with `end`, or leave both out to use the selected timeline interval. |
| `end` | timeline selection | Last project frame to mocap, inclusive. The interval must lie inside the keyed animation. |
| `from_frame` | `0` | First frame of the video clip to use. Typed into the Load video dialog's From field; not verified yet. |
| `to_frame` | last frame of the video | Last frame of the video clip to use. Typed into the dialog's To field; not verified yet. |
| `keep_reference` | `false` | `true` leaves the imported reference plane (and its decoded frames) in the scene. |
| `dry_run` | `false` | `true` stops at Cascadeur's confirmation dialog, cancels it and runs no model; it checks the video import and the character setup. |
| `timeout` | `600` | Seconds before the job cancels itself; must be greater than 0. |

Returns a `job_id` at once; `get_job` polls it for progress and the result, and `cancel_job` presses Cancel in Cascadeur's
window and deletes the plane only after that window has closed. The finished job reports `outcome` (`completed`,
`partial` when some frames had no recognised pose, or `cancelled` for a dry run), `character`, `frames` (the first and last
project frame), `keyframes`, `key_frames`, `failed_frames`, `elapsed_s`, and, useful after a dry run, `confirmation` (the text of
Cascadeur's confirmation dialog) and `objects_selected`. The call is refused at once when the `.mp4` is missing, when the model file
`mocap.pt` (about 825 MB, in `%LOCALAPPDATA%\Nekki Limited\Cascadeur\resources`) is not installed, or when a Load video or
Mocap window is already open; Cascadeur downloads the model only when View > Mocap is run by hand. The job stays on the
scene tab it started on: switching to another tab pauses it, and closing that tab stops it. Quality, duration and how
Cascadeur treats existing keys on the selected frames are not verified yet, so back up the animation if those frames
already have keys. Cascadeur's baked keys are their own undo step, so one undo does not revert the import, the mocap and
the plane deletion together.

## Review and output

### `render_image`

Renders one image of the 3D scene with Cascadeur's own renderer (`RenderToFile`), so other windows covering Cascadeur do
not matter. Use it to look at the scene or to grab a still from an animation; for several frames use
`render_contact_sheet`, and for the finished motion use `render_video`. Cascadeur must not be minimized.

| Parameter | Default | Notes |
|---|---|---|
| `frame` | current frame | Timeline frame to render: a whole number (a string is refused). The playhead goes back to its old position afterwards. Cascadeur may clamp a frame outside the animation range; the JSON then has `requested_frame` and a `note`. |
| `width` | `1280` | Image width in pixels (1–16384). |
| `height` | `720` | Image height in pixels (1–16384). |
| `samples` | Cascadeur's default (4) | Anti-aliasing samples per pixel (1–4096). |
| `path` | a new file under `%TEMP%\cascadeur-mcp-plus\renders` | Absolute path ending in `.png` (any letter case; the extension is written in lowercase). Its folder must exist. |
| `overwrite` | `false` | `true` replaces an existing file at `path`; otherwise an existing file is refused. |
| `return_image` | `true` | `false` returns only the JSON, without the image. |
| `max_width` | `1280` | Downscales only the returned image to this width; the file keeps `width` and `height`. |
| `camera_preset` | current viewport camera | One of `front`, `back`, `left`, `right`, `top`, `three_quarter`: frames the character with that view for the render, then restores the camera. |

Returns the image (downscaled to `max_width`) plus a JSON note with `path`, `frame` (the frame rendered), `current_frame`,
`width`, `height`, `file_size` (the PNG's pixel size), `file_bytes`, `samples`, `seconds`, `image_size` and `scaled`; with
`camera_preset` it also has `camera_preset`, `camera` and `camera_restored`. With `return_image=false` only the JSON is
returned. A render that times out (after 180 seconds) cannot be cancelled, and the error names the temporary file to
delete before retrying.

### `render_contact_sheet`

Renders a contact sheet: a grid of frames from the current animation, one rendered cell per frame with the frame number in
the cell's corner. Use it to check a whole motion in one picture before making the video with `render_video`. Every cell
uses the same camera and, by default, the clean View display; Cascadeur must not be minimized.

| Parameter | Default | Notes |
|---|---|---|
| `start` | animation's first frame | First frame of the interval: a whole number from 0, or a numeric string such as `"12"`. With none of `start`, `end`, `step` and `frames`, 8 frames are sampled evenly across the animation. |
| `end` | animation's last frame | Last frame of the interval, inclusive. |
| `step` | `1` | Frame step for `start` and `end`; at least 1. |
| `frames` | — | Exact frames: one number, a list such as `[0, 10, 20]` or a string such as `"0,10,20"`. Cannot be combined with `start`, `end` or `step`. A sheet holds at most 64 frames. |
| `columns` | `4` | Cells per row (1–32); reduced when there are fewer frames. |
| `cell_width` | `320` | Width of one cell in pixels (16–4096); the 16:9 height follows from it. |
| `camera_preset` | `"auto"` | `auto` fits one fixed camera to all poses of the motion (a side view when the character moves forward or backward); `current` keeps the viewport camera; `front`, `back`, `left`, `right`, `top`, `three_quarter` fit the same poses from that view. |
| `path` | a new file under `%TEMP%\cascadeur-mcp-plus\contact_sheets` | Absolute path ending in `.png`. Its folder must exist. |
| `overwrite` | `false` | `true` replaces an existing file at `path`; otherwise an existing file is refused. |
| `keep_overlays` | `false` | `true` renders the viewport's display as it is (controller points, joints, ghosts, trajectories, silhouette); `false` renders the clean View display (a solid shaded surface, no overlays). |

Returns the sheet as an image plus a JSON note with `path`, `frames` (the frames rendered), `requested_frames`, `count`,
`selection` (`frames`, `even sample` or `start/end/step`), `columns`, `rows`, `cell_width`, `cell_height`, `file_size` (the
sheet's pixel size), `file_bytes`, `camera_preset`, `camera_view`, `camera`, `clean_display` and `seconds`. The playhead,
camera and display are restored after the call. The call is refused when more than 64 frames are chosen, or when the sheet
would pass 16384 pixels on a side or 40,000,000 pixels in all. If the display cannot be put back after the sheet was
written, the call returns an error that names the file.

### `render_video`

Renders the animation timeline to an MP4 with Cascadeur's own encoder, through its Export video window
(`File.Export.Video...`), as a background job. Use it to produce the final video after checking the motion with
`render_contact_sheet`. By default the camera is fitted to the whole motion and the display switches to the clean View
display; the range, camera and display are restored afterwards.

| Parameter | Default | Notes |
|---|---|---|
| `path` | a new file under `%TEMP%\cascadeur-mcp-plus\videos` | Absolute path ending in `.mp4`. Its folder must exist. |
| `start` | animation's first frame | First frame of the interval: a whole number from 0, or a numeric string such as `"12"`. |
| `end` | animation's last frame | Last frame of the interval, inclusive. |
| `width` | `1920` | Video width in pixels (1–16384). |
| `height` | `1080` | Video height in pixels (1–16384). |
| `samples` | `MEDIUM` (4 samples) | Render-quality selector (1–4096): 1 is `LOW`, 2–4 is `MEDIUM`, more is `HIGH`. The Export video window has no exact sample count. |
| `camera_preset` | `"auto"` | One of `auto`, `current`, `front`, `back`, `left`, `right`, `top`, `three_quarter`. `auto` fits the whole motion; `current` keeps the viewport camera; the others fit the whole motion from that view. |
| `overwrite` | `false` | `true` replaces an existing file at `path`; otherwise an existing file is refused. |
| `keep_overlays` | `false` | `true` keeps the viewport's display (controller points, joints, ghosts, trajectories, silhouette) in the output; `false` renders the clean View display. |

Returns a `job_id` at once; `get_job` polls it for progress and the result, and `cancel_job` presses the Export video
window's Stop button. The finished job reports `path`, `file_bytes`, `frames` (the encoded frame count), `duration_s`,
`fps`, `range`, `width`, `height`, `quality`, and what was restored (`frame_restored`, `range_restored`,
`camera_restored`, `display_restored`). The call is refused at once when Cascadeur is minimized, when Cascadeur's Export
video window is already open or when another render is running. The job times out after 7200 seconds.

### `render_image_sequence`

Renders the animation timeline to a folder of image files with Cascadeur's own renderer, through the IMAGE FILES mode of
its Export video window, as a background job. Use it when a video is not needed or when the frames are processed later;
`render_video` makes the MP4. The whole-motion camera, the clean View display and the restoration are the same as for
`render_video`.

| Parameter | Default | Notes |
|---|---|---|
| `folder` | a new folder under `%TEMP%\cascadeur-mcp-plus\sequences` | Absolute path of a folder that is missing or empty (its parent folder must exist). A folder that already holds files is refused, so old and new frames cannot mix. |
| `start` | animation's first frame | First frame of the interval: a whole number from 0, or a numeric string such as `"12"`. |
| `end` | animation's last frame | Last frame of the interval, inclusive. |
| `width` | `1920` | Image width in pixels (1–16384). |
| `height` | `1080` | Image height in pixels (1–16384). |
| `samples` | `MEDIUM` (4 samples) | Render-quality selector (1–4096): 1 is `LOW`, 2–4 is `MEDIUM`, more is `HIGH`. The Export video window has no exact sample count. |
| `camera_preset` | `"auto"` | One of `auto`, `current`, `front`, `back`, `left`, `right`, `top`, `three_quarter`. `auto` fits the whole motion; `current` keeps the viewport camera; the others fit the whole motion from that view. |
| `keep_overlays` | `false` | `true` keeps the viewport's display (controller points, joints, ghosts, trajectories, silhouette) in the output; `false` renders the clean View display. |

Returns a `job_id` at once; `get_job` polls it for progress and the result, and `cancel_job` presses the Export video
window's Stop button. The finished job reports `path` (the folder), `frames` (the number of image files), `files` (their
names), `file_bytes`, `range`, `width`, `height`, `quality`, and what was restored (`frame_restored`, `range_restored`,
`camera_restored`, `display_restored`). The call is refused at once when Cascadeur is minimized, when Cascadeur's Export
video window is already open or when another render is running. The job times out after 7200 seconds.

## Camera and display

### `get_camera`

Reads the main 3D viewport camera and changes nothing. Use it before moving the camera or to check what a render will
show.

No parameters.

Returns `camera` with `position` and `target` (world coordinates, cm), `type` (`PERSPECTIVE` or `ISOMETRIC`), `distance`
(cm), `direction` (the unit vector from the target to the camera), `fov_deg` (the vertical field of view, in degrees) and
`tracking` (the object that this server's `track_object` follows, or `null`).

### `set_camera`

Moves the viewport camera by setting its world position and/or its look-at target (cm). Use it for a specific shot, or
`camera_preset` for standard views of a character. The camera cannot roll: its up is the world up projected onto the view
plane, like the viewport itself.

| Parameter | Default | Notes |
|---|---|---|
| `position` | current position | Camera position `[x, y, z]` in world cm. |
| `target` | current target | Look-at target `[x, y, z]` in world cm. |

Returns `camera` (as read back) and `previous` (the values before the call, so a caller can restore them). The call is
refused when both parameters are missing, or when position and target are less than 0.1 cm apart.

### `camera_preset`

Puts the camera in a standard view of a character. `front`, `back`, `left` and `right` follow the character's own facing,
taken from the pelvis, chest and shoulder control points and not from the world axes, so `front` still shows the face
after the character turns. The camera is switched to PERSPECTIVE, because the framing maths assumes it.

| Parameter | Default | Notes |
|---|---|---|
| `view` | required | One of `front`, `back`, `left`, `right`, `top`, `three_quarter`. `top` looks down from above; `three_quarter` is in front, to the character's right and a little above. |
| `target` | `"character"` | `"character"` needs exactly one character in the scene. Otherwise a character name from `list_characters`, or a control point name or alias from `list_controllers` to aim at that point. |
| `distance` | fits the whole character | Distance from the target to the camera in cm; must be greater than 0. Omitted, the camera frames the whole character with a small margin. |

Returns `camera` (as read back), `preset`, `target` (what was aimed at: `kind`, `name`, `character` and the aim `point`),
`distance` (cm), `fov_deg` and `aspect`.

### `frame_objects`

Moves the camera so every named object is inside the view, keeping the current view direction. Use it to look at a set of
control points or a prop; `camera_preset` is better for a standard view of a whole character. The camera stays at least
25 cm from the aim point, and objects that span less than 5 cm only get a re-aim at the current distance.

| Parameter | Default | Notes |
|---|---|---|
| `names` | required | Object names or character aliases (such as `left_hand`, `pelvis`, `head`); at least one. |

Returns `camera` (the new camera, as read back), `target` (the aim point, cm), `distance`, `objects` (each name with its
world position) and `projected` (each object's normalized screen position as `ndc`, and `on_screen`).

### `track_object`

Makes the viewport camera follow an object or character, or stops following when `name` is omitted. Use it while an
animation plays and the character moves out of frame. Cascadeur offers tracking only as the toggle action "Track
selection with camera", with no way to query it, so this tool remembers whether it turned tracking on and turns it off
again only if it did.

| Parameter | Default | Notes |
|---|---|---|
| `name` | — | Object name, character name, or control point name or alias to follow; a character is followed through its pelvis. Omit to stop. |

Returns `tracking`, `changed`, and when tracking starts the tracked `target` and `ids`. Tracking changes the scene
selection to the tracked object; stopping restores the previous selection (`selection_restored`).

### `get_display_state`

Reads the current display state and changes nothing. Use it before changing the display and to confirm the result of the
`set_*` display tools.

No parameters.

Returns `view_mode` (of the active viewport) and `view_modes` (one per viewport), `viewport_layout` (the number of
viewports), `ghosts` (with `ghost_base_mode` and `ghost_keyframes`), `trajectory_mode` (`off` when the trajectory display is
off), `trajectory_active`, `only_key_frames`, `silhouette` and `surface`. A value that Cascadeur's interface does not
report is `null` and is listed in `warnings`.

### `set_view_mode`

Switches the active 3D viewport's display mode. Use it to see the character as shaded geometry, joints, controller boxes
or points, or in Cascadeur's rigging view.

| Parameter | Default | Notes |
|---|---|---|
| `mode` | required | One of `mesh`, `joint`, `box_controller`, `point_controller`, `autoposing`, `rigging_view`, `view`. `view` draws a solid shaded surface without the editing overlays (controller points, joints). |

Returns `changed` and `view_mode` (the mode that is active afterwards).

### `set_ghosts`

Sets the Ghost tool mode. `keyframes` turns on Cascadeur's key-mode selection so the ghosts sit on keyframes (previous-frame
ghosts are enabled first when ghosts are off); the other modes use neighbouring or selected frames. Calling it again with
the same mode changes nothing.

| Parameter | Default | Notes |
|---|---|---|
| `mode` | required | One of `none`, `previous`, `next`, `neighbors`, `keyframes`, `selected_frames`. |

Returns `changed`, `ghosts` (the mode afterwards) and `ghost_keyframes`.

### `set_trajectories`

Sets the Trajectory tool mode and, optionally, restricts the trajectories to keyframes. Use it to show the arcs behind
selected or parented objects. Repeating the same call changes nothing.

| Parameter | Default | Notes |
|---|---|---|
| `mode` | required | One of `off`, `all`, `edges`, `edges_and_fixed`, `parents`; `off` turns the trajectory display off. |
| `only_key_frames` | current setting | `true` shows trajectories only through keyframes, `false` through every frame; omit to keep the current setting. |

Returns `changed`, `trajectory_mode` and `only_key_frames` (both as they are afterwards).

### `set_viewport_layout`

Changes the 3D viewport layout to 1, 2 or 4 viewports. Use it before comparing cameras or views. Calling it with the
current count changes nothing.

| Parameter | Default | Notes |
|---|---|---|
| `count` | required | Number of viewports: `1`, `2` or `4`. |

Returns `changed` and `viewport_layout` (the viewport count afterwards).

### `set_silhouette`

Turns the silhouette display of the 3D viewport on or off. Use it to check the character's outline against the
background. Calling it with the current value changes nothing.

| Parameter | Default | Notes |
|---|---|---|
| `enabled` | required | `true` turns the silhouette display on, `false` turns it off. Must be a boolean. |

Returns `changed` and `silhouette` (the state afterwards).

### `set_surface`

Turns the surface display of the 3D viewport on or off. Use it to switch between shaded surfaces and the wireframe or
joint view. Calling it with the current value changes nothing.

| Parameter | Default | Notes |
|---|---|---|
| `enabled` | required | `true` turns surfaces on, `false` turns them off. Must be a boolean. |

Returns `changed` and `surface` (the state afterwards).
## UI control (last resort)

### `ui_tree`

Lists the open windows and the UI elements on screen, and gives each element a `ref` that the other interface tools accept. Use it only to diagnose the interface or to find an element that no dedicated tool exposes; `get_scene_info`, `list_controllers`, `get_timeline` and `get_display_state` cover normal scene work. Only elements with text, a title, a tooltip, placeholder text, an `actionId` or an `objectName`, or that are clickable or text inputs, are listed.

| Parameter | Default | Notes |
|---|---|---|
| `window` | all windows | Window index or part of a window title; lists only that window. |
| `query` | — | Only elements whose text, title, tooltip, placeholder text, `actionId`, `objectName` or type contains this; not case-sensitive. |
| `interactive_only` | `false` | `true` lists only clickable elements, text inputs and elements with an `actionId`. |
| `include_hidden` | `false` | `true` also lists hidden windows and elements. Window indexes then include the hidden windows, so they can differ from the ones used without it. |
| `limit` | `150` | Maximum number of elements listed. |

Returns `windows` (each with its `window` index, `title`, `visible`, `active` and `size`), `elements`, `matched` and `truncated`. Each element has a `ref` such as `0:12`, its `type`, whichever of `text`, `title`, `toolTip`, `placeholderText`, `actionId` and `objectName` it has, `checked` or `active` when true, `enabled: false` when disabled, `clickable`, `input`, and `rect` as `[x, y, width, height]` in window coordinates. `matched` counts every match; `truncated` is `true` when `limit` cut the list. Refs reset on every `ui_tree` or `dialogs` call, also when another tool (for example a background job) looks for dialogs while it runs; an unknown `ref` is refused.

### `ui_click`

Clicks like a person: it sends synthesized mouse events through Qt's window-system input path (QtTest) to the center of an element, found by `ref` or `query`, or to the window point `x`/`y`. Use it only when no dedicated tool does the job: `set_pose` for posing, `select_objects` for selection, `set_key` for keying and `call_action` for a Cascadeur action. Clicking Quick Rigging's **Add rig elements** or **Generate rig** is refused outside Rig mode unless `force` is `true`.

| Parameter | Default | Notes |
|---|---|---|
| `ref` | — | Element `ref` from the latest `ui_tree` or `dialogs` result. |
| `query` | — | Text, tooltip, `actionId`, `objectName` or placeholder text of an on-screen element; not case-sensitive. An exact match ranks first, then clickable and enabled elements. Used when `ref` is not given. |
| `window` | focused window, else main window | Window index or part of a window title. Ignored when `ref` is given. |
| `x` | — | Window x coordinate (the units of `rect` in `ui_tree`). Give `x` and `y` together to click a point; `ref` and `query` are then ignored. |
| `y` | — | Window y coordinate. |
| `button` | `"left"` | `"left"`, `"right"` or `"middle"`. |
| `double` | `false` | `true` sends a double click. |
| `modifiers` | — | Keys held during the click: `"ctrl"`, `"shift"`, `"alt"` and `"meta"` (also `"win"`), for example `["ctrl"]`. |
| `index` | `0` | Which match of `query` to click: 0 is the best match, 1 the next one, and so on. |
| `force` | `false` | `true` skips the Quick Rigging guard (see [Safety](safety.md)). |
| `settle_ms` | `150` | Milliseconds to wait after the click so the UI can update, 0 to 5000. |

Returns `clicked` (the window title, the clicked `point` as `[x, y]` and, for an element, its details, with a `warning` when it is disabled) and, when `query` matched several elements, `other_matches`. A hidden window is refused.

### `ui_drag`

Drags with the mouse like a person: it presses at the start point, moves in steps with the button held, and releases at the end point; each step is a separate GUI-thread job, so AutoPosing and other live updates run between moves. Use it only for a widget that has no dedicated tool or action; to pose a character use `set_pose`, and to move the camera use `set_camera` or `camera_preset`. The start is `object`, else `x`/`y`, else `ref`/`query`; the end is `to_x`/`to_y`, else `dx`/`dy`, and one of the two is required.

| Parameter | Default | Notes |
|---|---|---|
| `object` | — | Scene object to start from, by exact name (for example `mixamorig:LeftHand_MainPoint`). Its position projected into the 3D viewport is the start point; refused when the object is not on screen. |
| `ref` | — | Element `ref` from the latest `ui_tree` or `dialogs` result; the drag starts at the center of the element. |
| `query` | — | Text, tooltip, `actionId`, `objectName` or placeholder text of an on-screen element; the drag starts at its center. |
| `window` | focused window, else main window | Window index or part of a window title. Ignored when `ref` is given. |
| `x` | — | Start x in window coordinates. Give `x` and `y` together. |
| `y` | — | Start y in window coordinates. |
| `to_x` | — | End x in window coordinates. Give `to_x` and `to_y` together. |
| `to_y` | — | End y in window coordinates. |
| `dx` | — | End point as a horizontal offset from the start point, in window coordinates. Used when `to_x`/`to_y` are not both given. |
| `dy` | — | End point as a vertical offset from the start point; negative moves up on screen. An omitted `dx` or `dy` counts as 0. |
| `button` | `"left"` | `"left"`, `"right"` or `"middle"`; held during the drag. |
| `modifiers` | — | Keys held during the drag, as in `ui_click`. |
| `steps` | `20` | Number of move events, 1 to 500; other values are clamped into that range. |
| `duration_ms` | `400` | Total time spent moving, in milliseconds. |
| `hold_ms` | `80` | Pause between the press and the first move, in milliseconds. |
| `settle_ms` | `300` | Milliseconds to wait after the release so the UI can update, 0 to 5000. |
| `method` | `"qtest"` | `"qtest"` goes through Qt's window-system input path; `"event"` sends `QMouseEvent`s directly. Any other value is refused. |
| `fov_deg` | `30` | Vertical field of view, in degrees, used to project `object`. |

Returns `window`, the actual `start` and `end` points as `[x, y]`, `steps`, `duration_ms`, `method` and `target` (the projected object or the element; empty for a plain point). The mouse button is always released, even if a move fails. If a dialog is open afterwards (for example an AutoPosing prompt), the result also has `dialogs` and a `warning`: answer the dialog with `ui_click(ref=...)` and drag again.

### `ui_hover`

Moves the mouse over an element or a window point, to show tooltips and hover menus. Use it only to inspect a widget that no dedicated tool describes; `get_scene_info`, `list_controllers` and `get_display_state` cover scene state.

| Parameter | Default | Notes |
|---|---|---|
| `ref` | — | Element `ref` from the latest `ui_tree` or `dialogs` result. |
| `query` | — | Text, tooltip, `actionId`, `objectName` or placeholder text of an on-screen element; not case-sensitive. An exact match ranks first, then clickable and enabled elements. Used when `ref` is not given. |
| `window` | focused window, else main window | Window index or part of a window title. Ignored when `ref` is given. |
| `x` | — | Window x coordinate (the units of `rect` in `ui_tree`). Give `x` and `y` together to hover a point; `ref` and `query` are then ignored. |
| `y` | — | Window y coordinate. |
| `settle_ms` | `150` | Milliseconds to wait after the move so a tooltip or menu can appear, 0 to 5000. |

Returns `hovered` with the `window` title and the `point` as `[x, y]`.

### `ui_key`

Presses keys or shortcuts. Use it only for a shortcut that has no dedicated tool or action; prefer `set_frame`, `undo`/`redo`, `call_action` and the scene tools for the same operation. The keys go to the window's focused item, so a focused text field can take them (`release_focus` in `ui_type` drops the focus).

| Parameter | Default | Notes |
|---|---|---|
| `keys` | required | Space-separated key combos, for example `"ctrl+s"`, `"Escape"`, `"ctrl+a Delete"` or `"F"`. Modifiers: `ctrl`, `shift`, `alt`, `meta` (also `win`). Keys: letters and digits, `F1` to `F12`, `Enter`, `Esc`, `Delete`, `Backspace`, `Tab`, `Space`, `Up`, `Down`, `Left`, `Right`, `Home`, `End`, `PageUp`, `PageDown`, `Insert`, `Plus`, `Minus`, `Comma`, `Period`, or another Qt key name without the `Key_` prefix. An unknown key or modifier is refused. |
| `window` | focused window, else main window | Window index or part of a window title. |
| `settle_ms` | `150` | Milliseconds to wait after the keys so the UI can update, 0 to 5000. |

Returns `window` (the title) and `pressed` (the combos that were pressed).

### `ui_type`

Types text into the focused element, or focuses an element by `ref` or `query` first. The text is sent as key events, so any Unicode text works. Use it only for a text field that no dedicated tool sets; `call_action` and the state tools should handle normal edits.

| Parameter | Default | Notes |
|---|---|---|
| `text` | required | Text to type. |
| `ref` | — | Element `ref` from the latest `ui_tree` or `dialogs` result; the element is focused first. If it is not a text input itself, the first text input inside it is used. |
| `query` | — | Text, tooltip, `actionId`, `objectName` or placeholder text of the element to focus first; not case-sensitive. Used when `ref` is not given. |
| `window` | focused window, else main window | Window index or part of a window title. Ignored when `ref` is given. |
| `replace` | `false` | `true` clears the field before typing; it only applies when `ref` or `query` selects the element. |
| `submit` | `false` | `true` presses Enter after typing. |
| `release_focus` | `false` | `true` drops keyboard focus afterwards, so shortcuts reach Cascadeur again. |
| `settle_ms` | `150` | Milliseconds to wait after typing so the UI can update, 0 to 5000. |

Returns `window` (the title), `typed` (the text) and `target` (the focused element, or `null` when neither `ref` nor `query` was given).

### `ui_set_property`

Sets a QML property on an element, for example `checked`, `value`, `text` or `currentIndex`. It bypasses the element's input handlers. Use it only when no dedicated tool or action exists; `set_view_mode`, `set_ghosts`, `set_trajectories`, `set_silhouette` and `set_surface` cover the display state.

| Parameter | Default | Notes |
|---|---|---|
| `ref` | required | Element `ref` from the latest `ui_tree` or `dialogs` result. |
| `name` | required | QML property name, for example `checked`, `value` or `text`. |
| `value` | required | New value: a string, a number, a boolean or `null`. |

Returns `ok` and the value before and after the change as `before` and `after` (both as text).

### `ui_invoke`

Invokes a QML method or signal that takes no arguments on an element, for example `clicked`, `toggle`, `open` or `close`. Use it only when the behavior has no dedicated tool or Cascadeur action; prefer `call_action` when the action has an `actionId`. The Quick Rigging guard always applies, and there is no `force`.

| Parameter | Default | Notes |
|---|---|---|
| `ref` | required | Element `ref` from the latest `ui_tree` or `dialogs` result. |
| `method` | required | Name of a QML method or signal without arguments, for example `clicked` or `toggle`. |

Returns `ok` and `element` (the element's details).

### `dialogs`

Lists the visible dialogs, popups and secondary windows with their texts and their buttons as refs. Use it to check that a tool left no dialog open, or to find a prompt that needs an answer. It resets the refs of `ui_tree`.

No parameters.

Returns `dialogs` and a `hint` to press a button with `ui_click(ref=...)`. Each secondary window has `kind: "window"` plus its `window` index, `title`, `visible`, `active` and `size`. Each dialog or popup has `kind: "dialog"` plus its `window` index, `type`, `texts` (at most 12) and `buttons`; a button has a `ref` such as `d0:3` and its `text`.

### `screenshot`

Captures a Cascadeur window, or one element, as an image with Win32 `PrintWindow`. Use it only for the interface itself (a widget, menu or dialog): use `render_image` to see the 3D scene and `render_contact_sheet` to inspect a motion. A minimized window renders nothing, so it is refused unless `restore` is `true`.

| Parameter | Default | Notes |
|---|---|---|
| `window` | focused window, else main window | Window index or part of a window title. With `ref`, the window of that element is captured. |
| `ref` | — | Element `ref` from the latest `ui_tree` or `dialogs` result; only this element is captured. |
| `max_width` | `1280` | A wider image is scaled down to this width; `0` keeps the full size. |
| `format` | `"jpeg"` | `"jpeg"` or `"png"`. |
| `restore` | `false` | `true` un-minimizes the window first. |

Returns an image plus a JSON note with `window` (the title), `window_size`, `image_size` and `window_units_per_pixel` (multiply image pixels by it to get window coordinates, the units of `ui_click` `x`/`y`; for an element capture, add the top-left corner of `crop_rect`). An element capture also has `crop_rect` as `[x, y, width, height]`. A black image adds a `warning`.

### `project_point`

Projects scene objects, by name, to window coordinates in the main 3D viewport, for the `x`/`y` of `ui_click` and `ui_drag`. It only computes coordinates; use `set_pose` to move control points, `select_objects` to select them and the camera tools for normal work. It uses the viewport camera's position and target with a vertical field of view of 30 degrees, measured on Cascadeur 2026.2.3, and supports perspective cameras only.

| Parameter | Default | Notes |
|---|---|---|
| `names` | required | List of scene object names, for example `["mixamorig:LeftHand_MainPoint"]`. Exact names only; aliases such as `left_hand` are not resolved here. |
| `fov_deg` | `30` | Vertical field of view of the viewport camera, in degrees. |

Returns `points`, keyed by name, each with `world` (position in cm), `depth` (distance in front of the camera, in cm), `window_xy` as `[x, y]` and `on_screen`; an object behind the camera has `on_screen: false` and an `error`, and no `window_xy`. It also returns `viewport_rect` as `[x, y, width, height]`, `fov_deg` and `camera` (`position` and `target`). An unknown object name is refused.

## Other

### `get_status`

Reports the MCP Plus and Cascadeur versions, the Cascadeur version this build was tested with (2026.2.3) and every tool that is unavailable in this build, with the API or action key it is missing. Use it first after a Cascadeur update, or when a tool reports that it is unavailable: such a tool stays in the tool list with `Unavailable in this Cascadeur version:` in front of its description, and a call to it is refused with the missing entries. It also lists the behaviors measured on the tested version (video export window, AutoPosing lock, animation layers, video mocap and more) that need re-checking on a different one.

| Parameter | Default | Notes |
|---|---|---|
| `force` | `false` | `true` audits the tools again instead of returning the report made at startup. |

Returns `ok`, `mcp_plus_version`, `cascadeur_version`, `tested_cascadeur_version`, `version_tested` (`true` when the two Cascadeur versions match), `version_note`, `tools` and `requirements` (how many were checked), `checked_at`, `unavailable_count`, `unavailable` (one entry per tool, with `tool`, `missing` and `reason`), `actions` (how the action keys were verified) and `version_dependent` (one `area` and `note` per behavior to re-check). If the audit itself fails, `ok` is `false` and `error` says why.

### `api_search`

Searches Cascadeur's `csc` type stubs, the `pycsc` wrappers and the official commands, samples and scripts for classes, functions, methods and docstring text. Use it before writing `run_python` code when you are unsure of the API. It is read-only, does not use the GUI thread, and builds its index on the first call.

| Parameter | Default | Notes |
|---|---|---|
| `query` | required | Class, function, method or docstring fragment; not case-sensitive. Name matches rank before docstring matches. An empty query is refused. |
| `limit` | `20` | Maximum number of results, 1 to 100; a larger number is capped, a smaller one is refused. |
| `sources` | all groups | List of source groups to search: `"all"`, `"python"` (every group except `stubs`), `"stubs"`, `"pycsc"`, `"commands"`, `"samples"`, `"scripts"` and `"rig_mode"`. |

Returns `ok`, `query`, `count` (results returned), `total` (all matches) and `results`; each result has `symbol`, `file`, `line`, `signature`, `kind` (class, function, method, attribute or usage) and `source` (the source group). `skipped_files` lists files that could not be parsed, and `hint` suggests a shorter name when nothing matches.

### `api_describe`

Shows the full type-stub block (signature, docstring and source text) of a `csc` class, function or method, including a class's fields and method list. Use it after `api_search` to learn exact signatures and member names. When Cascadeur is running, it also lists the members that exist at runtime but are missing from the stub; it is read-only and does not use the GUI thread.

| Parameter | Default | Notes |
|---|---|---|
| `symbol` | required | A `symbol` from `api_search`, for example `csc.tools.RenderToFile`. It is matched exactly, then without regard to case, then as the end of a symbol. A name that fits several symbols is refused with the `candidates`. |

Returns `ok`, `symbol`, `kind`, `file`, `line`, `signature`, `docstring`, `source` (the text of the block) and `source_group` (`stubs`, or another source group when no stub defines the symbol). A class also has `fields` and `methods`; `overloads` gives the number of definitions when there are several. For a stub symbol, `runtime` says whether the object exists in the running Cascadeur and, for a class, lists `missing_from_stub`.

### `run_python`

Runs Python inside Cascadeur on the GUI thread, with `csc`, `app`, `scene` (the domain scene), `view_scene` and `ui` in scope. Use it only when no dedicated tool covers the operation: call `api_search` first to check the API, and prefer the scene, pose and timeline tools for normal work. It freezes Cascadeur's UI until the code finishes.

| Parameter | Default | Notes |
|---|---|---|
| `code` | required | Python source. A lone expression is evaluated; otherwise the statements run and a variable named `result` is returned. Variables do not carry over between calls. |
| `timeout` | `60` | Seconds to wait for the code to finish, at most 600. When the time runs out the call fails; code that has already started keeps running. |

Returns `ok`, `stdout` and `value` (the `repr` of the lone expression or of `result`; absent when there is neither). On an exception, `ok` is `false`, `error` holds the traceback and `stdout` holds the output so far. `scene.modify*` and `edit` return `False` when their callback failed, so check that value.

### `get_log`

Reads the tail of Cascadeur's log file, where errors and Python tracebacks land. Use it after a tool returns an unexpected error or traceback, or while debugging `run_python`. It reads the file directly and does not use the GUI thread.

| Parameter | Default | Notes |
|---|---|---|
| `lines` | `60` | Number of trailing log lines to return. |
| `contains` | — | Keep only lines that contain this text (not case-sensitive); applied before `lines`. |

Returns `path` (the log file, `%LOCALAPPDATA%\Nekki Limited\Cascadeur\logs\cascadeur_log.log` unless the `CASCADEUR_LOG` environment variable names another file) and `lines`. If the file does not exist, it returns an `error` with the path.

### `batch`

Runs several tool calls in order and returns every result in one response. Use it for repetitive workflows, such as seeking a frame, setting a pose and adding a key, instead of one request per step. Each call runs exactly like a direct call, so a scene change keeps the undo step its tool gives it: one `undo` takes back one call, not the whole batch.

| Parameter | Default | Notes |
|---|---|---|
| `calls` | required | Tool calls in execution order, each `{"tool": name, "arguments": {...}}`; `arguments` defaults to `{}`. A call to `batch` itself is refused. Background tools return their usual `job_id` instead of waiting. |
| `stop_on_error` | `true` | `true` stops at the first failed call; `false` runs the remaining calls too. |
| `timeout` | `110` | Time budget for the whole batch, in seconds, at most 600; a value that is not a positive number is refused. Before each call the budget left is compared with that call's own `timeout` (60 seconds when it has none); a call that cannot fit does not start, and a call that has started is not interrupted. |

Returns `ok`, `count` and `results`; each entry of `results` has `index`, `tool`, `ok` and `result` (the called tool's own result; for an image tool, only the number of `images` and its `meta` note). When a call fails or the budget runs out, `ok` is `false` and `results` still holds the completed calls: `stopped_at` is the index where the batch stopped, `failed` lists the failed indexes when `stop_on_error` is `false`, and `not_run` lists the calls that did not start. Invalid input (a nested `batch`, a missing `tool`, an `arguments` that is not an object) is refused before any call runs.

### `get_job`

Looks up the status, progress, result or error of a background job. Use it with the `job_id` that a background tool returned. `wait_s` waits on the HTTP thread, so Cascadeur's UI is not blocked.

| Parameter | Default | Notes |
|---|---|---|
| `job_id` | required | Job id returned by a background tool, for example `"job-3"`. |
| `wait_s` | `0` | Seconds to wait for the job to finish, at most 60; the wait ends as soon as the job ends. |

Returns `ok`, `job_id`, `kind`, `status` (`running`, `done`, `failed`, `cancelled` or `lost`), `done` (`true` once the job has ended in any status), `progress` (0 to 1, or `null` when unknown), `elapsed_s`, `started_at`, `finished_at`, `result` (the tool's own result once the job is done) and, for a failed job, `error`, `hint` and `traceback`. `cancel_error` holds the traceback if the tool's cancel hook failed. A job that was running when the server restarted is `lost`; an unknown id returns `ok: false`.

### `list_jobs`

Lists recent background jobs, newest first, with their kind, status and progress. Use it to find a job id. At most 100 jobs are kept; the oldest finished ones are dropped first.

| Parameter | Default | Notes |
|---|---|---|
| `limit` | `20` | Maximum number of jobs returned, 1 to 100; other values are clamped into that range. |

Returns `ok`, `count` and `jobs`; each job has the same fields as in `get_job` (`job_id`, `kind`, `status`, `progress`, `result` and so on). A job that was running when the server restarted is listed as `lost`.

### `cancel_job`

Stops a running background job and marks it `cancelled`. Use it when a job is stuck or no longer needed. It calls the tool's own cancel hook (for example the Stop button of a render dialog), so no work or modal dialog is left running.

| Parameter | Default | Notes |
|---|---|---|
| `job_id` | required | Job id returned by a background tool. |

Returns `ok`, `cancelled: true` and the job's final fields as in `get_job`. A job that has already ended is refused with `ok: false`, because only a running job can be cancelled.

### `undo`

Undoes the last scene change, or the last several, with Cascadeur's own `Scene.Undo` action, called through `ActionManager` and not as a synthetic Ctrl+Z, so a focused text field cannot swallow it. Use it to back out a tool call that went wrong. The step names are read from Cascadeur's log.

| Parameter | Default | Notes |
|---|---|---|
| `steps` | `1` | Number of undo steps to take, 1 to 100; a larger number is capped at 100, a smaller one is refused. |

Returns `ok`, `action`, `requested`, `count` and `steps`: the names of the steps Cascadeur actually undid, in the order they were undone. If the undo stack runs out, fewer steps are undone and `hint` says so; if nothing could be undone, `ok` is `false`.

### `redo`

Redoes the last undone scene change, or the last several, with Cascadeur's own `Scene.Redo` action through `ActionManager`. Use it after `undo` when the undone change was the wanted one. The step names are read from Cascadeur's log.

| Parameter | Default | Notes |
|---|---|---|
| `steps` | `1` | Number of steps to redo, 1 to 100; a larger number is capped at 100, a smaller one is refused. |

Returns `ok`, `action`, `requested`, `count` and `steps`: the names of the steps Cascadeur actually redid, in the order they were redone. If fewer steps are left, only those are redone and `hint` says so; if nothing is left to redo, `ok` is `false` and `hint` says why.

### `get_undo_history`

Lists the recent steps of the current scene's undo history, oldest first, each marked `applied` or `undone`. Only the steps MCP Plus made (names starting with `MCP: `) are listed, because Cascadeur has no Python API for its full history; the list is rebuilt from Cascadeur's log. The record starts over when Cascadeur starts, when its memory manager clears all history, and when a scene is created or loaded.

| Parameter | Default | Notes |
|---|---|---|
| `limit` | `20` | Maximum number of steps returned, 1 to 200; a larger number is capped, a smaller one is refused. |

Returns `ok`, `steps` (each with `index`, `name` and `state`), `position` (the number of applied MCP steps), `count` (all tracked steps), `truncated` (`true` when `limit` left older steps out), `next_undo` and `next_redo` (the MCP step the next `undo` or `redo` would move), `recorded_since` and `reset_reason` (which event began the current record; MCP steps from before it are in no undo stack) and `log` (the log path). A `warning` is added when the log cannot be read.

### `list_actions`

Lists the Cascadeur action keys (`actionId`, for example `Commands.Rig mode.On`) found in the live UI, with labels. Use it when no dedicated tool exists for an operation, then trigger the key with `call_action`. The list comes from the live QML tree, so actions of tools that have never been opened may be missing.

| Parameter | Default | Notes |
|---|---|---|
| `query` | — | Only actions whose `actionId` contains this text; not case-sensitive. |
| `include_hidden` | `true` | `true` also includes actions of hidden windows and elements. |

Returns `actions`, sorted by `actionId`; each has `actionId`, a `label` when its element has a tooltip or text, and `on_screen: true` when a visible element carries it. A `note` repeats the warning about tools that were never opened.

### `call_action`

Triggers a Cascadeur action by key through `ActionManager.call_action`, exactly like its toolbar button or menu item. Use a dedicated tool first when one exists (`set_pose`, `set_key`, `select_objects` and others); `call_action` is for actions that have no wrapper. Use `list_actions` to find keys.

| Parameter | Default | Notes |
|---|---|---|
| `key` | required | Cascadeur `actionId` from `list_actions`, for example `Commands.Rig mode.On`. |
| `settle_ms` | `150` | Milliseconds to wait after the action so the UI can update, 0 to 5000. |

Returns `called`, the key that was triggered.
