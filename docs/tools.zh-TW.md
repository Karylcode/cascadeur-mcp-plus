[English](tools.md) | **繁體中文** · [文件索引](README.zh-TW.md)

# 工具

MCP Plus 有 18 個工具。`run_python` 涵蓋官方伺服器的 `run_script`，其他 17 個是新增的。

- 場景和 Python：`run_python`、`get_scene_info`、`get_object_info`、`project_point`
- 畫面和介面檢視：`screenshot`、`ui_tree`、`dialogs`
- 介面輸入：`ui_click`、`ui_hover`、`ui_drag`、`ui_key`、`ui_type`、`ui_set_property`、`ui_invoke`
- 動作、Rig 模式和日誌：`list_actions`、`call_action`、`enter_rig_mode`、`get_log`

呼叫失敗時，會回傳工具錯誤和說明訊息。

## 共用參數

| 參數 | 說明 |
|---|---|
| `window` | 視窗編號或標題的一部分。預設是目前焦點所在的視窗，沒有的話用 Cascadeur 主視窗。`ui_tree` 預設列出所有開啟的視窗。 |
| `ref` | 最近一次 `ui_tree`（例如 `0:12`）或 `dialogs`（例如 `d0:3`）回傳的元素 ref。每次呼叫 `ui_tree` 或 `dialogs`，ref 都會重設。 |
| `query` | 畫面上元素的文字、提示、actionId、objectName 或 placeholder 文字。完全符合的優先，其次是可點擊、已啟用的元素。 |
| `settle_ms` | 操作後等介面更新的毫秒數，最多 5000。預設 150；`ui_drag` 預設 300。只有 `ui_click`、`ui_hover`、`ui_drag`、`ui_key`、`ui_type` 和 `call_action` 有這個參數。 |

## 場景和 Python

### `run_python`

在 Cascadeur 的主執行緒執行 Python，可以直接使用 `csc`、`app`、`scene`（domain scene）、`view_scene` 和 `ui`。

| 參數 | 預設值 | 說明 |
|---|---|---|
| `code` | 必填 | Python 程式碼 |
| `timeout` | 60 | 等待秒數，最多 600 |

回傳 `ok`、`stdout` 和 `value`（單一運算式的值，或名為 `result` 的變數的值，以 repr 表示）。
發生例外時回傳 `ok: false` 和 traceback。`scene.modify*`／`edit` 的 callback 失敗時會回傳 `False`，請記得檢查。

### `get_scene_info`

整理目前場景的摘要。

| 參數 | 預設值 | 說明 |
|---|---|---|
| `max_objects` | 80 | 範例清單列出的物件數量 |

回傳場景名稱、路徑、開啟的場景數和每幀重力；目前幀、第一幀、最後一幀和動畫長度；物件總數、各類型數量和物件範例；
選取（數量和最多 50 個名稱）；`RigInfo`、`AnimationInfo`、`QrtInfo`、`Joint`、`MeshObject`、`AutoPosing`
這幾種 behaviour 的數量；以及授權狀態（pro 功能、匯出）。

### `get_object_info`

依名稱說明物件。

| 參數 | 預設值 | 說明 |
|---|---|---|
| `name` | 必填 | 先找完全相同的名稱；找不到時，改用不分大小寫的部分比對 |
| `max_properties` | 40 | 每個 behaviour 列出的屬性名稱數量 |

最多回傳 5 個物件，每個都有名稱、類型、id，以及 behaviour 和它們的屬性名稱。

### `project_point`

把場景物件投影成 3D 主視埠裡的主視窗座標，給 `ui_click`／`ui_drag` 的 `x`/`y` 使用。

| 參數 | 預設值 | 說明 |
|---|---|---|
| `names` | 必填 | 場景物件名稱的陣列 |
| `fov_deg` | 30 | 視埠相機的垂直視角 |

每個名稱回傳 `window_xy`、`on_screen`、`depth` 和世界座標（在相機後方的物件 `on_screen` 為 `false`），
另外回傳視埠範圍和相機的位置、目標。只支援透視相機。預設的 30° 是在 Cascadeur 2026.2.3 量測的。

## 畫面和介面檢視

### `screenshot`

用 Win32 的 `PrintWindow` 擷取 Cascadeur 視窗，或單一元素。

| 參數 | 預設值 | 說明 |
|---|---|---|
| `window` | 焦點視窗／主視窗 | |
| `ref` | — | 只擷取這個元素 |
| `max_width` | 1280 | 縮小到這個寬度 |
| `format` | `jpeg` | `jpeg` 或 `png` |
| `restore` | `false` | 先把最小化的視窗還原 |

