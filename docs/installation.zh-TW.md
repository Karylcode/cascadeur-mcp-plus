[English](installation.md) | **繁體中文** · [文件索引](README.zh-TW.md)

# 安裝

本頁說明 MCP Plus 的需求、如何在 MCP 用戶端註冊它、script server、自動啟動和設定。

## 需求

- **Windows 10 或 11。** 截圖和視窗處理使用 Win32 API。只在 Windows 上測試過。
- **Cascadeur 2026.2**，搭配它內建的 MCP script server（在 2026.2.3 測試過）。
- **Python 3.11 以上**，給代理程式和命令列工具使用。它們只用標準函式庫，所以不需要 `pip install` 任何東西。在 Cascadeur 裡執行的部分，使用 Cascadeur 內附的 Python 和 PySide6。
- **MCP 用戶端**，要能執行 stdio 伺服器，例如 Claude Code、Codex CLI、Cursor 或 VS Code。
- [uv](https://docs.astral.sh/uv/) 是選用的。只有使用下面指令的 `uvx` 形式時才需要它。

## 1. 取得程式碼

```bat
git clone https://github.com/Karylcode/cascadeur-mcp-plus.git
cd cascadeur-mcp-plus
```

這個套件還沒有發布到 PyPI，所以請在 checkout 資料夾裡執行指令。`proxy.py`、`start_mcp_plus.py`、`cascadeur_mcp_plus/` 和 `cascadeur_mcp_plus_cli/` 要放在同一個資料夾裡。除此之外沒有寫死任何路徑。

## 2. 在 MCP 用戶端註冊 MCP Plus

在 checkout 資料夾裡執行：

```bat
python -m cascadeur_mcp_plus_cli setup
```

`setup` 會把伺服器以 `cascadeur-plus` 為名，註冊到它偵測到的每個 MCP 用戶端：Claude Code、Codex CLI、Cursor 和 VS Code。它會先備份每個設定檔，再進行修改。註冊的指令是用 `-I` 執行 `proxy.py` 的 Python，所以不需要 `uv`。

| 選項 | 說明 |
|---|---|
| `--dry-run` | 顯示確切的變更，不寫入任何東西。 |
| `--client NAME` | 只變更一個用戶端：`claude-code`、`codex`、`cursor` 或 `vscode`。預設是偵測到的每個用戶端。 |
| `--command PATH` | 讓用戶端改為執行這個執行檔，而不是偵測到的那一個。 |
| `--yes` | 不詢問確認，直接套用變更（供腳本使用）。 |

要在任何資料夾執行 `setup`，請用 uv 搭配 checkout 的路徑：

```bat
uvx --from C:\path\to\cascadeur-mcp-plus cascadeur-mcp-plus setup
```

本頁的每個指令都有兩種寫法：在 checkout 資料夾裡用 `python -m cascadeur_mcp_plus_cli <command>`，或在任何地方用 `uvx --from C:\path\to\cascadeur-mcp-plus cascadeur-mcp-plus <command>`。

| 指令 | 用途 |
|---|---|
| `setup` | 註冊到偵測到的 MCP 用戶端。 |
| `uninstall` | 移除註冊。它和 `setup` 一樣接受 `--client`、`--dry-run` 和 `--yes` 選項。 |
| `doctor` | 檢查 Cascadeur 的安裝、8765 和 48766 連接埠、script server、MCP Plus 版本和偵測到的用戶端。`--json` 會輸出 JSON。只有遇到真正的問題時，結束代碼才會是 1。 |
| `autostart status`、`autostart enable`、`autostart disable` | 自動啟動 Cascadeur 的伺服器。請看[自動啟動](#自動啟動)。 |
| `serve` | 執行 stdio 代理程式。MCP 用戶端會替你啟動它，你不需要自己執行。 |

## 3. 手動註冊（選用）

如果你不想執行 `setup`，可以自己把 `proxy.py` 註冊成 stdio 伺服器。請把 `C:\path\to\cascadeur-mcp-plus` 換成你的 checkout 路徑。

Claude Code：

```bat
claude mcp add --scope user cascadeur-plus -- python -I C:\path\to\cascadeur-mcp-plus\proxy.py
```

其他用戶端，使用 JSON 設定：

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

- 如果 `python` 不在 `PATH` 上，請改用 `python.exe` 的完整路徑。
- `-I` 讓 Python 以隔離模式執行：它會忽略 `PYTHON*` 環境變數和使用者的 site-packages。代理程式只需要標準函式庫。
- 不需要 Cascadeur 自己的 `cascadeur` MCP 項目。`run_python` 涵蓋了它的 `run_script`。

代理程式一定連得上。Cascadeur 關閉時，它會提供 `tools_manifest.json` 裡快取的工具清單。

## 4. 啟動 script server

每次啟動 Cascadeur，都要點 **Scripts → MCP → Start script server**。MCP Plus 本身會在第一次呼叫工具時啟動。[自動啟動](#自動啟動)可以替你啟動這兩個伺服器。之後的操作流程，請看[使用方式](usage.zh-TW.md)。

## 自動啟動

在 checkout 資料夾裡執行一次 `python -m cascadeur_mcp_plus_cli autostart enable`，Cascadeur 啟動時就會自動啟動 script server 和 MCP Plus。

| 指令 | 作用 |
|---|---|
| `autostart status` | 顯示處理常式是否已安裝、它使用哪一份 runtime 副本，以及兩個伺服器是否有回應。`--json` 會輸出 JSON。 |
| `autostart enable` | 在 Cascadeur 的 `resources\scripts\python\events\application_started\` 資料夾安裝一個處理常式，並把 MCP Plus 複製到 `%LOCALAPPDATA%\cascadeur-mcp-plus\runtime\<version>\`。 |
| `autostart disable` | 移除處理常式，並還原它所取代的檔案。 |

- 寫入 Cascadeur 的安裝資料夾需要系統管理員權限，所以 Windows 會顯示一次 UAC 提示。`--dry-run` 會先顯示變更。`--yes` 會略過確認，但 UAC 提示仍然會出現。`--force` 允許取代或刪除不是這個工具寫入的檔案。
- runtime 副本是一份快照。更新套件之後、`status` 回報的 runtime 版本和已安裝的版本不同時，或你想用較新的程式碼時，請再執行一次 `enable`。
- `--runtime PATH` 讓 MCP Plus 從 checkout 資料夾執行，而不是從那份副本執行。這是給開發用的：如果 checkout 資料夾移動了，處理常式會失敗，而且只會寫入日誌。
- `--method admin` 是預設值，也是目前唯一可用的方法。尚未支援 `user`。

> **安全：** 自動啟動會開啟兩個沒有驗證的本機連接埠：8765（Cascadeur 的 script server）和 48766（MCP Plus）。這台電腦上的任何程式都可以透過它們，在 Cascadeur 裡執行 Python。不使用 agent 時，請執行 `python -m cascadeur_mcp_plus_cli autostart disable`。

## 設定

代理程式會讀前兩個變數。`setup`、`doctor`、`autostart` 和各個工具會讀其他的變數。請設在指令或代理程式執行時的環境裡。

| 變數 | 預設值 | 用途 |
|---|---|---|
| `CASCADEUR_PLUS_URL` | `http://127.0.0.1:48766` | Cascadeur 裡 MCP Plus 的位址。代理程式啟動 MCP Plus 時，會把這個連接埠傳給它。 |
| `CASCADEUR_OFFICIAL_URL` | `http://127.0.0.1:8765` | Cascadeur 內建 script server 的位址，代理程式用它來啟動 MCP Plus。 |
| `CASCADEUR_DIR` 或 `CASCADEUR_HOME` | 自動偵測 | Cascadeur 的安裝資料夾。 |
| `CASCADEUR_EXE` | 自動偵測 | Cascadeur 執行檔的路徑，`doctor` 用它來找到安裝位置。 |
| `CASCADEUR_DATA` | Cascadeur 每位使用者的資料夾 | 資料夾。動捕模型應位於其中的 `resources\mocap.pt`。 |
| `CASCADEUR_LOG` | 資料夾裡的 `logs\cascadeur_log.log` | `get_log` 讀取的日誌檔。 |
| `CASCADEUR_MCP_PLUS_COMMAND` | 自動偵測 | 用戶端為代理程式執行的指令。`setup --command` 的優先順序更高。 |
| `CLAUDE_CONFIG_DIR`、`CODEX_HOME` | 用戶端的預設值 | `setup` 到這裡尋找用戶端的設定檔。 |

`MCP_PLUS_PORT` 不是環境變數。它是你手動啟動 MCP Plus 時傳入的字典裡的全域變數。請看[開發](development.zh-TW.md#手動啟動和熱重載)。
