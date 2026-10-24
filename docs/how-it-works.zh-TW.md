[English](how-it-works.md) | **繁體中文** · [文件索引](README.zh-TW.md)

# 運作方式

本頁說明 MCP Plus 的各個部分，以及一次呼叫如何經過它們。

```text
MCP 用戶端 ──stdio──► proxy.py ──HTTP──► Cascadeur 內的 MCP Plus (127.0.0.1:48766)
                         │                       │ 呼叫在 Cascadeur 的 GUI 執行緒上執行
                         └─ 若它沒在執行 ──► Cascadeur script server (127.0.0.1:8765) /run → start_mcp_plus.py
```

## 代理程式

MCP 用戶端透過 stdio 啟動代理程式。checkout 資料夾裡的 `proxy.py` 是啟動器，負責啟動 `cascadeur_mcp_plus_cli/proxy.py` 裡的實作。代理程式只用 Python 標準函式庫，並且自己回應 `initialize` 和 `ping`，所以即使 Cascadeur 關閉，它也連得上。

- **`tools/list`**：MCP Plus 有在執行時，代理程式會轉送請求，並把結果存到 `tools_manifest.json`。否則就提供那份快取的清單。
- **`tools/call`**：
  1. 它檢查 MCP Plus 連接埠上的 `/health`，確認回應的伺服器是 MCP Plus（請看[安全](safety.zh-TW.md)）。
  2. 如果沒有任何回應，它用同樣的方式檢查 8765 連接埠上的 script server，再送給它一段短腳本（`POST /run`），由該腳本執行 `start_mcp_plus.py`。它最多等 10 秒，讓 MCP Plus 啟動。
  3. 它把呼叫轉送給 MCP Plus（`POST /mcp`）。如果連線被拒絕，例如因為 Cascadeur 重新啟動，它會再次檢查或啟動 MCP Plus，然後重試一次。
- 每個請求都在自己的執行緒中執行，所以慢的呼叫絕不會擋住 ping 或其他呼叫。
- **逾時。** 帶有數值 `timeout` 參數的工具，會等待那麼久，上限 600 秒，另外再加上一段等待回應的寬限時間。其他工具則是 210 秒。這足以涵蓋 `render_image` 內部 180 秒的等待。

## Cascadeur 內的伺服器（`cascadeur_mcp_plus/`）

- `start_mcp_plus.py` 會在自己旁邊找到套件，依相依順序重新載入，然後在 Cascadeur 的 GUI 執行緒呼叫 `start()`。如果已經有伺服器在執行，會先停掉它。
- 這個伺服器是 `127.0.0.1` 上的 HTTP 伺服器（預設連接埠 48766），提供 `POST /mcp` 和 `GET /health`。啟動時它會寫入 `tools_manifest.json`，讓代理程式的離線工具清單保持最新。
- Cascadeur 內附的 PySide6 與應用程式共用同一個 Qt runtime，所以 Python 可以存取執行中的 QML 項目樹、它的視窗和 Qt 的輸入系統。
- **啟動時的相容性檢查。** 每個工具都在 `requires` 裡列出它需要 Cascadeur 提供的東西：API 路徑、動作或腳本模組。MCP Plus 啟動時，會拿它們和執行中的 Cascadeur 比對。有缺少項目的工具仍留在 `tools/list` 裡，它的描述會以「Unavailable in this Cascadeur version:」開頭，呼叫時會回傳缺少的項目，而不是做到一半才失敗。`get_status` 會再執行一次檢查。

## GUI 執行緒排程

請求從背景的 HTTP 執行緒進來。存取 Cascadeur 的工具會先放進佇列，再由 GUI 執行緒上的 `QTimer` 每 25 ms 執行它們。排程器可以重入：某個工作開啟了模態對話框，也不會擋住下一個工作，而下一個工作可能正是要關閉它的那個。`settle_ms` 這類等待是在 HTTP 執行緒上進行，所以 Cascadeur 的事件迴圈會照常運作。

例外：

- **`get_log`** 只讀日誌檔，在 HTTP 執行緒上執行。
- **場景閒置工具**，例如 `enter_rig_mode`、`import_model`、`import_animation`、`quick_rig` 和 `video_mocap`，和官方 script server 一樣，在 Cascadeur 的場景閒置（idle）事件上執行。Rig 模式和場景圖的編輯需要場景閒置事件。
- **`screenshot`** 和 **`ui_drag`** 會拆成好幾個 GUI 執行緒工作，工作之間的等待在 HTTP 執行緒上進行。每個工作最多等 30 秒。`ui_drag` 把按下、每一步移動和放開當作獨立的工作來執行，所以 AutoPosing 等即時更新會在每次移動之間運作。
- **背景工具**在 GUI 執行緒上開始工作，並立刻回傳 `job_id`。工作本身用 `get_job` 輪詢。

一般的 GUI 執行緒工作會在 60 秒後逾時（`run_python` 則是在它的 `timeout` 之後）。因為工作在 GUI 執行緒上執行，長時間的操作會讓 Cascadeur 的介面凍結，直到完成。

## 輸入

點擊、懸停、按鍵和拖曳是透過 Qt 的視窗系統輸入路徑（QtTest）合成的，所以和真人操作走同一條路徑。`ui_drag` 也可以改成直接送出 `QMouseEvent`（`method="event"`）。`ui_type` 會把帶有文字的按鍵事件送給視窗，再由 Qt Quick 交給有焦點的項目，所以任何 Unicode 文字都能輸入。`ui_set_property` 和 `ui_invoke` 會略過輸入處理常式。

## 截圖

`screenshot` 使用 Win32 的 `PrintWindow` API。從 Python 強制 Qt Quick 額外繪製一次（`QQuickWindow.grabWindow`）曾讓 Cascadeur 當機，因為它的視埠是用自己的 OpenGL 程式碼繪製的，所以 MCP Plus 不這麼做。最小化的視窗不會繪製任何東西，所以 `screenshot` 可以先用 `restore=true` 將它還原。

## 渲染和輸出

- `render_image` 使用 Cascadeur 自己的渲染器（`RenderToFile`）。視窗必須是可見的。
- `render_video` 和 `render_image_sequence` 會操作 Cascadeur 官方的匯出影片視窗。Cascadeur 不可以最小化，也不可以開著其他匯出視窗。
- `render_contact_sheet`、`render_video` 和 `render_image_sequence` 在 Cascadeur 的 View 模式下渲染，並關閉殘影、軌跡和剪影。因此控制點、關節、方塊和軌跡都不會出現在畫面中。傳入 `keep_overlays=true`，就會照視埠目前的樣子渲染。
- `render_contact_sheet` 算出選定的影格，再把它們拼成一張 PNG 格狀圖，標上影格編號。

## 視埠投影

Python API 沒有提供視埠攝影機的視角。因此 `project_point` 和 `ui_drag`（使用 `object` 時）會使用主視埠攝影機的位置和目標，並假設是垂直視角 30° 的透視攝影機。這個值是在 Cascadeur 2026.2.3 上，把投影出來的控制點疊在視窗擷取圖上量測的，誤差約 1 px 以內。設定不同時，請傳入 `fov_deg`。其他類型的攝影機會被拒絕。
