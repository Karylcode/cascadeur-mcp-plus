**English** | [繁體中文](tools.zh-TW.md) · [Documentation index](README.md)

# Tools

MCP Plus has 18 tools. `run_python` covers the official server's `run_script`; the other 17 are new.

- Scene and Python: `run_python`, `get_scene_info`, `get_object_info`, `project_point`
- Screen and UI inspection: `screenshot`, `ui_tree`, `dialogs`
- UI input: `ui_click`, `ui_hover`, `ui_drag`, `ui_key`, `ui_type`, `ui_set_property`, `ui_invoke`
- Actions, Rig mode and log: `list_actions`, `call_action`, `enter_rig_mode`, `get_log`

A failed call comes back as a tool error with a message.

## Common parameters

| Parameter | Meaning |
|---|---|
| `window` | Window index or title substring. Default: the focused window, else the main Cascadeur window. `ui_tree` defaults to all open windows. |
| `ref` | Element ref from the latest `ui_tree` (such as `0:12`) or `dialogs` (such as `d0:3`) result. Refs reset on every `ui_tree` or `dialogs` call. |
| `query` | Text, tooltip, actionId, objectName or placeholder text of an on-screen element. Exact matches rank first, then clickable and enabled elements. |
| `settle_ms` | Milliseconds to wait for the UI after the action, at most 5000. Default 150; `ui_drag` uses 300. Only `ui_click`, `ui_hover`, `ui_drag`, `ui_key`, `ui_type` and `call_action` take it. |

## Scene and Python

### `run_python`

Runs Python inside Cascadeur on the GUI thread with `csc`, `app`, `scene` (the domain scene), `view_scene` and `ui` in scope.

| Parameter | Default | Notes |
|---|---|---|
| `code` | required | Python source |
| `timeout` | 60 | Seconds to wait, at most 600 |

Returns `ok`, `stdout`, and `value` (the repr of a lone expression, or of a variable named `result`).
On an exception it returns `ok: false` with the traceback. `scene.modify*` / `edit` return `False` when
their callback failed; check it.

### `get_scene_info`

Summarizes the current scene.

| Parameter | Default | Notes |
|---|---|---|
| `max_objects` | 80 | Number of objects listed in the sample |

Returns the scene name, path, number of open scenes and gravity per frame; the current, first and last frame
and the animation size; the object count, counts by type and a sample of objects; the selection (count and up
to 50 names); counts of the `RigInfo`, `AnimationInfo`, `QrtInfo`, `Joint`, `MeshObject` and `AutoPosing`
behaviours; and license flags (pro features, export).

### `get_object_info`

Describes objects by name.

| Parameter | Default | Notes |
|---|---|---|
| `name` | required | Exact name first; if nothing matches, a case-insensitive partial match |
| `max_properties` | 40 | Property names listed per behaviour |

Returns up to 5 objects, each with name, type, id and behaviours with their property names.

### `project_point`

Projects scene objects to main-window coordinates in the main 3D viewport, for `ui_click` / `ui_drag` `x`/`y`.

| Parameter | Default | Notes |
|---|---|---|
| `names` | required | Array of scene object names |
| `fov_deg` | 30 | Vertical field of view of the viewport camera |

Returns, per name, `window_xy`, `on_screen`, `depth` and the world position (an object behind the camera
gets `on_screen: false`), plus the viewport rect and the camera position and target. Only perspective
cameras are supported. The 30° default was measured on Cascadeur 2026.2.3.

## Screen and UI inspection

### `screenshot`

Captures a Cascadeur window, or one element, with Win32 `PrintWindow`.

| Parameter | Default | Notes |
|---|---|---|
| `window` | focused / main window | |
| `ref` | — | Capture only this element |
| `max_width` | 1280 | Downscale to this width |
| `format` | `jpeg` | `jpeg` or `png` |
| `restore` | `false` | Un-minimize the window first |

