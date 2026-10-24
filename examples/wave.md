# Example: a waving animation on Cascy, exported as an mp4

The task for the agent, in one sentence:

> Make a 2-second waving animation on Cascy and export an mp4.

This page lists the tool calls an agent makes for that task, with their arguments, in the order it makes
them. The numbers are the ones measured on the Cascy sample in Cascadeur 2026.2.3 (rounded to 0.1 cm).
`examples/wave.py` makes the same calls over HTTP and computes the numbers itself, so it also works on another
rig.

Before you start: Cascadeur 2026.2.3 is running with MCP Plus (Scripts > MCP > Start script server, or
autostart). The sample `C:\Program Files\Cascadeur\samples\Cascy.casc` comes with Cascadeur. The sample is opened in
a new tab, so your own tabs are not touched, and it is closed again without saving.

## The calls

| # | Tool | Arguments | What it does |
|---|---|---|---|
| 1 | `get_status` | `{}` | Checks the Cascadeur version (2026.2.3 is the tested one). |
| 2 | `open_scene` | `{"path": "C:\\Program Files\\Cascadeur\\samples\\Cascy.casc"}` | Opens the sample in a new tab. |
| 3 | `get_timeline` | `{}` | Reads the fps (30.0 for Cascy). The sample has a one-frame animation, so the range must be widened before keys are added. |
| 4 | `set_animation_range` | `{"first": 0, "last": 59}` | 2 seconds at 30 fps: frames 0 to 59, which is 60 frames. Not undoable. |
| 5 | `autoposing_enable` | `{"enabled": true}` | Turns AutoPosing on for the pose calls below. |
| 6 | `set_key` | `{"frames": [0]}` | Keys the whole rest pose on frame 0, so there is a baseline before posing. |
| 7 | `get_pose` | `{"controllers": ["right_shoulder"], "frame": 0}` | Reads the right shoulder: `[-2.4, 145.6, -5.2]`. |
| 8 | `get_pose` | `{"controllers": ["left_shoulder"], "frame": 0}` | Reads the left shoulder: `[2.4, 145.6, -5.2]`. Together with step 7 it gives the body's lateral axis. |
| 9 | `get_pose` | `{"controllers": ["right_hand"], "frame": 0}` | Reads the rest hand: `[-25.2, 96.6, -0.3]`. |
| 10 | `get_pose` | `{"controllers": ["right_elbow"], "frame": 0}` | Reads the rest elbow: `[-23.0, 117.4, -10.0]`. |
| 11 | `set_pose` | see below, frame 6 | Raises the hand to the centre of the wave. |
| 12 | `set_pose` | see below, frame 12 | Swings the hand outward. |
| 13 | `set_pose` | see below, frame 18 | Swings the hand inward. |
| 14 | `set_pose` | see below, frame 24 | Swings the hand outward. |
| 15 | `set_pose` | see below, frame 30 | Swings the hand inward. |
| 16 | `set_pose` | see below, frame 35 | Swings the hand outward. |
| 17 | `set_pose` | see below, frame 41 | Swings the hand inward. |
| 18 | `set_pose` | see below, frame 47 | Brings the hand back to the centre. |
| 19 | `set_pose` | see below, frame 59 | Returns the arm to rest on the last frame. |
| 20 | `set_interpolation` | `{"kind": "bezier_clamped", "start": 0, "end": 59}` | Makes the keys clamped Bezier, which does not overshoot. |
| 21 | `get_pose` | `{"controllers": ["right_hand"], "frame": f}` and `{"controllers": ["right_shoulder"], "frame": f}` for each pose frame `f` | Checks the result (step 22). |
| 22 | (check) | none | The hand must be at least 0.25 × arm above the shoulder (13.6 cm; measured 29.9 cm), and the side-to-side swing must be at least half of 2 × 0.16 × arm (8.7 cm; measured 17.4 cm). |
| 23 | `get_display_state` | `{}` | Records the current view mode (`autoposing` here) so it can be put back. |
| 24 | `set_view_mode` | `{"mode": "mesh"}` | Shows the character as a mesh for the sheet. |
| 25 | `render_contact_sheet` | `{"frames": [0, 6, 12, 18, 24, 30, 35, 41, 47, 59], "columns": 5, "cell_width": 384, "camera_preset": "front", "path": "C:\\Users\\<you>\\AppData\\Local\\Temp\\cascadeur-mcp-plus-tests\\T56\\wave_contact.png", "overwrite": true}` | Writes the PNG grid. The front camera keeps the side-to-side swing visible. |
| 26 | `set_view_mode` | `{"mode": "autoposing"}` | Puts the view mode back. |
| 27 | `render_video` | `{"path": "C:\\Users\\<you>\\AppData\\Local\\Temp\\cascadeur-mcp-plus-tests\\T56\\wave.mp4", "start": 0, "end": 59, "camera_preset": "front", "overwrite": true}` | Starts the MP4 export. It returns a `job_id` at once. |
| 28 | `get_job` | `{"job_id": "<job_id from step 27>", "wait_s": 30}` | Repeated until `status` is `done`. The result gives the path, the size and the frame count. |
| 29 | `close_scene` | `{"scene": "C:\\Program Files\\Cascadeur\\samples\\Cascy.casc", "discard_changes": true}` | Closes the test tab without saving. Wait 0.5 seconds before the next call. |

