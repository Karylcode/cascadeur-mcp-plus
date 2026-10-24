[English](usage.md) | **繁體中文** · [文件索引](README.zh-TW.md)

# 使用方式

本頁說明每次使用的流程、好用的做法、常見工作、輸出檔案的位置、一個完整範例，以及可以拿來開頭的提示詞。

## 每次使用

1. 啟動 Cascadeur，點 **Scripts → MCP → Start script server**。每次啟動後都要點，除非[自動啟動](installation.zh-TW.md#自動啟動)已經替你啟動它。
2. 請 agent 在 Cascadeur 裡工作。第一次呼叫工具時，會自動在 Cascadeur 裡啟動 MCP Plus，最多約需 10 秒。之後的呼叫都遠遠不到一秒。

如果 script server 沒有執行，呼叫工具會失敗，並附上提示你先啟動它的訊息。請看[疑難排解](troubleshooting.zh-TW.md)。

## 操作建議

代理程式會給 agent 這些指示：

- 點擊前先用 `ui_tree` 或 `screenshot` 檢查介面。
- 有 `actionId` 的元素，優先用 `call_action`。
- 可能默默失敗的操作，做完後用 `get_log` 檢查。

請先使用專用工具，例如 `set_pose`、`set_key`、`set_interpolation` 和渲染工具。介面工具（`ui_*`、`dialogs`、`screenshot`）是最後手段。

## 慣例

- **單位和影格。** 位置的單位是公分，旋轉的單位是度（Euler XYZ）。影格是從 0 開始的整數。
- **名稱。** 傳入物件名稱，或 `left_hand`、`right_foot`、`pelvis`、`head` 這類別名。`list_controllers` 會列出它們。有歧義的名稱會回傳 `candidates`；不認得的名稱會回傳 `closest` 建議。
- **結果。** 大多數工具成功時回傳 `{"ok": true, ...}`。失敗時回傳 `{"ok": false, "error": "...", "hint": "..."}`，請依照提示處理。少數工具（例如 `get_scene_info`）直接回傳資料，不含 `ok`。
- **復原。** 每個會改變場景的工具，都會產生一個名為 `MCP: <tool>` 的復原步驟。用 `undo` 或 Ctrl+Z 復原它，`get_undo_history` 會列出這些步驟。部分工具需要多次復原，少數工具無法完整復原；請看[行為與限制](limitations.zh-TW.md)。
- **背景工作。** `render_video`、`render_image_sequence`、`physics_snap`、`fix_foot_sliding`、`fix_collisions`、`prepare_keys_by_fulcrums`、`ai_inbetween`、`generate_motion` 和 `video_mocap` 會立刻回傳 `job_id`。請輪詢 `get_job`（它的 `wait_s` 最多等 60 秒），並用 `cancel_job` 停止工作。失敗或已取消的工作仍然會有 `ok: true`，所以要檢查 `status`。伺服器重新啟動時還在執行的工作，會回報 `lost`。
- **批次。** `batch` 在一個請求裡執行多個呼叫，逾時和錯誤的處理方式和單一呼叫相同。批次裡不能再包含另一個批次。
- **資源。** 四個唯讀的 MCP 資源不需要呼叫工具，就能提供同樣的資料：`cascadeur://scene`（和 `get_scene_info` 一樣）、`cascadeur://timeline`、`cascadeur://controllers`（最多 500 筆）和 `cascadeur://log`（最後 200 行）。
- **攝影機預設視角。** `render_contact_sheet` 和 `render_video` 接受 `camera_preset`。預設的 `auto` 會讓一部固定的攝影機涵蓋整段動作；`current` 沿用視埠攝影機。六個視角 `front`、`back`、`left`、`right`、`top` 和 `three_quarter` 會從該視角涵蓋整段動作。`render_image` 只接受這六個視角。

## 常見工作

**檢視場景。** `get_scene_info` 整理影格、物件、選取、綁定相關的 behaviour 和授權旗標。`get_object_info` 依名稱說明物件。`list_keys` 列出影格範圍內每個控制器的關鍵影格。

**執行 Python。** `run_python` 執行程式碼時，可以直接使用 `csc`、`app`、`scene`、`view_scene` 和 `ui`。它會回傳 stdout，以及單一運算式的值，或名為 `result` 的變數的值。不熟悉的 API 名稱，請先用 `api_search` 和 `api_describe` 確認。

**用數字擺姿勢。** `set_pose` 在某個影格設定指定控制器的位置或旋轉，並為它們設定關鍵影格。使用 `autoposing: false` 和 `key: true` 時，每個指定的控制器都會落在你設定的位置。用 `get_pose` 讀回結果，用 `render_contact_sheet` 檢查動作，再用 `render_video` 寫出 MP4。

**設定關鍵影格和插值。** `set_animation_range` 設定第一個和最後一個影格。`set_key` 在影格上設定關鍵影格，`set_interpolation` 設定區間類型（例如 `bezier_clamped`），`list_keys` 顯示結果。

**建立綁定。** `quick_rig` 用 Quick Rigging 為尚未綁定的骨架建立綁定。場景已經有綁定時，除非 `replace=true`，否則它會拒絕。如果要改用 Quick Rigging 的對話框，請先呼叫 `enter_rig_mode`。對剛匯入的模型，Cascadeur 接著會顯示「Rig mode helper」對話框：呼叫 `dialogs`，再用 `ui_click(ref=...)` 按下它的 *Yes* 按鈕。除非場景在 Rig 模式，否則 **Add rig elements** 和 **Generate rig** 按鈕會被拒絕；請看[安全](safety.zh-TW.md)。

**拖曳控制器（最後手段）。**

1. `project_point` 會顯示場景物件在視窗中的位置，以及它們是否在畫面上。
2. 用 `ui_drag`，把 `object` 設成那個名稱、`dy=-80`，就會把它往上拖 80 px。AutoPosing 會在每一步之間更新。
3. 用 `screenshot` 檢查結果。如果 `ui_drag` 回傳了 `dialogs`，請先回答對話框，再拖一次。

**操作介面（最後手段）。** 先呼叫 `ui_tree` 或 `dialogs` 取得元素參照（`ref`），再交給 `ui_click`、`ui_type`、`ui_set_property` 或 `ui_invoke` 使用。每次呼叫 `ui_tree` 或 `dialogs`，元素參照都會重設。擷取整個視窗時，把圖片像素乘上回傳的 `window_units_per_pixel`，就會得到 `ui_click` 的 `x` 和 `y` 所使用的視窗座標。

## 輸出檔案

| 輸出 | 預設位置 |
|---|---|
| `render_image` | `%TEMP%\cascadeur-mcp-plus\renders\` |
| `render_video` | `%TEMP%\cascadeur-mcp-plus\videos\` |
| `render_image_sequence` | `%TEMP%\cascadeur-mcp-plus\sequences\` |
| `render_contact_sheet` | `%TEMP%\cascadeur-mcp-plus\contact_sheets\` |
| `video_mocap` 的參考影格 | `%TEMP%\cascadeur-mcp-plus\mocap-frames\<timestamp>\`，執行結束後會刪除，除非設了 `keep_reference=true` |
| `examples/wave.py` | `%TEMP%\cascadeur-mcp-plus-tests\T56\` |

只有設了 `overwrite=true`，才會取代既有的檔案。渲染或匯出失敗時，舊檔案會留在原處。`render_image` 只寫出 PNG 檔案。

## 完整範例

只要給 agent 一句話，例如 *在 Cascy 上做一段 2 秒的揮手動畫，並匯出 mp4*。它所做的工具呼叫及其參數，都列在 [examples/wave.md](../examples/wave.md)。[examples/wave.py](../examples/wave.py) 只用標準函式庫，透過 HTTP 對執行中的 MCP Plus 執行同樣的步驟。請在 Cascadeur 已開啟、MCP Plus 正在執行的情況下，從 checkout 資料夾執行它：

```bat
python examples\wave.py
```

它會把 `wave.mp4` 和 `wave_contact.png` 寫到 `%TEMP%\cascadeur-mcp-plus-tests\T56\`。用 `--out` 可以選擇其他資料夾。

## 提示詞範例

- 「列出目前 Cascadeur 場景裡的物件、影格範圍和選取的控制器。」
- 「在角色上做一段 2 秒的揮手動畫，用格狀圖檢查，再渲染輸出 MP4。」
- 「把右手控制器投影成視窗座標，往上拖 80 px 讓 AutoPosing 調整手臂，然後給我看截圖。」
- 「在 Cascadeur 裡執行一段 Python，印出目前的影格。如果失敗，讀取日誌的最後 50 行。」

## 手動啟動 MCP Plus

代理程式會替你啟動 MCP Plus。如果要手動啟動，或用最新的程式碼重新啟動，請在 Cascadeur 的 Python 裡執行這段（透過 script server 或 Python Console）：

```python
p = r"C:\path\to\cascadeur-mcp-plus\start_mcp_plus.py"
exec(open(p, encoding="utf-8").read(), {"__file__": p})
```

要監聽其他連接埠，請看[開發](development.zh-TW.md#手動啟動和熱重載)。
