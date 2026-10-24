[English](development.md) | **繁體中文** · [文件索引](README.zh-TW.md)

# 開發

## 測試

```bash
python -m unittest discover -s tests -v
```

測試只涵蓋 `proxy.py`，用假的本機伺服器執行，不需要 Cascadeur。Cascadeur 裡的套件（`cascadeur_mcp_plus/`）
需要 Cascadeur 的 Python，只能實機測試。

## 程式碼檢查和排版

Ruff 的設定在 `pyproject.toml`。先安裝和 CI 相同的版本，再分別執行每個檢查：

```bash
pip install ruff==0.17.2
ruff check .
ruff format --check .
```

## CI

`.github/workflows/ci.yml` 會在推送到 `main`、開 pull request，以及手動觸發時執行：

- **Ruff**：在 Ubuntu 上用 Python 3.13 做程式碼檢查和排版檢查。
- **測試**：在 Windows 上用 Python 3.11 和 3.13 跑單元測試。

## 手動啟動和熱重載

`start_mcp_plus.py` 會依相依順序重新載入套件並重新啟動伺服器，所以改完程式碼後不用重開 Cascadeur，
下次啟動就會生效。請在 Cascadeur 的 Python 裡執行（例如透過 script server 或 Python Console）：

```python
p = r"C:\path\to\cascadeur-mcp-plus\start_mcp_plus.py"
exec(open(p, encoding="utf-8").read(), {"__file__": p})
```

`MCP_PLUS_PORT` 是放在這個 dict 裡的全域變數，不是環境變數。要監聽其他連接埠時加上它：

```python
exec(open(p, encoding="utf-8").read(), {"__file__": p, "MCP_PLUS_PORT": 48767})
```

請用 `CASCADEUR_PLUS_URL` 讓代理程式連到同一個連接埠（請看[安裝](installation.zh-TW.md)）。
代理程式自己啟動 MCP Plus 時，會用這個 URL 的連接埠當作 `MCP_PLUS_PORT` 傳進去。

## 版本和預設連接埠

`cascadeur_mcp_plus/version.py` 存放伺服器和代理程式共用的 `VERSION`（目前是 0.4.0）和 `DEFAULT_PORT`（48766）。
代理程式直接讀這個檔案取得 `VERSION`，不會匯入套件。`proxy.py` 裡 `CASCADEUR_PLUS_URL` 的預設值也寫了這個連接埠，
請讓兩邊保持一致。更新紀錄在 [CHANGELOG.md](../CHANGELOG.md)。

## 工具清單檔

伺服器每次啟動都會寫入 `tools_manifest.json`，代理程式成功取得即時的 `tools/list` 後也會重寫它。
Cascadeur 沒開時，代理程式就提供這個檔案的內容。

## 原始碼結構

| 路徑 | 用途 |
|---|---|
| `proxy.py` | stdio MCP 代理程式：`/health` 確認、自動啟動、轉送、離線工具清單 |
| `start_mcp_plus.py` | 在 Cascadeur 裡執行：重新載入套件，啟動或重新啟動伺服器 |
| `tools_manifest.json` | 給代理程式用的快取工具清單 |
| `cascadeur_mcp_plus/__init__.py` | 匯出 `start`、`stop`、`status`、`VERSION`、`DEFAULT_PORT` |
| `cascadeur_mcp_plus/version.py` | 版本和預設連接埠 |
| `cascadeur_mcp_plus/server.py` | HTTP 伺服器（`/mcp`、`/health`）、工具定義、`QTimer` 主執行緒排程 |
| `cascadeur_mcp_plus/ui.py` | QML 介面樹和 ref、QtTest 輸入、`PrintWindow` 截圖、對話框、動作、視埠投影和拖曳 |
| `cascadeur_mcp_plus/scene_tools.py` | `run_python`、場景和物件資訊、`enter_rig_mode`、`get_log` |
| `tests/test_proxy.py` | 用假伺服器測試代理程式 |
| `pyproject.toml` | 專案資訊和 Ruff 設定（沒有東西要安裝） |
| `.github/workflows/ci.yml` | CI：Ruff 和測試 |
| `CHANGELOG.md` | 更新紀錄 |
