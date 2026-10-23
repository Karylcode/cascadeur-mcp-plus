"""MCP client detection and registration for the setup and uninstall commands.

Standard library only, like proxy.py. Every path is resolved from the environment when it is
used (never at import time), so tests can point HOME/APPDATA/CODEX_HOME at a temporary
directory and the real client configuration is never touched.

The changes are planned first and only written after the user confirms them. Before a file is
replaced its previous content is copied next to it as "<name>.cascadeur-mcp-plus.<time>.bak".
Claude Code is registered through its own CLI ("claude mcp add --scope user"), which keeps
Claude Code in charge of its config file; only a backup copy is made by us.
"""

from __future__ import annotations

import difflib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import ROOT

SERVER_NAME = "cascadeur-plus"
CLIENT_KEYS = ("claude-code", "codex", "cursor", "vscode")
CLIENT_LABELS = {
    "claude-code": "Claude Code",
    "codex": "Codex CLI",
    "cursor": "Cursor",
    "vscode": "VS Code",
}
CHANGING = ("add", "update", "remove")


class CliError(RuntimeError):
    """A change cannot be planned or applied; the message is printed for the user."""


@dataclass(frozen=True)
class Launch:
    """The command an MCP client runs to start the stdio proxy."""

    command: str
    args: tuple[str, ...] = ("serve",)
    note: str = ""

    def argv(self) -> list[str]:
        return [self.command, *self.args]


@dataclass
class Plan:
    """One client's pending change: a config file edit, or a "claude" CLI invocation."""

    key: str
    label: str
    kind: str  # "file" or "command"
    installed: bool
    status: str  # "add", "update", "remove" or "unchanged"
    path: Path | None = None
    argv: list[str] | None = None
    before: str | None = None
    after: str | None = None
    note: str = ""

    @property
    def changes(self) -> bool:
        return self.status in CHANGING


def which(name: str) -> str | None:
    return shutil.which(name)


def run_command(argv: list[str]) -> subprocess.CompletedProcess:
    """Run a client's own CLI; tests replace this to record the invocation."""
    return subprocess.run(argv, capture_output=True, text=True, timeout=120)


def format_argv(argv: list[str]) -> str:
    if os.name == "nt":
        return subprocess.list2cmdline(argv)
    return shlex.join(argv)


# ---------------------------------------------------------------------------
# Where the clients keep their configuration
# ---------------------------------------------------------------------------


def home() -> Path:
    value = os.environ.get("HOME") or os.environ.get("USERPROFILE")
    return Path(value).expanduser() if value else Path.home()


def appdata() -> Path:
    value = os.environ.get("APPDATA")
    return Path(value) if value else home() / "AppData" / "Roaming"


def codex_config() -> Path:
    value = os.environ.get("CODEX_HOME")
    return (Path(value) if value else home() / ".codex") / "config.toml"


def cursor_config() -> Path:
    return home() / ".cursor" / "mcp.json"


def vscode_user_dir() -> Path:
    base = appdata()
    stable = base / "Code" / "User"
    insiders = base / "Code - Insiders" / "User"
    if insiders.is_dir() and not stable.is_dir():
        return insiders
    return stable


def vscode_config() -> Path:
    return vscode_user_dir() / "mcp.json"


def claude_config() -> Path:
    """Claude Code's user-scope config file; the "claude" CLI writes it, we only back it up."""
    return home() / ".claude.json"


def is_installed(key: str) -> bool:
    if key == "claude-code":
        return which("claude") is not None
    if key == "codex":
        return which("codex") is not None or codex_config().is_file()
    if key == "cursor":
        return which("cursor") is not None or cursor_config().parent.is_dir()
    if key == "vscode":
        return which("code") is not None or which("code-insiders") is not None or vscode_user_dir().is_dir()
    return False


# ---------------------------------------------------------------------------
# How clients should start the proxy
# ---------------------------------------------------------------------------