Returns the image and a JSON note with `window`, `window_size`, `image_size` and `window_units_per_pixel`
(converts image pixels to window coordinates), plus `crop_rect` for an element. A minimized window returns
an error unless `restore=true`. A black image adds a warning.

### `ui_tree`

Lists open windows and on-screen UI elements, with refs for the other `ui_*` tools.

| Parameter | Default | Notes |
|---|---|---|
| `window` | all windows | Limit to one window |
| `query` | — | Only elements whose text, tooltip, actionId, objectName or type contains this |
| `interactive_only` | `false` | Only clickable, input or action elements |
| `include_hidden` | `false` | Include hidden windows and items |
| `limit` | 150 | Maximum number of elements |

Returns `windows`, a flat list of `elements` (ref, type, text / title / toolTip / placeholderText, actionId,
objectName, `checked` / `active` when true, `enabled: false` when disabled, `clickable`, `input`, and
`rect` as `[x, y, width, height]`), `matched` and `truncated`.

### `dialogs`

No parameters. Lists visible dialogs, popups and secondary windows with their texts, and their buttons as refs.

## UI input

Clicks, hovers, keys and drags are synthesized through Qt's window-system input path (QtTest).

### `ui_click`

Clicks the center of an element, or a window point.

| Parameter | Default | Notes |
|---|---|---|
| `ref` / `query` | — | Target element |
| `x`, `y` | — | Window coordinates instead of an element |
| `window` | focused / main window | |
| `button` | `left` | `left`, `right` or `middle` |
| `double` | `false` | Double click |
| `modifiers` | — | For example `["ctrl"]`, `["shift"]`; also `alt`, `meta` / `win` |
| `index` | 0 | Pick the n-th best match for `query` |
| `force` | `false` | Skip the Quick Rigging guard (see [Safety](safety.md)) |
| `settle_ms` | 150 | |

Returns the clicked target, and `other_matches` when `query` matched several elements. Warns when the
element is disabled; fails when the window is hidden.

### `ui_hover`

Moves the mouse over an element or point (shows tooltips and hover menus).

| Parameter | Default | Notes |
|---|---|---|
| `ref` / `query` | — | Target element |
| `x`, `y` | — | Window coordinates instead of an element |
| `window` | focused / main window | |
| `settle_ms` | 150 | |

### `ui_drag`

Presses at the start, moves in steps with the button held, and releases at the end. Each step is its own
GUI-thread job, so AutoPosing and other live updates run between moves.

| Parameter | Default | Notes |
|---|---|---|
| `object` | — | Start at a scene object, projected into the 3D viewport (e.g. `mixamorig:LeftHand_MainPoint`) |
| `x`, `y` | — | Start at window coordinates |
| `ref` / `query` | — | Start at the center of an element |
| `window` | focused / main window | |
| `to_x`, `to_y` | — | End point in window coordinates |
| `dx`, `dy` | — | End point as an offset from the start; negative `dy` moves up |
| `button` | `left` | `left`, `right` or `middle` |
| `modifiers` | — | As in `ui_click` |
| `steps` | 20 | Number of move events (1–500) |
| `duration_ms` | 400 | Total time spent moving |
| `hold_ms` | 80 | Pause after pressing, before moving |
| `settle_ms` | 300 | Wait after releasing |
| `method` | `qtest` | `qtest` uses Qt's window-system input path; `event` sends `QMouseEvent`s directly |
| `fov_deg` | 30 | Field of view for projecting `object` |

The start is `object`, else `x`/`y`, else `ref`/`query`. The end is `to_x`/`to_y`, else `dx`/`dy`; one of
them is required. The button is always released, even if a move fails. Returns the window, the actual
start and end points, `steps`, `duration_ms`, `method` and the target. If a dialog opened during the drag,
the result also has `dialogs` and a warning: answer the dialog and drag again.

### `ui_key`

Presses keys or shortcuts.

| Parameter | Default | Notes |
|---|---|---|
| `keys` | required | Space-separated combos, e.g. `ctrl+s`, `Escape`, `ctrl+a Delete`, `F` |
| `window` | focused / main window | |
| `settle_ms` | 150 | |

