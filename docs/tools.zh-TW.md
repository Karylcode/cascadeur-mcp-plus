[English](tools.md) | **繁體中文** · [文件索引](README.zh-TW.md)

# 工具

MCP Plus 有 105 個工具。以下依主題分組。每個工具都有一句說明、參數表（含預設值和說明），以及回傳內容的說明。

- **場景和檔案**：`new_scene`、`open_scene`、`save_scene`、`list_open_scenes`、`switch_scene`、`close_scene`、`import_model`、`import_animation`、`list_fbx_takes`、`export_scene`、`get_scene_info`、`get_object_info`
- **角色和選取**：`list_characters`、`list_controllers`、`select_objects`、`get_selection`、`clear_selection`、`enter_rig_mode`、`list_rig_templates`、`quick_rig`、`retarget_animation`
- **姿勢和關鍵影格**：
  - **AutoPosing 和姿勢**：`get_pose`、`set_pose`、`mirror_pose`、`copy_pose`、`paste_pose`、`reset_pose`、`get_autoposing_state`、`autoposing_enable`、`autoposing_update`、`lock_controllers`
  - **時間軸和關鍵影格**：`get_timeline`、`set_frame`、`set_animation_range`、`set_key`、`delete_key`、`list_keys`、`select_frames`、`create_cycle`、`remove_cycles`
  - **插值和 IK/FK**：`set_interpolation`、`set_ik_fk`、`reset_tangents`、`reset_easing`
  - **動畫圖層**：`list_layers`、`create_layer`、`create_layer_folder`、`delete_layer`、`set_layer_visible`、`set_layer_locked`、`rename_layer`、`move_to_layer`
- **物理和 AI**：`get_physics_state`、`physics_enable`、`physics_snap`、`physics_freeze`、`physics_set_priority_frame`、`physics_show_fulcrums`、`fix_foot_sliding`、`fix_collisions`、`prepare_keys_by_fulcrums`、`ai_inbetween`、`generate_motion`、`video_mocap`
- **檢視和輸出**：`render_image`、`render_contact_sheet`、`render_video`、`render_image_sequence`
- **攝影機和顯示**：`get_camera`、`set_camera`、`camera_preset`、`frame_objects`、`track_object`、`get_display_state`、`set_view_mode`、`set_ghosts`、`set_trajectories`、`set_viewport_layout`、`set_silhouette`、`set_surface`
- **介面操作（最後手段）**：`ui_tree`、`ui_click`、`ui_drag`、`ui_hover`、`ui_key`、`ui_type`、`ui_set_property`、`ui_invoke`、`dialogs`、`screenshot`、`project_point`
- **其他**：`get_status`、`api_search`、`api_describe`、`run_python`、`get_log`、`batch`、`get_job`、`list_jobs`、`cancel_job`、`undo`、`redo`、`get_undo_history`、`list_actions`、`call_action`

每個結果都是 JSON 物件。大多數工具成功時回傳 `"ok": true`。失敗時回傳 `"ok": false`、`error` 說明，通常還有 `hint`，請依照提示處理。有些工具（例如 `get_scene_info`）直接回傳資料，不含 `ok`。如果呼叫無法執行，例如工具名稱不存在、參數型別錯誤或逾時，會以工具錯誤和說明訊息回傳。

## 共用參數

| 參數 | 說明 |
|---|---|
| `window` | 視窗編號或標題的一部分。預設是目前焦點所在的視窗，沒有的話用 Cascadeur 主視窗。省略時，`ui_tree` 會列出所有視窗。 |
| `ref` | 最近一次 `ui_tree`（例如 `0:12`）或 `dialogs`（例如 `d0:3`）回傳的元素參照。每次呼叫 `ui_tree` 或 `dialogs`，參照都會重設。 |
| `query` | 畫面上元素的文字、提示、actionId、objectName 或 placeholder 文字。完全符合的優先，其次是可點擊、已啟用的元素。 |
| `settle_ms` | 操作後等待介面更新的毫秒數，最多 5000。預設 150；`ui_drag` 預設 300。`ui_click`、`ui_drag`、`ui_hover`、`ui_key`、`ui_type` 和 `call_action` 的結構描述列有這個參數。 |
| `start`、`end`、`frame`、`frames` | 從 0 開始的整數影格。也接受數字字串，例如 `"120"`。 |
| `controllers` | 控制器的名稱或別名，可以是清單，也可以是一個字串。`list_controllers` 會列出它們。 |

位置單位是公分，旋轉單位是度（Euler XYZ）。物件名稱是 Cascadeur 的物件名稱，或 `left_hand`、`right_foot`、`pelvis`、`head` 這類別名。

## 場景和檔案

### `new_scene`

開啟新的空白場景分頁，並設為目前的場景。要從頭開始、又不想取代任何已開啟的場景時使用。

沒有參數。

回傳 `scene`（新的分頁：`index`、`name`、`path`、`current`、`modified`）和 `open_scenes`（已開啟的分頁數量）。

### `open_scene`

在新的場景分頁開啟 `.casc` 檔案，並設為目前的場景。它不會載入到目前的分頁，所以已開啟的場景不會被取代。

| 參數 | 預設值 | 說明 |
|---|---|---|
| `path` | 必填 | 既有 `.casc` 檔案的絕對路徑。相對路徑、其他副檔名或不存在的檔案都會被拒絕。 |

回傳 `scene`（新的分頁，欄位和 `new_scene` 相同）和 `open_scenes`。Cascadeur 無法載入檔案時，呼叫會回傳錯誤，原本的分頁會恢復為目前的場景，空白的分頁也會關閉。

### `save_scene`

把目前的場景存回它自己的檔案；指定 `path` 時，則存成新的 `.casc` 檔案。要覆寫既有檔案（包括場景自己的檔案），需要 `overwrite=true`。

| 參數 | 預設值 | 說明 |
|---|---|---|
| `path` | 場景自己的檔案 | 位於既有資料夾內的 `.casc` 絕對路徑。省略時存回場景自己的檔案；從未儲存過的場景沒有檔案，必須給 `path`。 |
| `overwrite` | `false` | 必須是 `true` 才能覆寫既有檔案。存回場景自己的檔案時一定要設。 |

回傳 `saved_to`（寫入的路徑）、`overwritten`（該路徑原本是否已有檔案）和 `scene`（分頁：`index`、`name`、`path`、`current`、`modified`）。

### `list_open_scenes`

列出所有已開啟的場景分頁。要替 `switch_scene` 和 `close_scene` 挑選索引或名稱時使用。

沒有參數。

回傳 `scenes`（依分頁順序，每個分頁一筆：`index`、`name`、`path`、`current`、`modified`）、`count` 和 `current`（目前分頁的索引，沒有時為 `-1`）。Cascadeur 提供未儲存變更的旗標時，`modified` 為 `true` 或 `false`，否則為 `null`。

### `switch_scene`

把某個已開啟的場景分頁設為目前的場景。請先呼叫 `list_open_scenes`：索引只在當下的分頁順序中有效。

| 參數 | 預設值 | 說明 |
|---|---|---|
| `scene` | 必填 | 整數：`list_open_scenes` 給的分頁索引；字串：完全相同的場景名稱，或場景的檔案路徑。會被拒絕的有：超出範圍的索引、不存在的名稱、多個分頁共用的名稱（請改用索引）和布林值。 |

回傳 `scene`（分頁：`index`、`name`、`path`、`current`、`modified`）和 `was_current`（該分頁原本就是目前的分頁時為 `true`）。

### `close_scene`

關閉一個場景分頁，不動其他任何分頁。暫時用或測試用的分頁不再需要時使用。有未儲存變更的場景會保持開啟，除非 `discard_changes` 為 `true`；場景絕不會被儲存。

| 參數 | 預設值 | 說明 |
|---|---|---|
| `scene` | 必填 | 分頁索引（整數），或完全相同的場景名稱、檔案路徑（字串）；規則和 `switch_scene` 相同。 |
| `discard_changes` | `false` | `true` 會關閉有未儲存變更的場景並捨棄變更；`false` 會讓這類場景保持開啟，並回傳錯誤。 |

回傳 `closed`（被關閉的分頁：`index`、`name`、`path`、`current`、`modified`）、`discarded`（Cascadeur 的關閉提示出現，且以 *Don't Save* 回應時為 `true`）和 `prompt`（提示的詳細資料，沒有出現提示時為 `null`）。

### `import_model`

把 FBX 或 GLB 模型匯入目前的場景並進入 Rig 模式，做法和 Cascadeur 的 File > Import 相同。要在 Quick Rigging 之前載入角色時使用：單純用 Python 匯入會略過 `RigInfo` 這一步，沒有它 Quick Rigging 會當機。USD 檔案會被拒絕，因為 Cascadeur 的 Python API 沒有 USD 匯入功能。

| 參數 | 預設值 | 說明 |
|---|---|---|
| `path` | 必填 | 既有 `.fbx` 或 `.glb` 檔案的絕對路徑。`.gltf` 檔案會交給 GLB 匯入功能，但尚未測試。其他副檔名，包括 USD，都會被拒絕。 |
| `enter_rig_mode` | `true` | 匯入後進入 Rig 模式，僅在場景沒有 `RigInfo` 時才會進入。`false` 會略過這一步。 |
| `timeout` | `60` | 等待匯入和 Rig 模式步驟的秒數；上限 `600`。 |

回傳 `path`、`format`（`fbx` 或 `glb`）、`objects_added`、`joints` 和 `meshes`（數量）、`animation_info` 和 `rig_info`（behaviour 數量）、`rig_mode`（場景剛好有 1 個 `AnimationInfo` 時為 `true`）和 `undo_steps`。這不是單一復原步驟：Cascadeur 的載入功能會記錄自己的復原指令，進入 Rig 模式又會再記錄一個，所以 `undo_steps`（1 或 2）就是要完整還原這次呼叫所需的復原次數。「Rig mode helper」對話框出現時，`next` 會說明如何回應。`rig_error` 會回報 Rig 模式的問題，但不會讓整個呼叫失敗。如果模型匯入之後呼叫才失敗，錯誤裡會有 `imported: true`：模型已經在場景裡，請不要再匯入一次。

### `import_animation`

把 FBX 或 BVH 檔案裡的動畫套用到目前場景的角色。請先用 `list_fbx_takes` 挑選 take；目標骨架的關節名稱不同時，改用 `retarget_animation`。Cascadeur 的 BVH 指令會取代它寫入的動畫圖層上已設定關鍵影格的動畫，所以只想匯入一部分時請用 FBX。

| 參數 | 預設值 | 說明 |
|---|---|---|
| `path` | 必填 | 既有 `.fbx` 或 `.bvh` 檔案的絕對路徑。其他副檔名，包括 USD，都會被拒絕。 |
| `take` | 檔案的預設 take | 只適用於 FBX，BVH 會忽略。take 名稱（不分大小寫），或 `list_fbx_takes` 給的從 0 開始的索引（整數）。不存在的名稱或超出範圍的索引都會被拒絕。 |
| `target` | `"all"` | `all`（每個關節）、`selected_objects`（只套用到選取的物件）或 `selected_frames`（寫入選取的時間軸影格）。BVH 只支援 `all`。 |
| `timeout` | `60` | 等待匯入的秒數；上限 `600`。 |

回傳 `path`、`format`、`target`、`take`（實際使用的 take 名稱，使用預設時為 `null`）、`before` 和 `after`（各含動畫的 `first_frame`、`last_frame`、`size` 和 `last_key`）、`changed`（取樣的關節姿勢或最後一個關鍵影格是否有變）和 `undo_steps`（1：一個 Cascadeur 原生復原指令）。`changed` 為 `false` 時，`hint` 會建議比對檔案和場景的關節名稱。Cascadeur 只會在日誌裡回報被拒絕的貼上，所以日誌中的失敗（例如 `Import animation: failed` 或 `No BVH joint names matched`）會以 `ok: false` 回傳，並在 `marker` 和 `log` 附上標記和日誌行。

### `list_fbx_takes`

依檔案順序列出 FBX 檔案裡的動畫 take 名稱。要給 `import_animation` 準確的 take 名稱時，請先用它。take 只存在於 FBX 檔案；GLB 只有一段動畫。

| 參數 | 預設值 | 說明 |
|---|---|---|
| `path` | 必填 | 既有 `.fbx` 檔案的絕對路徑。其他副檔名會被拒絕。 |
| `timeout` | `60` | 等待的秒數；上限 `600`。 |

回傳 `path`、`takes`（依檔案順序的 take 名稱）和 `count`。

### `export_scene`

把目前的場景匯出成 FBX 或 GLB 檔案。既有的目標檔案會在匯出前先移到一旁，匯出失敗時再放回原處，所以失敗的匯出不會被誤報成成功、還帶著舊檔案的大小。Cascadeur 2026.2.3 沒有 USD 匯出的 Python API，USD 會被拒絕。

| 參數 | 預設值 | 說明 |
|---|---|---|
| `path` | 必填 | 以 `.fbx` 或 `.glb` 結尾的輸出絕對路徑，資料夾必須已經存在。 |
| `format` | 依副檔名 | `fbx`、`glb` 或 `usd`；必須和副檔名相符。`usd` 一律會被拒絕。 |
| `objects` | `"all"` | `all`（每個物件）或 `selected`（只匯出選取的物件）。 |
| `frames` | `"all"` | `all`（整段動畫）或 `selected`（只匯出選取的時間軸影格）。 |
| `overwrite` | `false` | `true` 會覆寫既有檔案；否則既有檔案會被拒絕。 |
| `timeout` | `60` | 等待匯出的秒數；上限 `600`。 |

回傳 `path`、`format`、`bytes`（寫出檔案的大小）、`objects` 和 `frames`（實際使用的選項），以及 `method`（執行的 Cascadeur 匯出方法）。Cascadeur 的匯出授權無法使用時，會在寫入任何東西之前回傳錯誤。

### `get_scene_info`

整理目前場景的摘要。要先了解開啟了什麼時，最先用它；再用 `list_controllers` 看角色的控制點，或用 `get_timeline` 看影格範圍。

| 參數 | 預設值 | 說明 |
|---|---|---|
| `max_objects` | `80` | `objects.sample` 最多列出的物件數量。 |

回傳 `scene`（`name`、`path`、`open_scenes`、`gravity_per_frame`）、`frames`（`current`、`first`、`last`、`animation_size`）、`objects`（`count`、`by_type`，以及 `sample`，即 `name` 和 `type` 的清單）、`selection`（`count` 和最多 50 個 `names`）、`behaviours`（`RigInfo`、`AnimationInfo`、`QrtInfo`、`Joint`、`MeshObject` 和 `AutoPosing` 這幾種 behaviour 的數量；這個 Cascadeur 版本沒有定義的類型會省略）和 `license`（`pro_features` 和 `export` 旗標）。

### `get_object_info`

依名稱說明物件：類型、id，以及 behaviour 和它們的屬性名稱。`list_controllers` 涵蓋不到的物件（網格、攝影機或輔助物件），或要在 `run_python` 之前確認 behaviour 時使用。

| 參數 | 預設值 | 說明 |
|---|---|---|
| `name` | 必填 | Cascadeur 物件名稱。先找完全相同的名稱；找不到時，改用不分大小寫的部分名稱比對。不會解析別名。 |
| `max_properties` | `40` | 每個 behaviour 最多列出的屬性名稱數量。 |

回傳 `objects`（最多 5 個；每個都有 `name`、`type`、`id` 和 `behaviours`，後者是 `name` 和 `properties` 的清單）和 `matches`（符合的物件數量）。

## 角色和選取

### `list_characters`

列出場景裡的角色：有 `RigInfo` behaviour 的根物件；場景沒有任何 `RigInfo` 時，則是有 `AnimationInfo` behaviour 的根物件。要先了解場景裡有哪些角色或綁定時，最先用它，再把回傳的名稱（或根關節、網格名稱）傳給有 `character` 參數的工具。唯讀。

