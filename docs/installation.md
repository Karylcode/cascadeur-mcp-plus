**English** | [繁體中文](installation.zh-TW.md) · [Documentation index](README.md)

# Installation

This page covers what MCP Plus needs, how to register it with your MCP client, the script server, automatic start and configuration.

## Requirements

- **Windows 10 or 11.** Screenshots and window handling use the Win32 API. Only Windows is tested.
- **Cascadeur 2026.2** with its built-in MCP script server (tested with 2026.2.3).
- **Python 3.11 or newer** for the proxy and the command-line tool. They use only the standard library, so there is nothing to `pip install`. The part that runs inside Cascadeur uses the Python and PySide6 bundled with Cascadeur.
- **An MCP client** that runs stdio servers, such as Claude Code, Codex CLI, Cursor or VS Code.
- [uv](https://docs.astral.sh/uv/) is optional. It is needed only for the `uvx` form of the commands below.

## 1. Get the code

```bat
git clone https://github.com/Karylcode/cascadeur-mcp-plus.git
cd cascadeur-mcp-plus
```

The package is not on PyPI yet, so run the commands from the checkout folder. Keep `proxy.py`, `start_mcp_plus.py`, `cascadeur_mcp_plus/` and `cascadeur_mcp_plus_cli/` together in that folder. No other paths are hard-coded.

## 2. Register MCP Plus with your MCP client

From the checkout folder, run:

```bat
python -m cascadeur_mcp_plus_cli setup
```

`setup` registers the server, under the name `cascadeur-plus`, with each MCP client it detects: Claude Code, Codex CLI, Cursor and VS Code. It backs up each config file before changing it. The registered command is the Python that runs `proxy.py` with `-I`, so `uv` is not needed.

| Option | Meaning |
|---|---|
| `--dry-run` | Print the exact changes and write nothing. |
| `--client NAME` | Change one client only: `claude-code`, `codex`, `cursor` or `vscode`. The default is every detected client. |
| `--command PATH` | Make the clients run this executable instead of the detected one. |
| `--yes` | Apply the changes without asking for confirmation (for scripts). |

To run `setup` from any folder, use uv with the checkout path:

```bat
uvx --from C:\path\to\cascadeur-mcp-plus cascadeur-mcp-plus setup
```

Every command in this page works in either form: `python -m cascadeur_mcp_plus_cli <command>` inside the checkout, or `uvx --from C:\path\to\cascadeur-mcp-plus cascadeur-mcp-plus <command>` from anywhere.

| Command | Purpose |
|---|---|
| `setup` | Register with the detected MCP clients. |
| `uninstall` | Remove the registration again. It takes the same `--client`, `--dry-run` and `--yes` options as `setup`. |
| `doctor` | Check the Cascadeur installation, ports 8765 and 48766, the script server, the MCP Plus version and the detected clients. `--json` prints JSON. It exits with 1 only for real problems. |
| `autostart status`, `autostart enable`, `autostart disable` | Start Cascadeur's servers automatically. See [Autostart](#autostart). |
| `serve` | Run the stdio proxy. MCP clients start it for you; you do not run it yourself. |

## 3. Register manually (optional)

If you do not want to run `setup`, register `proxy.py` as a stdio server yourself. Replace `C:\path\to\cascadeur-mcp-plus` with your checkout.

Claude Code:

```bat
claude mcp add --scope user cascadeur-plus -- python -I C:\path\to\cascadeur-mcp-plus\proxy.py
```

Other clients, with a JSON config:

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
- `-I` runs Python in isolated mode: it ignores `PYTHON*` environment variables and the user site-packages. The proxy needs nothing beyond the standard library.
- You do not need Cascadeur's own `cascadeur` MCP entry. `run_python` covers its `run_script`.

The proxy always connects. While Cascadeur is closed, it serves the cached tool list in `tools_manifest.json`.

## 4. Start the script server

Each time you start Cascadeur, click **Scripts → MCP → Start script server**. MCP Plus itself starts on the first tool call. [Autostart](#autostart) can start both servers for you. For the workflow after that, see [Usage](usage.md).

## Autostart

`python -m cascadeur_mcp_plus_cli autostart enable`, run once from the checkout, makes Cascadeur start the script server and MCP Plus when it launches.

| Command | What it does |
|---|---|
| `autostart status` | Shows whether the handler is installed, which runtime copy it uses, and whether both servers answer. `--json` prints JSON. |
| `autostart enable` | Installs a handler in Cascadeur's `resources\scripts\python\events\application_started\` folder and copies MCP Plus to `%LOCALAPPDATA%\cascadeur-mcp-plus\runtime\<version>\`. |
| `autostart disable` | Removes the handler and restores the file it replaced. |

- Writing into the Cascadeur install folder needs administrator rights, so Windows shows one UAC prompt. `--dry-run` prints the changes first. `--yes` skips the confirmation, but the UAC prompt still appears. `--force` allows replacing or deleting a file that this tool did not write.
- The runtime copy is a snapshot. Run `enable` again after you update the package, when `status` reports a runtime version that differs from the installed one, or when you want newer code.
- `--runtime PATH` runs MCP Plus from a checkout instead of the copy. It is for development: if the checkout moves, the handler fails and only writes to the log.
- `--method admin` is the default and the only method available. `user` is not supported yet.

> **Security:** autostart opens two local ports without authentication: 8765 (Cascadeur's script server) and 48766 (MCP Plus). Any program on this computer can then run Python inside Cascadeur through them. Run `python -m cascadeur_mcp_plus_cli autostart disable` when you are not using the agent.

## Configuration

The proxy reads the first two variables. `setup`, `doctor`, `autostart` and the tools read the others. Set them in the environment of the command or the proxy.

| Variable | Default | Purpose |
|---|---|---|
| `CASCADEUR_PLUS_URL` | `http://127.0.0.1:48766` | Address of MCP Plus inside Cascadeur. When the proxy starts MCP Plus, it passes this port to it. |
| `CASCADEUR_OFFICIAL_URL` | `http://127.0.0.1:8765` | Address of Cascadeur's built-in script server, which the proxy uses to start MCP Plus. |
| `CASCADEUR_DIR` or `CASCADEUR_HOME` | detected | Cascadeur's installation folder. |
| `CASCADEUR_EXE` | detected | Path to Cascadeur's executable, which `doctor` uses to find the installation. |
| `CASCADEUR_DATA` | Cascadeur's per-user data folder | Data folder. The mocap model is expected at `resources\mocap.pt` inside it. |
| `CASCADEUR_LOG` | `logs\cascadeur_log.log` in the data folder | The log file that `get_log` reads. |
| `CASCADEUR_MCP_PLUS_COMMAND` | detected | Command that the clients run for the proxy. `setup --command` takes precedence. |
| `CLAUDE_CONFIG_DIR`, `CODEX_HOME` | client defaults | Where `setup` looks for client config files. |

`MCP_PLUS_PORT` is not an environment variable. It is a global in the dictionary that you pass when you start MCP Plus by hand. See [Development](development.md#manual-start-and-hot-reload).
