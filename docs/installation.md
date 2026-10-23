**English** | [繁體中文](installation.zh-TW.md) · [Documentation index](README.md)

# Installation

## Requirements

- **Windows 10 or 11.** Screenshots and window handling use the Win32 API.
- **Cascadeur 2026.2** with its built-in MCP script server (tested with 2026.2.3).
- **Python 3.11+** for the proxy. It uses only the standard library, so there is nothing to `pip install`.
  The part that runs inside Cascadeur uses Cascadeur's bundled Python and PySide6.
- **An MCP client** that runs stdio servers, such as Claude Code, Codex or Cursor.

## 1. Get the code

```bash
git clone https://github.com/Karylcode/cascadeur-mcp-plus.git
```

`proxy.py`, `start_mcp_plus.py` and the `cascadeur_mcp_plus/` package must stay together in this folder.
No other paths are hard-coded.

## 2. Register the proxy

Register `proxy.py` as a stdio MCP server. Replace `C:\path\to\cascadeur-mcp-plus` with the path of your clone.

**Claude Code:**

```bash
claude mcp add --scope user cascadeur-plus -- python -I C:\path\to\cascadeur-mcp-plus\proxy.py
```

**Other clients (JSON config):**

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

- Use the full path to `python.exe` if `python` is not on `PATH`.
- `-I` runs Python in isolated mode: it ignores `PYTHON*` environment variables and the user site-packages.
  The proxy needs nothing beyond the standard library.
- You do not need to register Cascadeur's own `cascadeur` MCP entry. `run_python` covers `run_script`.

The proxy always connects. While Cascadeur is closed, it serves a cached tool list (`tools_manifest.json`).

## 3. Start the script server

Each time you start Cascadeur, click **Scripts → MCP → Start script server**.
MCP Plus itself starts automatically on the first tool call. See [Usage](usage.md).

## Configuration

The proxy reads two environment variables. Set them in the environment the proxy runs in.

| Variable | Default | Purpose |
|---|---|---|
| `CASCADEUR_PLUS_URL` | `http://127.0.0.1:48766` | Address of MCP Plus inside Cascadeur. When the proxy starts MCP Plus, it passes this port to it. |
| `CASCADEUR_OFFICIAL_URL` | `http://127.0.0.1:8765` | Address of Cascadeur's built-in script server, which the proxy uses to start MCP Plus. |

`MCP_PLUS_PORT` is not an environment variable. It only applies when you start MCP Plus by hand;
see [Development](development.md).
