# Cascadeur MCP Plus

[![CI](https://github.com/Karylcode/cascadeur-mcp-plus/actions/workflows/ci.yml/badge.svg)](https://github.com/Karylcode/cascadeur-mcp-plus/actions/workflows/ci.yml)

[English](README.md)

讓 AI agent（Claude Code、Codex、Cursor 等）操作 [Cascadeur](https://cascadeur.com/) 的 MCP 伺服器：
可以執行 Python、檢視場景、截圖，也能像真人一樣操作介面，包括點擊、拖曳、打字、按快速鍵和回答對話框。
概念上就是「Cascadeur 版的 BlenderMCP」。

Cascadeur 2026.2 內建一個陽春的 script server，只有 `run_script` 一個工具。MCP Plus 建立在它之上，
另外加了 17 個工具，其中最重要的是可以完整操作 Cascadeur 的 Qt Quick 介面。

> **非官方專案。** 與 Nekki 沒有任何關聯，也未經 Nekki 背書。Cascadeur 是 Nekki Limited 的商標。
> 本專案不包含任何 Cascadeur 的程式碼，只透過 Cascadeur 的 Python API 和 Qt 與它互動。

## 工具

| 工具 | 功能 |
|---|---|
| `run_python` | 在 Cascadeur 裡執行 Python，可直接使用 `csc`、`app`、`scene`、`view_scene`、`ui`，回傳輸出和結果值 |
| `get_scene_info`、`get_object_info` | 場景摘要（幀數、物件、選取、Rig 狀態、授權）和物件的 behaviour |
| `screenshot` | 擷取整個視窗或單一介面元素，可以先把最小化的視窗還原 |
| `ui_tree` | 列出畫面上的介面元素（文字、提示、actionId、類型、位置），附 ref 給其他工具使用 |
| `ui_click`、`ui_hover` | 依 ref、文字或視窗座標點擊或懸停，送出的是真正的滑鼠事件 |
| `ui_drag` | 按下、按住並分段移動、放開；起點可以是 ref、文字、座標或**場景物件名稱** |
| `ui_key`、`ui_type` | 快速鍵（例如 `ctrl+s`）和文字輸入（支援中文） |
| `ui_set_property`、`ui_invoke` | 設定 QML 屬性、呼叫 QML 方法或訊號 |
| `dialogs` | 目前顯示的對話框和彈出選單，以及其中的按鈕 |
| `list_actions`、`call_action` | Cascadeur 的動作 key（例如 `Commands.Rig mode.On`）和觸發方式 |
| `project_point` | 場景物件在 3D 視埠中的視窗座標 |
| `enter_rig_mode` | 用介面匯入時的同一套流程進入 Rig 模式（使用 Quick Rigging 前必須先做） |
| `get_log` | 讀取 Cascadeur 日誌的最後幾行，錯誤和 Python traceback 都在這裡 |

## 需求

- Windows 10 或 11（截圖和視窗處理使用 Win32 API）
- Cascadeur 2026.2，需要內建的 MCP script server（在 2026.2.3 測試過）
- Python 3.11 以上，給代理程式使用（只用標準函式庫，不需要 `pip install`）
- 一個 MCP 用戶端

## 安裝

1. 取得程式碼：

   ```bash
   git clone https://github.com/Karylcode/cascadeur-mcp-plus.git
   ```

2. 在 MCP 用戶端註冊代理程式。Claude Code：

   ```bash
   claude mcp add --scope user cascadeur-plus -- python -I C:\path\to\cascadeur-mcp-plus\proxy.py
   ```

   其他用戶端（JSON 設定）：

   ```json
   {
     "mcpServers": {
       "cascadeur-plus": {
         "command": "python",
         "args": ["-I", "C:\\path\\to\\cascadeur-mcp-plus\\proxy.py"]
       }
     }
   }
   ```

   如果 `python` 不在 `PATH` 上，改用 `python.exe` 的完整路徑。

## 使用

1. 開啟 Cascadeur，點 **Scripts → MCP → Start script server**（每次開啟都要點）。
2. 請 agent 操作 Cascadeur 即可。第一次呼叫工具時，MCP Plus 會自動在 Cascadeur 裡啟動，大約 10 秒；
   之後每次呼叫都在 1 秒內。

不需要另外註冊 Cascadeur 官方的 `cascadeur` MCP，`run_python` 已經涵蓋 `run_script` 的功能。

## 運作方式

```text
MCP 用戶端 ──stdio──► proxy.py ──HTTP──► Cascadeur 裡的 MCP Plus（127.0.0.1:48766）
                         │                       │ 每個呼叫都在 Cascadeur 的主執行緒執行
                         └─ 沒在跑時 ──► Cascadeur script server（127.0.0.1:8765）/run → start_mcp_plus.py
```

- Cascadeur 內附的 PySide6 和主程式共用同一個 Qt，所以 Python 可以直接存取執行中的 QML 介面樹、
  視窗和 Qt 的輸入系統。
- 點擊、拖曳和按鍵透過 Qt 的視窗系統輸入路徑（QtTest）送出，和真人操作走同一條路。拖曳的每一步
  都是獨立的主執行緒工作，所以 AutoPosing 等即時更新會在每次移動之間正常運作。
- 截圖使用 Win32 的 `PrintWindow`。從 Python 強制 Qt Quick 額外繪製（`QQuickWindow.grabWindow`）
  曾讓 Cascadeur 當機，因為它的 3D 視埠是用自己的 OpenGL 程式繪製的。
- 代理程式一定連得上（Cascadeur 沒開時會提供快取的工具清單），送出任何請求前都會先透過 `/health`
  確認對方的身分。

## 安全

- 兩個伺服器都只監聽 `127.0.0.1`，但就像 Cascadeur 官方的 script server 一樣，會執行收到的任何
  Python。用完請關閉 script server。
- 代理程式不會把請求轉送到被其他程式佔用的連接埠：如果 `48766` 或 `8765` 有回應但不是預期的
  Cascadeur 伺服器，呼叫會失敗並說明原因。
- 場景不在 Rig 模式時（AnimationInfo 不是剛好 1 個），`ui_click` 和 `ui_invoke` 會拒絕點擊 Quick Rigging
  的 **Add rig elements／Generate rig**。在這個狀態下 Cascadeur 會丟出 `Rig mode several animationInfo`
  然後當機。請先呼叫 `enter_rig_mode`，或傳入 `force=true`。

## 提示和已知限制

- **8765 被佔用**：*Start script server* 會出現 `WinError 10013`，代表有其他程式用了這個連接埠。
  請把那個程式移到別的埠（`netstat -ano | findstr :8765` 可以查出是誰）。
- **視窗最小化**：最小化時畫面不會繪製，截圖請加上 `restore=true`。
- **拖曳用程式擺好的姿勢**：Cascadeur 可能跳出「Pose in the current frame is different from the AutoPosing
  prediction…」。`ui_drag` 會在 `dialogs` 裡回報，選 *Yes* 保留姿勢後再拖一次。
- **視埠投影**假設是透視相機、垂直視角 30°（在 2026.2.3 量測）。設定不同時，請在 `project_point`
  或 `ui_drag` 傳入 `fov_deg`。
- 所有呼叫都在 Cascadeur 的主執行緒執行，長時間的操作會讓介面暫停回應，直到完成。
- Cascadeur 改版時介面結構可能改變，盡量用 `actionId` 或按鈕文字定位，少用座標。

## 設定

| 設定 | 預設值 | 位置 |
|---|---|---|
| `CASCADEUR_PLUS_URL` | `http://127.0.0.1:48766` | 代理程式的環境變數；代理自動啟動時會把這個埠傳給 Cascadeur 裡的伺服器 |
| `CASCADEUR_OFFICIAL_URL` | `http://127.0.0.1:8765` | 代理程式的環境變數 |

手動啟動，或用最新的程式碼重新啟動（在 Cascadeur 的 Python 裡執行，例如透過 script server 或 Python Console）：

```python
p = r"C:\path\to\cascadeur-mcp-plus\start_mcp_plus.py"
exec(open(p, encoding="utf-8").read(), {"__file__": p})
```

## 開發

```bash
python -m unittest discover -s tests -v   # 用假伺服器測試代理程式，不需要 Cascadeur
ruff check . && ruff format --check .
```

`cascadeur_mcp_plus/` 需要 Cascadeur 的 Python，只能實機測試。`start_mcp_plus.py` 會重新載入它，
所以改完程式碼後不用重開 Cascadeur，下次啟動就會生效。

## 授權

[MIT](LICENSE)
