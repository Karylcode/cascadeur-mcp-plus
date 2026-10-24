"""Read-only diagnostics for the doctor command.

It checks the Cascadeur install, who answers on the script-server and MCP Plus ports, the
auto-start script, the versions and the detected MCP clients. It never starts, restarts or
closes Cascadeur or MCP Plus; a server that is down is a warning, not a failure.
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from . import PACKAGE_ROOT, runtime_dir, version
from . import clients


def plus_url() -> str:
    return os.environ.get("CASCADEUR_PLUS_URL", "http://127.0.0.1:48766")


def official_url() -> str:
    return os.environ.get("CASCADEUR_OFFICIAL_URL", "http://127.0.0.1:8765")


@dataclass
class Check:
    name: str
    level: str  # "ok", "warn" or "fail"
    detail: str
    hint: str = ""


@dataclass
class Probe:
    kind: str  # "plus", "official", "closed" or "other"
    detail: str = ""
    health: dict | None = None


def _get_json(url: str, timeout: float = 3.0):
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        text = response.read().decode("utf-8", errors="replace")
    return json.loads(text) if text else None


def probe(base: str) -> Probe:
    """Who answers on this port: MCP Plus, Cascadeur's script server, something else, or nobody."""
    try:
        payload = _get_json(f"{base}/health")
    except urllib.error.HTTPError as exc:
        return Probe("other", f"HTTP {exc.code} from a program that is not Cascadeur")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return Probe("closed", str(getattr(exc, "reason", exc)))
    except ValueError as exc:  # JSONDecodeError and friends
        return Probe("other", f"answered, but did not return JSON ({exc})")
    if not isinstance(payload, dict):
        return Probe("other", "answered /health without a JSON object")
    if payload.get("server") == "cascadeur-mcp-plus":
        return Probe("plus", health=payload)
    if "idle_subscription" in payload and "message_subscription" in payload:
        return Probe("official", health=payload)
    return Probe("other", "answered /health, but is not a Cascadeur server")


def _port(url: str) -> int:
    return urllib.parse.urlsplit(url).port or 0


