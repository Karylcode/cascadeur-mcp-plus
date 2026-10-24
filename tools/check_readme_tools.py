#!/usr/bin/env python3
"""Compare the tool tables in the READMEs with the server's tools (T56).

Three lists of tool names are compared:

* the registry, read offline from cascadeur_mcp_plus/tools (no Cascadeur, PySide6 or csc needed);
* the live tools/list of a running MCP Plus (POST <url>/mcp), unless --offline is given;
* the tool tables in README.md and README.zh-TW.md: the table rows under the "Tools" section whose
  first cell is one backticked tool name.

Every tool must appear exactly once in each README, and the two READMEs must list the same tools in
the same order. The exit status is 0 only when every comparison has zero differences.

Run from anywhere:
    python tools/check_readme_tools.py                 registry and live tools/list
    python tools/check_readme_tools.py --offline       registry and READMEs only
    python tools/check_readme_tools.py --url http://127.0.0.1:48766
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import textwrap
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
READMES = ("README.md", "README.zh-TW.md")
TOOLS_HEADINGS = ("## Tools", "## 工具")
DEFAULT_URL = "http://127.0.0.1:48766"
ROW = re.compile(r"^\|\s*`([a-z][a-z0-9_]*)`\s*\|")

# Runs in a fresh interpreter. The tool modules import Cascadeur's own packages when they load,
# so those are replaced by stand-ins that accept any attribute access.
_REGISTRY_SCRIPT = textwrap.dedent(
    """
    import importlib.machinery
    import json
    import sys
    import types
    from unittest import mock

    STUBBED = {"csc", "PySide6", "shiboken6", "numpy"}

    class _Stub(types.ModuleType):
        def __getattr__(self, name):
            if name.startswith("__"):
                raise AttributeError(name)
            return mock.MagicMock(name=f"{self.__name__}.{name}")

    class _Finder:
        def find_spec(self, fullname, path, target=None):
            if fullname.split(".")[0] in STUBBED:
                return importlib.machinery.ModuleSpec(fullname, self, is_package=True)
            return None

        def create_module(self, spec):
            module = _Stub(spec.name)
            module.__path__ = []
            return module

        def exec_module(self, module):
            pass

    sys.meta_path.insert(0, _Finder())
    sys.path.insert(0, sys.argv[1])
    from cascadeur_mcp_plus.tools import load_tools

    print(json.dumps([tool["name"] for tool in load_tools()]))
    """
)


def registry_names(root: str = ROOT) -> list[str]:
    """Tool names from load_tools(), in registry order, read without Cascadeur."""
    completed = subprocess.run(
        [sys.executable, "-I", "-c", _REGISTRY_SCRIPT, root],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=300,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError("could not load the tool registry:\n" + completed.stderr[-2000:])
    return json.loads(completed.stdout.strip().splitlines()[-1])


def live_names(url: str = DEFAULT_URL) -> list[str]:
    """Tool names from a running MCP Plus: POST tools/list to <url>/mcp."""
    payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    request = urllib.request.Request(
        url.rstrip("/") + "/mcp",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        body = json.loads(response.read().decode("utf-8"))
    return [tool["name"] for tool in body["result"]["tools"]]


def readme_names(path: str) -> list[str]:
    """Tool names in the tool tables of one README, in document order (duplicates kept)."""
    names: list[str] = []
    in_tools = False
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            if line.startswith("## "):
                in_tools = line.strip() in TOOLS_HEADINGS
                continue
            if in_tools:
                match = ROW.match(line)
                if match:
                    names.append(match.group(1))
    return names


def differences(expected: list[str], found: list[str], label: str) -> list[str]:
    """Human-readable problems: duplicates, names missing from found, and names found but not expected."""
    problems = []
    for name in sorted({name for name in found if found.count(name) > 1}):
        problems.append(f"{label}: {name} appears more than once")
    missing = sorted(set(expected) - set(found))
    if missing:
        problems.append(f"{label}: missing {', '.join(missing)}")
    extra = sorted(set(found) - set(expected))
    if extra:
        problems.append(f"{label}: not a tool of the server: {', '.join(extra)}")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check the README tool tables against the server's tools.")
    parser.add_argument("--offline", action="store_true", help="skip the live tools/list check")
    parser.add_argument("--url", default=DEFAULT_URL, help="MCP Plus address (default: %(default)s)")
    args = parser.parse_args(argv)

    problems: list[str] = []
    registry = registry_names(ROOT)
    print(f"registry (offline): {len(registry)} tools")
    if len(set(registry)) != len(registry):
        problems.append("registry: duplicate tool names")

    if not args.offline:
        try:
            live = live_names(args.url)
        except (OSError, ValueError, KeyError) as error:
            problems.append(
                f"live tools/list at {args.url} is unreachable ({error}); start Cascadeur with MCP Plus or pass --offline"
            )
        else:
            print(f"live tools/list at {args.url}: {len(live)} tools")
            problems += differences(registry, live, "live tools/list")

    tables = {}
    for name in READMES:
        tables[name] = readme_names(os.path.join(ROOT, name))
        print(f"{name}: {len(tables[name])} table rows")
        problems += differences(registry, tables[name], name)
    if tables[READMES[0]] != tables[READMES[1]]:
        problems.append(f"{READMES[0]} and {READMES[1]} do not list the same tools in the same order")

    for problem in problems:
        print("DIFFERENCE: " + problem)
    if problems:
        print(f"{len(problems)} difference(s)")
        return 1
    print("no differences")
    return 0


if __name__ == "__main__":
    sys.exit(main())