沒有參數。

回傳 `count` 和 `characters`。每個角色有 `name`、`mode`（`rig` 或 `animation`）、`root_joint`、`info`（它的 `RigInfo` 或 `AnimationInfo` 物件名稱）、`meshes`（網格名稱）、`controllers`（控制點數量）和 `joints`（關節數量）。場景沒有任何角色時，`hint` 會說明如何取得。

### `list_controllers`

列出角色的控制點（主控制點、方向控制器、方塊、關節和其他綁定物件），附上別名、種類、世界位置和 AutoPosing 鎖定狀態。要找出該移動哪個控制點、又不想猜 `mixamorig:LeftHand_MainPoint` 這類內部名稱時使用。多數接受控制器的工具，都可以用 `left_hand` 這類別名取代內部名稱。

| 參數 | 預設值 | 說明 |
|---|---|---|
| `character` | 所有角色 | `list_characters` 給的名稱；根關節或網格名稱也可以。比對不分大小寫，先完全相同，再部分符合。不存在或有歧義的名稱會被拒絕，並附上候選項目。 |
| `kinds` | 所有種類 | 只回傳這些種類：`main_point`、`direction`、`box`、`joint`、`other`。可以是清單，或單一種類的字串。不認得的種類會被拒絕。 |

回傳 `count`、`controllers` 和 `characters`（結果中的角色名稱）。每個控制器有 `name`、`alias`、`kind`、`type`（Cascadeur 的物件類型：`Point`、`Box`、`Limb Direction`、`Joint` 或 `Center of Mass`）、`position`、`autoposing_locked` 和 `character`。`position` 是目前影格的世界座標 `[x, y, z]`，單位是公分（`cm`）。`alias` 是 `pelvis`、`chest`、`head`、`neck`、`center_of_mass`，或 `left_`、`right_` 加上 `hand`、`elbow`、`shoulder`、`foot`、`knee`、`toe`；沒有別名的物件為 `null`。`autoposing_locked` 一律是 `null`，因為 Cascadeur 2026.2.3 的 Python API 沒有提供這個鎖定狀態。

### `select_objects`

依名稱或別名選取場景物件（控制點、關節、方塊、網格等），效果和在視埠裡點選它們相同。給作用在物件選取上的工具使用，例如 AutoPosing 鎖定或攝影機取景；時間軸動作的範圍來自 `select_frames`，多數其他工具則會從自己的參數選取需要的控制器。選取的變更不會產生復原步驟。

| 參數 | 預設值 | 說明 |
|---|---|---|
| `names` | 必填 | 物件名稱、別名或部分名稱，例如 `["left_hand", "right_hand"]`；也可以只給一個字串。每個名稱都必須剛好符合一個物件：有歧義的名稱會被拒絕並附上 `candidates`，不存在的名稱則附上最接近的名稱。 |
| `mode` | `"replace"` | `replace`（取代目前的選取）、`add`（加入目前的選取）或 `remove`（取消選取指定的物件）。 |

回傳 `mode`、`requested`（去掉空白項目和重複後的名稱）、`count`（之後選取的物件數量）、`names` 和 `selection`。每筆 `selection` 有 `name`、`alias`、`kind`、`type`、`character` 和 `id`。Cascadeur 的選取和要求的集合不一致時，`hint` 會說明。

### `get_selection`

列出目前的選取，附上每個物件的名稱、別名、種類、Cascadeur 類型、角色和 id。要確認某個動作會作用在哪些物件上，或要看 `select_objects` 的結果時使用。唯讀。

沒有參數。

回傳 `count`、`names` 和 `selection`。每筆都有 `name`、`alias`、`kind`（`main_point`、`direction`、`box`、`joint` 或 `other`；不是綁定控制器的物件，例如網格，則為 `null`）、`type`、`character` 和 `id`。AutoPosing 控制點這類工具物件沒有模型名稱：它們會以 `tool_object: true` 列出，`name` 的格式是 `<Type id>`。

### `clear_selection`

取消場景中所有物件的選取，效果和在視埠空白處點一下相同。要建立新的選取前，或不想讓動作作用在舊的選取上時使用。選取的變更不會產生復原步驟。

沒有參數。

回傳 `count`（成功時為 0）、`names` 和 `selection`（成功時兩者皆為空）。如果還有部分選取沒有清掉，`hint` 會提醒確認沒有對話框擋住 Cascadeur。

### `enter_rig_mode`

用 Cascadeur 自己的 `rig_mode.on` 進入點進入 Rig 模式。在 `import_model` 之後，或使用 Quick Rigging 之前使用：在 Rig 模式之外產生綁定元素會讓 Cascadeur 當機。和官方 script server 一樣，在場景閒置（idle）時執行。

| 參數 | 預設值 | 說明 |
|---|---|---|
| `color` | `[0, 0.5, 0]` | Rig 顏色，一組 RGB 數字，每個值介於 0 到 1。 |

回傳 `rig_mode`、`changed`、`path` 和 `animation_info`。場景剛好有 1 個 `AnimationInfo` 時，代表已經在 Rig 模式：不會改變任何東西，也沒有 `path`。超過 1 個 `AnimationInfo` 會回傳錯誤，請先手動處理再做綁定。其餘情況下，已有 `RigInfo` 時 `path` 為 `run_raw`；剛匯入的模型則為 `run_after_import`，它會建立 `RigInfo`、進入 Rig 模式，並跳出「Rig mode helper」對話框；`next` 會說明要用 `dialogs` 和 `ui_click` 按下它的 *Yes* 按鈕。

### `list_rig_templates`

列出 Cascadeur 內建的 Quick Rigging 範本（`resources/autorig_templates`，`*.qrigcasc` 和較舊的 `*.qrt`）。自動比對的範本不對或有歧義時，在 `quick_rig` 之前使用。

沒有參數。

回傳 `directory`（讀取的資料夾）、`count` 和 `templates`。每個範本有 `name`、`file`、`path`（絕對路徑）、`format`、`readable`、`joints`（有名稱的關節數量）、`bones`、`sections`（身體部位區段）和 `error`（範本可讀取時為 `null`）。

### `quick_rig`

用 Quick Rigging 為沒有綁定的骨架建立 Cascadeur 綁定（綁定元素、控制點，以及選擇性的 AutoPosing）。它會先確認場景在 Rig 模式，因為在 Rig 模式之外產生綁定元素會讓 Cascadeur 2026.2.3 當機；接著載入範本、加入綁定元素、按下 **Generate rig**，再關閉 Quick Rigging。在 `import_model` 之後、骨架還沒有綁定時使用。

| 參數 | 預設值 | 說明 |
|---|---|---|
| `template` | 最接近的範本 | 範本名稱、檔案名稱，或 `.qrigcasc`（或 `.qrt`）的絕對路徑；請看 `list_rig_templates`。省略時：選出關節名稱和場景最符合的範本，條件是範本的關節至少有 5 個存在於場景，且比例至少達 60%。不存在或有歧義的名稱、沒有夠接近的範本，或兩個範本不分高下時，會回傳附上 `candidates` 的錯誤。 |
| `mirror_plane` | Quick Rigging 目前的設定 | `XY`、`XZ`、`YZ`、`Undef`，或 0 到 3 的整數（XY=0、XZ=1、YZ=2、Undef=3）。其他值會被拒絕。 |
| `create_autoposing` | `true` | 隨綁定一起建立 AutoPosing。 |
| `replace` | `false` | `true` 會重建已經有 `RigInfo` 或原型綁定元素的場景。`false` 會拒絕這種場景，避免在沒有提示的情況下完成一個做到一半的綁定。 |
| `enter_rig_mode` | `true` | 場景不在 Rig 模式時進入 Rig 模式。`false` 要求場景已經在 Rig 模式（剛好 1 個 `AnimationInfo`），否則呼叫會被拒絕。 |
| `timeout` | `60` | 等待 Quick Rigging 的秒數；上限 `600`。 |

回傳 `template`（`name`、`path`、`auto_selected`、關節數量 `matched` 和 `template_joints`，以及 `candidates`）、`rig`（新的角色：`name`、`root_joint`、`controllers`、`joints`、`meshes`）、`controllers`（`total`、`main_points`、`by_kind`、`names`）、`autoposing`（`requested`、`created`）、`rig_mode`（`entered`、`path`、`animation_info`）、`rig_info`、`animation_info`、`template_applied`、`warnings`、`undo_commands` 和 `undo_steps`。成功代表範本已套用，且場景剛好有 1 個 `RigInfo`、沒有 `AnimationInfo`。這不是單一復原步驟：Rig 模式、建立綁定元素和關閉 Rig 模式各會記錄一個 Cascadeur 指令，`undo_commands` 列出它們，`undo_steps` 計算它們的數量。Quick Rigging 執行期間發生的錯誤會附上 `stage`（呼叫停在哪一步）、`warnings`、`undo_commands` 和 `log`；無效的參數、沒有任何關節的場景，以及無法選出範本的情況，都會在執行任何動作之前被拒絕。

### `retarget_animation`

用 Cascadeur 的 Edit > Retargeting Copy 和 Paste 動作，把動畫從一個場景複製到另一個場景，適用於綁定不同的角色。當 take 是為另一副骨架製作、`import_animation` 又無法對應關節時使用。需要 Pro 或 Studio 授權、兩個不同的場景分頁（各自剛好有 1 個 `RigInfo`），而且兩邊的模型類型必須相同。

| 參數 | 預設值 | 說明 |
|---|---|---|
| `source` | 必填 | 已開啟場景分頁的名稱，或 `.casc` 的絕對路徑；尚未開啟的路徑會在暫時的分頁中開啟，結束後關閉。`.fbx`、`.glb`、`.gltf`、`.bvh` 和 USD 檔案會被拒絕：請先把動畫匯入骨架相同的場景。 |
| `target` | 目前的場景 | 已開啟場景分頁的名稱，或已開啟場景的 `.casc` 絕對路徑。它必須已經開啟，而且和 `source` 不同；貼上的關鍵影格會留在那個分頁，不會存到磁碟。 |
| `start` | 來源的第一個影格 | 要複製的第一個來源影格（含），必須是整數的影格編號。 |
| `end` | 來源的最後一個影格 | 要複製的最後一個來源影格（含），必須是整數的影格編號。`start` 不能在 `end` 之後，而且兩者都必須落在來源的動畫範圍內。 |

回傳 `source` 和 `target`，各有 `scene`（分頁名稱）、`path`、`frames`（`start`、`end`、`count`）、`boundary`、`rig_info` 和 `animation_info`。`target` 還有 `before`（貼上前的影格範圍、`animation_size` 和 `last_key`）、貼上後的 `animation_size` 和 `last_key`，以及 `pose`，其中的 `changed_joints` 和 `all_changed` 會比較貼上前後取樣的骨盆、手和腳關節。`seconds` 是經過的時間。沒有任何取樣的關節改變時，`hint` 會建議用 `render_image` 確認結果。貼上之前，工具會在目標選取相同的 `start` 到 `end` 影格，目標較短時會把它的最後影格延長到 `end`。來源在多影格的範圍內沒有動畫時，會在複製任何東西之前被拒絕。成功貼上後如果無法關閉暫時的來源分頁，呼叫會回傳帶有 `retargeted: true` 的錯誤。Cascadeur 原生的 Copy 和 Paste 會記錄自己的復原指令，不是 `MCP:` 步驟。
## AutoPosing 和姿勢

### `get_pose`

以數字讀取目前的姿勢：每個控制器在指定影格的位置（cm）、旋轉（Euler XYZ 角度，另附四元數），可選世界座標或區域座標。要查看目前的數值時，在 `set_pose` 之前使用；要確認修改有沒有生效時，在 `set_pose` 之後使用。唯讀，不會改變場景、選取或播放磁頭。

| 參數 | 預設值 | 說明 |
|---|---|---|
| `controllers` | 所有主要控制點 | 名稱或別名；可以是清單，也可以是單一字串。 |
| `frame` | 目前影格 | 從 0 開始的整數。任何影格都能讀取，播放磁頭不會移動。 |
| `space` | `"world"` | `world` 讀取世界座標的位置和旋轉；`local` 讀取區域座標的位置和旋轉。 |

回傳 `frame`、`current_frame`、`space`、`count` 和 `controllers`。每個控制器有 `name`、`alias`、`kind`、`character`、`position`（cm）、`rotation`（Euler XYZ 角度）和 `quaternion`。在 Cascadeur 2026.2.3 中，主要控制點沒有旋轉資料，所以它們的 `rotation` 和 `quaternion` 是 `null`，`rotation_available` 是 `false`；box（例如 `hand_Box_l`）和關節則有旋轉。控制器在該影格還沒有資料時，會在 `frame_used` 回報最接近、可讀取的影格。

### `set_pose`

用數字設定姿勢，不必拖曳控制器：在指定影格寫入控制點的位置（cm）和旋轉（度），為它們設定關鍵影格，預設再讓 AutoPosing 擺好身體其餘的部分。調整姿勢時一律用它，不要用 `ui_drag` 拖曳控制器。移動骨盆時，綁定階層會在 AutoPosing 執行之前先帶動雙腳，就算雙腳已被 AutoPosing 鎖定也一樣。

| 參數 | 預設值 | 說明 |
|---|---|---|
| `values` | 必填 | 物件，把控制器名稱或別名對應到 `position` `[x, y, z]`（絕對位置，cm）、`offset` `[dx, dy, dz]`（cm，加到該影格目前的值上）和／或 `rotation` `[rx, ry, rz]`（度，Euler XYZ），全部以 `space` 為準。`position` 和 `offset` 擇一，不能同時給；`rotation` 可以搭配任一個。在 Cascadeur 2026.2.3 中，主要控制點不能旋轉，請改為旋轉 box 和關節。不可為空。 |
| `frame` | 目前影格 | 從 0 開始的整數。播放磁頭不會移動。 |
| `autoposing` | `true` | `true` 先用 `AutoPosing.Autolock` 鎖定該影格，只寫入指定的控制器，再執行 AutoPosing 更新，讓身體跟著動；AutoPosing 如果是關閉的，會先開啟。`false` 只寫入指定的值，不動其他控制點。 |
| `key` | `true` | `true` 為寫入的控制器在該影格設定關鍵影格。`false` 只修改已有的關鍵影格，所以該影格必須已有資料。 |
| `space` | `"world"` | `world` 或 `local`。 |
| `tolerance_cm` | `1.0` | 必須大於 0。每個要求的位置可以容許的最大誤差（cm），超過時會改成不用 AutoPosing 寫入；旋轉固定以 1.0 度為準。 |
| `answer` | `"yes"` | Cascadeur「Adjust AutoPosing lock state?」提示的答案：`yes`（Autolock：鎖定姿勢與 AutoPosing 預測不同的點）、`no`（保留目前的鎖定）或 `cancel`（關閉提示，不改變鎖定）。 |

回傳 `frame`、`space`、`keyed`、`keys`、`controllers`、`adjusted`、`autoposing`、`autoposing_applied`、`fallback`、`extended_to` 和 `undo_steps`。`controllers` 的每個項目有 `requested` 的值、`before` 和 `after` 的值，以及和要求相比的 `position_error_cm` 和 `rotation_error_deg`；`adjusted` 列出 AutoPosing 一併移動的其他主要控制點，附上 `moved_cm`；場景在 `frame` 還沒有資料時，`extended_to` 是圖層被延伸到的影格。AutoPosing 更新之後，只要有控制器的誤差超過 `tolerance_cm`（或 1 度），同一次呼叫就會改成不用 AutoPosing，重新寫入所有要求的值：此時 `autoposing_applied` 是 `false`、附上 `warning`，每個項目還有 `position_error_cm_before_fallback` 和 `rotation_error_deg_before_fallback`，該影格上身體其餘的部分也不會跟著動。如果仍有控制器超出誤差，`ok` 是 `false`，並由 `missed` 列出。寫入、關鍵影格和 AutoPosing 更新是一個復原步驟 `MCP: set_pose`；Cascadeur 自己的 Autolock 步驟無法併入，所以完整復原需要兩次復原（發生上述重新寫入時是三次），`undo_steps` 由新到舊列出這些步驟。控制器在已鎖定的圖層上時會被拒絕。

