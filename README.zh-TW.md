[English](README.md) | **繁體中文**

# Cascadeur MCP Plus

[![CI](https://github.com/Karylcode/cascadeur-mcp-plus/actions/workflows/ci.yml/badge.svg)](https://github.com/Karylcode/cascadeur-mcp-plus/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![Cascadeur 2026.2](https://img.shields.io/badge/Cascadeur-2026.2-orange.svg)](https://cascadeur.com/)

讓 AI agent（Claude Code、Codex、Cursor 和其他 stdio MCP 用戶端）操作 [Cascadeur](https://cascadeur.com/)
的 MCP 伺服器：可以執行 Python、檢視場景、截圖，也能像真人一樣操作介面，包括點擊、拖曳、打字、按快速鍵和回答對話框。
概念上就是「Cascadeur 版的 BlenderMCP」。Cascadeur 2026.2 內建一個陽春的 script server，只有 `run_script`
一個工具。MCP Plus 建立在它之上，另外加了 17 個工具，其中最重要的是可以完整操作 Cascadeur 的 Qt Quick 介面。
它分成兩個部分：只用 Python 標準函式庫的 stdio 代理程式，以及在 Cascadeur 裡執行、使用 Cascadeur 內附
Python 和 PySide6 的伺服器。

> [!IMPORTANT]
> - **非官方專案。** 與 Nekki 沒有任何關聯，也未經 Nekki 背書。Cascadeur 是 Nekki Limited 的商標。
> - 本專案不包含任何 Cascadeur 的程式碼，只透過 Cascadeur 的 Python API 和 Qt 與它互動。

## 可以做什麼

- **Python**：在 Cascadeur 裡執行程式碼，可以直接使用 `csc`、`app`、`scene`、`view_scene` 和 `ui`（`run_python`，涵蓋官方的 `run_script`）。
- **場景資訊**：幀數、物件、選取、Rig 相關 behaviour 和授權狀態（`get_scene_info`、`get_object_info`）。
- **介面檢視**：視窗或單一元素的截圖、執行中的 Qt Quick 介面樹，以及開著的對話框和它們的按鈕（`screenshot`、`ui_tree`、`dialogs`）。
- **介面輸入**：點擊、移到元素上、拖曳、按鍵和快速鍵、打字、設定 QML 屬性、呼叫 QML 方法或訊號（`ui_click`、`ui_hover`、`ui_drag`、`ui_key`、`ui_type`、`ui_set_property`、`ui_invoke`）。
- **動作和日誌**：依 `actionId` 觸發 Cascadeur 動作（`list_actions`、`call_action`），讀取日誌裡的錯誤和 Python traceback（`get_log`）。
- **Rig 模式**：`enter_rig_mode`，加上一道防護：不在 Rig 模式時，拒絕點擊 Quick Rigging 的 *Add rig elements*／*Generate rig*，因為這時 Cascadeur 會當機。
- **AutoPosing**：`project_point` 找出場景物件在視窗裡的位置，`ui_drag` 拖曳它們，AutoPosing 會在每一步之間更新。

## 需求

- Windows 10 或 11
- Cascadeur 2026.2，需要內建的 MCP script server（在 2026.2.3 測試過）
- Python 3.11 以上，給代理程式使用（只用標準函式庫，不需要 `pip install`）
- 可以執行 stdio 伺服器的 MCP 用戶端

詳細：[安裝](docs/installation.zh-TW.md)。

## 快速開始

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

3. 開啟 Cascadeur，點 **Scripts → MCP → Start script server**（每次開啟都要點）。
4. 請 agent 操作 Cascadeur 即可。第一次呼叫工具時，MCP Plus 會自動在 Cascadeur 裡啟動，大約 10 秒。
   之後就沒有啟動等待時間。

接著看：[使用方式](docs/usage.zh-TW.md)。

## 疑難排解

- **點 *Start script server* 出現 `WinError 10013`**：有其他程式佔用 8765 連接埠；`netstat -ano | findstr :8765` 可以查出是誰。
- **視窗最小化**：畫面不會繪製；截圖時請在 `screenshot` 加上 `restore=true`。
- **拖曳用程式擺好的姿勢時跳出 AutoPosing 對話框**：`ui_drag` 會在 `dialogs` 裡回報；選 *Yes* 保留姿勢後再拖一次。

更多：[疑難排解](docs/troubleshooting.zh-TW.md)。

## 文件

- [文件索引](docs/README.zh-TW.md)：所有頁面，以及各頁的內容
- [安裝](docs/installation.zh-TW.md)：需求、用戶端設定、環境變數
- [使用方式](docs/usage.zh-TW.md)：操作流程、常見工作、提示詞範例
- [工具](docs/tools.zh-TW.md)：18 個工具的參數和預設值
- [運作方式](docs/how-it-works.zh-TW.md)：代理程式、Cascadeur 裡的伺服器、主執行緒排程
- [安全](docs/safety.zh-TW.md)：兩個伺服器都會執行收到的任何 Python，請務必閱讀
- [疑難排解](docs/troubleshooting.zh-TW.md)：已知限制和錯誤訊息
- [開發](docs/development.zh-TW.md)：測試、程式碼檢查、熱重載、原始碼結構
- [更新紀錄](CHANGELOG.md)：各版本變更（目前版本 0.4.0）

## 授權

MIT，詳見 [LICENSE](LICENSE)。