Modifiers: `ctrl`, `shift`, `alt`, `meta` / `win`. Keys: letters and digits, `F1`–`F12`, `Enter`, `Esc`,
`Delete`, `Backspace`, `Tab`, `Space`, arrow keys, `Home`, `End`, `PageUp`, `PageDown`, `Insert`, `Plus`,
`Minus`, `Comma`, `Period`, and other Qt key names without the `Key_` prefix.

### `ui_type`

Types text into the focused element, or focuses an element by `ref` / `query` first. Works for any Unicode
text. If that element is a container, it types into the first text input inside it.

| Parameter | Default | Notes |
|---|---|---|
| `text` | required | |
| `ref` / `query` | — | Element to focus first |
| `window` | focused / main window | |
| `replace` | `false` | Clear the field first |
| `submit` | `false` | Press Enter afterwards |
| `release_focus` | `false` | Drop keyboard focus afterwards, so shortcuts reach Cascadeur again |
| `settle_ms` | 150 | |

### `ui_set_property`

Sets a QML property on an element (e.g. `checked`, `value`, `text`, `currentIndex`). It bypasses input
handlers; prefer `ui_click` when possible.

| Parameter | Default | Notes |
|---|---|---|
| `ref` | required | |
| `name` | required | Property name |
| `value` | required | New value |

Returns `ok` and the value before and after. It has no `settle_ms`.

### `ui_invoke`

Invokes a no-argument QML method or signal on an element (e.g. `clicked`, `toggle`, `open`, `close`).
Useful when an element cannot be clicked on screen.

| Parameter | Default | Notes |
|---|---|---|
| `ref` | required | |
| `method` | required | Method or signal name |

It has no `settle_ms` and no `force`; the Quick Rigging guard always applies.

## Actions, Rig mode and log

### `list_actions`

Lists Cascadeur action keys (`actionId`, e.g. `Commands.Rig mode.On`) found in the UI, with labels.

| Parameter | Default | Notes |
|---|---|---|
| `query` | — | Only keys that contain this |
| `include_hidden` | `true` | Include hidden windows and items |

The list comes from the live QML tree, so actions of tools that have never been opened may be missing.

### `call_action`

Triggers an action by key through `ActionManager.call_action`, like its toolbar button or menu item.

| Parameter | Default | Notes |
|---|---|---|
| `key` | required | For example `Commands.Rig mode.On` |
| `settle_ms` | 150 | |

### `enter_rig_mode`

Enters Rig mode through Cascadeur's own `rig_mode.on` entry points. Required before Quick Rigging's
**Add rig elements** / **Generate rig**. Runs on the scene idle event, like the official script server.

| Parameter | Default | Notes |
|---|---|---|
| `color` | `[0, 0.5, 0]` | Rig color, RGB 0..1 |

What it does depends on the scene:

| Scene | Action |
|---|---|
| Exactly one `AnimationInfo` | Already in Rig mode; nothing changes |
| More than one `AnimationInfo` | Returns an error; resolve it manually before rigging |
| A `RigInfo` exists | `rig_mode.on.run_raw` |
| No `RigInfo` (freshly imported model) | `rig_mode.on.run_after_import`: the step a UI import does automatically but a Python `import_model` skips. It creates the `RigInfo`, enters Rig mode and shows the "Rig mode helper" dialog; press its *Yes* button with `dialogs` / `ui_click` to open Quick Rigging. |

Returns `rig_mode`, `changed`, the `path` used and the `AnimationInfo` count.

### `get_log`

Reads the tail of Cascadeur's log (`%LOCALAPPDATA%\Nekki Limited\Cascadeur\logs\cascadeur_log.log`),
where errors and Python tracebacks land.

| Parameter | Default | Notes |
|---|---|---|
| `lines` | 60 | Number of lines from the end |
| `contains` | — | Keep only lines that contain this (case-insensitive) |
