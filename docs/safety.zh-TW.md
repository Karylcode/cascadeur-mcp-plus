[English](safety.md) | **繁體中文** · [文件索引](README.zh-TW.md)

# 安全

本頁列出兩個伺服器能做什麼、保護它們的檢查，以及會讓 Cascadeur 當機的呼叫。

## 兩個伺服器都會執行任何 Python

Cascadeur 的 script server（連接埠 8765）和 MCP Plus（連接埠 48766）都只監聽 `127.0.0.1`，但兩者都會執行收到的任何 Python。`run_python` 也會在 Cascadeur 裡執行 Python。

- 用完請停止 script server。
- MCP Plus 啟動後，代理程式只跟它溝通。只有要啟動 MCP Plus 時，代理程式才需要再用到 script server。
- MCP Plus 會一直執行到 Cascadeur 關閉。
- 如果 `POST` 請求的 `Origin` 標頭不是 `127.0.0.1`、`localhost` 或 `::1`，MCP Plus 會拒絕。
- [自動啟動](installation.zh-TW.md#自動啟動)會讓兩個連接埠在沒有驗證的情況下一直開著。不使用 agent 時，請停用它。

## 伺服器身分確認

代理程式絕不會把呼叫轉送給被其他程式佔用的連接埠。送出任何東西之前，它會先向該連接埠的 `GET /health` 詢問是誰在回應：

- MCP Plus 會回應 `"server": "cascadeur-mcp-plus"`。
- Cascadeur 的 script server 靠它的 `idle_subscription` 和 `message_subscription` 欄位辨認。
- 其他任何回應都代表是別的程式。

如果 48766 或 8765 連接埠有回應，但不是預期的 Cascadeur 伺服器，呼叫會失敗並附上說明，也不會有任何東西送到那個連接埠。請釋放該連接埠，或把另一個程式移走，再重試。

這個檢查只看上述欄位，不比對版本。檢查成功後，結果會沿用 60 秒；連線被拒絕時會重新檢查。

## Quick Rigging 防護

Quick Rigging 的 **Add rig elements** 和 **Generate rig** 需要剛好一個 `AnimationInfo`，這只有在 Rig 模式才會有。在其他任何狀態下，Cascadeur 會拋出 `Rig mode several animationInfo`，然後當機。

- `ui_click` 和 `ui_invoke` 會拒絕文字、工具提示或 actionId 符合 *Add rig elements* 或 *Generate rig*（不分大小寫）的元素，除非場景剛好有一個 `AnimationInfo`。`ui_click` 用 `x` 和 `y` 點擊時，檢查的是該點底下的元素。
- 請先呼叫 `enter_rig_mode`。
- `ui_click` 可以傳入 `force=true` 略過防護。`ui_invoke` 沒有 `force` 參數。
- 只有這兩個工具有防護。`call_action`、`ui_key`、`ui_set_property`、`ui_drag` 和 `run_python` 都不會檢查。

## 會讓 Cascadeur 當機的呼叫

- **場景。** 請用 `close_scene` 關閉場景。不要在 `run_python` 裡關閉場景，不要呼叫 `SceneManager.remove_application_scene`，也不要在關閉場景的同一個呼叫裡再去碰那個場景。這些做法在測試時都讓 Cascadeur 當機。
- **Python API。** 請在 `run_python` 裡避開這些。它們在測試時讓 Cascadeur 當機，或拋出錯誤：`csc.layers.CyclesViewer`、`csc.layers.Cycle`、`CyclesEditor`、`Viewer.item()`、`Folder.children_ordered()`，以及用 `modify_update` 旋轉角色。請改用 `list_keys`、`list_layers` 和 `set_pose`。
- **Rig 模式。** 使用 Quick Rigging 之前，請先呼叫 `enter_rig_mode`；請看上面的防護。
- **影片動捕。** `video_mocap` 是實驗性功能：取消正在執行的動捕工作在測試時讓 Cascadeur 當機。呼叫前請先存檔。

## 圖層鎖定

MCP Plus 的寫入工具會遵守動畫圖層的鎖定。Cascadeur 的 Python API 不會檢查鎖定，所以 `run_python` 可以更改被鎖定圖層上的物件。
