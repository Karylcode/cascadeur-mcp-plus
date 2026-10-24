[English](README.md) | **繁體中文**

# Cascadeur MCP Plus

[![CI](https://github.com/Karylcode/cascadeur-mcp-plus/actions/workflows/ci.yml/badge.svg)](https://github.com/Karylcode/cascadeur-mcp-plus/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![Cascadeur 2026.2](https://img.shields.io/badge/Cascadeur-2026.2-orange.svg)](https://cascadeur.com/)

讓 AI agent（Claude Code、Codex、Cursor 和其他 stdio MCP 用戶端）用 [Cascadeur](https://cascadeur.com/)
自己的功能做動畫的 MCP 伺服器：開啟和儲存場景、用數字擺姿勢並讓 AutoPosing 帶動全身、在時間軸上設關鍵影格和
內插、執行 AutoPhysics 和清理，再用 Cascadeur 自己的渲染器輸出影片和格狀圖。沒有對應的專用工具時，也能像真人
一樣操作介面，包括點擊、拖曳、打字、按快速鍵和回答對話框。概念上就是「Cascadeur 版的 BlenderMCP」。
Cascadeur 2026.2 內建一個陽春的 script server，只有 `run_script` 一個工具。MCP Plus 建立在它之上，共有 105 個
工具：`run_python` 涵蓋 `run_script`，其餘 104 個都是新的。它分成兩個部分：只用 Python 標準函式庫的 stdio
代理程式，以及在 Cascadeur 裡執行、使用 Cascadeur 內附 Python 和 PySide6 的伺服器。

> [!IMPORTANT]
> - **非官方專案。** 與 Nekki 沒有任何關聯，也未經 Nekki 背書。Cascadeur 是 Nekki Limited 的商標。
> - 本專案不包含任何 Cascadeur 的程式碼，只透過 Cascadeur 的 Python API 和 Qt 與它互動。

## 可以做什麼

- **場景和檔案**：新增、開啟、儲存、切換和關閉場景；匯入模型和 FBX 動畫；匯出 FBX 或 GLB（`open_scene`、`save_scene`、`import_model`、`import_animation`、`export_scene`）。
- **角色和 Rig**：列出角色和控制器、選取物件、用 Quick Rigging 幫只有骨架的模型建 Rig、在不同 Rig 之間轉移動畫（`list_characters`、`select_objects`、`quick_rig`、`retarget_animation`）。
- **姿勢和 AutoPosing**：以公分和角度讀寫控制點，讓 AutoPosing 擺好身體其他部分；鏡像、複製和貼上姿勢（`get_pose`、`set_pose`、`mirror_pose`、`autoposing_enable`）。
- **時間軸和關鍵影格**：動畫範圍、關鍵影格、循環、內插、IK/FK、切線和緩動（`set_animation_range`、`set_key`、`create_cycle`、`set_interpolation`、`set_ik_fk`）。
- **動畫圖層**：建立、重新命名、隱藏、鎖定圖層，以及把物件移到其他圖層（`create_layer`、`set_layer_locked`、`move_to_layer`）。
- **物理和 AI**：AutoPhysics、修正腳滑和碰撞穿插、AI 補間、動作生成和實驗性的影片動捕（`physics_enable`、`fix_foot_sliding`、`ai_inbetween`、`generate_motion`、`video_mocap`）。
- **檢查和輸出**：用 Cascadeur 自己的渲染器和 Export video 視窗輸出圖片、格狀圖、圖片序列和 MP4 影片（`render_contact_sheet`、`render_video`）。
- **攝影機和顯示**：攝影機預設視角、取景、跟隨、檢視模式、殘影和軌跡（`camera_preset`、`frame_objects`、`track_object`、`set_view_mode`）。
- **介面操作（最後手段）**：截圖、執行中的 Qt Quick 介面樹、對話框，以及像真人一樣點擊、拖曳、打字和按鍵（`screenshot`、`ui_tree`、`ui_click`、`ui_drag`、`dialogs`）。
- **Python、背景工作和復原**：執行 Python，可以直接使用 `csc`、`app` 和 `scene`；用一個 `batch` 執行多個呼叫；追蹤背景工作；復原和重做；觸發動作和讀取日誌（`run_python`、`get_job`、`undo`、`call_action`、`get_log`）。
- **版本檢查**：`get_status` 列出這個 Cascadeur 版本不能用的工具；呼叫它們會直接回傳明確的錯誤，不會做到一半才失敗。

## 需求

- Windows 10 或 11
- Cascadeur 2026.2，需要內建的 MCP script server（在 2026.2.3 測試過）
- Python 3.11 以上，給代理程式和 CLI 使用（只用標準函式庫，不需要 `pip install`）
- 可以執行 stdio 伺服器的 MCP 用戶端

詳細：[安裝](docs/installation.zh-TW.md)。

## 快速開始

1. 取得程式碼：

   ```bash
   git clone https://github.com/Karylcode/cascadeur-mcp-plus.git
   ```

2. 在 MCP 用戶端註冊代理程式。在 checkout 資料夾裡執行一個指令，就會註冊到偵測到的所有用戶端（Claude Code、
   Codex CLI、Cursor、VS Code），修改前會先備份每個設定檔；加上 `--dry-run` 只顯示變更、不寫入：

   ```bash
   cd C:\path\to\cascadeur-mcp-plus
   python -m cascadeur_mcp_plus_cli setup
   ```

   手動註冊，Claude Code：

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

3. 開啟 Cascadeur，點 **Scripts → MCP → Start script server**（每次開啟都要點）。不想每次點，可以在 checkout
   資料夾裡執行一次 `python -m cascadeur_mcp_plus_cli autostart enable`（會跳出一次 UAC），之後 Cascadeur 開啟時會
   自己啟動兩個伺服器。
4. 請 agent 操作 Cascadeur 即可。第一次呼叫工具時，MCP Plus 會自動在 Cascadeur 裡啟動，大約 10 秒。
   之後就沒有啟動等待時間。

接著看：[使用方式](docs/usage.zh-TW.md)。

## 疑難排解

- **連不上**：在 checkout 資料夾裡執行 `python -m cascadeur_mcp_plus_cli doctor`。它會檢查 Cascadeur 的安裝、8765 和 48766 連接埠、兩個伺服器和已註冊的用戶端。
- **點 *Start script server* 出現 `WinError 10013`**：有其他程式佔用 8765 連接埠；`netstat -ano | findstr :8765` 可以查出是誰。
- **渲染被拒絕或畫面是空的**：Cascadeur 視窗最小化了。先還原視窗，例如在 `screenshot` 加上 `restore=true`。
- **「Unavailable in this Cascadeur version」**：呼叫 `get_status`，它會列出每個工具缺少的 API 或動作。

更多：[疑難排解](docs/troubleshooting.zh-TW.md)。

## 文件

- [文件索引](docs/README.zh-TW.md)：所有頁面，以及各頁的內容
- [安裝](docs/installation.zh-TW.md)：需求、註冊、用戶端設定、自動啟動、環境變數
- [使用方式](docs/usage.zh-TW.md)：操作流程、常見工作、輸出檔案、完整範例、提示詞範例
- [工具](docs/tools.zh-TW.md)：105 個工具的參數和預設值
- [運作方式](docs/how-it-works.zh-TW.md)：代理程式、Cascadeur 裡的伺服器、主執行緒排程、背景工作、渲染
- [安全](docs/safety.zh-TW.md)：兩個伺服器都會執行收到的任何 Python，請務必閱讀
- [行為與限制](docs/limitations.zh-TW.md)：工具做不到的事，以及要預期的行為
- [疑難排解](docs/troubleshooting.zh-TW.md)：常見問題和錯誤訊息
- [開發](docs/development.zh-TW.md)：測試、程式碼檢查、實機測試、熱重載、原始碼結構
- [更新紀錄](CHANGELOG.md)：各版本變更（目前版本 0.5.0）

## 授權

MIT，詳見 [LICENSE](LICENSE)。