def _ephemeral_path(path: str) -> bool:
    """True when path is inside a throwaway uvx environment (uv's cache or a temp archive).

    uvx installs the package into <cache>\\archive-v0\\<hash> (or a .tmp... directory with
    --no-cache); registering that path would break as soon as uv cleans up. A "uv tool
    install", pipx or venv console script does not match and is registered normally.
    """
    normalized = os.path.normcase(os.path.abspath(path)).replace("/", "\\")
    if "\\uv\\cache\\" in normalized:
        return True
    return any(marker in normalized for marker in ("\\archive-v0\\", "\\environments-v0\\", "\\environments-v2\\"))


def launch_command(explicit: str | None = None) -> Launch:
    """Pick the command MCP clients should run.

    An installed console script is preferred. From a source checkout (or an editable install)
    the current Python runs proxy.py directly, exactly like the documented manual setup. In a
    throwaway uvx environment the proxy is started through uvx instead, so the registration
    survives the environment being removed.
    """
    if explicit:
        return Launch(explicit, note="from --command")
    env_value = os.environ.get("CASCADEUR_MCP_PLUS_COMMAND")
    if env_value:
        return Launch(env_value, note="from CASCADEUR_MCP_PLUS_COMMAND")
    found = which("cascadeur-mcp-plus")
    if found and not _ephemeral_path(found):
        return Launch(found, note="the installed console script")
    proxy = Path(ROOT) / "proxy.py"
    if proxy.is_file() and not _ephemeral_path(sys.executable):
        return Launch(sys.executable, ("-I", str(proxy)), note="this Python and proxy.py (standard library only)")
    uvx = which("uvx")
    if uvx:
        return Launch(
            uvx,
            ("cascadeur-mcp-plus", "serve"),
            note='uvx; this needs the package on PyPI (install it with "uv tool install cascadeur-mcp-plus")',
        )
    if proxy.is_file():
        return Launch(sys.executable, ("-I", str(proxy)), note="this Python and proxy.py")
    return Launch(
        sys.executable,
        ("-m", "cascadeur_mcp_plus_cli", "serve"),
        note="this Python; its environment must survive for clients to start the proxy",
    )


# ---------------------------------------------------------------------------
# Config file content: JSON (Cursor, VS Code) and TOML (Codex)
# ---------------------------------------------------------------------------


def _json_key(key: str) -> str:
    return "servers" if key == "vscode" else "mcpServers"


def _entry(key: str, launch: Launch) -> dict:
    entry: dict = {"command": launch.command, "args": list(launch.args)}
    if key == "vscode":
        entry = {"type": "stdio", **entry}
    return entry


def read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None


def _load_json(path: Path, text: str | None) -> dict:
    if text is None or not text.strip():
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CliError(f"{path} is not valid JSON ({exc}); fix it, or choose another client") from exc
    if not isinstance(data, dict):
        raise CliError(f"{path} must contain a JSON object")
    return data


def json_add(text: str | None, path: Path, key: str, launch: Launch) -> str:
    data = _load_json(path, text)
    servers = data.setdefault(_json_key(key), {})
    if not isinstance(servers, dict):
        raise CliError(f'{path}: "{_json_key(key)}" must be a JSON object')
    entry = _entry(key, launch)
    if text is not None and servers.get(SERVER_NAME) == entry:
        return text
    servers[SERVER_NAME] = entry
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def json_remove(text: str | None, path: Path, key: str) -> str:
    if text is None:
        return ""
    data = _load_json(path, text)
    servers = data.get(_json_key(key))
    if not isinstance(servers, dict) or SERVER_NAME not in servers:
        return text
    del servers[SERVER_NAME]
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


_TABLE = re.compile(
    r"^\s*\[\s*mcp_servers\s*\.\s*(?:\"cascadeur-plus\"|'cascadeur-plus'|cascadeur-plus)\s*\]\s*(?:#.*)?$"
)
_SECTION = re.compile(r"^\s*\[")


def _toml_string(value: str) -> str:
    # JSON escaping is also valid TOML basic-string escaping (quotes, backslashes, control chars).
    return json.dumps(value, ensure_ascii=False)


