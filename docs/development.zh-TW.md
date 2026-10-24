[English](development.md) | **繁體中文** · [文件索引](README.zh-TW.md)

# 開發

## 單元測試

```bat
python -m unittest discover -s tests -v
```

請使用 Python 3.11 或更新的版本。單元測試不會啟動 Cascadeur，也不會連線到執行中的 MCP Plus。需要 Cascadeur 自己的 Python 才能執行的測試，放在 `tests/live/` 底下。

## 實機測試

實機測試會對已開啟、且 MCP Plus 正在執行的 Cascadeur 執行：

```bat
python tests\live\run.py [pattern]
```

`pattern` 是 unittest 的測試檔案樣式，預設為 `test_*.py`。`run.py` 是唯一支援的進入點：它會在測試探索之前設定 `CASCADEUR_LIVE=1`。單純的 unittest 測試探索不會設定這個變數，會跳過所有實機測試，也不會對 Cascadeur 送出任何請求。實機測試會開啟和關閉場景分頁，所以有別人正在使用同一個 Cascadeur 時，請不要執行。

## 程式碼檢查和格式化

Ruff 的設定在 `pyproject.toml`。請先安裝 CI 使用的版本，再執行這兩個檢查：

```bat
python -m pip install ruff==0.17.2
ruff check .
ruff format --check .
```

## CI

`.github/workflows/ci.yml` 會在推送到 `main`、開 pull request，以及手動觸發時執行。它有兩個工作：

- **Ruff**：程式碼檢查和格式檢查。
- **Tests**：在 Windows 上用 Python 3.11 和 3.13 執行單元測試。

## 手動啟動和熱重載

`start_mcp_plus.py` 會依相依順序重新載入套件並重新啟動伺服器，所以修改在下一次啟動時就會生效，不需要重新啟動 Cascadeur。請在 Cascadeur 的 Python 裡執行它（script server 或 Python Console）：

```python
p = r"C:\path\to\cascadeur-mcp-plus\start_mcp_plus.py"
exec(open(p, encoding="utf-8").read(), {"__file__": p})
```

`MCP_PLUS_PORT` 是傳給 `exec` 的字典裡的全域變數，不是環境變數。要監聽其他連接埠時，請把它加進去：

```python
exec(open(p, encoding="utf-8").read(), {"__file__": p, "MCP_PLUS_PORT": 48767})
```

請用 `CASCADEUR_PLUS_URL` 讓代理程式連到同一個連接埠（請看[安裝](installation.zh-TW.md#設定)）。代理程式自己啟動 MCP Plus 時，會把這個 URL 的連接埠傳給它。

## 版本和預設連接埠

`cascadeur_mcp_plus/version.py` 存放 Cascadeur 內的伺服器所用的 `VERSION` 和 `DEFAULT_PORT`（48766）。代理程式直接讀這個檔案取得 `VERSION`，不會匯入套件。它自己的預設連接埠，也就是 `cascadeur_mcp_plus_cli/proxy.py` 裡的 `DEFAULT_PORT`，重複了這個值，所以請讓兩者保持一致。更新紀錄在 [CHANGELOG.md](../CHANGELOG.md)。

## 工具清單檔

伺服器每次啟動都會寫入 `tools_manifest.json`，代理程式在取得即時的 `tools/list` 之後也會重寫它。Cascadeur 關閉時，代理程式提供的就是這個檔案。

## 新增和檢查工具

每個工具都是 `cascadeur_mcp_plus/tools/` 中某個模組裡的一個字典。`tools/__init__.py` 裡的 `load_tools()` 會依檔案名稱的順序收集這些模組，並拒絕重複的名稱。每個工具都會宣告 `requires`：它需要的 Cascadeur API 路徑（以 `csc` 為根）、`action:<id>` 鍵，以及 `module:<script>` 項目。`get_status` 會回報那些所需項目在執行中的 Cascadeur 裡找不到的工具。

`tools/check_readme_tools.py` 會檢查 `docs/tools.md` 和 `docs/tools.zh-TW.md` 的工具章節。登錄表中的每個工具在每個檔案裡都必須剛好出現一次，而且兩個檔案列出工具的順序必須相同。沒有加 `--offline` 時，它還會把清單和即時的 `tools/list` 比對，這需要 MCP Plus 正在執行的 Cascadeur：

```bat
python tools\check_readme_tools.py --offline
```

## 原始碼結構

| 路徑 | 用途 |
|---|---|
| `proxy.py` | stdio 代理程式的啟動器（`python -I proxy.py`） |
| `start_mcp_plus.py` | 在 Cascadeur 裡執行：重新載入套件，啟動或重新啟動伺服器 |
| `tools_manifest.json` | 給代理程式用的快取工具清單 |
| `cascadeur_mcp_plus_cli/main.py` | 命令列工具：`setup`、`uninstall`、`doctor`、`autostart`、`serve` |
| `cascadeur_mcp_plus_cli/proxy.py` | stdio 代理程式：`/health` 檢查、自動啟動、轉送、離線工具清單 |
| `cascadeur_mcp_plus_cli/clients.py` | MCP 用戶端設定檔：偵測、備份和註冊 |
| `cascadeur_mcp_plus_cli/autostart.py`, `doctor.py` | 自動啟動的處理程式，以及健康報告 |
| `cascadeur_mcp_plus/__init__.py` | 匯出 `start`、`stop`、`status`、`VERSION` 和 `DEFAULT_PORT` |
| `cascadeur_mcp_plus/version.py` | 版本和預設連接埠 |
| `cascadeur_mcp_plus/server.py` | HTTP 伺服器（`/mcp`、`/health`）、工具分派和 GUI 執行緒排程器 |
| `cascadeur_mcp_plus/tools/` | 工具模組；每個模組都定義一個 `TOOLS` 清單 |
| `cascadeur_mcp_plus/tools/__init__.py` | `load_tools()`，負責收集並檢查登錄表 |
| `cascadeur_mcp_plus/compat.py` | 啟動時檢查每個工具的 `requires` |
| `cascadeur_mcp_plus/ui.py` | QML 樹和元素參照、QtTest 輸入、`PrintWindow` 截圖、對話框、動作、視埠投影和拖曳 |
| `cascadeur_mcp_plus/resources.py` | 四個 MCP 資源 |
| `cascadeur_mcp_plus/paths.py` | Cascadeur 的資料夾、日誌和安裝資料夾 |
| `tests/` | 單元測試；`tests/live/` 放著實機測試和 `run.py` |
| `tools/check_readme_tools.py` | 對照登錄表，檢查文件的工具章節 |
| `examples/` | `wave.md` 和 `wave.py`，一個完整的範例 |
| `pyproject.toml` | 專案中繼資料和 Ruff 設定（沒有東西要安裝） |
| `.github/workflows/ci.yml` | CI |
| `CHANGELOG.md` | 更新紀錄 |