def port_owner(port: int) -> str | None:
    """Best-effort 'PID 1234 (program.exe)' for a listening port (Windows only)."""
    if os.name != "nt" or not port:
        return None
    try:
        output = subprocess.run(["netstat", "-ano"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    for line in output.splitlines():
        parts = line.split()
        if len(parts) < 5 or parts[0].upper() != "TCP" or parts[3].upper() != "LISTENING":
            continue
        if not parts[1].endswith(f":{port}"):
            continue
        pid = parts[4]
        name = _process_name(pid)
        return f"PID {pid}" + (f" ({name})" if name else "")
    return None


def _process_name(pid: str) -> str | None:
    try:
        output = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    first = output.splitlines()[0] if output.splitlines() else ""
    name = first.split(",")[0].strip('"').strip()
    return name or None


def cascadeur_install() -> str | None:
    explicit = os.environ.get("CASCADEUR_EXE") or os.environ.get("CASCADEUR_DIR")
    if explicit:
        path = Path(explicit)
        candidate = path / "cascadeur.exe" if path.is_dir() else path
        if candidate.is_file():
            return str(candidate)
    found = clients.which("cascadeur")
    if found:
        return found
    candidates = []
    for env in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        base = os.environ.get(env)
        if not base:
            continue
        candidates.append(Path(base) / "Cascadeur" / "cascadeur.exe")
        candidates.append(Path(base) / "Programs" / "Cascadeur" / "cascadeur.exe")
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return None


def collect() -> list[Check]:
    checks: list[Check] = []
    package_version = version()
    runtime = runtime_dir()
    proxy_module = Path(PACKAGE_ROOT) / "cascadeur_mcp_plus_cli" / "proxy.py"
    if runtime is None:
        checks.append(
            Check(
                "package",
                "fail",
                f"{package_version} at {PACKAGE_ROOT}; no start_mcp_plus.py and cascadeur_mcp_plus/ next to it",
                "Reinstall cascadeur-mcp-plus; the wheel must contain cascadeur_mcp_plus_runtime/.",
            )
        )
    else:
        required = (
            Path(runtime) / "start_mcp_plus.py",
            Path(runtime) / "cascadeur_mcp_plus" / "__init__.py",
            Path(runtime) / "cascadeur_mcp_plus" / "version.py",
            proxy_module,
        )
        missing = [str(path) for path in required if not path.is_file()]
        detail = f"{package_version} at {PACKAGE_ROOT}; runtime {runtime}"
        if missing:
            detail += f"; missing {', '.join(missing)}"
        checks.append(
            Check(
                "package",
                "fail" if missing else "ok",
                detail,
                "Reinstall cascadeur-mcp-plus; the wheel must contain proxy.py, start_mcp_plus.py "
                "and cascadeur_mcp_plus/."
                if missing
                else "",
            )
        )
        if os.path.basename(runtime) == "cascadeur_mcp_plus_runtime":
            runtime_detail = f"Cascadeur adds only {runtime} to sys.path (never site-packages)"
        else:
            runtime_detail = f"source checkout: Cascadeur adds the repository root {runtime} to sys.path"
        checks.append(Check("runtime", "ok", runtime_detail))

    python_info = f"{platform.python_version()} ({sys.executable})"
    checks.append(
        Check(
            "Python",
            "ok" if sys.version_info >= (3, 11) else "fail",
            python_info,
            "" if sys.version_info >= (3, 11) else "The proxy needs Python 3.11 or newer.",
        )
    )

    install = cascadeur_install()
    if install:
        checks.append(Check("Cascadeur", "ok", install))
    else:
        checks.append(
            Check(
                "Cascadeur",
                "fail",
                "not found",
                "Install Cascadeur 2026.2+, or set CASCADEUR_EXE to cascadeur.exe.",
            )
        )

    official = probe(official_url())
    if official.kind == "official":
        health = official.health or {}
        checks.append(
            Check(
                "official script server",
                "ok",
                f"{official_url()} up (Cascadeur {health.get('version', '?')}, {health.get('queued', 0)} queued)",
            )
        )
    elif official.kind == "closed":
        checks.append(
            Check(
                "official script server",
                "warn",
                f"{official_url()} not running",
                "Open Cascadeur and click Scripts > MCP > Start script server.",
            )
        )
    else:
        owner = port_owner(_port(official_url()))
        checks.append(
            Check(
                "official script server",
                "fail",
                f"{official_url()} is used by another program" + (f": {owner}" if owner else ""),
                "Free the port (netstat -ano | findstr :8765) or move the other program; "
                "Cascadeur cannot start its script server while that port is taken.",
            )
        )

    plus = probe(plus_url())
    if plus.kind == "plus":
        health = plus.health or {}
        detail = f"{plus_url()} up (MCP Plus {health.get('version', '?')}, {health.get('queued', 0)} queued)"
        if health.get("version") and package_version != "unknown" and health.get("version") != package_version:
            checks.append(
                Check(
                    "MCP Plus",
                    "warn",
                    detail + f"; this install is {package_version}",
                    "Restart Cascadeur MCP Plus from this checkout so the in-app server and the proxy match.",
                )
            )
        else:
            checks.append(Check("MCP Plus", "ok", detail))
    elif plus.kind == "closed":
        checks.append(
            Check(
                "MCP Plus",
                "warn",
                f"{plus_url()} not running",
                "It starts automatically on the first tool call while Cascadeur's script server is up.",
            )
        )
    else:
        owner = port_owner(_port(plus_url()))
        checks.append(
            Check(
                "MCP Plus",
                "fail",
                f"{plus_url()} is used by another program" + (f": {owner}" if owner else ""),
                "Free the port, or point CASCADEUR_PLUS_URL (in the proxy's environment) at another one.",
            )
        )

    start = Path(runtime) / "start_mcp_plus.py" if runtime else None
    if start is not None and start.is_file():
        checks.append(Check("auto-start script", "ok", str(start)))
    else:
        checks.append(
            Check(
                "auto-start script",
                "fail",
                f"{start or 'start_mcp_plus.py'} is missing",
                "Reinstall cascadeur-mcp-plus so Cascadeur can start the server from the installed path.",
            )
        )

    detected = [clients.CLIENT_LABELS[key] for key in clients.CLIENT_KEYS if clients.is_installed(key)]
    if detected:
        checks.append(
            Check("MCP clients", "ok", ", ".join(detected) + " detected", "Register with: cascadeur-mcp-plus setup")
        )
    else:
        checks.append(
            Check(
                "MCP clients",
                "warn",
                "none detected",
                "Install Claude Code, Codex, Cursor or VS Code, then run cascadeur-mcp-plus setup.",
            )
        )

    launch = clients.launch_command()
    checks.append(
        Check(
            "setup command",
            "ok",
            clients.format_argv(launch.argv()) + (f" ({launch.note})" if launch.note else ""),
        )
    )
    return checks


def run(json_output: bool = False) -> int:
    checks = collect()
    problems = [check for check in checks if check.level == "fail"]
    warnings = [check for check in checks if check.level == "warn"]
    if json_output:
        payload = {
            "ok": not problems,
            "version": version(),
            "root": PACKAGE_ROOT,
            "runtime": runtime_dir(),
            "python": platform.python_version(),
            "urls": {"plus": plus_url(), "official": official_url()},
            "checks": [
                {"name": check.name, "level": check.level, "detail": check.detail, "hint": check.hint}
                for check in checks
            ],
            "problems": len(problems),
            "warnings": len(warnings),
        }
        print(json.dumps(payload, ensure_ascii=False, indent=1))
        return 1 if problems else 0
    labels = {"ok": "OK  ", "warn": "WARN", "fail": "FAIL"}
    print(f"Cascadeur MCP Plus doctor ({version()})")
    for check in checks:
        print(f"[{labels[check.level]}] {check.name}: {check.detail}")
        if check.hint:
            print(f"       {check.hint}")
    print()
    ok_count = len(checks) - len(warnings) - len(problems)
    print(f"{len(checks)} checks: {ok_count} ok, {len(warnings)} warnings, {len(problems)} problems")
    if problems:
        print("Fix the FAIL lines above, then run doctor again.")
    return 1 if problems else 0
