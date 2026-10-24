"""MCP client detection and registration for the setup and uninstall commands.

Standard library only. Every path is resolved from the environment when it is used (never at
import time), so tests can point USERPROFILE/HOME/APPDATA/CODEX_HOME/CLAUDE_CONFIG_DIR at a
temporary directory and the real client configuration is never touched.

The changes are planned first and only written after the user confirms them. Before a file is
replaced its previous content is copied next to it as "<name>.cascadeur-mcp-plus.<time>.bak".
Claude Code is registered through its own CLI ("claude mcp add --scope user"), which keeps
Claude Code in charge of its config file; that CLI is only run after a backup of the file
succeeded, and its plan shows the current entry so "replace" is never mistaken for "add".
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

from . import PACKAGE_ROOT

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


@dataclass(frozen=True)
class Source:
    """A config file as read from disk; text is None when the file does not exist."""

    text: str | None
    bom: bool = False
    newline: str = "\n"


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
    bom: bool = False
    newline: str = "\n"
    # Claude Code only: what its config file shows, and what the plan would register.
    config_state: str = ""  # "exists" or "missing"
    existing: dict | None = None
    desired: dict | None = None
    replace_argv: list[str] | None = None

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
    r"""The user's home directory.

    On Windows USERPROFILE wins: HOME is often an MSYS2/Cygwin path such as
    C:\msys64\home\User, which is not where Claude Code, Cursor, VS Code or Codex store
    their configuration. Elsewhere HOME wins, with USERPROFILE as a fallback.
    """
    if os.name == "nt":
        candidates = (os.environ.get("USERPROFILE"), os.environ.get("HOME"))
    else:
        candidates = (os.environ.get("HOME"), os.environ.get("USERPROFILE"))
    for value in candidates:
        if value:
            return Path(value).expanduser()
    return Path.home()


def appdata() -> Path:
    r"""The folder GUI applications keep their per-user configuration in (T55).

    Windows: %APPDATA% (or ~/AppData/Roaming)
    macOS:   ~/Library/Application Support
    Linux:   $XDG_CONFIG_HOME (or ~/.config)
    """
    if sys.platform == "win32":
        value = os.environ.get("APPDATA")
        return Path(value) if value else home() / "AppData" / "Roaming"
    if sys.platform == "darwin":
        return home() / "Library" / "Application Support"
    value = os.environ.get("XDG_CONFIG_HOME")
    return Path(value) if value else home() / ".config"


def local_app_data() -> Path:
    r"""The folder applications keep non-roaming per-user data in (T55).

    Windows: %LOCALAPPDATA% (or ~/AppData/Local)
    macOS:   ~/Library/Application Support
    Linux:   $XDG_DATA_HOME (or ~/.local/share)

    The in-app package resolves the same folder in cascadeur_mcp_plus/paths.py; the CLI must
    not import that package (it needs Cascadeur's Python), so the rules live in both places.
    """
    if sys.platform == "win32":
        value = os.environ.get("LOCALAPPDATA")
        return Path(value) if value else home() / "AppData" / "Local"
    if sys.platform == "darwin":
        return home() / "Library" / "Application Support"
    value = os.environ.get("XDG_DATA_HOME")
    return Path(value) if value else home() / ".local" / "share"


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
    """Claude Code's user-scope config file.

    Claude Code resolves it as <CLAUDE_CONFIG_DIR or home>/.claude.json (the same directory
    its settings live in); the claude CLI writes it, so we only read it and back it up.
    """
    configured = os.environ.get("CLAUDE_CONFIG_DIR")
    base = Path(configured).expanduser() if configured else home()
    return base / ".claude.json"


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

    uvx installs the package into <cache>/archive-v0/<hash> (or a .tmp... directory with
    --no-cache); registering that path would break as soon as uv cleans up. A "uv tool
    install", pipx or venv console script does not match and is registered normally.

    The check works on path components, never on separators, so the same rules hold for
    %LOCALAPPDATA%\\uv\\cache on Windows and ~/.cache/uv/archive-v0 on macOS and Linux (T55).
    """
    parts = Path(os.path.abspath(path)).parts
    lowered = [part.lower() for part in parts]
    if any(first == "uv" and second == "cache" for first, second in zip(lowered, lowered[1:], strict=False)):
        return True
    return any(part.startswith(("archive-v0", "environments-v0", "environments-v2")) for part in lowered)


def launch_command(explicit: str | None = None) -> Launch:
    """Pick the command MCP clients should run.

    An installed console script is preferred. From a source checkout (or an editable install)
    the current Python runs the root proxy.py launcher. In a throwaway uvx environment the
    proxy is started through uvx instead, so the registration survives the environment being
    removed. A wheel installed without a console script on PATH uses "python -m
    cascadeur_mcp_plus_cli serve".
    """
    if explicit:
        return Launch(explicit, note="from --command")
    env_value = os.environ.get("CASCADEUR_MCP_PLUS_COMMAND")
    if env_value:
        return Launch(env_value, note="from CASCADEUR_MCP_PLUS_COMMAND")
    found = which("cascadeur-mcp-plus")
    if found and not _ephemeral_path(found):
        return Launch(found, note="the installed console script")
    proxy = Path(PACKAGE_ROOT) / "proxy.py"
    if proxy.is_file() and not _ephemeral_path(sys.executable):
        return Launch(sys.executable, ("-I", str(proxy)), note="this Python and proxy.py (standard library only)")
    uvx = which("uvx")
    if uvx and _ephemeral_path(sys.executable):
        return Launch(
            uvx,
            ("cascadeur-mcp-plus", "serve"),
            note='uvx; this needs the package on PyPI (install it with "uv tool install cascadeur-mcp-plus")',
        )
    if proxy.is_file():
        return Launch(sys.executable, ("-I", str(proxy)), note="this Python and proxy.py")
    return Launch(
        sys.executable,
        ("-I", "-m", "cascadeur_mcp_plus_cli", "serve"),
        note='this Python and the installed package (use "uv tool install cascadeur-mcp-plus" for a stable command)',
    )


# ---------------------------------------------------------------------------
# Reading and writing configuration files
# ---------------------------------------------------------------------------


def read_source(path: Path) -> Source:
    """Read a config file as UTF-8 (a BOM is tolerated and remembered).

    Raises CliError with the path for permission problems and non-UTF-8 files, so the CLI
    prints a clear message instead of a traceback. Line endings are normalised to "\n" here
    and restored on write with Source.newline.
    """
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return Source(None)
    except OSError as exc:
        raise CliError(f"could not read {path}: {exc}") from exc
    bom = raw.startswith(b"\xef\xbb\xbf")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise CliError(f"{path} is not UTF-8 text ({exc}); fix it, or choose another client") from exc
    newline = "\r\n" if "\r\n" in text else "\n"
    return Source(text.replace("\r\n", "\n"), bom, newline)


# ---------------------------------------------------------------------------
# JSON clients: Cursor and VS Code
# ---------------------------------------------------------------------------


def _json_key(key: str) -> str:
    return "servers" if key == "vscode" else "mcpServers"


def _entry(key: str, launch: Launch) -> dict:
    entry: dict = {"command": launch.command, "args": list(launch.args)}
    if key == "vscode":
        entry = {"type": "stdio", **entry}
    return entry


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
    existing = servers.get(SERVER_NAME)
    # Keep options the user added (env, timeout, ...); only command/args/type are ours.
    merged = dict(existing) if isinstance(existing, dict) else {}
    merged.update(entry)
    if text is not None and existing == merged:
        return text
    servers[SERVER_NAME] = merged
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


# ---------------------------------------------------------------------------
# TOML client: Codex
# ---------------------------------------------------------------------------

_TOML_NAME = "\"cascadeur-plus\"|'cascadeur-plus'|cascadeur-plus"
_TABLE = re.compile(rf"^\s*\[\s*mcp_servers\s*\.\s*(?:{_TOML_NAME})\s*\]\s*(?:#.*)?$")
_SUBTABLE = re.compile(rf"^\s*\[\s*mcp_servers\s*\.\s*(?:{_TOML_NAME})\s*\.[^\]]+\]\s*(?:#.*)?$")
_SECTION = re.compile(r"^\s*\[")
_INLINE_SERVERS = re.compile(r'^\s*"?mcp_servers"?\s*=')
_KEY = re.compile(r'^(\s*)"?(command|args)"?\s*=')


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


def _validate_toml(path: Path, text: str) -> None:
    """Refuse to write a change that tomllib cannot read back."""
    try:
        tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise CliError(
            f"the change to {path} would make it invalid TOML ({exc}); nothing was written. "
            "Edit the file by hand or remove the conflicting mcp_servers entry."
        ) from exc


def _toml_entry(data: dict) -> dict | None:
    servers = data.get("mcp_servers")
    if not isinstance(servers, dict):
        return None
    entry = servers.get(SERVER_NAME)
    return entry if isinstance(entry, dict) else None


def _block_end(lines: list[str], start: int) -> int:
    """One past the last non-blank line of the table that starts at lines[start]."""
    end = start + 1
    while end < len(lines) and not _SECTION.match(lines[end]):
        end += 1
    while end > start + 1 and not lines[end - 1].strip():
        end -= 1
    return end


def _parent_span(lines: list[str]) -> tuple[int, int] | None:
    """Line span of [mcp_servers.cascadeur-plus], without trailing blank lines."""
    for index, line in enumerate(lines):
        if _TABLE.match(line):
            return index, _block_end(lines, index)
    return None


def _our_spans(lines: list[str]) -> list[tuple[int, int]]:
    """Every line span that defines mcp_servers.cascadeur-plus or one of its subtables."""
    spans = []
    index = 0
    while index < len(lines):
        if _TABLE.match(lines[index]) or _SUBTABLE.match(lines[index]):
            end = _block_end(lines, index)
            spans.append((index, end))
            index = end
        else:
            index += 1
    return spans


def _has_inline_servers(lines: list[str]) -> bool:
    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith("#") or _SECTION.match(line):
            continue
        if _INLINE_SERVERS.match(line):
            return True
    return False


def _value_end(lines: list[str], start: int) -> int:
    """Index one past the last line of the (possibly multi-line) value on lines[start]."""
    index = start
    depth = 0
    quote = ""
    escaped = False
    while index < len(lines):
        line = lines[index]
        position = line.find("=") + 1 if index == start else 0
        while position < len(line):
            char = line[position]
            if quote:
                if quote in ('"', "'"):
                    if escaped:
                        escaped = False
                    elif char == "\\" and quote == '"':
                        escaped = True
                    elif char == quote:
                        quote = ""
                elif line.startswith(quote, position):
                    quote = ""
                    position += 3
                    continue
            elif char == '"':
                if line.startswith('"""', position):
                    quote = '"""'
                    position += 3
                    continue
                quote = '"'
            elif char == "'":
                if line.startswith("'''", position):
                    quote = "'''"
                    position += 3
                    continue
                quote = "'"
            elif char == "#":
                break
            elif char in "[{":
                depth += 1
            elif char in "]}":
                depth -= 1
            position += 1
        index += 1
        if not quote and depth <= 0:
            return index
    return len(lines)


def _replace_keys(block: list[str], launch: Launch) -> list[str]:
    """Rewrite command/args inside an existing table, keeping env, comments and other keys."""
    command = f"command = {_toml_string(launch.command)}\n"
    args = f"args = [{', '.join(_toml_string(arg) for arg in launch.args)}]\n"
    lines = [block[0]]
    seen = set()
    index = 1
    while index < len(block):
        match = _KEY.match(block[index])
        if match:
            key = match.group(2)
            lines.append(match.group(1) + (command if key == "command" else args))
            seen.add(key)
            index = _value_end(block, index)
        else:
            lines.append(block[index])
            index += 1
    missing = [line for key, line in (("command", command), ("args", args)) if key not in seen]
    if missing:
        lines[1:1] = missing
    return lines


def toml_add(text: str | None, path: Path, launch: Launch) -> str:
    body = text or ""
    data = _load_toml(path, text)
    entry = _toml_entry(data)
    if isinstance(entry, dict):
        if entry.get("command") == launch.command and entry.get("args") == list(launch.args):
            return body
        lines = body.splitlines(keepends=True)
        span = _parent_span(lines)
        if span is None:
            raise CliError(
                f"{path} defines mcp_servers.{SERVER_NAME} without a [{SERVER_NAME}] table "
                "(for example as an inline table); edit that entry by hand or run uninstall first"
            )
        start, end = span
        after = "".join(lines[:start] + _replace_keys(lines[start:end], launch) + lines[end:])
        _validate_toml(path, after)
        return after
    lines = body.splitlines(keepends=True)
    if _has_inline_servers(lines):
        raise CliError(
            f"{path} defines mcp_servers as an inline table; add a separate "
            f"[mcp_servers.{SERVER_NAME}] table by hand (this tool will not rewrite it)"
        )
    block = toml_block(launch)
    if not body:
        after = block
    else:
        normalized = body if body.endswith("\n") else body + "\n"
        if not normalized.endswith("\n\n"):
            normalized += "\n"
        after = normalized + block
    _validate_toml(path, after)
    return after


def toml_remove(text: str | None, path: Path) -> str:
    body = text or ""
    data = _load_toml(path, text)
    if _toml_entry(data) is None:
        return body
    lines = body.splitlines(keepends=True)
    spans = _our_spans(lines)
    if not spans:
        raise CliError(f"{path} defines mcp_servers.{SERVER_NAME} as an inline table; remove that entry by hand")
    for start, end in reversed(spans):
        if start > 0 and not lines[start - 1].strip():
            start -= 1
        del lines[start:end]
    remaining = "".join(lines)
    if remaining:
        remaining = remaining.lstrip("\n")
        if not remaining.endswith("\n"):
            remaining += "\n"
    _validate_toml(path, remaining)
    return remaining


# ---------------------------------------------------------------------------
# Claude Code (its own CLI owns the file)
# ---------------------------------------------------------------------------


def _claude_servers(path: Path) -> tuple[dict | None, str]:
    """(user-scope mcpServers mapping, "exists" or "missing")."""
    source = read_source(path)
    if source.text is None:
        return None, "missing"
    if not source.text.strip():
        return {}, "exists"
    try:
        data = json.loads(source.text)
    except json.JSONDecodeError as exc:
        raise CliError(f"{path} is not valid JSON ({exc}); fix it before registering Claude Code") from exc
    if not isinstance(data, dict):
        raise CliError(f"{path} must contain a JSON object")
    servers = data.get("mcpServers")
    if servers is None:
        return {}, "exists"
    if not isinstance(servers, dict):
        raise CliError(f'{path}: "mcpServers" is not a JSON object')
    return servers, "exists"


def _claude_exe() -> str:
    return which("claude") or "claude"


def _claude_plan(launch: Launch | None, installed: bool, removing: bool) -> Plan:
    path = claude_config()
    servers, state = _claude_servers(path)
    existing = servers.get(SERVER_NAME) if servers is not None else None
    existing = existing if isinstance(existing, dict) else None
    note = "Claude Code manages its own config file; it is copied before the claude CLI runs"
    if state == "missing":
        note = (
            "the config file does not exist yet, so it cannot be backed up; apply stops before "
            "running the claude CLI until Claude Code has created it (start Claude Code once, or set CLAUDE_CONFIG_DIR)"
        )
    if removing:
        status = "remove" if existing is not None else "unchanged"
        if status == "unchanged":
            note = (
                f"no {SERVER_NAME} entry in {path}; nothing to remove"
                if state == "exists"
                else "the config file does not exist, so there is nothing to back up or remove"
            )
        return Plan(
            key="claude-code",
            label=CLIENT_LABELS["claude-code"],
            kind="command",
            installed=installed,
            status=status,
            path=path,
            argv=[_claude_exe(), "mcp", "remove", "--scope", "user", SERVER_NAME],
            note=note,
            config_state=state,
            existing=existing,
        )
    assert launch is not None  # setup always has one; uninstall returns above
    desired = {"command": launch.command, "args": list(launch.args)}
    same = (
        existing is not None and existing.get("command") == launch.command and existing.get("args") == list(launch.args)
    )
    if existing is None:
        status = "add"
    elif same:
        status = "unchanged"
        note = f"{SERVER_NAME} is already registered with this command"
    else:
        status = "update"
    return Plan(
        key="claude-code",
        label=CLIENT_LABELS["claude-code"],
        kind="command",
        installed=installed,
        status=status,
        path=path,
        argv=[_claude_exe(), "mcp", "add", "--scope", "user", SERVER_NAME, "--", *launch.argv()],
        note=note,
        config_state=state,
        existing=existing,
        desired=desired,
        replace_argv=[_claude_exe(), "mcp", "remove", "--scope", "user", SERVER_NAME] if status == "update" else None,
    )


# ---------------------------------------------------------------------------
# Plans
# ---------------------------------------------------------------------------


def _client_path(key: str) -> Path:
    return {"codex": codex_config(), "cursor": cursor_config(), "vscode": vscode_config()}[key]


def plan_setup(key: str, launch: Launch) -> Plan:
    label = CLIENT_LABELS[key]
    installed = is_installed(key)
    if key == "claude-code":
        return _claude_plan(launch, installed, removing=False)
    path = _client_path(key)
    source = read_source(path)
    after = toml_add(source.text, path, launch) if key == "codex" else json_add(source.text, path, key, launch)
    if source.text is None:
        status = "add"
    elif after == source.text:
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
        before=source.text,
        after=after,
        bom=source.bom,
        newline=source.newline,
    )


