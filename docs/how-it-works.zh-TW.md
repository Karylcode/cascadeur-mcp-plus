# 運作方式

[English](how-it-works.md) | **繁體中文** · [文件索引](README.zh-TW.md)

```text
MCP 用戶端 ──stdio──► proxy.py ──HTTP──► Cascadeur 裡的 MCP Plus（127.0.0.1:48766）
                         │                       │ 呼叫在 Cascadeur 的主執行緒執行
                         └─ 沒在跑時 ──► Cascadeur script server（127.0.0.1:8765）/run → start_mcp_plus.py
```

## 代理程式（`proxy.py`）

MCP 用戶端透過 stdio 啟動代理程式。它只用 Python 標準函式庫，並且自己回應 `initialize` 和 `ping`，
所以就算 Cascadeur 沒開，也一定連得上。

- **`tools/list`**：MCP Plus 有在跑時，代理程式會轉送請求，並把結果存到 `tools_manifest.json`。
  沒在跑時，就提供這份快取清單。
- **`tools/call`**：
  1. 透過 `/health` 確認 MCP Plus 的連接埠上是 MCP Plus 在回應（請看[安全](safety.zh-TW.md)）。
  2. 如果沒有任何回應，用同樣方式確認 8765 上的 script server，再送一段短程式給它（`POST /run`），
     用代理程式的連接埠執行 `start_mcp_plus.py`。接著最多等 10 秒讓 MCP Plus 啟動。
  3. 把呼叫轉送給 MCP Plus（`POST /mcp`）。如果連線被拒絕（例如 Cascadeur 重開過），
     就重新確認或啟動 MCP Plus，再試一次。
- 每個請求都在自己的執行緒處理，所以慢的工具呼叫不會擋住 ping 或其他呼叫。
- 代理程式等一個工具呼叫最多 120 秒；`run_python` 則是 `timeout` 再加 15 秒。

## Cascadeur 裡的伺服器（`cascadeur_mcp_plus/`）

- `start_mcp_plus.py` 會從自己所在的位置找到套件，依相依順序重新載入，然後在 Cascadeur 的主執行緒呼叫 `start()`。
  如果伺服器已經在跑，會先停掉。
- 伺服器是 `127.0.0.1` 上的 HTTP 伺服器（預設連接埠 48766），提供 `POST /mcp` 和 `GET /health`。
  啟動時會寫入 `tools_manifest.json`，讓代理程式的離線工具清單保持最新。
- Cascadeur 內附的 PySide6 和主程式共用同一個 Qt，所以 Python 可以直接存取執行中的 QML 介面樹、
  視窗和 Qt 的輸入系統。

## 主執行緒排程

請求從背景的 HTTP 執行緒進來。會碰到 Cascadeur 的工具先放進佇列，再由主執行緒上的 `QTimer` 執行。
排程器可以重入：某個工作打開了模態對話框，也不會擋住下一個工作，而下一個工作可能正是要關掉那個對話框的。
`settle_ms` 這類等待是在 HTTP 執行緒上進行，所以 Cascadeur 的事件迴圈會正常運作。

例外：

- **`get_log`** 只讀日誌檔，在 HTTP 執行緒上執行，不使用主執行緒。
- **`enter_rig_mode`** 和官方 script server 一樣，在場景的 idle 事件上執行。
- **`screenshot`** 和 **`ui_drag`** 會拆成好幾個主執行緒工作，中間的等待在 HTTP 執行緒上進行。
  `ui_drag` 的按下、每一步移動和放開都是獨立的工作，所以 AutoPosing 等即時更新會在每次移動之間正常運作。

主執行緒工作超過 60 秒會逾時（`run_python` 依它的 `timeout`；`screenshot` 和 `ui_drag` 的每一步是 30 秒）。因為工作在主執行緒執行，
長時間的操作會讓 Cascadeur 的介面暫停回應，直到完成。

## 輸入

點擊、懸停、按鍵和拖曳透過 Qt 的視窗系統輸入路徑（QtTest）送出，和真人操作走同一條路。
`ui_drag` 也可以改成直接送出 `QMouseEvent`（`method="event"`）。`ui_type` 會把帶有文字的按鍵事件送給視窗，
再由 Qt Quick 交給目前有焦點的元素；任何 Unicode 文字都可以輸入。

## 截圖

截圖使用 Win32 的 `PrintWindow`。從 Python 強制 Qt Quick 額外繪製（`QQuickWindow.grabWindow`）
曾讓 Cascadeur 當機，因為它的 3D 視埠是用自己的 OpenGL 程式繪製的。最小化的視窗不會繪製，
所以 `screenshot` 可以先把它還原（`restore=true`）。

## 視埠投影

Python API 沒有提供視埠相機的視角。`project_point` 和 `ui_drag`（使用 `object` 時）會用主視埠相機的位置和目標，
並假設是透視相機、垂直視角 30°。這個值是在 Cascadeur 2026.2.3 上，把投影出來的控制點疊在視窗截圖上量測的
（誤差約 1 px 以內）。設定不同時，請傳入 `fov_deg`。