### `mirror_pose`

在指定影格，沿著角色的鏡像平面鏡像姿勢，舉起的左手臂會變成舉起的右手臂。它使用 Cascadeur 自己的 MirrorTool，所以 box 和關節都會正確旋轉，而且一律鏡像整個角色：`controllers` 只決定結果回報哪些鏡像後的控制器。一次呼叫只鏡像一個角色。

| 參數 | 預設值 | 說明 |
|---|---|---|
| `frame` | 目前影格 | 從 0 開始的整數。該影格必須已有資料；請先用 `set_pose` 或 `set_key` 設定關鍵影格。播放磁頭不會移動。 |
| `controllers` | 所有主要控制點 | 名稱或別名；可以是清單，也可以是單一字串。只決定結果回報什麼。場景有多個角色時，請傳入同一個角色的控制器，並且每個角色各呼叫一次。 |

回傳 `frame`、`character`、`scope`、`plane`（鏡像使用的世界座標平面）、`controllers`、`unpaired`、`max_symmetry_error_cm` 和 `undo`。`controllers` 的每個項目有 `name`、`mirror_of`、`before_position`、`after_position`、`expected_mirror_position`（cm）和 `symmetry_error_cm`。復原步驟是 Cascadeur 原生的 `Mirror` 步驟，不是 `MCP:` 步驟；復原一次就能還原姿勢。該影格沒有資料、控制器在已鎖定的圖層上，或場景有多個角色而傳入的控制器不是剛好屬於其中一個角色時，會被拒絕。

### `copy_pose`

把指定影格的姿勢複製到 MCP Plus 自己的姿勢儲存區，之後可以用 `paste_pose` 貼到另一個影格。位置以 cm 儲存（世界座標），旋轉以度儲存（區域座標）；複製的內容存在 MCP Plus 的記憶體裡，伺服器重新啟動就會消失。唯讀：不會改變姿勢、關鍵影格或播放磁頭。

| 參數 | 預設值 | 說明 |
|---|---|---|
| `frame` | 必填 | 從 0 開始的整數。該影格必須已有資料；請先設定關鍵影格。 |
| `controllers` | 所有主要控制點 | 名稱或別名；可以是清單，也可以是單一字串。 |

回傳 `name`（新的複製名稱：`pose_1`、`pose_2`，依此類推）、`frame`、`count`、`controllers`、`planes` 和 `stored`。`controllers` 的每個項目有 `name`、`alias`、`kind`、`character`、`position`、`rotation` 和 `quaternion`；主要控制點沒有旋轉，所以它們的 `rotation` 是 `null`。`planes` 是每個角色在被複製的影格上的鏡像平面，`mirrored` 設為 `true` 的 `paste_pose` 會以它為基準反射；`stored` 是目前存放的複製數量。

### `paste_pose`

把用 `copy_pose` 複製的姿勢貼到另一個影格，整個動作是一個復原步驟。位置以世界座標（cm）寫入，旋轉以區域座標（度）寫入。儲存的名稱必須完全相符（完整的物件名稱，不透過別名），所以場景裡要有和複製來源相同的控制器名稱。

| 參數 | 預設值 | 說明 |
|---|---|---|
| `frame` | 必填 | 從 0 開始的整數。 |
| `name` | 最近一次的複製 | `copy_pose` 回傳的名稱，例如 `pose_1`。 |
| `mirrored` | `false` | `true` 會沿著複製時一併儲存的鏡像平面，鏡像儲存的位置。儲存的 box 和關節旋轉不會被鏡像；這些請對現有的綁定使用 `mirror_pose`。 |
| `key` | `true` | `true` 為貼上的控制器在該影格設定關鍵影格。`false` 只修改已有的關鍵影格，所以該影格必須已有資料。 |

回傳 `name`、`source_frame`、`frame`、`mirrored`、`keyed`、`controllers`、`count`、`skipped`、`rotations_skipped`、`passes`、`extended_to`、`keys` 和 `undo`（`MCP: paste_pose`）。`controllers` 的每個項目有 `name`、`source`、`requested_position`、`after_position` 和 `position_error_cm`，貼上旋轉時還有旋轉相關的欄位。box 和關節會在主要控制點之後，以第二輪寫入（此時 `passes` 為 2）；`skipped` 列出沒有鏡像對應或沒有儲存位置的控制器；場景在 `frame` 還沒有資料時，`extended_to` 是圖層被延伸到的影格。還沒複製過任何姿勢，或 `name` 不存在時會失敗；目標在已鎖定的圖層上時會被拒絕。

### `reset_pose`

在指定影格，把控制器重設成綁定的預設 T-pose，或蒙皮網格的綁定姿勢（bind pose），整個動作是一個復原步驟。預設的 T-pose 通常不是場景儲存時的姿勢。

| 參數 | 預設值 | 說明 |
|---|---|---|
| `kind` | `"default"` | `default` 寫入綁定中儲存的 T-pose（主要控制點取得 T-pose 的世界座標位置，box 和關節取得區域座標的變換，做法和 Cascadeur 的 Go to T-pose 指令相同）。`bind` 根據蒙皮網格的反向綁定矩陣寫入關節的變換，和 Go to Bind Pose 相同。 |
| `controllers` | 依 `kind` 而定 | 名稱或別名；可以是清單，也可以是單一字串。省略時，`default` 重設所有主要控制點、box 和關節，`bind` 重設所有關節。沒有 T-pose 或綁定資料的控制器維持不變，並列在 `skipped`。 |
| `frame` | 目前影格 | 從 0 開始的整數。該影格會設定關鍵影格，需要時也會讓它可以寫入。播放磁頭不會移動。 |

回傳 `kind`、`frame`、`count`、`controllers`、`skipped`、`rotation_error_max_deg`、`extended_to`、`keys` 和 `undo`（`MCP: reset_pose`）。`controllers` 的每個項目有 `name`、`kind`、`after_position`、`expected_position` 和 `position_error_cm`（cm），姿勢有儲存旋轉時還有 `rotation_error_deg`。box 和關節會在主要控制點之後，以第二輪寫入。控制器在已鎖定的圖層上時會被拒絕。

### `get_autoposing_state`

回報目前場景的 AutoPosing 是否開啟，依據 Cascadeur 介面上 AutoPosing 工具列按鈕的狀態判斷。在 `set_pose` 或 `autoposing_update` 之前使用。唯讀。

沒有參數。

回傳 `enabled`、`known`、`action`、`frame`、`sources` 和 `note`，另外有 `locked_controllers`（一律是 `null`）和 `locked_known`（一律是 `false`）：Cascadeur 2026.2.3 沒有可以查詢鎖定集合的 Python 介面，請用鎖定對位置造成的效果來確認。無法從介面讀到開關狀態時會失敗，例如沒有開啟含綁定角色的場景。

### `autoposing_enable`

開啟或關閉 AutoPosing。它先讀取開關，只在狀態不同時才切換，所以呼叫兩次的結果相同。它也會回答 Cascadeur 的「Adjust AutoPosing lock state?」提示，不會留下對話框。

| 參數 | 預設值 | 說明 |
|---|---|---|
| `enabled` | `true` | `true` 開啟 AutoPosing，`false` 關閉。 |
| `answer` | `"yes"` | Cascadeur「Adjust AutoPosing lock state?」提示的答案：`yes`（Autolock：鎖定姿勢與 AutoPosing 預測不同的點）、`no`（保留目前的鎖定）或 `cancel`（關閉提示，不改變鎖定）。 |

回傳 `requested`、`enabled`（呼叫後的狀態）、`before`、`changed` 和 `dialog`（提示的處理結果；沒有出現提示時是 `null`）。開關已經是要求的狀態時，`changed` 是 `false`，也不會觸發任何動作。

### `autoposing_update`

依目前的 AutoPosing 鎖定，重新計算目前影格的姿勢（Cascadeur 的「Update AutoPosing」操作），整個動作是一個復原步驟。只有被 AutoPosing 鎖定的點會保留指令碼寫入的位置，沒鎖定的點會回到 AutoPosing 的預測，所以請先鎖定該影格（請看 `lock_controllers`）。它也會回答「Adjust AutoPosing lock state?」提示，不會留下對話框。

| 參數 | 預設值 | 說明 |
|---|---|---|
| `answer` | `"yes"` | Cascadeur「Adjust AutoPosing lock state?」提示的答案：`yes`（Autolock：鎖定姿勢與 AutoPosing 預測不同的點）、`no`（保留目前的鎖定）或 `cancel`（關閉提示，不改變鎖定）。 |

回傳 `frame`、`undo`（`MCP: autoposing_update`）、`enabled`（呼叫後 AutoPosing 是否開啟）、`enabled_before` 和 `dialog`。AutoPosing 關閉時，姿勢可能不會改變，請先呼叫 `autoposing_enable`。角色的任何控制點在已鎖定的圖層上時，會被拒絕。

### `lock_controllers`

鎖定目前影格的 AutoPosing 控制器，讓其他點移動時，AutoPosing 仍保留它們的位置。它會選取指定的控制器、執行 Cascadeur 的 `AutoPosing.Autolock` 指令，再還原選取；整個影格的鎖定集合由 Cascadeur 決定，所以被鎖定的點可能比指定的更多。它也會回答「Adjust AutoPosing lock state?」提示。

| 參數 | 預設值 | 說明 |
|---|---|---|
| `names` | 必填 | 控制器名稱或別名，用清單傳入。 |
| `locked` | `true` | `true` 鎖定。`false` 會被拒絕：Cascadeur 2026.2.3 沒有可用在模型控制點的解鎖動作。 |
| `answer` | `"yes"` | Cascadeur「Adjust AutoPosing lock state?」提示的答案：`yes`（Autolock：鎖定姿勢與 AutoPosing 預測不同的點）、`no`（保留目前的鎖定）或 `cancel`（關閉提示，不改變鎖定）。 |

回傳 `locked`、`action`、`count`、`controllers`（每個項目有 `name` 和 `id`）、`scope`、`undo`（`AutoPosing.Autolock`）、`readback` 和 `dialog`。鎖定狀態無法讀回，所以 `readback` 一律是 `null`。

## 時間軸和關鍵影格

### `get_timeline`

回傳目前場景的時間軸狀態：播放磁頭、動畫範圍、可見範圍、長度和影格率。切換影格或修改動畫範圍之前使用。唯讀。

沒有參數。

回傳 `frame`（播放磁頭的實際位置，即使在動畫範圍之外）、`first_frame` 和 `last_frame`（動畫範圍）、`first_visible_frame` 和 `last_visible_frame`、`length`（影格數）、`fps` 和 `seconds`。這個 Cascadeur 版本沒有提供影格率時，`fps` 是 `null`；`seconds` 只在知道 `fps` 時才會加入。

### `set_frame`

把時間軸的播放磁頭移到指定影格。要執行作用在目前影格的動作之前使用。動畫範圍之外的影格也能到達，但那裡的關鍵影格不會顯示在時間軸上。

| 參數 | 預設值 | 說明 |
|---|---|---|
| `frame` | 必填 | 從 0 開始的整數。 |

回傳 `frame`（之後播放磁頭所在的影格）和 `requested`。影格在動畫範圍之外時，提示會建議用 `set_animation_range` 擴大範圍。

### `set_animation_range`

設定動畫的第一個和／或最後一個影格，用來加長或縮短時間軸。要在目前的動畫範圍之外設定關鍵影格之前使用。動畫範圍是場景設定，不是模型資料：復原不會還原它，要還原請用舊的值再呼叫一次。

| 參數 | 預設值 | 說明 |
|---|---|---|
| `first` | — | 從 0 開始的整數。省略時，維持目前的第一個影格。`first` 和 `last` 至少要給一個。 |
| `last` | — | 從 0 開始的整數。省略時，維持目前的最後一個影格。不可小於第一個影格。 |

回傳 `requested_first`、`requested_last`、`first_frame`、`last_frame`、`first_visible_frame`、`last_visible_frame`、`frame`（播放磁頭；範圍縮短時可能會移動）和 `undoable`（一律是 `false`）。動畫範圍會隨場景一起儲存；Cascadeur 調整了要求的範圍時，提示會說明。

### `set_key`

為指定的控制器在指定影格加上關鍵影格；省略 `controllers` 時，則為整個角色加上。在插值、AI 補間或清理之前，用它先把姿勢固定下來。整個呼叫是一個復原步驟 `MCP: set_key`。

| 參數 | 預設值 | 說明 |
|---|---|---|
| `frames` | 必填 | 影格編號（一個整數，或整數清單）。也接受 `"0,10,20"` 這種寫法。 |
| `controllers` | 所有控制器 | 名稱、別名或部分名稱；可以是清單，也可以是單一字串。 |

回傳 `frames`、`controllers`（符合的名稱）、`layers`（符合的控制器所在的動畫圖層數）、`keys_added`、`keys_already`（原本就有的關鍵影格數）和 `undo`。動畫範圍之外的影格也會設定關鍵影格，但不會顯示在時間軸上，此時結果會附上 `warning`。控制器在已鎖定的圖層上時會被拒絕。

### `delete_key`

從指定的控制器移除指定影格上的關鍵影格；省略 `controllers` 時，則從整個角色移除。要在重新設定關鍵影格之前清除舊的關鍵影格、移除錯誤的關鍵影格，或縮短動畫時使用。整個呼叫是一個復原步驟 `MCP: delete_key`。

| 參數 | 預設值 | 說明 |
|---|---|---|
| `frames` | 必填 | 影格編號（一個整數，或整數清單）。也接受 `"0,10,20"` 這種寫法。 |
| `controllers` | 所有控制器 | 名稱、別名或部分名稱；可以是清單，也可以是單一字串。 |

回傳 `frames`、`controllers`（符合的名稱）、`layers`、`keys_removed`、`frames_without_key` 和 `undo`。`frames_without_key` 計算的是沒有關鍵影格的「圖層和影格組合」數量，不是整個影格的數量。控制器在已鎖定的圖層上時會被拒絕。

### `list_keys`

列出控制器在某個影格範圍內的關鍵影格，以及每個關鍵影格所開始的區間使用的插值類型。要在編輯前找出關鍵影格的位置，或確認插值動作有沒有改變某個範圍時使用。唯讀。

| 參數 | 預設值 | 說明 |
|---|---|---|
| `controllers` | 所有控制器 | 名稱或別名；可以是清單，也可以是單一字串。省略時，只回報在範圍內有關鍵影格的控制器；指定控制器的話，即使沒有關鍵影格也會列出。 |
| `start` | 動畫的第一個影格 | 從 0 開始的整數。第一個要檢查的影格。 |
| `end` | 動畫的最後一個影格 | 從 0 開始的整數。最後一個要檢查的影格；不可小於 `start`。 |

回傳 `start`、`end`、`frame_count`、`key_frames`（所有關鍵影格的聯集）、`count` 和 `controllers`。`controllers` 的每個項目對應一個動畫圖層：`name`（圖層上第一個控制器）、`names`（圖層上符合的控制器）、`keys`、`interpolation`（每個關鍵影格的插值類型）和 `segments`（使用同一種插值的連續影格，每段有 `first`、`last`、`key` 和 `interpolation`）。插值類型有 `bezier`、`linear`、`step`、`fixed`、`clamped_bezier`、`low_amplitude_bezier`、`none` 和 `ai`。省略 `controllers` 時，結果還有 `scanned_layers` 和 `without_keys`；`without_layer` 列出沒有圖層的指定控制器。

### `select_frames`

在時間軸上選取一段影格區間，讓 Cascadeur 的 `Timeline.* on selected interval` 動作（插值、IK/FK、循環）作用在這個區間上。要執行這類動作之前使用，例如搭配 `call_action`。不會改變關鍵影格。

