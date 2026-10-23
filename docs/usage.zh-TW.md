[English](usage.md) | **繁體中文** · [文件索引](README.zh-TW.md)

# 使用方式

## 每次使用

1. 開啟 Cascadeur，點 **Scripts → MCP → Start script server**。每次開啟都要點。
2. 請 agent 操作 Cascadeur 即可。第一次呼叫工具時，MCP Plus 會自動在 Cascadeur 裡啟動，大約 10 秒。
   之後就沒有啟動等待時間。

如果 script server 沒有開，呼叫工具會失敗，並提示你先開啟它。

## 操作建議

代理程式會給 agent 這些指示：

- 點擊前先用 `ui_tree` 或 `screenshot` 檢查介面。
- 有 `actionId` 的元素，優先用 `call_action`。
- 可能默默失敗的操作，做完後用 `get_log` 檢查。

## 常見工作

**檢視場景。** `get_scene_info` 整理幀數、物件、選取、Rig 相關 behaviour 和授權狀態。
`get_object_info` 依名稱說明物件。

**執行 Python。** `run_python` 執行程式碼時，可以直接使用 `csc`、`app`、`scene`、`view_scene` 和 `ui`。
它會回傳輸出，以及單一運算式的值，或名為 `result` 的變數的值。

**操作介面。** 先用 `ui_tree` 或 `dialogs` 取得 ref，再交給 `ui_click`、`ui_type`、`ui_set_property`
或 `ui_invoke` 使用。每次呼叫 `ui_tree` 或 `dialogs`，ref 都會重設。

**點擊截圖裡看到的位置。** 整個視窗的截圖，把圖片像素乘上回傳的 `window_units_per_pixel`，
就是 `ui_click` 的 `x`/`y` 使用的視窗座標。

**進入 Rig 模式並使用 Quick Rigging。**

1. 呼叫 `enter_rig_mode`（使用 Quick Rigging 前必須先做）。如果是剛匯入的模型，
   Cascadeur 接著會跳出「Rig mode helper」對話框，詢問是否開啟 Quick Rigging。
2. 呼叫 `dialogs`，再用 `ui_click(ref=...)` 按下 *Yes*。
3. 使用 Quick Rigging 的 **Add rig elements**／**Generate rig**。不在 Rig 模式時，這兩個按鈕會被拒絕點擊，
   請看[安全](safety.zh-TW.md)。

**用 AutoPosing 調整角色姿勢。**

1. `project_point` 會告訴你場景物件（例如控制點）在視窗裡的位置，以及是否在畫面上。
2. 用 `ui_drag`，把 `object` 設成那個名稱、`dy=-80`，就會往上拖 80 px。AutoPosing 會在每一步之間更新。
3. 用 `screenshot` 檢查結果。如果 `ui_drag` 回傳了 `dialogs`，先回答對話框，再拖一次。

## 提示詞範例

- 「列出目前 Cascadeur 場景裡的物件、幀數範圍和選取的控制器。」
- 「把我剛匯入的模型切換到 Rig 模式，檢查對話框，然後開啟 Quick Rigging。」
- 「把右手控制器投影到視窗座標，往上拖 80 px 讓 AutoPosing 調整手臂，然後截圖給我看。」
- 「在 Cascadeur 裡執行一段 Python，印出目前的幀數。如果失敗，讀取最後 50 行 log。」

## 手動啟動 MCP Plus

代理程式會自動啟動 MCP Plus。如果要手動啟動，或用最新的程式碼重新啟動，請在 Cascadeur 的 Python 裡執行
（例如透過 script server 或 Python Console）：

```python
p = r"C:\path\to\cascadeur-mcp-plus\start_mcp_plus.py"
exec(open(p, encoding="utf-8").read(), {"__file__": p})
```

要改用其他連接埠，請看[開發](development.zh-TW.md)。