def toml_block(launch: Launch) -> str:
    args = ", ".join(_toml_string(arg) for arg in launch.args)
    return f"[mcp_servers.{SERVER_NAME}]\ncommand = {_toml_string(launch.command)}\nargs = [{args}]\n"


def _load_toml(path: Path, text: str | None) -> dict:
    if text is None or not text.strip():
        return {}
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise CliError(f"{path} is not valid TOML ({exc}); fix it, or choose another client") from exc


def _toml_entry(data: dict) -> dict | None:
    servers = data.get("mcp_servers")
    if not isinstance(servers, dict):
        return None
    entry = servers.get(SERVER_NAME)
    return entry if isinstance(entry, dict) else None


def _table_span(lines: list[str]) -> tuple[int, int] | None:
    """Line span of [mcp_servers.cascadeur-plus], without trailing blank lines."""
    for index, line in enumerate(lines):
        if _TABLE.match(line):
            end = index + 1
            while end < len(lines) and not _SECTION.match(lines[end]):
                end += 1
            while end > index + 1 and not lines[end - 1].strip():
                end -= 1
            return index, end
    return None


def toml_add(text: str | None, path: Path, launch: Launch) -> str:
    body = text or ""
    data = _load_toml(path, text)
    entry = _toml_entry(data)
    wanted = {"command": launch.command, "args": list(launch.args)}
    if entry is not None and entry == wanted:
        return body
    lines = body.splitlines(keepends=True)
    span = _table_span(lines)
    if entry is not None and span is None:  # e.g. mcp_servers = { cascadeur-plus = { ... } }
        raise CliError(f"{path} defines mcp_servers.{SERVER_NAME} as an inline table; edit that entry by hand")
    block = toml_block(launch)
    if span is not None:
        start, end = span
        return "".join(lines[:start]) + block + "".join(lines[end:])
    if not body:
        return block
    if not body.endswith("\n"):
        body += "\n"
    if not body.endswith("\n\n"):
        body += "\n"
    return body + block


def toml_remove(text: str | None, path: Path) -> str:
    body = text or ""
    data = _load_toml(path, text)
    if _toml_entry(data) is None:
        return body
    lines = body.splitlines(keepends=True)
    span = _table_span(lines)
    if span is None:  # e.g. mcp_servers = { cascadeur-plus = { ... } } on one line
        raise CliError(f"{path} defines mcp_servers.{SERVER_NAME} as an inline table; remove that entry by hand")
    start, end = span
    if start > 0 and not lines[start - 1].strip():
        start -= 1
    remaining = "".join(lines[:start] + lines[end:])
    if remaining and not remaining.endswith("\n"):
        remaining += "\n"
    return remaining


# ---------------------------------------------------------------------------
# Plans
# ---------------------------------------------------------------------------


def _client_path(key: str) -> Path:
    return {"codex": codex_config(), "cursor": cursor_config(), "vscode": vscode_config()}[key]


def plan_setup(key: str, launch: Launch) -> Plan:
    label = CLIENT_LABELS[key]
    installed = is_installed(key)
    if key == "claude-code":
        claude = which("claude") or "claude"
        return Plan(
            key=key,
            label=label,
            kind="command",
            installed=installed,
            status="add",
            path=claude_config(),
            argv=[claude, "mcp", "add", "--scope", "user", SERVER_NAME, "--", *launch.argv()],
            note="Claude Code manages its own config file; its current content is copied first",
        )
    path = _client_path(key)
    before = read_text(path)
    after = toml_add(before, path, launch) if key == "codex" else json_add(before, path, key, launch)
    if before is None:
        status = "add"
    elif after == before:
        status = "unchanged"
    else:
        status = "update"
    return Plan(
        key=key,
        label=label,
        kind="file",
        installed=installed,
        status=status,
        path=path,
        before=before,
        after=after,
    )