def plan_uninstall(key: str) -> Plan:
    label = CLIENT_LABELS[key]
    installed = is_installed(key)
    if key == "claude-code":
        return _claude_plan(None, installed, removing=True)
    path = _client_path(key)
    source = read_source(path)
    if source.text is None:
        return Plan(
            key=key,
            label=label,
            kind="file",
            installed=installed,
            status="unchanged",
            path=path,
            note="no config file",
        )
    after = toml_remove(source.text, path) if key == "codex" else json_remove(source.text, path, key)
    return Plan(
        key=key,
        label=label,
        kind="file",
        installed=installed,
        status="unchanged" if after == source.text else "remove",
        path=path,
        before=source.text,
        after=after,
        bom=source.bom,
        newline=source.newline,
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


def _brief(value: dict | None) -> str:
    if value is None:
        return "none"
    return json.dumps(value, ensure_ascii=False)


def describe(plan: Plan) -> str:
    lines = [f"{plan.label} ({plan.key})", f"  detected: {'yes' if plan.installed else 'no'}"]
    if plan.kind == "command":
        state = "exists" if plan.config_state == "exists" else "not present"
        lines.append(f"  config: {plan.path} ({state}, user scope)")
        if plan.config_state == "missing":
            lines.append("  current entry: unknown (file not found; apply will stop before running the claude CLI)")
        else:
            lines.append(f"  current entry: {_brief(plan.existing)}")
        if plan.changes and plan.desired is not None:
            lines.append(f"  will become:   {_brief(plan.desired)}")
        elif plan.changes:
            lines.append("  will become:   none (registration removed)")
        if plan.changes:
            labels = {"add": "add", "update": "update (replace the existing registration)", "remove": "remove"}
            lines.append(f"  change: {labels[plan.status]}")
            if plan.replace_argv:
                lines.append(f"  commands: {format_argv(plan.replace_argv)}")
                lines.append(f"            {format_argv(plan.argv or [])}")
            else:
                lines.append(f"  command: {format_argv(plan.argv or [])}")
            lines.append(f"  backup: a copy of {plan.path} is written beside it before anything changes")
        else:
            lines.append("  change: none")
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
    try:
        shutil.copy2(path, target)
    except OSError as exc:
        raise CliError(f"could not back up {path}: {exc}") from exc
    return target


def _not_registered(output: str) -> bool:
    lowered = output.lower()
    return any(marker in lowered for marker in ("no mcp server", "not found", "does not exist"))


def apply_plan(plan: Plan) -> str:
    """Apply one plan and return a one-line note; raises CliError when it fails."""
    if plan.kind == "command":
        assert plan.argv is not None  # plans always carry their command
        saved = backup(plan.path)
        if saved is None:
            raise CliError(
                f"{plan.path} was not found, so it could not be backed up; {plan.label} was not changed. "
                "Start Claude Code once (or set CLAUDE_CONFIG_DIR) so its config file exists, then retry."
            )
        commands = ([plan.replace_argv] if plan.replace_argv else []) + [plan.argv]
        notes = []
        for argv in commands:
            try:
                result = run_command(argv)
            except FileNotFoundError as exc:
                raise CliError(f"could not run {format_argv(argv)}: {exc}") from exc
            output = ((result.stdout or "") + (result.stderr or "")).strip()
            if result.returncode != 0:
                if argv is plan.replace_argv and _not_registered(output):
                    continue
                raise CliError(f"{format_argv(argv)} failed ({result.returncode}): {output or 'no output'}")
            line = format_argv(argv)
            if output:
                line += f" -> {output.splitlines()[0]}"
            notes.append(line)
        return f"{plan.label}: " + "; ".join(notes) + f" (backup: {saved})"
    assert plan.path is not None and plan.after is not None  # plans always carry a path and content
    saved = backup(plan.path)
    try:
        plan.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = plan.path.with_name(f"{plan.path.name}.cascadeur-mcp-plus.tmp")
        temporary.write_text(
            plan.after,
            encoding="utf-8-sig" if plan.bom else "utf-8",
            newline=plan.newline,
        )
        os.replace(temporary, plan.path)
    except OSError as exc:
        raise CliError(f"could not write {plan.path}: {exc}") from exc
    note = f"{plan.label}: wrote {plan.path}"
    if saved is not None:
        note += f" (backup: {saved})"
    return note