| 參數 | 預設值 | 說明 |
|---|---|---|
| `start` | 必填 | 從 0 開始的整數。區間的第一個影格。 |
| `end` | 必填 | 區間的最後一個影格（`start` 或更大）。 |
| `controllers` | 所有圖層 | 名稱或別名；可以是清單，也可以是單一字串。把選取限制在它們所在的圖層。 |

回傳 `requested_first`、`requested_last`、`first` 和 `last`（選取器實際回報的區間）、`layers`（選取的圖層數），以及指定 `controllers` 時的 `controllers`。Cascadeur 回報的區間和要求的不同時，提示會說明。

### `create_cycle`

用 Cascadeur 自己的 `Timeline.Create cycle` 動作，把 `start` 到 `end` 的影格範圍變成時間軸循環（迴圈）。關鍵影格都設定好之後、渲染循環動畫之前使用。範圍必須落在動畫範圍和設有關鍵影格的影格之內，動畫也至少要有兩個設有關鍵影格的影格；只有一個影格的動畫會被拒絕，因為 Cascadeur 可能當機。

| 參數 | 預設值 | 說明 |
|---|---|---|
| `start` | 必填 | 從 0 開始的整數。循環作用範圍的第一個影格。 |
| `end` | 必填 | 循環作用範圍的最後一個影格；必須大於 `start`，因為循環至少要有兩個影格。 |
| `offset` | `"none"` | `none`（單純的迴圈，重複同一個姿勢）、`position`（持續往前移動，例如走路循環）或 `position_rotation`（持續保留位置和旋轉的位移）。 |

回傳 `start`、`end`、`offset`、`action`（執行的 Cascadeur 動作）和 `selection`（確認過的時間軸選取）。任何有物件的圖層已鎖定時，會在選取任何東西之前就被拒絕。之後會把先前的時間軸選取還原，這是第二個原生指令，所以要復原兩次：第一次復原選取的還原，第二次才復原循環；`undo` 是 `null`。這個版本無法安全地讀回循環結構，請比較範圍內某個影格和一個循環之後的同一個影格，來驗證結果。

### `remove_cycles`

用 Cascadeur 自己的 `Timeline.Remove cycles` 動作，移除和 `start` 到 `end` 相交的時間軸循環；兩者都省略時，移除所有循環。用來取代一個迴圈，或讓動畫在最後一次重複之後停止。不會刪除關鍵影格。

| 參數 | 預設值 | 說明 |
|---|---|---|
| `start` | 動畫的第一個影格 | 從 0 開始的整數。範圍的第一個影格。只給 `start` 時，會從該影格移除到結尾。 |
| `end` | 最後一個設有關鍵影格的影格 | 從 0 開始的整數。範圍的最後一個影格。只給 `end` 時，會移除到該影格為止。 |

回傳 `start` 和 `end`（實際使用的範圍）、`action`、`selection`（確認過的時間軸選取）和 `undo`（`null`）。動畫少於兩個設有關鍵影格的影格、範圍超出動畫範圍或設有關鍵影格的範圍，或任何有物件的圖層已鎖定時，會在選取任何東西之前就被拒絕。移除循環和還原先前的時間軸選取是兩個原生復原步驟。

## 插值和 IK/FK

### `set_interpolation`

設定從 `start` 到 `end` 的時間軸區間的插值類型。為姿勢設定關鍵影格之後，用它改變動畫在姿勢之間的運動方式。它直接寫入 Cascadeur 的時間軸資料，所以物件選取、時間軸選取和播放磁頭都不會改變。

| 參數 | 預設值 | 說明 |
|---|---|---|
| `kind` | 必填 | `bezier`、`bezier_clamped`、`bezier_viscous`、`linear`、`step` 或 `fixed`。 |
| `start` | 必填 | 從 0 開始的整數。範圍的第一個影格。 |
| `end` | 必填 | 範圍的最後一個影格（`start` 或更大）。與範圍重疊的區間都會被改變，只有從 `end` 開始的區間例外，所以 0 到 20 只會改變從 0 開始的區間。`start` 等於 `end` 時，只會改變從該影格開始的區間。 |
| `controllers` | 所有控制器 | 名稱或別名；可以是清單，也可以是單一字串。 |

回傳 `kind`、`start`、`end`、`controllers`、`layer_count`、`layers`（每個圖層中，每個選取區間讀回的插值）、`sections`（範圍內的區間數）、`sections_changed`、`readback`（每種插值類型的數量）和 `undo`（`MCP: set_interpolation`）。讀回時，clamped 和 viscous 這兩種類型的名稱是 `clamped_bezier` 和 `low_amplitude_bezier`。範圍內沒有任何關鍵影格時不會改變任何東西，提示會建議先用 `set_key` 加上關鍵影格。控制器在已鎖定的圖層上時會被拒絕。

### `set_ik_fk`

切換從 `start` 到 `end` 的時間軸區間的 IK/FK 類型：`ik`、`fk` 或 `gr`（global FK）。它會選取區間、執行 Cascadeur 自己的「IK type on selected interval」動作，再還原物件和時間軸的選取。和 `set_interpolation` 不同的是，從 `end` 開始的區間也會被改變。

| 參數 | 預設值 | 說明 |
|---|---|---|
| `mode` | 必填 | `ik`、`fk` 或 `gr`（global FK）。 |
| `start` | 必填 | 從 0 開始的整數。範圍的第一個影格。 |
| `end` | 必填 | 範圍的最後一個影格（`start` 或更大）。 |
| `controllers` | 所有控制器 | 名稱或別名；可以是清單，也可以是單一字串。 |

回傳 `mode`、`action`、`start`、`end`、`controllers`、`layer_count`、`layers`（每個圖層讀回的區間模式和關鍵影格模式）、`sections`、`sections_changed`、`readback` 和 `undo_source`（`"Cascadeur action"`：復原步驟由 Cascadeur 自己命名，不是 `MCP: set_ik_fk`）。範圍內沒有任何關鍵影格時，不會呼叫動作：`action_called` 是 `false`，提示會建議先加上關鍵影格。動畫少於兩個設有關鍵影格的影格、範圍超出動畫範圍或設有關鍵影格的範圍，或控制器在已鎖定的圖層上時，會被拒絕。

### `reset_tangents`

清除從 `start` 到 `end` 的時間軸關鍵影格上的自訂切線，恢復連續切線（Cascadeur 的「Clear custom tangents on selected interval」）。編輯切線之後，用它去除過衝或手動調出來的曲線。它直接寫入關鍵影格資料，所以物件選取、時間軸選取和播放磁頭都不會改變。

| 參數 | 預設值 | 說明 |
|---|---|---|
| `start` | 必填 | 從 0 開始的整數。範圍的第一個影格。 |
| `end` | 必填 | 範圍的最後一個影格（`start` 或更大）。從 `end` 開始的關鍵影格不會被改變，和 `set_interpolation` 相同。 |
| `controllers` | 所有控制器 | 名稱或別名；可以是清單，也可以是單一字串。 |

回傳 `start`、`end`、`controllers`、`layer_count`、`layers`（每個圖層讀回的切線）、`sections`、`sections_changed`、`readback`、`reset`（`"continuous"`）和 `undo`（`MCP: reset_tangents`）。控制器在已鎖定的圖層上時會被拒絕。

### `reset_easing`

把從 `start` 到 `end` 的時間軸關鍵影格的緩動權重，重設成中性值，也就是三分之一（Cascadeur 的「Reset easing weights on selected interval」）。編輯緩動之後，用它讓關鍵影格之間的運動重新對稱。它直接寫入關鍵影格資料，所以物件選取、時間軸選取和播放磁頭都不會改變。

| 參數 | 預設值 | 說明 |
|---|---|---|
| `start` | 必填 | 從 0 開始的整數。範圍的第一個影格。 |
| `end` | 必填 | 範圍的最後一個影格（`start` 或更大）。從 `end` 開始的關鍵影格不會被改變，和 `set_interpolation` 相同。 |
| `controllers` | 所有控制器 | 名稱或別名；可以是清單，也可以是單一字串。 |

回傳 `start`、`end`、`controllers`、`layer_count`、`layers`（每個圖層讀回的左、右權重速度）、`sections`、`sections_changed`、`readback`、`weight`（三分之一）和 `undo`（`MCP: reset_easing`）。控制器在已鎖定的圖層上時會被拒絕。

## 動畫圖層

### `list_layers`

列出場景的動畫圖層樹：每個資料夾和圖層的名稱、id、路徑、是否可見、是否鎖定，以及所含的物件數。要移動控制點或修改圖層之前使用，因為圖層名稱不一定唯一：兩個項目同名時，請傳入回傳的 `path` 或 `id`。大部分圖層工具會依 id、完整路徑、完全相符的名稱或唯一的部分名稱找出項目（不分大小寫），名稱符合多個項目時會拒絕，並在 `candidates` 列出符合的路徑。

沒有參數。

回傳 `root`（`id` 和 `name`）、`counts`（`layers` 和 `folders`）和 `tree`。每個節點有 `kind`（`layer` 或 `folder`）、`name`、`id`、`path`（例如 `parent/child`）和 `objects`。圖層另有 `visible` 和 `locked`；資料夾另有 `children` 和整個子樹的物件總數，資料夾的 `visible` 和 `locked` 是 `null`。

### `create_layer`

在資料夾底下建立空的動畫圖層。要在 `move_to_layer` 之前先建立獨立的軌道時使用，讓一組控制點可以一起隱藏、鎖定或編輯。整個呼叫是一個復原步驟。

| 參數 | 預設值 | 說明 |
|---|---|---|
| `name` | 必填 | 新圖層的名稱。不可為空，也不可包含 `/`，而且在同一層的項目中不可重複（不分大小寫）。 |
| `parent` | 根資料夾 | `list_layers` 回傳的資料夾名稱、路徑（`parent/child`）或 id。 |

回傳 `layer`（含 `id`、`name` 和 `path`）和 `undo`。

### `create_layer_folder`

在動畫圖層樹裡建立資料夾。Cascadeur 2026.2.3 不接受沒有圖層的資料夾，所以新資料夾會附帶一個空的 `Track0` 圖層。整個呼叫是一個復原步驟。

| 參數 | 預設值 | 說明 |
|---|---|---|
| `name` | 必填 | 新資料夾的名稱。不可為空，也不可包含 `/`，而且在同一層的項目中不可重複（不分大小寫）。 |
| `parent` | 根資料夾 | `list_layers` 回傳的資料夾名稱、路徑（`parent/child`）或 id。 |

回傳 `folder`（含 `id`、`name` 和 `path`）、`default_layer`（`Track0` 圖層）和 `undo`。

### `delete_layer`

刪除圖層，或刪除資料夾和它底下的所有項目。裡面有物件的圖層和不是空的資料夾，除非 `force` 為 `true`，否則會被拒絕；使用 `force` 時，物件會先移到場景的預設圖層（或第一個其他圖層）。整個呼叫是一個復原步驟。

| 參數 | 預設值 | 說明 |
|---|---|---|
| `name` | 必填 | 圖層或資料夾的名稱、路徑（`parent/child`）或 id。必須是完全相符的名稱、路徑或 id，不會比對部分名稱。 |
| `force` | `false` | `true` 會刪除裡面有物件的圖層，或有子項目的資料夾；刪除資料夾時，由下而上移除它底下的所有項目（圖層先於容納它們的資料夾）。 |

回傳 `deleted`（含 `name`、`path`、`id` 和 `kind`）、`objects`（它的圖層原本所含的物件數）、`undo`，有移動物件時還有 `objects_moved_to`（物件移到的圖層）。要刪除的圖層已鎖定、物件要移進已鎖定的圖層，或沒有其他圖層可以接收物件時，會被拒絕。

### `set_layer_visible`

顯示或隱藏動畫圖層。隱藏圖層會讓它的物件動畫不顯示在視埠中；儲存的動畫不受影響。整個呼叫是一個復原步驟。

| 參數 | 預設值 | 說明 |
|---|---|---|
| `name` | 必填 | 圖層名稱、路徑（`parent/child`）或 id。不接受資料夾。 |
| `visible` | 必填 | `true` 顯示圖層，`false` 隱藏圖層。 |

回傳 `layer`（含新的狀態）、`visible` 和 `undo`。圖層已經是該狀態時，不會改變任何東西，`changed` 是 `false`。

### `set_layer_locked`

鎖定或解鎖動畫圖層。已鎖定的圖層受到 MCP Plus 寫入工具（例如 `set_pose`、`set_key` 和 `set_interpolation`）的保護：這些工具會拒絕編輯圖層上的物件，並回傳指出圖層名稱的錯誤，因為 Cascadeur 的 Python API 本身不會強制執行鎖定。擺姿勢或設定關鍵影格之前，請先用 `locked=false` 解鎖。整個呼叫是一個復原步驟。

| 參數 | 預設值 | 說明 |
|---|---|---|
| `name` | 必填 | 圖層名稱、路徑（`parent/child`）或 id。不接受資料夾。 |
| `locked` | 必填 | `true` 鎖定圖層，`false` 解鎖圖層。 |

回傳 `layer`（含新的狀態）、`locked` 和 `undo`。圖層已經是該狀態時，不會改變任何東西，`changed` 是 `false`。

### `rename_layer`

重新命名動畫圖層或資料夾。新名稱在同一層的項目中不可重複。整個呼叫是一個復原步驟。

| 參數 | 預設值 | 說明 |
|---|---|---|
| `name` | 必填 | 目前的圖層或資料夾名稱、路徑或 id。 |
| `new_name` | 必填 | 新名稱。不可為空，也不可包含 `/`，而且在同一層的項目中不可重複（不分大小寫）。 |

回傳 `item`（含新的 `name` 和 `path` 的圖層或資料夾）和 `undo`。新名稱和目前的相同時，不會改變任何東西，`changed` 是 `false`。

### `move_to_layer`

把控制點移到動畫圖層。用它把角色的控制點分組，讓整個圖層可以當成一個單位隱藏或鎖定。整個呼叫是一個復原步驟。

| 參數 | 預設值 | 說明 |
|---|---|---|
| `controllers` | 必填 | 控制點名稱、別名或部分名稱；可以是清單，也可以是單一字串。 |
| `layer` | 必填 | 目標圖層的名稱、路徑（`parent/child`）或 id。資料夾不是圖層。 |

回傳 `layer`、`moved`（移動的物件名稱）、`already_in_layer` 和 `undo`；控制器都已經在該圖層時，不會改變任何東西，`changed` 是 `false`。控制器目前所在的圖層或目標圖層已鎖定時，會被拒絕。
## 物理和 AI

### `get_physics_state`

讀取目前場景的 Physics Assistant（AutoPhysics）狀態，不會改動任何東西。變更物理設定或執行 `physics_snap` 之前，可以先用它確認。

沒有參數。

回傳 `enabled`、`frozen`、`show_fulcrums`（是否顯示支點）和 `only_key_fulcrums`（是否只用關鍵支點）、`priority_frames`（影格編號的清單）、`current_frame`、`first_frame`、`last_frame` 和 `fps`，以及每影格的重力 `gravity_per_frame` 和 `gravity_cm_per_frame2`（單位是 cm/影格²，等於 980 / fps²）。Cascadeur 沒有回報的值是 `null`。

### `physics_enable`

開啟或關閉 Cascadeur 的 Physics Assistant（AutoPhysics）。要在 `physics_snap` 之前開始物理求解，或要停止求解時使用。它會先讀取狀態，已經符合就什麼都不做，所以重複呼叫是安全的。

| 參數 | 預設值 | 說明 |
|---|---|---|
| `enabled` | 必填 | `true` 開啟 Physics Assistant，`false` 關閉。必須是布林值。 |

回傳 `changed`（狀態原本就符合時為 `false`）和 `enabled`（呼叫後的狀態）。

### `physics_snap`