回傳圖片，以及一段 JSON：`window`、`window_size`、`image_size` 和 `window_units_per_pixel`
（把圖片像素換算成視窗座標），擷取元素時還有 `crop_rect`。視窗最小化時，除非 `restore=true`，否則會回傳錯誤。
圖片全黑時會附上警告。

### `ui_tree`

列出開啟的視窗和畫面上的介面元素，並附上 ref 給其他 `ui_*` 工具使用。

| 參數 | 預設值 | 說明 |
|---|---|---|
| `window` | 所有視窗 | 只看某個視窗 |
| `query` | — | 只列出文字、提示、actionId、objectName 或類型包含這段字的元素 |
| `interactive_only` | `false` | 只列出可點擊、可輸入或有 actionId 的元素 |
| `include_hidden` | `false` | 包含隱藏的視窗和元素 |
| `limit` | 150 | 最多列出幾個元素 |

回傳 `windows`、平的 `elements` 清單（ref、類型、text／title／toolTip／placeholderText、actionId、objectName、
為 true 時的 `checked`／`active`、停用時的 `enabled: false`、`clickable`、`input`，以及 `[x, y, 寬, 高]` 的
`rect`）、`matched` 和 `truncated`。

### `dialogs`

沒有參數。列出目前顯示的對話框、彈出選單和次要視窗，以及其中的文字，按鈕會附上 ref。

## 介面輸入

點擊、懸停、按鍵和拖曳都透過 Qt 的視窗系統輸入路徑（QtTest）送出。

### `ui_click`

點擊元素的中心，或視窗上的某個點。

| 參數 | 預設值 | 說明 |
|---|---|---|
| `ref`／`query` | — | 目標元素 |
| `x`、`y` | — | 改用視窗座標 |
| `window` | 焦點視窗／主視窗 | |
| `button` | `left` | `left`、`right` 或 `middle` |
| `double` | `false` | 雙擊 |
| `modifiers` | — | 例如 `["ctrl"]`、`["shift"]`；也可以用 `alt`、`meta`／`win` |
| `index` | 0 | 選 `query` 第 n 個最符合的結果 |
| `force` | `false` | 略過 Quick Rigging 防護（請看[安全](safety.zh-TW.md)） |
| `settle_ms` | 150 | |

回傳點擊的目標；`query` 符合多個元素時，還會回傳 `other_matches`。元素停用時會警告；視窗隱藏時會失敗。

### `ui_hover`

把滑鼠移到元素或某個點上（用來顯示提示和懸停選單）。

| 參數 | 預設值 | 說明 |
|---|---|---|
| `ref`／`query` | — | 目標元素 |
| `x`、`y` | — | 改用視窗座標 |
| `window` | 焦點視窗／主視窗 | |
| `settle_ms` | 150 | |

### `ui_drag`

在起點按下、按住並分段移動、在終點放開。每一步都是獨立的主執行緒工作，所以 AutoPosing 等即時更新會在每次移動之間正常運作。

| 參數 | 預設值 | 說明 |
|---|---|---|
| `object` | — | 從場景物件開始，會投影到 3D 視埠（例如 `mixamorig:LeftHand_MainPoint`） |
| `x`、`y` | — | 從視窗座標開始 |
| `ref`／`query` | — | 從元素中心開始 |
| `window` | 焦點視窗／主視窗 | |
| `to_x`、`to_y` | — | 終點的視窗座標 |
| `dx`、`dy` | — | 終點相對於起點的位移；`dy` 為負數時往上 |
| `button` | `left` | `left`、`right` 或 `middle` |
| `modifiers` | — | 和 `ui_click` 相同 |
| `steps` | 20 | 移動事件的次數（1–500） |
| `duration_ms` | 400 | 移動的總時間 |
| `hold_ms` | 80 | 按下後、開始移動前的停頓 |
| `settle_ms` | 300 | 放開後的等待時間 |
| `method` | `qtest` | `qtest` 走 Qt 的視窗系統輸入路徑；`event` 直接送出 `QMouseEvent` |
| `fov_deg` | 30 | 投影 `object` 時用的視角 |

起點依序看 `object`、`x`/`y`、`ref`/`query`。終點依序看 `to_x`/`to_y`、`dx`/`dy`，兩者至少要給一組。
就算中途移動失敗，滑鼠按鍵也一定會放開。回傳視窗、實際的起點和終點、`steps`、`duration_ms`、`method` 和目標。
如果拖曳時跳出對話框，結果裡還會有 `dialogs` 和警告：請先回答對話框，再拖一次。

### `ui_key`

按下按鍵或快速鍵。

| 參數 | 預設值 | 說明 |
|---|---|---|
| `keys` | 必填 | 用空白分隔的組合，例如 `ctrl+s`、`Escape`、`ctrl+a Delete`、`F` |
| `window` | 焦點視窗／主視窗 | |
| `settle_ms` | 150 | |

