# 安裝

[English](installation.md) | **繁體中文** · [文件索引](README.zh-TW.md)

## 需求

- **Windows 10 或 11。** 截圖和視窗處理使用 Win32 API。
- **Cascadeur 2026.2**，需要內建的 MCP script server（在 2026.2.3 測試過）。
- **Python 3.11 以上**，給代理程式使用。只用標準函式庫，不需要 `pip install`。
  在 Cascadeur 裡執行的部分，使用 Cascadeur 內附的 Python 和 PySide6。
- **MCP 用戶端**，要能執行 stdio 伺服器，例如 Claude Code、Codex 或 Cursor。

## 1. 取得程式碼

```bash
git clone https://github.com/Karylcode/cascadeur-mcp-plus.git
```

`proxy.py`、`start_mcp_plus.py` 和 `cascadeur_mcp_plus/` 套件要放在同一個資料夾裡。
除此之外沒有寫死任何路徑。

## 2. 註冊代理程式

把 `proxy.py` 註冊成 stdio MCP 伺服器。請把 `C:\path\to\cascadeur-mcp-plus` 換成你的資料夾路徑。

**Claude Code：**

```bash
claude mcp add --scope user cascadeur-plus -- python -I C:\path\to\cascadeur-mcp-plus\proxy.py
```

**其他用戶端（JSON 設定）：**

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

- 如果 `python` 不在 `PATH` 上，改用 `python.exe` 的完整路徑。
- `-I` 讓 Python 用隔離模式執行：不讀 `PYTHON*` 環境變數，也不載入使用者的 site-packages。
  代理程式只需要標準函式庫。
- 不需要另外註冊 Cascadeur 官方的 `cascadeur` MCP。`run_python` 已經涵蓋 `run_script` 的功能。

代理程式一定連得上。Cascadeur 沒開時，它會提供快取的工具清單（`tools_manifest.json`）。

## 3. 開啟 script server

每次開啟 Cascadeur，都要點 **Scripts → MCP → Start script server**。
MCP Plus 本身會在第一次呼叫工具時自動啟動。請看[使用方式](usage.zh-TW.md)。

## 設定

代理程式會讀兩個環境變數。請設在代理程式執行時的環境裡。

| 變數 | 預設值 | 用途 |
|---|---|---|
| `CASCADEUR_PLUS_URL` | `http://127.0.0.1:48766` | Cascadeur 裡 MCP Plus 的位址。代理程式自動啟動 MCP Plus 時，會把這個埠傳給它。 |
| `CASCADEUR_OFFICIAL_URL` | `http://127.0.0.1:8765` | Cascadeur 內建 script server 的位址。代理程式透過它啟動 MCP Plus。 |

`MCP_PLUS_PORT` 不是環境變數，只在手動啟動 MCP Plus 時使用，請看[開發](development.zh-TW.md)。