以背景工作把 AutoPhysics 的結果烘焙進動畫（Snap to Auto Physics）。在 `physics_enable` 開始求解之後使用。這個工作會用 All Frames 回答 Cascadeur 的影格選擇對話框，並等到姿勢不再變化。

沒有參數。

回傳 `job_id`，不必等烘焙完成；用 `get_job` 查詢進度和結果，用 `cancel_job` 停止工作。工作完成後的結果有 `frames`（有關鍵影格的動畫範圍，含 `first` 和 `last`）、`changed_first`、`changed_last`、`changed_frame_count`、`changed_frames`（最多 200 個）、`max_delta_cm`、`max_rotation_delta_deg` 和 `objects`（有移動的控制點）；比較的對象只有骨盆、頭、雙手和雙腳。`commands` 列出這次 snap 執行的 Cascadeur 指令，它們是 Cascadeur 自己的復原步驟，不是 `MCP:` 步驟。Physics Assistant 關閉、有關鍵影格的動畫不到兩個影格、有圖層被鎖定，或另一個 snap 還在執行時，呼叫會立刻被拒絕，不會建立工作。工作 300 秒後逾時。

### `physics_freeze`

凍結或解除凍結 AutoPhysics 的結果。凍結後的結果不會隨後續的動畫編輯而變動；解除凍結後才會重新求解。它會先讀取狀態，已經符合就什麼都不做。

| 參數 | 預設值 | 說明 |
|---|---|---|
| `enabled` | 必填 | `true` 凍結物理結果，`false` 解除凍結。必須是布林值。 |

回傳 `changed` 和 `frozen`（呼叫後的狀態）。Physics Assistant 關閉時會被拒絕。

### `physics_set_priority_frame`

把某個影格標記為 AutoPhysics 的優先影格：求解其餘動畫時，求解器會保留該影格的姿勢。要固定落地姿勢或接觸時使用。它會把播放磁頭移到該影格，執行 Cascadeur 的 Set priority frame 動作，再把播放磁頭移回原處。

| 參數 | 預設值 | 說明 |
|---|---|---|
| `frame` | 必填 | 影格編號：從 0 開始的整數，或像 `"12"` 這樣的數字字串。必須落在有關鍵影格的動畫範圍內（這個範圍至少有兩個影格），但該影格本身不必有關鍵影格。 |

回傳 `changed`、`frame`、`priority`（該影格現在是優先影格時為 `true`）、`priority_frames`（完整清單）和 `playhead_restored`（播放磁頭移回的影格）。已經是優先影格的影格會維持原狀（`changed` 為 `false`）；這個工具無法清除優先影格。Physics Assistant 關閉或圖層被鎖定時會被拒絕。

### `physics_show_fulcrums`

顯示或隱藏 Cascadeur 的 Fulcrum Visualizer，它會畫出角色所有的支點（接觸點），讓你看出哪些點碰到地面或其他表面。Physics Assistant 開啟時 Cascadeur 無法顯示它，所以要先用 `physics_enable` 並設 `enabled=false`。它會先讀取狀態，已經符合就什麼都不做。

| 參數 | 預設值 | 說明 |
|---|---|---|
| `enabled` | 必填 | `true` 顯示所有支點，`false` 隱藏。必須是布林值。 |

回傳 `changed` 和 `show_fulcrums`（呼叫後的狀態）。Physics Assistant 開啟時，`enabled=true` 會被拒絕。

### `fix_foot_sliding`

以背景工作用 Cascadeur 的 Fulcrum Motion Cleaning（`View.FixFoot`）消除接觸區間內的腳滑。用關鍵影格做好走路或跑步動畫之後、渲染之前使用。它會量測清理前後，持續接觸的支點在相鄰兩個影格之間的水平位移總和（cm）。

| 參數 | 預設值 | 說明 |
|---|---|---|
| `start` | 動畫的第一個影格 | 區間的第一個影格：從 0 開始的整數，或像 `"12"` 這樣的數字字串。 |
| `end` | 最後一個關鍵影格 | 區間的最後一個影格（含）。區間必須落在有關鍵影格的動畫範圍內，而且至少 3 個影格長。 |
| `controllers` | 整個角色 | 控制器名稱或別名；清單或單一字串。空清單會被拒絕。 |

回傳 `job_id`，不必等工作完成；用 `get_job` 查詢進度和結果，用 `cancel_job` 停止工作。工作完成後的結果有 `frames`、`controllers`、`before` 和 `after`（含 `slide_total_cm` 和 `slide_max_cm`），以及 `change`（`slide_reduction_cm` 和 `slide_reduction_percent`）。如果 Cascadeur 對這個範圍還沒有快取的接觸資料，`before` 沒有接觸資料，`measurement_hint` 會建議先對同一範圍執行 `prepare_keys_by_fulcrums`。清理動作是 Cascadeur 自己的復原指令（列在 `commands`），不是 `MCP:` 步驟。有圖層被鎖定（用 `set_layer_locked` 解鎖），或另一個清理工作還在執行時，呼叫會立刻被拒絕。工作 300 秒後逾時。

### `fix_collisions`

以背景工作用 Cascadeur 的 Collision Penetration Cleaning（`View.FixCollisions`）把互相穿插的身體部位推開。肢體穿進身體或地板的區間可以用它。它會量測清理前後的穿插深度總和與最大值（cm）。

| 參數 | 預設值 | 說明 |
|---|---|---|
| `start` | 動畫的第一個影格 | 區間的第一個影格：從 0 開始的整數，或像 `"12"` 這樣的數字字串。 |
| `end` | 最後一個關鍵影格 | 區間的最後一個影格（含）。區間必須落在有關鍵影格的動畫範圍內，而且至少 3 個影格長。 |
| `controllers` | 整個角色 | 控制器名稱或別名；清單或單一字串。空清單會被拒絕。 |

回傳 `job_id`，不必等工作完成；用 `get_job` 查詢進度和結果，用 `cancel_job` 停止工作。工作完成後的結果有 `frames`、`controllers`、`before` 和 `after`（含 `penetration_total_cm` 和 `penetration_max_cm`），以及 `change`（`penetration_reduction_cm` 和 `penetration_reduction_percent`）。和 `fix_foot_sliding` 一樣，如果 Cascadeur 對這個範圍還沒有快取的碰撞點，`before` 沒有碰撞資料。清理動作是 Cascadeur 自己的復原指令（列在 `commands`），不是 `MCP:` 步驟。有圖層被鎖定，或另一個清理工作還在執行時，呼叫會立刻被拒絕。工作 300 秒後逾時。

### `prepare_keys_by_fulcrums`

以背景工作用 Cascadeur 的 Auto keys（`View.AutoInterpolation_Keys`）依支點的運動準備區間內的關鍵影格。清理接觸之後使用，讓清理過的動作由較少、位置較合適的關鍵影格承載，而不是密集的影格。它一律作用在整個角色上。

| 參數 | 預設值 | 說明 |
|---|---|---|
| `start` | 動畫的第一個影格 | 區間的第一個影格：從 0 開始的整數，或像 `"12"` 這樣的數字字串。 |
| `end` | 最後一個關鍵影格 | 區間的最後一個影格（含）。區間必須落在有關鍵影格的動畫範圍內，而且至少 2 個影格長。 |

回傳 `job_id`，不必等工作完成；用 `get_job` 查詢進度和結果，用 `cancel_job` 停止工作。工作完成後的結果有 `frames`、`before` 和 `after`（關鍵影格摘要，含 `key_count`、`layers_with_keys`、`key_frames` 和 `interpolation`，也就是每種插值類型的關鍵影格數），以及 `change`（`keys_before`、`keys_after` 和 `key_delta`）。Auto keys 是 Cascadeur 自己的復原指令（列在 `commands`），不是 `MCP:` 步驟。有圖層被鎖定，或另一個清理工作還在執行時，呼叫會立刻被拒絕。工作 300 秒後逾時。

### `ai_inbetween`

以背景工作用 Cascadeur 的 AI 補間（時間軸動作 `Timeline.AI.AI on selected interval`）補出關鍵姿勢之間的影格。請先用 `set_key` 在區間前後設好姿勢：已設的關鍵姿勢會保留，Cascadeur 會合成它們之間的動作。AI 功能需要 Pro 授權。

| 參數 | 預設值 | 說明 |
|---|---|---|
| `start` | 必填 | 區間的第一個影格：從 0 開始的整數，或像 `"12"` 這樣的數字字串。 |
| `end` | 必填 | 區間的最後一個影格；必須大於 `start`。 |
| `controllers` | 整個角色 | 控制器名稱或別名，例如 `left_hand`；清單或單一字串。 |

回傳 `job_id`，不必等工作完成；用 `get_job` 查詢進度和結果，用 `cancel_job` 停止工作。工作完成後的結果有 `first_frame`、`last_frame`、`changed_first`、`changed_last`、`layers_changed`、`ai_layers`（有幾個圖層得到 AI 插值）和 `ai_layer_names`、`keys_added`、`keys_removed`、`interpolation`（每種插值類型的關鍵影格數）和 `seconds`。工作完成時會還原先前的選取和播放磁頭。授權不含 AI 功能、選取的圖層被鎖定，或另一個 AI 工作還在執行時，呼叫會立刻被拒絕。關鍵影格不符合條件時也會被拒絕：每個選取的圖層，在 `start` 或更早的影格上要有一個關鍵影格，在 `end` 或更晚的影格上也要有一個，區間內至少要有一個關鍵影格，而且相鄰兩個關鍵影格的間隔不能超過 Cascadeur 的 `InbetweeningTool/MaxIntervalLength` 設定。工作 1800 秒後逾時。

### `generate_motion`

以背景工作用 Cascadeur 的 Run Motion Generation（`View.Inbetweening_RunMotionGeneration`）為區間產生新的動作。當關鍵姿勢只是粗略的構想，想讓 Cascadeur 的動作生成器取代關鍵影格之間的動畫時使用。和 `ai_inbetween` 不同，它會改寫關鍵影格的值，還可能把區間設成固定插值，所以關鍵姿勢必須保留時，請先複製動畫。

| 參數 | 預設值 | 說明 |
|---|---|---|
| `start` | 必填 | 區間的第一個影格：從 0 開始的整數，或像 `"12"` 這樣的數字字串。 |
| `end` | 必填 | 區間的最後一個影格；必須大於 `start`。 |
| `controllers` | 整個角色 | 控制器名稱或別名，例如 `left_hand`；清單或單一字串。 |

回傳 `job_id`，不必等工作完成；用 `get_job` 查詢進度和結果，用 `cancel_job` 停止工作。工作完成後的結果有 `first_frame`、`last_frame`、`changed_first`、`changed_last`、`layers_changed` 和 `changed_layer_names`、`keys_added`、`keys_removed` 和 `seconds`。Cascadeur 2026.2.3 不會為產生的動作建立復原步驟。Cascadeur 開啟後的第一次執行會載入 Torch 模型（約 20 秒），載入期間 Cascadeur 的 GUI 可能沒有回應，不過 `job_id` 仍會立刻回傳；之後的執行很快。工作完成時會還原先前的選取和播放磁頭。在和 `ai_inbetween` 相同的情況下（Pro 授權、區間前後的關鍵影格、被鎖定的圖層、另一個 AI 工作還在執行），呼叫會立刻被拒絕。工作 1800 秒後逾時。

### `video_mocap`