修飾鍵：`ctrl`、`shift`、`alt`、`meta`／`win`。按鍵：英文字母和數字、`F1`–`F12`、`Enter`、`Esc`、`Delete`、
`Backspace`、`Tab`、`Space`、方向鍵、`Home`、`End`、`PageUp`、`PageDown`、`Insert`、`Plus`、`Minus`、`Comma`、
`Period`，以及其他去掉 `Key_` 前綴的 Qt 按鍵名稱。

### `ui_type`

在目前焦點所在的元素輸入文字，或先用 `ref`／`query` 讓某個元素取得焦點。支援任何 Unicode 文字（包括中文）。
如果那個元素是容器，會輸入到它裡面的第一個文字輸入框。

| 參數 | 預設值 | 說明 |
|---|---|---|
| `text` | 必填 | |
| `ref`／`query` | — | 先取得焦點的元素 |
| `window` | 焦點視窗／主視窗 | |
| `replace` | `false` | 先清空欄位 |
| `submit` | `false` | 輸入後按 Enter |
| `release_focus` | `false` | 輸入後放掉鍵盤焦點，讓快速鍵重新作用在 Cascadeur 上 |
| `settle_ms` | 150 | |

### `ui_set_property`

設定元素的 QML 屬性（例如 `checked`、`value`、`text`、`currentIndex`）。這會略過輸入處理，能用 `ui_click` 時請優先用 `ui_click`。

| 參數 | 預設值 | 說明 |
|---|---|---|
| `ref` | 必填 | |
| `name` | 必填 | 屬性名稱 |
| `value` | 必填 | 新的值 |

回傳 `ok` 和設定前、設定後的值。沒有 `settle_ms` 參數。

### `ui_invoke`

呼叫元素上沒有參數的 QML 方法或訊號（例如 `clicked`、`toggle`、`open`、`close`）。元素無法在畫面上點到時很有用。

| 參數 | 預設值 | 說明 |
|---|---|---|
| `ref` | 必填 | |
| `method` | 必填 | 方法或訊號名稱 |

沒有 `settle_ms`，也沒有 `force`；Quick Rigging 防護一定會生效。

## 動作、Rig 模式和日誌

### `list_actions`

列出介面中找得到的 Cascadeur 動作 key（`actionId`，例如 `Commands.Rig mode.On`）和標籤。

| 參數 | 預設值 | 說明 |
|---|---|---|
| `query` | — | 只列出包含這段字的 key |
| `include_hidden` | `true` | 包含隱藏的視窗和元素 |

清單來自目前的 QML 介面樹，所以從沒開過的工具，它的動作可能不會出現。

### `call_action`

透過 `ActionManager.call_action` 依 key 觸發動作，效果和按下它的工具列按鈕或選單項目相同。

| 參數 | 預設值 | 說明 |
|---|---|---|
| `key` | 必填 | 例如 `Commands.Rig mode.On` |
| `settle_ms` | 150 | |

### `enter_rig_mode`

用 Cascadeur 自己的 `rig_mode.on` 進入 Rig 模式。使用 Quick Rigging 的 **Add rig elements**／**Generate rig**
前必須先做。和官方 script server 一樣，在場景的 idle 事件上執行。

| 參數 | 預設值 | 說明 |
|---|---|---|
| `color` | `[0, 0.5, 0]` | Rig 顏色，RGB 0..1 |

實際做法依場景而定：

| 場景 | 做法 |
|---|---|
| 剛好 1 個 `AnimationInfo` | 已經在 Rig 模式，不做任何事 |
| 超過 1 個 `AnimationInfo` | 回傳錯誤；請先手動處理再做 Rig |
| 有 `RigInfo` | `rig_mode.on.run_raw` |
| 沒有 `RigInfo`（剛匯入的模型） | `rig_mode.on.run_after_import`：用介面匯入時會自動做、但 Python 的 `import_model` 會略過的步驟。它會建立 `RigInfo`、進入 Rig 模式，並跳出「Rig mode helper」對話框；用 `dialogs`／`ui_click` 按下 *Yes* 就會開啟 Quick Rigging。 |

回傳 `rig_mode`、`changed`、使用的 `path` 和 `AnimationInfo` 數量。

### `get_log`

讀取 Cascadeur 日誌（`%LOCALAPPDATA%\Nekki Limited\Cascadeur\logs\cascadeur_log.log`）的最後幾行，錯誤和 Python traceback 都在這裡。

| 參數 | 預設值 | 說明 |
|---|---|---|
| `lines` | 60 | 從最後往回讀的行數 |
| `contains` | — | 只保留包含這段字的行（不分大小寫） |