def plan_uninstall(key: str) -> Plan:
    label = CLIENT_LABELS[key]
    installed = is_installed(key)
    if key == "claude-code":
        claude = which("claude") or "claude"
        return Plan(
            key=key,
            label=label,
            kind="command",
            installed=installed,
            status="remove",
            path=claude_config(),
            argv=[claude, "mcp", "remove", "--scope", "user", SERVER_NAME],
            note="Claude Code manages its own config file; its current content is copied first",
        )
    path = _client_path(key)
    before = read_text(path)
    if before is None:
        return Plan(
            key=key,
            label=label,
            kind="file",
            installed=installed,
            status="unchanged",
            path=path,
            note="no config file",
        )
    after = toml_remove(before, path) if key == "codex" else json_remove(before, path, key)
    return Plan(
        key=key,
        label=label,
        kind="file",
        installed=installed,
        status="unchanged" if after == before else "remove",
        path=path,
        before=before,
        after=after,
    )


# ---------------------------------------------------------------------------
# Showing and applying a plan
# ---------------------------------------------------------------------------


def diff_of(plan: Plan) -> str:
    before = plan.before or ""
    after = plan.after or ""
    from_label = f"{plan.path} (current)" if plan.before is not None else f"{plan.path} (does not exist)"
    lines = difflib.unified_diff(
        before.splitlines(keepends=True),
        after.splitlines(keepends=True),
        fromfile=from_label,
        tofile=f"{plan.path} (after)",
        n=2,
    )
    return "".join(lines)


def describe(plan: Plan) -> str:
    lines = [f"{plan.label} ({plan.key})", f"  detected: {'yes' if plan.installed else 'no'}"]
    if plan.kind == "command":
        state = "exists" if plan.path is not None and plan.path.is_file() else "not present"
        lines.append(f"  config: {plan.path} ({state})")
        lines.append(f"  command: {format_argv(plan.argv or [])}")
        lines.append(f"  change: {plan.status}" if plan.changes else "  change: none")
    else:
        state = "not present" if plan.before is None else "exists"
        lines.append(f"  config: {plan.path} ({state})")
        if plan.changes:
            lines.append(f"  change: {plan.status}")
            lines.extend("  " + line for line in diff_of(plan).rstrip("\n").splitlines())
        else:
            lines.append(f"  change: none ({SERVER_NAME} is already as requested)")
    if plan.note:
        lines.append(f"  note: {plan.note}")
    return "\n".join(lines)


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def backup(path: Path) -> Path | None:
    """Copy path next to itself before it is replaced; returns the copy's path."""
    if not path.is_file():
        return None
    stamp = _timestamp()
    target = path.with_name(f"{path.name}.cascadeur-mcp-plus.{stamp}.bak")
    counter = 1
    while target.exists():
        counter += 1
        target = path.with_name(f"{path.name}.cascadeur-mcp-plus.{stamp}-{counter}.bak")
    shutil.copy2(path, target)
    return target


def apply_plan(plan: Plan) -> str:
    """Apply one plan and return a one-line note; raises CliError when it fails."""
    if plan.kind == "command":
        assert plan.argv is not None  # plans always carry their command
        saved = backup(plan.path) if plan.path is not None else None
        try:
            result = run_command(plan.argv)
        except FileNotFoundError as exc:
            raise CliError(f"could not run {format_argv(plan.argv)}: {exc}") from exc
        output = ((result.stdout or "") + (result.stderr or "")).strip()
        if result.returncode != 0:
            raise CliError(f"{format_argv(plan.argv)} failed ({result.returncode}): {output or 'no output'}")
        note = f"{plan.label}: {format_argv(plan.argv)}"
        if output:
            note += f" -> {output.splitlines()[0]}"
        if saved is not None:
            note += f" (backup: {saved})"
        return note
    assert plan.path is not None and plan.after is not None
    saved = backup(plan.path)
    plan.path.parent.mkdir(parents=True, exist_ok=True)
    temporary = plan.path.with_name(f"{plan.path.name}.cascadeur-mcp-plus.tmp")
    temporary.write_text(plan.after, encoding="utf-8", newline="\n")
    os.replace(temporary, plan.path)
    note = f"{plan.label}: wrote {plan.path}"
    if saved is not None:
        note += f" (backup: {saved})"
    return note