> [!WARNING]
> **實驗性功能。** 在 Cascadeur 2026.2.3 的測試中，這個工具曾讓 Cascadeur 當機。呼叫前請先存檔。
> 請看[行為與限制](limitations.zh-TW.md#重定向綁定和動捕)。

以背景工作用 Cascadeur 內建的影片動捕（`View.MocapTool`）把參考影片變成角色動畫，全程在本機執行。這個工具會把 `.mp4` 匯入成場景的參考平面，把 Load video 對話框的影格資料夾指到可寫入的 `%TEMP%` 資料夾，選取角色和時間軸上的影格，按下 Mocap，回答確認對話框，最後刪除平面。Cascadeur 會在角色上為每個選取的影格寫入一個關鍵影格。

| 參數 | 預設值 | 說明 |
|---|---|---|
| `video_path` | 必填 | 參考 `.mp4` 的絕對路徑（單人、全身、3–5 秒）。檔案必須存在，而且不是空的。 |
| `character` | 場景中唯一的角色 | `list_characters` 列出的角色名稱（根關節或網格名稱也符合）。場景有多個角色時必須指定。 |
| `start` | 時間軸選取範圍 | 動捕的第一個專案影格（是專案影格，不是影片的秒數）。要和 `end` 一起給，或兩者都不給，改用時間軸上選取的區間。 |
| `end` | 時間軸選取範圍 | 動捕的最後一個專案影格（含）。區間必須落在有關鍵影格的動畫範圍內。 |
| `from_frame` | `0` | 要使用的影片片段的第一個影格。會輸入到 Load video 對話框的 From 欄位；尚未驗證。 |
| `to_frame` | 影片的最後一個影格 | 要使用的影片片段的最後一個影格。會輸入到對話框的 To 欄位；尚未驗證。 |
| `keep_reference` | `false` | `true` 會把匯入的參考平面（和它解碼出的影格）留在場景裡。 |
| `dry_run` | `false` | `true` 會停在 Cascadeur 的確認對話框並取消，不執行模型；用來檢查影片匯入和角色設定。 |
| `timeout` | `600` | 工作自行取消前的秒數；必須大於 0。 |

回傳 `job_id`，不必等工作完成；用 `get_job` 查詢進度和結果，`cancel_job` 會在 Cascadeur 的視窗按下 Cancel，並等該視窗關閉後才刪除平面。工作完成後的結果有 `outcome`（`completed`；部分影格辨識不出姿勢時是 `partial`；試跑時是 `cancelled`）、`character`、`frames`（專案的第一個和最後一個影格）、`keyframes`、`key_frames`、`failed_frames`、`elapsed_s`，以及（試跑後特別有用的）`confirmation`（Cascadeur 確認對話框的文字）和 `objects_selected`。`.mp4` 不存在、模型檔 `mocap.pt`（約 825 MB，位於 `%LOCALAPPDATA%\Nekki Limited\Cascadeur\resources`）沒有安裝，或已經開著 Load video 或 Mocap 視窗時，呼叫會立刻被拒絕；Cascadeur 只有在手動執行 View > Mocap 時才會下載模型。工作會留在它開始時的場景分頁：切到其他分頁會暫停，關閉該分頁會停止。品質、所需時間，以及 Cascadeur 如何處理選取影格上已有的關鍵影格，都還沒有驗證，所以那些影格如果已有關鍵影格，請先備份動畫。Cascadeur 烘焙的關鍵影格是獨立的復原步驟，所以一次復原無法同時還原匯入、動捕和刪除平面。

## 檢視和輸出

### `render_image`

用 Cascadeur 自己的渲染器（`RenderToFile`）渲染一張 3D 場景的圖片，所以其他視窗蓋住 Cascadeur 也沒有影響。要看場景，或要從動畫擷取一張靜態畫面時使用；要一次看好幾個影格用 `render_contact_sheet`，要輸出完成的動作用 `render_video`。Cascadeur 視窗不可以最小化。

| 參數 | 預設值 | 說明 |
|---|---|---|
| `frame` | 目前的影格 | 要渲染的時間軸影格：整數（字串會被拒絕）。渲染後播放磁頭會回到原來的位置。超出動畫範圍的影格，Cascadeur 可能會把它限制在範圍內；這時 JSON 會有 `requested_frame` 和 `note`。 |
| `width` | `1280` | 圖片寬度，單位是像素（1–16384）。 |
| `height` | `720` | 圖片高度，單位是像素（1–16384）。 |
| `samples` | Cascadeur 的預設值（4） | 每個像素的抗鋸齒取樣數（1–4096）。 |
| `path` | `%TEMP%\cascadeur-mcp-plus\renders` 底下的新檔案 | 以 `.png` 結尾的絕對路徑（大小寫不拘，副檔名會寫成小寫）。資料夾必須已經存在。 |
| `overwrite` | `false` | `true` 會取代 `path` 已存在的檔案，否則已存在的檔案會被拒絕。 |
| `return_image` | `true` | `false` 只回傳 JSON，不附圖片。 |
| `max_width` | `1280` | 只縮小回傳的圖片到這個寬度；檔案維持 `width` 和 `height`。 |
| `camera_preset` | 目前視埠的攝影機 | `front`、`back`、`left`、`right`、`top`、`three_quarter` 其中之一：渲染時用這個視角對準角色，渲染後還原攝影機。 |

回傳圖片（縮小到 `max_width`）和一段 JSON：`path`、`frame`（實際渲染的影格）、`current_frame`、`width`、`height`、`file_size`（PNG 的像素尺寸）、`file_bytes`、`samples`、`seconds`、`image_size` 和 `scaled`；用了 `camera_preset` 時還有 `camera_preset`、`camera` 和 `camera_restored`。`return_image=false` 時只回傳 JSON。渲染逾時（180 秒）後無法取消，錯誤訊息會指出重試前要刪除的暫存檔。

### `render_contact_sheet`

渲染格狀圖：從目前的動畫取出一組影格排成格子，每個影格渲染成一格，格子的角落標上影格編號。要在製作影片前用一張圖檢查整段動作時使用，影片用 `render_video` 製作。每一格都使用相同的攝影機，預設使用乾淨的 View 顯示；Cascadeur 視窗不可以最小化。

| 參數 | 預設值 | 說明 |
|---|---|---|
| `start` | 動畫的第一個影格 | 區間的第一個影格：從 0 開始的整數，或像 `"12"` 這樣的數字字串。`start`、`end`、`step`、`frames` 都沒給時，會在整段動畫中均勻取樣 8 個影格。 |
| `end` | 動畫的最後一個影格 | 區間的最後一個影格（含）。 |
| `step` | `1` | `start` 和 `end` 之間的影格間隔；至少是 1。 |
| `frames` | — | 指定的影格：一個數字、像 `[0, 10, 20]` 的清單，或像 `"0,10,20"` 的字串。不能和 `start`、`end`、`step` 一起用。一張格狀圖最多 64 個影格。 |
| `columns` | `4` | 每列的格數（1–32）；影格比較少時會減少。 |
| `cell_width` | `320` | 一格的寬度，單位是像素（16–4096）；16:9 的高度由寬度算出。 |
| `camera_preset` | `"auto"` | `auto` 用固定的攝影機涵蓋整段動作的所有姿勢（角色前後移動時用側視）；`current` 沿用視埠的攝影機；`front`、`back`、`left`、`right`、`top`、`three_quarter` 從該視角涵蓋同樣的姿勢。 |
| `path` | `%TEMP%\cascadeur-mcp-plus\contact_sheets` 底下的新檔案 | 以 `.png` 結尾的絕對路徑。資料夾必須已經存在。 |
| `overwrite` | `false` | `true` 會取代 `path` 已存在的檔案，否則已存在的檔案會被拒絕。 |
| `keep_overlays` | `false` | `true` 照視埠目前的顯示渲染（控制點、關節、殘影、軌跡、剪影）；`false` 使用乾淨的 View 顯示（實心著色的表面，沒有疊加顯示）。 |

回傳格狀圖的圖片，以及一段 JSON：`path`、`frames`（實際渲染的影格）、`requested_frames`、`count`、`selection`（`frames`、`even sample` 或 `start/end/step`）、`columns`、`rows`、`cell_width`、`cell_height`、`file_size`（格狀圖的像素尺寸）、`file_bytes`、`camera_preset`、`camera_view`、`camera`、`clean_display` 和 `seconds`。呼叫結束後會還原播放磁頭、攝影機和顯示。選了超過 64 個影格，或格狀圖會超過單邊 16384 像素或總共 40,000,000 像素時，呼叫會被拒絕。如果格狀圖寫入後無法還原顯示，呼叫會回傳錯誤，並指出檔案位置。

### `render_video`

以背景工作用 Cascadeur 自己的編碼器，透過它的 Export video 視窗（`File.Export.Video...`）把動畫時間軸渲染成 MP4。用 `render_contact_sheet` 檢查過動作之後，用它製作最後的影片。預設會讓攝影機涵蓋整段動作，並把顯示切換成乾淨的 View 顯示；結束後會還原範圍、攝影機和顯示。

| 參數 | 預設值 | 說明 |
|---|---|---|
| `path` | `%TEMP%\cascadeur-mcp-plus\videos` 底下的新檔案 | 以 `.mp4` 結尾的絕對路徑。資料夾必須已經存在。 |
| `start` | 動畫的第一個影格 | 區間的第一個影格：從 0 開始的整數，或像 `"12"` 這樣的數字字串。 |
| `end` | 動畫的最後一個影格 | 區間的最後一個影格（含）。 |
| `width` | `1920` | 影片寬度，單位是像素（1–16384）。 |
| `height` | `1080` | 影片高度，單位是像素（1–16384）。 |
| `samples` | `MEDIUM`（4 個取樣） | 渲染品質的選擇值（1–4096）：1 是 `LOW`，2–4 是 `MEDIUM`，更大是 `HIGH`。Export video 視窗沒有精確的取樣數設定。 |
| `camera_preset` | `"auto"` | `auto`、`current`、`front`、`back`、`left`、`right`、`top`、`three_quarter` 其中之一。`auto` 涵蓋整段動作；`current` 沿用視埠的攝影機；其他的從該視角涵蓋整段動作。 |
| `overwrite` | `false` | `true` 會取代 `path` 已存在的檔案，否則已存在的檔案會被拒絕。 |
| `keep_overlays` | `false` | `true` 在輸出中保留視埠目前的顯示（控制點、關節、殘影、軌跡、剪影）；`false` 使用乾淨的 View 顯示。 |

回傳 `job_id`，不必等工作完成；用 `get_job` 查詢進度和結果，`cancel_job` 會按下 Export video 視窗的 Stop 按鈕。工作完成後的結果有 `path`、`file_bytes`、`frames`（編碼的影格數）、`duration_s`、`fps`、`range`、`width`、`height`、`quality`，以及還原了哪些東西（`frame_restored`、`range_restored`、`camera_restored`、`display_restored`）。Cascadeur 最小化、Export video 視窗已經開著，或另一個渲染還在執行時，呼叫會立刻被拒絕。工作 7200 秒後逾時。

### `render_image_sequence`

以背景工作用 Cascadeur 自己的渲染器，透過 Export video 視窗的 IMAGE FILES 模式，把動畫時間軸渲染成一個資料夾的圖片檔。不需要影片，或之後還要處理這些影格時使用；要 MP4 請用 `render_video`。涵蓋整段動作的攝影機、乾淨的 View 顯示和還原的做法都和 `render_video` 相同。

| 參數 | 預設值 | 說明 |
|---|---|---|
| `folder` | `%TEMP%\cascadeur-mcp-plus\sequences` 底下的新資料夾 | 不存在或是空的資料夾的絕對路徑（它的上層資料夾必須已經存在）。已經有檔案的資料夾會被拒絕，避免新舊影格混在一起。 |
| `start` | 動畫的第一個影格 | 區間的第一個影格：從 0 開始的整數，或像 `"12"` 這樣的數字字串。 |
| `end` | 動畫的最後一個影格 | 區間的最後一個影格（含）。 |
| `width` | `1920` | 圖片寬度，單位是像素（1–16384）。 |
| `height` | `1080` | 圖片高度，單位是像素（1–16384）。 |
| `samples` | `MEDIUM`（4 個取樣） | 渲染品質的選擇值（1–4096）：1 是 `LOW`，2–4 是 `MEDIUM`，更大是 `HIGH`。Export video 視窗沒有精確的取樣數設定。 |
| `camera_preset` | `"auto"` | `auto`、`current`、`front`、`back`、`left`、`right`、`top`、`three_quarter` 其中之一。`auto` 涵蓋整段動作；`current` 沿用視埠的攝影機；其他的從該視角涵蓋整段動作。 |
| `keep_overlays` | `false` | `true` 在輸出中保留視埠目前的顯示（控制點、關節、殘影、軌跡、剪影）；`false` 使用乾淨的 View 顯示。 |

回傳 `job_id`，不必等工作完成；用 `get_job` 查詢進度和結果，`cancel_job` 會按下 Export video 視窗的 Stop 按鈕。工作完成後的結果有 `path`（資料夾）、`frames`（圖片檔的數量）、`files`（檔名）、`file_bytes`、`range`、`width`、`height`、`quality`，以及還原了哪些東西（`frame_restored`、`range_restored`、`camera_restored`、`display_restored`）。Cascadeur 最小化、Export video 視窗已經開著，或另一個渲染還在執行時，呼叫會立刻被拒絕。工作 7200 秒後逾時。

## 攝影機和顯示

### `get_camera`

讀取 3D 主視埠的攝影機，不會改動任何東西。移動攝影機之前，或要確認渲染會拍到什麼時使用。

沒有參數。

回傳 `camera`，其中有 `position` 和 `target`（世界座標，cm）、`type`（`PERSPECTIVE` 或 `ISOMETRIC`）、`distance`（cm）、`direction`（從目標指向攝影機的單位向量）、`fov_deg`（垂直視角，單位是度）和 `tracking`（這個伺服器的 `track_object` 正在跟隨的物件，沒有則為 `null`）。

### `set_camera`

設定視埠攝影機在世界座標中的位置和／或注視目標（cm），藉此移動攝影機。要拍特定鏡頭時使用；想要角色的標準視角，請用 `camera_preset`。攝影機不能翻滾：它的上方向是世界上方向投影到視圖平面的結果，和視埠本身一樣。

| 參數 | 預設值 | 說明 |
|---|---|---|
| `position` | 目前的位置 | 攝影機位置 `[x, y, z]`，單位是世界座標的 cm。 |
| `target` | 目前的目標 | 注視目標 `[x, y, z]`，單位是世界座標的 cm。 |

回傳 `camera`（讀回的值）和 `previous`（呼叫前的值，讓呼叫端可以還原）。兩個參數都沒給，或位置和目標相距不到 0.1 cm 時，呼叫會被拒絕。

### `camera_preset`

把攝影機放到角色的標準視角。`front`、`back`、`left` 和 `right` 依角色本身的朝向決定，這個朝向取自骨盆、胸和肩膀的控制點，而不是世界座標軸，所以角色轉身後 `front` 仍然看得到臉。攝影機會切換成 PERSPECTIVE，因為取景的計算以它為前提。

| 參數 | 預設值 | 說明 |
|---|---|---|
| `view` | 必填 | `front`、`back`、`left`、`right`、`top`、`three_quarter` 其中之一。`top` 從上方往下看；`three_quarter` 在角色前方、角色的右側，稍微偏上。 |
| `target` | `"character"` | `"character"` 要求場景裡剛好有一個角色。也可以給 `list_characters` 列出的角色名稱，或 `list_controllers` 列出的控制點名稱或別名，以對準那個點。 |
| `distance` | 涵蓋整個角色 | 目標到攝影機的距離，單位是 cm；必須大於 0。省略時，攝影機會以小幅留白涵蓋整個角色。 |

回傳 `camera`（讀回的值）、`preset`、`target`（對準的對象：`kind`、`name`、`character` 和注視點 `point`）、`distance`（cm）、`fov_deg` 和 `aspect`。

### `frame_objects`

移動攝影機，讓每個指定的物件都進入畫面，並維持目前的視線方向。要看一組控制點或一個道具時使用；整個角色的標準視角用 `camera_preset` 比較合適。攝影機與注視點至少相距 25 cm，範圍不到 5 cm 的物件只會在目前的距離重新對準。

| 參數 | 預設值 | 說明 |
|---|---|---|
| `names` | 必填 | 物件名稱或角色別名（例如 `left_hand`、`pelvis`、`head`）；至少一個。 |

回傳 `camera`（新的攝影機，讀回的值）、`target`（注視點，cm）、`distance`、`objects`（每個名稱和它的世界座標）和 `projected`（每個物件正規化的螢幕位置 `ndc`，以及 `on_screen`）。

### `track_object`

讓視埠攝影機跟隨某個物件或角色；省略 `name` 就停止跟隨。動畫播放時角色移出畫面的情況可以用它。Cascadeur 只提供「Track selection with camera」這個切換動作，沒有辦法查詢狀態，所以這個工具會記住是不是自己開啟了跟隨，而且只有在自己開啟過時才會再把它關掉。

| 參數 | 預設值 | 說明 |
|---|---|---|
| `name` | — | 要跟隨的物件名稱、角色名稱，或控制點名稱或別名；跟隨角色時追蹤的是它的骨盆。省略就停止跟隨。 |

回傳 `tracking`、`changed`，開始跟隨時還有被跟隨的 `target` 和 `ids`。開始跟隨會把場景的選取改成被跟隨的物件；停止時會還原先前的選取（`selection_restored`）。

### `get_display_state`

讀取目前的顯示狀態，不會改動任何東西。變更顯示之前，以及要確認 `set_*` 顯示工具的結果時使用。

沒有參數。

回傳 `view_mode`（作用中視埠的模式）和 `view_modes`（每個視埠一個）、`viewport_layout`（視埠數量）、`ghosts`（以及 `ghost_base_mode` 和 `ghost_keyframes`）、`trajectory_mode`（軌跡顯示關閉時是 `off`）、`trajectory_active`、`only_key_frames`、`silhouette` 和 `surface`。Cascadeur 的介面沒有回報的值是 `null`，並列在 `warnings` 裡。

### `set_view_mode`

切換作用中 3D 視埠的顯示模式。要把角色看成著色的幾何形狀、關節、控制器方塊或控制點，或切到 Cascadeur 的綁定檢視時使用。

| 參數 | 預設值 | 說明 |
|---|---|---|
| `mode` | 必填 | `mesh`、`joint`、`box_controller`、`point_controller`、`autoposing`、`rigging_view`、`view` 其中之一。`view` 畫出實心著色的表面，不含控制點、關節等編輯用的疊加顯示。 |

回傳 `changed` 和 `view_mode`（呼叫後作用中的模式）。

### `set_ghosts`

設定殘影模式。`keyframes` 會開啟 Cascadeur 的關鍵影格模式選取，讓殘影放在關鍵影格上（殘影關閉時會先啟用前一個影格的殘影）；其他模式使用相鄰或選取的影格。再用同一個模式呼叫一次，不會有任何變化。

| 參數 | 預設值 | 說明 |
|---|---|---|
| `mode` | 必填 | `none`、`previous`、`next`、`neighbors`、`keyframes`、`selected_frames` 其中之一。 |

回傳 `changed`、`ghosts`（呼叫後的模式）和 `ghost_keyframes`。

### `set_trajectories`

設定 Trajectory 工具的模式，也可以選擇只在關鍵影格上顯示軌跡。要顯示選取或有父子關係的物件所走的弧線時使用。重複同一個呼叫不會有任何變化。

| 參數 | 預設值 | 說明 |
|---|---|---|
| `mode` | 必填 | `off`、`all`、`edges`、`edges_and_fixed`、`parents` 其中之一；`off` 會關閉軌跡顯示。 |
| `only_key_frames` | 目前的設定 | `true` 只在關鍵影格上顯示軌跡，`false` 在每個影格上都顯示；省略就維持目前的設定。 |

回傳 `changed`、`trajectory_mode` 和 `only_key_frames`（都是呼叫後的狀態）。

### `set_viewport_layout`

把 3D 視埠的版面改成 1、2 或 4 個視埠。要比較不同的攝影機或視角之前使用。用目前的數量呼叫，不會有任何變化。

| 參數 | 預設值 | 說明 |
|---|---|---|
| `count` | 必填 | 視埠數量：`1`、`2` 或 `4`。 |

回傳 `changed` 和 `viewport_layout`（呼叫後的視埠數量）。

### `set_silhouette`

開啟或關閉 3D 視埠的剪影顯示。要對照背景檢查角色的輪廓時使用。用目前的值呼叫，不會有任何變化。

| 參數 | 預設值 | 說明 |
|---|---|---|
| `enabled` | 必填 | `true` 開啟剪影顯示，`false` 關閉。必須是布林值。 |

回傳 `changed` 和 `silhouette`（呼叫後的狀態）。

### `set_surface`

開啟或關閉 3D 視埠的表面顯示。要在著色的表面和線框或關節檢視之間切換時使用。用目前的值呼叫，不會有任何變化。

| 參數 | 預設值 | 說明 |
|---|---|---|
| `enabled` | 必填 | `true` 開啟表面，`false` 關閉。必須是布林值。 |

回傳 `changed` 和 `surface`（呼叫後的狀態）。
## 介面操作（最後手段）

### `ui_tree`

列出開啟的視窗和畫面上的介面元素，並為每個元素附上元素參照（`ref`），供其他介面工具使用。只在診斷介面，或要找沒有專用工具能提供的元素時使用；一般的場景操作請用 `get_scene_info`、`list_controllers`、`get_timeline` 和 `get_display_state`。只會列出有文字、標題、工具提示、placeholder 文字、`actionId` 或 `objectName` 的元素，以及可點擊或可輸入文字的元素。

| 參數 | 預設值 | 說明 |
|---|---|---|
| `window` | 所有視窗 | 視窗編號或視窗標題的一部分；只列出該視窗。 |
| `query` | — | 只列出文字、標題、工具提示、placeholder 文字、`actionId`、`objectName` 或類型包含這段字的元素；不分大小寫。 |
| `interactive_only` | `false` | `true` 時只列出可點擊的元素、文字輸入框和有 `actionId` 的元素。 |
| `include_hidden` | `false` | `true` 時也列出隱藏的視窗和元素。此時視窗編號也包含隱藏的視窗，可能和不加這個參數時的編號不同。 |
| `limit` | `150` | 最多列出幾個元素。 |

回傳 `windows`（每個視窗有 `window` 編號、`title`、`visible`、`active` 和 `size`）、`elements`、`matched` 和 `truncated`。每個元素有 `ref`（例如 `0:12`）、`type`、它具備的 `text`、`title`、`toolTip`、`placeholderText`、`actionId` 和 `objectName`、為 true 時的 `checked` 或 `active`、停用時的 `enabled: false`、`clickable`、`input`，以及以視窗座標表示的 `rect`（`[x, y, 寬, 高]`）。`matched` 是所有符合的數量；`limit` 截斷清單時，`truncated` 為 `true`。每次呼叫 `ui_tree` 或 `dialogs`，`ref` 都會重設；其他工具（例如背景工作）在執行時檢查對話框也一樣；用到已失效的 `ref` 會被拒絕。

### `ui_click`

像使用者一樣點擊：透過 Qt 的視窗系統輸入路徑（QtTest）送出模擬的滑鼠事件，點在以 `ref` 或 `query` 找到的元素中心，或視窗上的 `x`／`y` 這個點。只在沒有專用工具能完成時使用：擺姿勢用 `set_pose`、選取用 `select_objects`、設定關鍵影格用 `set_key`、觸發 Cascadeur 動作用 `call_action`。在 Rig 模式以外點擊 Quick Rigging 的 **Add rig elements** 或 **Generate rig** 會被拒絕，除非 `force` 為 `true`。

| 參數 | 預設值 | 說明 |
|---|---|---|
| `ref` | — | 最近一次 `ui_tree` 或 `dialogs` 結果裡的元素參照（`ref`）。 |
| `query` | — | 畫面上元素的文字、工具提示、`actionId`、`objectName` 或 placeholder 文字；不分大小寫。完全符合的優先，其次是可點擊且已啟用的元素。沒有給 `ref` 時才使用。 |
| `window` | 焦點視窗，否則主視窗 | 視窗編號或視窗標題的一部分。有給 `ref` 時會忽略。 |
| `x` | — | 視窗的 x 座標（單位和 `ui_tree` 的 `rect` 相同）。`x` 和 `y` 要一起給才會點擊該點，此時會忽略 `ref` 和 `query`。 |
| `y` | — | 視窗的 y 座標。 |
| `button` | `"left"` | `"left"`、`"right"` 或 `"middle"`。 |
| `double` | `false` | `true` 時送出雙擊。 |
| `modifiers` | — | 點擊時按住的修飾鍵：`"ctrl"`、`"shift"`、`"alt"` 和 `"meta"`（也可用 `"win"`），例如 `["ctrl"]`。 |
| `index` | `0` | 點 `query` 的第幾個符合結果：0 是最符合的，1 是下一個，依此類推。 |
| `force` | `false` | `true` 時略過 Quick Rigging 防護（請看[安全](safety.zh-TW.md)）。 |
| `settle_ms` | `150` | 點擊後等待介面更新的毫秒數，0 到 5000。 |

回傳 `clicked`（視窗標題、點擊的 `point`（`[x, y]`），點到元素時還有元素的詳細資料，元素停用時會附上 `warning`），以及 `query` 符合多個元素時的 `other_matches`。視窗隱藏時會被拒絕。

### `ui_drag`

像使用者一樣用滑鼠拖曳：在起點按下、按住並分段移動、在終點放開；每一步都是獨立的 GUI 執行緒工作，所以 AutoPosing 等即時更新會在每次移動之間正常運作。只在沒有專用工具或動作可用的介面元件上使用；擺角色姿勢請用 `set_pose`，移動攝影機請用 `set_camera` 或 `camera_preset`。起點依序看 `object`、`x`／`y`、`ref`／`query`；終點依序看 `to_x`／`to_y`、`dx`／`dy`，兩者至少要給一組。

| 參數 | 預設值 | 說明 |
|---|---|---|
| `object` | — | 起點的場景物件，用確切名稱（例如 `mixamorig:LeftHand_MainPoint`）。它投影到 3D 視埠的位置就是起點；物件不在畫面上時會被拒絕。 |
| `ref` | — | 最近一次 `ui_tree` 或 `dialogs` 結果裡的元素參照（`ref`）；從元素中心開始拖曳。 |
| `query` | — | 畫面上元素的文字、工具提示、`actionId`、`objectName` 或 placeholder 文字；從該元素中心開始拖曳。 |
| `window` | 焦點視窗，否則主視窗 | 視窗編號或視窗標題的一部分。有給 `ref` 時會忽略。 |
| `x` | — | 起點的視窗 x 座標。`x` 和 `y` 要一起給。 |
| `y` | — | 起點的視窗 y 座標。 |
| `to_x` | — | 終點的視窗 x 座標。`to_x` 和 `to_y` 要一起給。 |
| `to_y` | — | 終點的視窗 y 座標。 |
| `dx` | — | 終點相對於起點的水平位移（視窗座標）。`to_x`／`to_y` 沒有同時給時才使用。 |
| `dy` | — | 終點相對於起點的垂直位移；負數往畫面上方移動。只給 `dx` 或 `dy` 其中一個時，另一個視為 0。 |
| `button` | `"left"` | `"left"`、`"right"` 或 `"middle"`；拖曳期間按住。 |
| `modifiers` | — | 拖曳期間按住的修飾鍵，和 `ui_click` 相同。 |
| `steps` | `20` | 移動事件的次數，1 到 500；超出範圍的值會被調整到這個範圍內。 |
| `duration_ms` | `400` | 移動的總時間，單位為毫秒。 |
| `hold_ms` | `80` | 按下後、開始移動前的停頓，單位為毫秒。 |
| `settle_ms` | `300` | 放開後等待介面更新的毫秒數，0 到 5000。 |
| `method` | `"qtest"` | `"qtest"` 走 Qt 的視窗系統輸入路徑；`"event"` 直接送出 `QMouseEvent`。其他值會被拒絕。 |
| `fov_deg` | `30` | 投影 `object` 時用的垂直視角，單位為度。 |

回傳 `window`、實際的起點 `start` 和終點 `end`（皆為 `[x, y]`）、`steps`、`duration_ms`、`method` 和 `target`（投影的物件或元素；單純的點則為空）。就算中途移動失敗，滑鼠按鍵也一定會放開。如果拖曳之後有對話框開著（例如 AutoPosing 的提示），結果還會有 `dialogs` 和 `warning`：請用 `ui_click(ref=...)` 回答對話框，再拖一次。

### `ui_hover`

把滑鼠移到元素或視窗上的某個點，用來顯示工具提示和懸停選單。只在要檢查沒有專用工具能說明的介面元件時使用；場景狀態請用 `get_scene_info`、`list_controllers` 和 `get_display_state`。

| 參數 | 預設值 | 說明 |
|---|---|---|
| `ref` | — | 最近一次 `ui_tree` 或 `dialogs` 結果裡的元素參照（`ref`）。 |
| `query` | — | 畫面上元素的文字、工具提示、`actionId`、`objectName` 或 placeholder 文字；不分大小寫。完全符合的優先，其次是可點擊且已啟用的元素。沒有給 `ref` 時才使用。 |
| `window` | 焦點視窗，否則主視窗 | 視窗編號或視窗標題的一部分。有給 `ref` 時會忽略。 |
| `x` | — | 視窗的 x 座標（單位和 `ui_tree` 的 `rect` 相同）。`x` 和 `y` 要一起給才會移到該點，此時會忽略 `ref` 和 `query`。 |
| `y` | — | 視窗的 y 座標。 |
| `settle_ms` | `150` | 移動後等待介面更新的毫秒數，讓工具提示或選單有時間出現，0 到 5000。 |

回傳 `hovered`，內含 `window` 標題和 `point`（`[x, y]`）。

### `ui_key`

按下按鍵或快速鍵。只在沒有專用工具或動作可用的快速鍵時使用；同樣的操作請優先用 `set_frame`、`undo`／`redo`、`call_action` 和場景工具。按鍵會送到視窗目前焦點所在的元素，所以文字欄位有焦點時可能攔截按鍵（`ui_type` 的 `release_focus` 可以放掉焦點）。

| 參數 | 預設值 | 說明 |
|---|---|---|
| `keys` | 必填 | 用空白分隔的按鍵組合，例如 `"ctrl+s"`、`"Escape"`、`"ctrl+a Delete"` 或 `"F"`。修飾鍵：`ctrl`、`shift`、`alt`、`meta`（也可用 `win`）。按鍵：英文字母和數字、`F1` 到 `F12`、`Enter`、`Esc`、`Delete`、`Backspace`、`Tab`、`Space`、`Up`、`Down`、`Left`、`Right`、`Home`、`End`、`PageUp`、`PageDown`、`Insert`、`Plus`、`Minus`、`Comma`、`Period`，或其他去掉 `Key_` 前綴的 Qt 按鍵名稱。不認得的按鍵或修飾鍵會被拒絕。 |
| `window` | 焦點視窗，否則主視窗 | 視窗編號或視窗標題的一部分。 |
| `settle_ms` | `150` | 按鍵後等待介面更新的毫秒數，0 到 5000。 |

回傳 `window`（標題）和 `pressed`（已按下的組合）。

### `ui_type`

在目前焦點所在的元素輸入文字，或先用 `ref` 或 `query` 讓某個元素取得焦點。文字以按鍵事件送出，所以任何 Unicode 文字都能輸入。只在沒有專用工具能設定的文字欄位使用；一般的編輯請用 `call_action` 和狀態工具。

| 參數 | 預設值 | 說明 |
|---|---|---|
| `text` | 必填 | 要輸入的文字。 |
| `ref` | — | 最近一次 `ui_tree` 或 `dialogs` 結果裡的元素參照（`ref`）；該元素會先取得焦點。如果它本身不是文字輸入框，就用它裡面的第一個文字輸入框。 |
| `query` | — | 要先取得焦點的元素的文字、工具提示、`actionId`、`objectName` 或 placeholder 文字；不分大小寫。沒有給 `ref` 時才使用。 |
| `window` | 焦點視窗，否則主視窗 | 視窗編號或視窗標題的一部分。有給 `ref` 時會忽略。 |
| `replace` | `false` | `true` 時先清空欄位再輸入；只有用 `ref` 或 `query` 指定元素時才有作用。 |
| `submit` | `false` | `true` 時輸入後按 Enter。 |
| `release_focus` | `false` | `true` 時輸入後放掉鍵盤焦點，讓快速鍵重新作用在 Cascadeur 上。 |
| `settle_ms` | `150` | 輸入後等待介面更新的毫秒數，0 到 5000。 |

回傳 `window`（標題）、`typed`（輸入的文字）和 `target`（取得焦點的元素；沒有給 `ref` 或 `query` 時為 `null`）。

### `ui_set_property`

設定元素的 QML 屬性，例如 `checked`、`value`、`text` 或 `currentIndex`。這會略過元素的輸入處理。只在沒有專用工具或動作時使用；顯示狀態請用 `set_view_mode`、`set_ghosts`、`set_trajectories`、`set_silhouette` 和 `set_surface`。

| 參數 | 預設值 | 說明 |
|---|---|---|
| `ref` | 必填 | 最近一次 `ui_tree` 或 `dialogs` 結果裡的元素參照（`ref`）。 |
| `name` | 必填 | QML 屬性名稱，例如 `checked`、`value` 或 `text`。 |
| `value` | 必填 | 新的值：字串、數字、布林值或 `null`。 |

回傳 `ok`，以及變更前後的值 `before` 和 `after`（兩者都是文字）。

### `ui_invoke`

呼叫元素上沒有參數的 QML 方法或訊號，例如 `clicked`、`toggle`、`open` 或 `close`。只在該行為沒有專用工具或 Cascadeur 動作時使用；動作有 `actionId` 時請優先用 `call_action`。Quick Rigging 防護一定會生效，也沒有 `force`。

| 參數 | 預設值 | 說明 |
|---|---|---|
| `ref` | 必填 | 最近一次 `ui_tree` 或 `dialogs` 結果裡的元素參照（`ref`）。 |
| `method` | 必填 | 沒有參數的 QML 方法或訊號名稱，例如 `clicked` 或 `toggle`。 |

回傳 `ok` 和 `element`（元素的詳細資料）。

### `dialogs`

列出目前顯示的對話框、彈出視窗和次要視窗，以及其中的文字，按鈕以 `ref` 附上。用來確認某個工具沒有留下開著的對話框，或找出需要回答的提示。呼叫後 `ui_tree` 的 `ref` 會重設。

沒有參數。

回傳 `dialogs` 和一則 `hint`，提示用 `ui_click(ref=...)` 按下按鈕。每個次要視窗有 `kind: "window"`，以及它的 `window` 編號、`title`、`visible`、`active` 和 `size`。每個對話框或彈出視窗有 `kind: "dialog"`，以及它的 `window` 編號、`type`、`texts`（最多 12 筆）和 `buttons`；每個按鈕有 `ref`（例如 `d0:3`）和 `text`。

### `screenshot`

用 Win32 的 `PrintWindow` 把 Cascadeur 視窗或單一元素擷取成圖片。只在要看介面本身（元件、選單或對話框）時使用：要看 3D 場景請用 `render_image`，要用格狀圖檢查動作請用 `render_contact_sheet`。視窗最小化時什麼都畫不出來，所以除非 `restore` 為 `true`，否則會被拒絕。

| 參數 | 預設值 | 說明 |
|---|---|---|
| `window` | 焦點視窗，否則主視窗 | 視窗編號或視窗標題的一部分。有給 `ref` 時，擷取該元素所在的視窗。 |
| `ref` | — | 最近一次 `ui_tree` 或 `dialogs` 結果裡的元素參照（`ref`）；只擷取這個元素。 |
| `max_width` | `1280` | 圖片比這個寬時會縮小到這個寬度；`0` 表示維持原尺寸。 |
| `format` | `"jpeg"` | `"jpeg"` 或 `"png"`。 |
| `restore` | `false` | `true` 時先把最小化的視窗還原。 |

回傳圖片，以及一段 JSON 說明：`window`（標題）、`window_size`、`image_size` 和 `window_units_per_pixel`（把圖片像素乘上它，就是視窗座標，也就是 `ui_click` 的 `x`／`y` 所用的單位；擷取元素時要再加上 `crop_rect` 的左上角）。擷取元素時還有 `crop_rect`（`[x, y, 寬, 高]`）。圖片全黑時會附上 `warning`。

### `project_point`

依名稱把場景物件投影成 3D 主視埠裡的視窗座標，給 `ui_click` 和 `ui_drag` 的 `x`／`y` 使用。它只計算座標；移動控制點請用 `set_pose`，選取請用 `select_objects`，一般的攝影機操作請用攝影機工具。它使用視埠攝影機的位置和目標，以及 30 度的垂直視角（在 Cascadeur 2026.2.3 量測），只支援透視攝影機。

| 參數 | 預設值 | 說明 |
|---|---|---|
| `names` | 必填 | 場景物件名稱的清單，例如 `["mixamorig:LeftHand_MainPoint"]`。只認確切名稱；`left_hand` 之類的別名在這裡不會被解析。 |
| `fov_deg` | `30` | 視埠攝影機的垂直視角，單位為度。 |

回傳 `points`，以名稱為鍵，每筆有 `world`（位置，單位 cm）、`depth`（在攝影機前方的距離，單位 cm）、`window_xy`（`[x, y]`）和 `on_screen`；在攝影機後方的物件 `on_screen` 為 `false`，帶有 `error`，沒有 `window_xy`。另外回傳 `viewport_rect`（`[x, y, 寬, 高]`）、`fov_deg` 和 `camera`（`position` 和 `target`）。物件名稱不存在時會被拒絕。

## 其他

### `get_status`

回報 MCP Plus 和 Cascadeur 的版本、這個版本測試過的 Cascadeur 版本（2026.2.3），以及這個版本中無法使用的每個工具和它缺少的 API 或動作 key。Cascadeur 更新後，或工具回報無法使用時，請先用它：這類工具仍留在工具清單中，描述前面會加上 `Unavailable in this Cascadeur version:`，呼叫時會被拒絕並附上缺少的項目。它也會列出在測試版本上量測過、換到其他版本時需要重新確認的行為（影片匯出視窗、AutoPosing 鎖定、動畫圖層、影片動捕等）。

| 參數 | 預設值 | 說明 |
|---|---|---|
| `force` | `false` | `true` 時重新檢查所有工具，而不是回傳啟動時做的報告。 |

回傳 `ok`、`mcp_plus_version`、`cascadeur_version`、`tested_cascadeur_version`、`version_tested`（兩個 Cascadeur 版本相同時為 `true`）、`version_note`、`tools` 和 `requirements`（已檢查的數量）、`checked_at`、`unavailable_count`、`unavailable`（每個工具一筆，內含 `tool`、`missing` 和 `reason`）、`actions`（動作 key 如何驗證）和 `version_dependent`（每個需要重新確認的行為各有 `area` 和 `note`）。檢查本身失敗時，`ok` 為 `false`，`error` 會說明原因。

### `api_search`

在 Cascadeur 的 `csc` 型別 stub、`pycsc` 包裝器，以及官方的 commands、samples 和 scripts 中搜尋類別、函式、方法和 docstring 文字。不確定 API 時，請在撰寫 `run_python` 程式碼前先用它。它是唯讀的、不使用 GUI 執行緒，第一次呼叫時才建立索引。

| 參數 | 預設值 | 說明 |
|---|---|---|
| `query` | 必填 | 類別、函式、方法或 docstring 的片段；不分大小寫。名稱符合的排在 docstring 符合的前面。空的查詢會被拒絕。 |
| `limit` | `20` | 最多回傳幾筆結果，1 到 100；比 100 大會限制為 100，比 1 小會被拒絕。 |
| `sources` | 所有來源群組 | 要搜尋的來源群組清單：`"all"`、`"python"`（除了 `stubs` 以外的所有群組）、`"stubs"`、`"pycsc"`、`"commands"`、`"samples"`、`"scripts"` 和 `"rig_mode"`。 |

回傳 `ok`、`query`、`count`（回傳的結果數）、`total`（所有符合的數量）和 `results`；每筆結果有 `symbol`、`file`、`line`、`signature`、`kind`（class、function、method、attribute 或 usage）和 `source`（來源群組）。`skipped_files` 列出無法解析的檔案；沒有任何符合時，`hint` 會建議改用較短的名稱。

### `api_describe`

顯示 `csc` 類別、函式或方法完整的型別 stub 區塊（函式簽章、docstring 和原始碼文字），包括類別的欄位和方法清單。要確認精確的簽章和成員名稱時，請在 `api_search` 之後使用。Cascadeur 執行中時，它也會列出執行階段存在、但 stub 裡沒有的成員；它是唯讀的，也不使用 GUI 執行緒。

| 參數 | 預設值 | 說明 |
|---|---|---|
| `symbol` | 必填 | `api_search` 回傳的 `symbol`，例如 `csc.tools.RenderToFile`。比對順序是完全相同、不分大小寫相同、符合某個 symbol 的結尾。名稱符合多個 symbol 時會被拒絕，並附上 `candidates`。 |

回傳 `ok`、`symbol`、`kind`、`file`、`line`、`signature`、`docstring`、`source`（區塊的文字）和 `source_group`（`stubs`；沒有 stub 定義該 symbol 時，則是其他來源群組）。類別還有 `fields` 和 `methods`；有多個定義時，`overloads` 是定義的數量。stub 的 symbol 還有 `runtime`，說明這個物件是否存在於執行中的 Cascadeur，類別還會列出 `missing_from_stub`。

### `run_python`

在 Cascadeur 的 GUI 執行緒執行 Python，可以直接使用 `csc`、`app`、`scene`（domain scene）、`view_scene` 和 `ui`。只在沒有專用工具涵蓋該操作時使用：請先用 `api_search` 確認 API，一般的操作請優先用場景、姿勢和時間軸工具。程式碼執行完畢前，Cascadeur 的介面會暫停回應。

| 參數 | 預設值 | 說明 |
|---|---|---|
| `code` | 必填 | Python 程式碼。單一運算式會被求值；否則執行這些陳述式，並回傳名為 `result` 的變數。變數不會在呼叫之間保留。 |
| `timeout` | `60` | 等待程式碼執行完畢的秒數，最多 600。時間到時這次呼叫會失敗；已經開始執行的程式碼會繼續跑完。 |

回傳 `ok`、`stdout` 和 `value`（單一運算式或 `result` 的 `repr`；兩者都沒有時不會出現）。發生例外時，`ok` 為 `false`，`error` 是 traceback，`stdout` 是到那時為止的輸出。`scene.modify*` 和 `edit` 的 callback 失敗時會回傳 `False`，請記得檢查。

### `get_log`

讀取 Cascadeur 日誌檔的最後幾行，錯誤和 Python traceback 都在這裡。工具回傳意外的錯誤或 traceback 後，或在除錯 `run_python` 時使用。它直接讀取檔案，不使用 GUI 執行緒。

| 參數 | 預設值 | 說明 |
|---|---|---|
| `lines` | `60` | 從最後往回讀的行數。 |
| `contains` | — | 只保留包含這段字的行（不分大小寫）；會先於 `lines` 套用。 |

回傳 `path`（日誌檔，預設是 `%LOCALAPPDATA%\Nekki Limited\Cascadeur\logs\cascadeur_log.log`，除非環境變數 `CASCADEUR_LOG` 指定了其他檔案）和 `lines`。檔案不存在時會回傳 `error`，內含路徑。

### `batch`

依序執行多個工具呼叫，並在一次回應中回傳每個結果。用於重複的流程，例如跳到某個影格、設定姿勢再加一個關鍵影格，不必每一步各送一次請求。每個呼叫都和直接呼叫完全相同，所以場景變更會保留該工具自己的復原步驟：一次 `undo` 只復原一個呼叫，而不是整個批次。

| 參數 | 預設值 | 說明 |
|---|---|---|
| `calls` | 必填 | 依執行順序排列的工具呼叫，每個是 `{"tool": 名稱, "arguments": {...}}`；`arguments` 預設為 `{}`。不能在裡面呼叫 `batch` 本身。會啟動背景工作的工具會照常回傳 `job_id`，不會等待。 |
| `stop_on_error` | `true` | `true` 時在第一個失敗的呼叫就停止；`false` 時其餘呼叫也會繼續執行。 |
| `timeout` | `110` | 整個批次的時間預算，單位為秒，最多 600；不是正數的值會被拒絕。每個呼叫開始前，會拿剩餘的預算和該呼叫自己的 `timeout`（沒有的話用 60 秒）比較；放不下的呼叫不會開始，已經開始的呼叫不會被中斷。 |

回傳 `ok`、`count` 和 `results`；`results` 的每一筆有 `index`、`tool`、`ok` 和 `result`（被呼叫工具自己的結果；圖片工具只有 `images` 的數量和它的 `meta` 說明）。呼叫失敗或預算用完時，`ok` 為 `false`，`results` 仍包含已完成的呼叫：`stopped_at` 是批次停下來的位置，`stop_on_error` 為 `false` 時 `failed` 列出失敗的位置，`not_run` 列出沒有開始的呼叫。無效的輸入（巢狀的 `batch`、缺少 `tool`、不是物件的 `arguments`）會在任何呼叫執行前就被拒絕。

### `get_job`

查詢背景工作的狀態、進度、結果或錯誤。搭配會啟動背景工作的工具所回傳的 `job_id` 使用。`wait_s` 在 HTTP 執行緒上等待，所以不會卡住 Cascadeur 的介面。

| 參數 | 預設值 | 說明 |
|---|---|---|
| `job_id` | 必填 | 會啟動背景工作的工具所回傳的工作 id，例如 `"job-3"`。 |
| `wait_s` | `0` | 等待工作結束的秒數，最多 60；工作一結束，等待就結束。 |

回傳 `ok`、`job_id`、`kind`、`status`（`running`、`done`、`failed`、`cancelled` 或 `lost`）、`done`（工作以任何狀態結束後為 `true`）、`progress`（0 到 1，未知時為 `null`）、`elapsed_s`、`started_at`、`finished_at`、`result`（工作完成後，該工具自己的結果），工作失敗時還有 `error`、`hint` 和 `traceback`。工具的取消處理失敗時，`cancel_error` 會有 traceback。伺服器重新啟動時還在執行的工作會是 `lost`；找不到的 id 會回傳 `ok: false`。

### `list_jobs`

列出最近的背景工作，由新到舊，附上種類、狀態和進度。用來找工作 id。最多保留 100 個工作，最舊的已結束工作會先被移除。

| 參數 | 預設值 | 說明 |
|---|---|---|
| `limit` | `20` | 最多回傳幾個工作，1 到 100；超出範圍的值會被調整到這個範圍內。 |

回傳 `ok`、`count` 和 `jobs`；每個工作的欄位和 `get_job` 相同（`job_id`、`kind`、`status`、`progress`、`result` 等）。伺服器重新啟動時還在執行的工作會列為 `lost`。

### `cancel_job`

停止執行中的背景工作，並標為 `cancelled`。工作卡住或不再需要時使用。它會呼叫該工具自己的取消處理（例如渲染對話框的 Stop 按鈕），所以不會留下仍在執行的工作或開著的模態對話框。

| 參數 | 預設值 | 說明 |
|---|---|---|
| `job_id` | 必填 | 會啟動背景工作的工具所回傳的工作 id。 |

回傳 `ok`、`cancelled: true`，以及和 `get_job` 相同的工作最終欄位。已經結束的工作會以 `ok: false` 被拒絕，因為只有執行中的工作能取消。

### `undo`

用 Cascadeur 自己的 `Scene.Undo` 動作復原最近一次（或最近幾次）場景變更，透過 `ActionManager` 呼叫，而不是模擬 Ctrl+Z，所以有焦點的文字欄位不會攔截它。工具呼叫出錯、想退回時使用。步驟名稱讀自 Cascadeur 的日誌。

| 參數 | 預設值 | 說明 |
|---|---|---|
| `steps` | `1` | 要復原的步驟數，1 到 100；比 100 大會限制為 100，比 1 小會被拒絕。 |

回傳 `ok`、`action`、`requested`、`count` 和 `steps`：Cascadeur 實際復原的步驟名稱，依復原的順序排列。復原堆疊用完時，復原的步數會比較少，`hint` 會說明；完全無法復原時，`ok` 為 `false`。

### `redo`

用 Cascadeur 自己的 `Scene.Redo` 動作，透過 `ActionManager` 重做最近一次（或最近幾次）被復原的場景變更。用 `undo` 後發現被復原的變更才是想要的，就用它。步驟名稱讀自 Cascadeur 的日誌。

| 參數 | 預設值 | 說明 |
|---|---|---|
| `steps` | `1` | 要重做的步驟數，1 到 100；比 100 大會限制為 100，比 1 小會被拒絕。 |

回傳 `ok`、`action`、`requested`、`count` 和 `steps`：Cascadeur 實際重做的步驟名稱，依重做的順序排列。剩下的步驟比較少時，只會重做那些，`hint` 會說明；完全沒有可重做的步驟時，`ok` 為 `false`，`hint` 會說明原因。

### `get_undo_history`

列出目前場景復原歷程最近的步驟，由舊到新，每一步標示為 `applied` 或 `undone`。只列出 MCP Plus 做的步驟（名稱以 `MCP: ` 開頭），因為 Cascadeur 沒有可取得完整歷程的 Python API；清單是從 Cascadeur 的日誌重建的。Cascadeur 啟動、記憶體管理員清除所有歷程、以及建立或載入場景時，記錄都會重新開始。

| 參數 | 預設值 | 說明 |
|---|---|---|
| `limit` | `20` | 最多回傳幾個步驟，1 到 200；比 200 大會限制為 200，比 1 小會被拒絕。 |

回傳 `ok`、`steps`（每筆有 `index`、`name` 和 `state`）、`position`（已套用的 MCP 步驟數）、`count`（追蹤到的所有步驟）、`truncated`（`limit` 排除了較舊的步驟時為 `true`）、`next_undo` 和 `next_redo`（下一次 `undo` 或 `redo` 會動到的 MCP 步驟）、`recorded_since` 和 `reset_reason`（哪個事件開始了目前的記錄；在它之前的 MCP 步驟已不在任何復原堆疊中）以及 `log`（日誌路徑）。日誌無法讀取時會附上 `warning`。

### `list_actions`

列出在目前介面中找得到的 Cascadeur 動作 key（`actionId`，例如 `Commands.Rig mode.On`）和標籤。沒有專用工具可用時使用，再用 `call_action` 觸發該 key。清單來自目前的 QML 介面樹，所以從沒開過的工具，它的動作可能不會出現。

| 參數 | 預設值 | 說明 |
|---|---|---|
| `query` | — | 只列出 `actionId` 包含這段字的動作；不分大小寫。 |
| `include_hidden` | `true` | `true` 時也包含隱藏視窗和元素的動作。 |

回傳 `actions`，依 `actionId` 排序；每筆有 `actionId`，元素有工具提示或文字時有 `label`，有可見的元素帶有它時 `on_screen` 為 `true`。`note` 會重複提醒從沒開過的工具可能缺少動作。

### `call_action`

透過 `ActionManager.call_action` 依 key 觸發 Cascadeur 動作，效果和按下它的工具列按鈕或選單項目完全相同。有專用工具時請先用專用工具（`set_pose`、`set_key`、`select_objects` 等）；`call_action` 是給沒有包裝工具的動作使用。用 `list_actions` 找 key。

| 參數 | 預設值 | 說明 |
|---|---|---|
| `key` | 必填 | `list_actions` 列出的 Cascadeur `actionId`，例如 `Commands.Rig mode.On`。 |
| `settle_ms` | `150` | 動作後等待介面更新的毫秒數，0 到 5000。 |

回傳 `called`，也就是觸發的 key。