`<you>` stands for your Windows user name; `%TEMP%` is the same folder.

### The pose arguments (steps 11 to 19)

Each pose is one `set_pose` that writes the right hand only, with `autoposing` false and `key` true. The hand is
at the centre of the raise, `[-26.8, 175.4, -5.2]`, moved outward or inward by 8.7 cm along the body's lateral axis
`[-1, 0, 0]` for the swing. The rig solves the elbow from the hand and keeps the upper-arm length (26.4 cm, from the arm joint to the elbow) and the
forearm length (23.1 cm); the script checks those lengths on every pose frame (`elbow_length_error_cm`).

| Frame | Swing | `values` (right hand, cm, world space) |
|---|---|---|
| 6 | centre | `{"right_hand": {"position": [-26.8, 175.4, -5.2]}}` |
| 12 | outward | `{"right_hand": {"position": [-35.5, 175.4, -5.2]}}` |
| 18 | inward | `{"right_hand": {"position": [-18.1, 175.4, -5.2]}}` |
| 24 | outward | `{"right_hand": {"position": [-35.5, 175.4, -5.2]}}` |
| 30 | inward | `{"right_hand": {"position": [-18.1, 175.4, -5.2]}}` |
| 35 | outward | `{"right_hand": {"position": [-35.5, 175.4, -5.2]}}` |
| 41 | inward | `{"right_hand": {"position": [-18.1, 175.4, -5.2]}}` |
| 47 | centre | `{"right_hand": {"position": [-26.8, 175.4, -5.2]}}` |
| 59 | rest | `{"right_hand": {"position": [-25.2, 96.6, -0.3]}}` |

Every call has `"autoposing": false` and `"key": true`. For example, frame 12 is:

```json
{"tool": "set_pose",
 "arguments": {"values": {"right_hand": {"position": [-35.5, 175.4, -5.2]}},
               "frame": 12, "autoposing": false, "key": true}}
```

## Why the example writes the hand only, without AutoPosing

- With `autoposing: true`, the first pose (frame 6) landed on its target, but a `set_pose` on a later frame (frame 12)
  was reset by AutoPosing: the hand stayed 22 to 51 cm short of its target after the AutoPosing update. `set_pose`
  then re-applies the values without AutoPosing in the same call (`autoposing_applied` false, a `warning`, three undo
  steps), so the hand is exact either way. The example uses `autoposing: false` on every frame to keep one path.
- Writing the elbow as well misses it by about 4.4 cm on every pose, whatever the order of the two writes. The rig
  keeps the arm's lengths, so the elbow follows the hand and the rig picks its own bend, which is not the bend a
  two-bone solve would choose. The example therefore writes the hand only, and the rig's elbow is what the checks see.

## Running the script instead

```bat
python examples\wave.py
```

Cascadeur must be open with MCP Plus running. The script prints a JSON summary (the fps, the checks, the output
paths, the timings) and exits with 0 when the MP4 and the contact sheet were written and the checks passed.

Options:

- `--url`: the MCP Plus address (default `http://127.0.0.1:48766`).
- `--sample`: the scene to open (default the Cascy sample).
- `--out`: the folder for the outputs (default `%TEMP%\cascadeur-mcp-plus-tests\T56`).

## Outputs

| File | What it is | Measured in the final run |
|---|---|---|
| `wave.mp4` | The animation, rendered by Cascadeur's own export window from the front, 1920 × 1080, MEDIUM quality. | 60 frames, about 2 seconds, 1,026,713 bytes. |
| `wave_contact.png` | A contact sheet of frames 0, 6, 12, 18, 24, 30, 35, 41, 47 and 59, with the frame number in each cell. | 707,403 bytes. |

Both are written under `%TEMP%\cascadeur-mcp-plus-tests\T56\`. The video is checked by the tool (frame count and
duration) and reported; whether the motion looks right is for you to judge.

## Notes

- **Timing.** The pose frames are fixed fractions of the clip (0.10, 0.20, ..., 0.80 of the way through), so the
  same timing works at another fps. The clip is 2 seconds at the scene's fps.
- **Checks.** Step 22 uses the numbers the tools return, not the picture. If the hand is not raised or does not
  swing, the script stops and prints what it measured.
- **Frame labels.** The render shows Cascadeur's own `Frame: N` text in a corner. The contact sheet labels each cell
  with its frame number.
- **Cleanup.** The sample is closed with `discard_changes`, so nothing is saved. The view mode is put back to what it
  was before the contact sheet.
