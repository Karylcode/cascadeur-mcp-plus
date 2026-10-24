**English** | [繁體中文](limitations.zh-TW.md) · [Documentation index](README.md)

# Behavior and limitations

These limits and behaviours were observed in testing on Cascadeur 2026.2.3 on Windows. Other Cascadeur versions may behave differently. `get_status` lists the tools that this Cascadeur build cannot use.

## Tested setup

- Only Windows 10 and 11 with Cascadeur 2026.2.3 has been tested.
- These areas depend on the Cascadeur version: the video export window (`render_video`, `render_image_sequence`), the AutoPosing lock (`set_pose`, `lock_controllers`), animation layers, video mocap, cycles, AutoPhysics, AI inbetweening (`ai_inbetween`, `generate_motion`), retargeting and the interface tools.

## Poses and AutoPosing

- **AutoPosing may not hold a pose on every frame.** In testing, with `autoposing=true`, the pose that `set_pose` wrote on a frame after another posed frame was reset, and the hand stayed 22 to 51 cm short of its target. The call then re-applies the requested values without AutoPosing in the same call, so the named controllers land exactly. The result has `autoposing_applied: false`, a `warning`, and three undo steps. The rest of the body does not follow on that frame, so key the frames and check the numbers with `get_pose`. A controller that still misses by more than `tolerance_cm` (default 1.0 cm; rotations 1.0 degree) makes the call return `ok: false` with the errors.
- **Writing one controller can move its neighbours.** The rig keeps the arm lengths, so moving a hand moves the elbow. The rig can also refuse a value: a head that the rig pulls back is reported with `ok: false`. Writing an elbow to a value that the rig does not hold misses it by about 4 cm, so `examples/wave.py` writes the hand only.
- **A second `set_pose` on the same frame can revert the first call's other controllers.** The fallback checks only the controllers named in the call. Write all controllers of a frame in one call when the body must follow, or use `autoposing=false`.
- **Main control points have no rotation.** In 2026.2.3 they have positions only. Rotate the boxes and joints instead, for example `hand_Box_l`.

## Undo

- Each scene-changing tool makes one `MCP:` undo step. `set_pose` with AutoPosing takes two undos: its own step, then Cascadeur's AutoPosing.Autolock. The fallback described above takes three.
- `set_animation_range` cannot be undone.
- IK/FK, retargeting, cleaning, cycles, mocap and Quick Rigging run Cascadeur's own commands. One Ctrl+Z may not revert them completely, and `get_undo_history` does not list them.
- The history restarts when Cascadeur restarts or a scene loads.

## Locks and scene state

- `lock_controllers` cannot unlock, because the 2026.2.3 API has no unlock.
- MCP Plus write tools respect animation layer locks; Cascadeur's Python API does not. See [Safety](safety.md).
- `list_open_scenes` reports `modified` as `null`, because 2026.2.3 has no public unsaved-changes flag.
- The display settings (view mode, ghosts, trajectories, viewport layout, silhouette and surface) are global interface state, not scene data. Closing a test scene does not restore them.

## Output and display

- `render_video`, `render_image_sequence` and `render_contact_sheet` render with the clean View display. Pass `keep_overlays=true` to render the viewport as it is. View mode uses the model's own material: the Cascy sample comes out glossy silver, not the matte white-grey of the AutoPosing view.
- The renderer still draws its own `Frame: N` label in a corner. No setting that turns it off was found in Cascadeur 2026.2.3.
- The contact sheet fits its camera to the control points, so hair or cloth outside them can be cut off.
- `render_video` and `render_image_sequence` drive Cascadeur's official Export video window. Cascadeur must not be minimized, and no other export window may be open. In one run, switching scene tabs during an export stalled it at 86%.

## Long operations

- Calls run on Cascadeur's GUI thread, so a long job blocks it. GUI-bound calls such as `get_scene_info` queue behind a running cleaning job. Poll `get_job` at short intervals instead of waiting in one long call.

## AI, generated motion and physics

- `generate_motion` overwrites posed keys and creates no undo step, so copy the animation first. Its first run in each Cascadeur process loads a model for about 20 to 26 seconds, and calls made in that time can time out.
- `ai_inbetween` needs a Pro license and at least two keys.
- `physics_snap` applies to all frames, and Physics Assistant turns off after a snap, so call `physics_enable` again before the next one.
- `physics_set_priority_frame` needs a frame inside the keyed range.
- Cleaning uses Cascadeur's default settings, and `prepare_keys_by_fulcrums` must run first. Judge a cleaning by the `same_point_frames` comparison, not by the total.

## Retargeting, rigging and mocap

- `retarget_animation` needs a Pro or Studio license, works between humanoid rigs, and the source must have keys.
- `quick_rig` is for skeleton-only characters. By default it enters Rig mode itself, and in one live run that step crashed Cascadeur. Import the model with `import_model`, which enters Rig mode, and then call `quick_rig`; that order passed the live tests.
- **`video_mocap` is experimental.** In testing on Cascadeur 2026.2.3, cancelling a running mocap job crashed Cascadeur twice, and the cause is not known. Save your scene before calling it, and do not cancel the job while Cascadeur's Mocap window is open.
- `video_mocap` has no public Python API. It drives Cascadeur's Mocap dialog and needs the model file `resources\mocap.pt`, about 825 MB, in the data folder (`CASCADEUR_DATA`). If the file is missing, the call is refused with a hint: open **View > Mocap** once with a video and let Cascadeur download the model, or copy `mocap.pt` into place.
- The motion quality of `video_mocap` has not been verified on real video, and non-Pro licenses have not been tested.

## Import and export

- USD is not supported. `import_model` takes FBX and GLB.
- `import_animation` needs matching joint names. Use `retarget_animation` between different skeletons.
- A failed import can leave the model in the scene. Do not import it again.

## Interface tools

- The interface tools are the last resort. Coordinates and layouts change between Cascadeur versions, so prefer an `actionId` or visible text over coordinates.
- A minimized window renders nothing, so restore it with `screenshot` and `restore=true` first.

## Rig naming

- The aliases were checked on Cascadeur's standard rig and on the Cascy, UE4 Mannequin and Roblox R15 samples. Mixamo-style names were only unit tested.

## Not on PyPI yet

- Install from a checkout, as described in [Installation](installation.md).
