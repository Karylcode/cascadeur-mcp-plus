[English](safety.md) | **繁體中文** · [文件索引](README.zh-TW.md)

# 安全

## 兩個伺服器都會執行收到的任何 Python

Cascadeur 的 script server（`8765`）和 MCP Plus（`48766`）都只監聽 `127.0.0.1`，但兩者都會執行收到的任何 Python。
用完請關閉 script server。

- MCP Plus 啟動後，代理程式只跟它溝通。只有要啟動 MCP Plus 時，代理程式才需要 script server。
- MCP Plus 會一直執行到 Cascadeur 關閉。
- `POST` 請求的 `Origin` 標頭如果不是 `127.0.0.1`、`localhost` 或 `::1`，MCP Plus 會拒絕。

## 伺服器身分確認

代理程式不會把呼叫轉送到被其他程式佔用的連接埠。送出任何東西前，它會先用該連接埠的 `GET /health` 確認是誰在回應：

- MCP Plus 會回應 `"server": "cascadeur-mcp-plus"`。
- Cascadeur 的 script server 用它的 `idle_subscription` 和 `message_subscription` 欄位辨認。
- 其他回應都當成別的程式。

如果 `48766` 或 `8765` 有回應，但不是預期的 Cascadeur 伺服器，呼叫會失敗並說明原因，不會送任何東西過去。
請釋放那個連接埠，或把那個程式移到別的埠，再試一次。

這個確認只看上面這些欄位，不比對版本。確認成功後，結果會沿用 60 秒；連線被拒絕時會重新確認。

## Quick Rigging 防護

Quick Rigging 的 **Add rig elements**／**Generate rig** 需要剛好 1 個 `AnimationInfo`，這只有在 Rig 模式才會有。
其他狀態下，Cascadeur 會丟出 `Rig mode several animationInfo`，然後當機。

- 場景的 `AnimationInfo` 不是剛好 1 個時，如果元素的文字、提示或 actionId 符合 *Add rig elements* 或
  *Generate rig*（不分大小寫），`ui_click` 和 `ui_invoke` 會拒絕。`ui_click` 用 `x`/`y` 點擊時，會檢查那個點底下的元素。
- 請先呼叫 `enter_rig_mode`。
- `ui_click` 可以傳入 `force=true` 略過防護。`ui_invoke` 沒有 `force`。
- 只有這兩個工具有防護。`call_action`、`ui_key`、`ui_set_property`、`ui_drag` 和 `run_python` 都不會檢查。
