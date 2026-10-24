"""Search Cascadeur's bundled csc type stubs and official Python scripts.

The index is read from the files shipped with Cascadeur (the install root is derived from the
csc module, or set with CASCADEUR_DIR), built on the first call and cached in memory.
Nothing here touches Qt or the scene, so both tools run on the HTTP thread (gui: False).
"""

from __future__ import annotations

import ast
import importlib
import os
import re
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from ._schema import schema

SOURCE_GROUPS = ("stubs", "pycsc", "commands", "samples", "scripts", "rig_mode")
_PYTHON_GROUPS = ("pycsc", "commands", "samples", "scripts", "rig_mode")
_SOURCE_ALIASES = {
    "stub": "stubs",
    "stubs": "stubs",
    "pycsc": "pycsc",
    "commands": "commands",
    "command": "commands",
    "samples": "samples",
    "sample": "samples",
    "scripts": "scripts",
    "script": "scripts",
    "rig_mode": "rig_mode",
    "rigmode": "rig_mode",
}
_SOURCE_RANK = {name: rank for rank, name in enumerate(SOURCE_GROUPS)}
_DEF_KINDS = ("class", "function", "method", "attribute")
_MAX_LIMIT = 100
_NONE_MEMBER = "__stub_None__"
_BUILTINS = {
    "abs",
    "all",
    "any",
    "bool",
    "bytes",
    "callable",
    "dict",
    "dir",
    "enumerate",
    "filter",
    "float",
    "format",
    "getattr",
    "hasattr",
    "id",
    "input",
    "int",
    "isinstance",
    "issubclass",
    "iter",
    "len",
    "list",
    "map",
    "max",
    "min",
    "next",
    "object",
    "open",
    "ord",
    "print",
    "range",
    "repr",
    "reversed",
    "round",
    "set",
    "setattr",
    "sorted",
    "str",
    "sum",
    "super",
    "tuple",
    "type",
    "zip",
}


@dataclass(slots=True)
class Entry:
    """One searchable symbol, definition or usage."""

    symbol: str
    name: str
    kind: str
    path: str
    line: int
    signature: str
    doc: str
    source: str
    parent: str = ""
    body_start: int = 0
    body_end: int = 0
    fields: list[dict] = field(default_factory=list)
    methods: list[dict] = field(default_factory=list)
    name_l: str = ""
    symbol_l: str = ""
    signature_l: str = ""
    doc_l: str = ""

    def __post_init__(self) -> None:
        self.name_l = self.name.lower()
        self.symbol_l = self.symbol.lower()
        self.signature_l = self.signature.lower()
        self.doc_l = self.doc.lower()


@dataclass(slots=True)
class Index:
    root: str
    entries: list[Entry]
    by_symbol: dict[str, list[Entry]]
    files: int
    skipped: list[dict]
    built_at: float


class _ApiError(Exception):
    """A configuration or input problem that should become an ok:false result."""

    def __init__(self, message: str, hint: str | None = None) -> None:
        super().__init__(message)
        self.hint = hint


_CACHE: dict[str, Index] = {}
_CACHE_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# Locating and reading the shipped sources
# ---------------------------------------------------------------------------


def _normalise_root(raw: object) -> str | None:
    """Return the Cascadeur install root containing resources/scripts/stubs/csc."""
    if not raw:
        return None
    try:
        path = Path(str(raw))
    except (OSError, TypeError, ValueError):
        return None
    if path.is_file():
        path = path.parent
    for candidate in (path, *path.parents):
        if (candidate / "resources" / "scripts" / "stubs" / "csc").is_dir():
            return str(candidate)
    return None


def _install_dir() -> str:
    """Find Cascadeur's install directory without importing GUI modules."""
    override = os.environ.get("CASCADEUR_DIR")
    if override:
        root = _normalise_root(override)
        if root is None:
            raise _ApiError(
                f"CASCADEUR_DIR={override!r} is not a Cascadeur install folder "
                "(it must contain resources/scripts/stubs/csc).",
                "Point CASCADEUR_DIR at the folder that contains resources/scripts/stubs/csc.",
            )
        return root

    from .. import paths

    candidates: list[object] = []
    try:
        csc = importlib.import_module("csc")
        candidates.append(getattr(csc, "__file__", None))
    except Exception:
        pass
    # The running program's own folders (csc, this interpreter, sys.prefix) come before the
    # platform's usual install folders, so a second installed version cannot supply the stubs
    # for the scene that is open (T55, T55-fix). CASCADEUR_HOME/CASCADEUR_DIR stay first.
    folders = paths.install_dirs()
    candidates.extend(folders)
    candidates.extend([sys.executable, sys.prefix])
    for raw in candidates:
        root = _normalise_root(raw)
        if root:
            return root
    example = folders[0] if folders else "the folder that holds resources"
    raise _ApiError(
        "Could not find the Cascadeur installation (looked next to csc, at CASCADEUR_HOME/CASCADEUR_DIR "
        "and in the platform's usual install folders).",
        f"Set CASCADEUR_DIR to the Cascadeur install folder, for example {example}.",
    )


def _walk_files(directory: Path, suffix: str) -> list[Path]:
    files: list[Path] = []
    if not directory.is_dir():
        return files
    for base, dirs, names in os.walk(directory):
        dirs[:] = sorted(d for d in dirs if d != "__pycache__")
        for name in sorted(names):
            if name.endswith(suffix):
                files.append(Path(base) / name)
    return files


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _module_name(path: Path, root: Path) -> str:
    parts = list(path.relative_to(root).with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _display_name(name: str) -> str:
    return "None" if name == _NONE_MEMBER else name


def _stub_source(path: Path) -> str:
    """pybind11 writes an enum member literally named None, which ast cannot parse."""
    text = _read_text(path)
    return re.sub(r"(?m)^(\s*)None(\s*:)", rf"\1{_NONE_MEMBER}\2", text)


def _docstring(node: ast.AST) -> str:
    value = ast.get_docstring(node, clean=True)
    return value or ""


def _first_line(text: str) -> str:
    for line in text.splitlines():
        if line.strip():
            return line.strip()
    return ""


def _function_signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    returns = f" -> {ast.unparse(node.returns)}" if node.returns is not None else ""
    return f"{prefix} {node.name}({ast.unparse(node.args)}){returns}"


def _class_signature(node: ast.ClassDef) -> str:
    bases = [ast.unparse(base) for base in node.bases]
    bases.extend(ast.unparse(keyword.value) for keyword in node.keywords)
    suffix = f"({', '.join(bases)})" if bases else ""
    return f"class {node.name}{suffix}"


def _body_start(node: ast.AST) -> int:
    line = getattr(node, "lineno", 1)
    for decorator in getattr(node, "decorator_list", []):
        line = min(line, decorator.lineno)
    return line


def _body_end(node: ast.AST) -> int:
    return getattr(node, "end_lineno", None) or getattr(node, "lineno", 1)


def _member(node: ast.FunctionDef | ast.AsyncFunctionDef) -> dict:
    doc = _docstring(node)
    return {
        "name": _display_name(node.name),
        "signature": _function_signature(node),
        "doc": _first_line(doc),
        "line": node.lineno,
    }


# ---------------------------------------------------------------------------
# Stub index
# ---------------------------------------------------------------------------


def _stub_class_entries(node: ast.ClassDef, module: str, source: str, entries: list[Entry]) -> Entry:
    symbol = f"{module}.{_display_name(node.name)}"
    doc = _docstring(node)
    entry = Entry(
        symbol=symbol,
        name=_display_name(node.name),
        kind="class",
        path=source,
        line=node.lineno,
        signature=_class_signature(node),
        doc=doc,
        source="stubs",
        parent=module,
        body_start=_body_start(node),
        body_end=_body_end(node),
    )
    entries.append(entry)
    for child in node.body:
        if isinstance(child, ast.AnnAssign) and isinstance(child.target, ast.Name):
            name = _display_name(child.target.id)
            if name.startswith("_"):
                continue
            annotation = ast.unparse(child.annotation) if child.annotation is not None else ""
            signature = f"{name}: {annotation}"
            entry.fields.append({"name": name, "signature": signature, "line": child.lineno})
            entries.append(
                Entry(
                    symbol=f"{symbol}.{name}",
                    name=name,
                    kind="attribute",
                    path=source,
                    line=child.lineno,
                    signature=signature,
                    doc="",
                    source="stubs",
                    parent=symbol,
                    body_start=child.lineno,
                    body_end=child.end_lineno or child.lineno,
                )
            )
        elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            name = _display_name(child.name)
            entry.methods.append(_member(child))
            entries.append(
                Entry(
                    symbol=f"{symbol}.{name}",
                    name=name,
                    kind="method",
                    path=source,
                    line=child.lineno,
                    signature=_function_signature(child),
                    doc=_docstring(child),
                    source="stubs",
                    parent=symbol,
                    body_start=_body_start(child),
                    body_end=_body_end(child),
                )
            )
        elif isinstance(child, ast.ClassDef):
            _stub_class_entries(child, symbol, source, entries)
    return entry


def _stub_entries(path: Path, module: str) -> list[Entry]:
    tree = ast.parse(_stub_source(path), filename=str(path))
    entries: list[Entry] = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            _stub_class_entries(node, module, str(path), entries)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            name = _display_name(node.name)
            if name.startswith("_"):
                continue
            entries.append(
                Entry(
                    symbol=f"{module}.{name}",
                    name=name,
                    kind="function",
                    path=str(path),
                    line=node.lineno,
                    signature=_function_signature(node),
                    doc=_docstring(node),
                    source="stubs",
                    parent=module,
                    body_start=_body_start(node),
                    body_end=_body_end(node),
                )
            )
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            name = _display_name(node.target.id)
            if name.startswith("_"):
                continue
            annotation = ast.unparse(node.annotation) if node.annotation is not None else ""
            entries.append(
                Entry(
                    symbol=f"{module}.{name}",
                    name=name,
                    kind="attribute",
                    path=str(path),
                    line=node.lineno,
                    signature=f"{name}: {annotation}",
                    doc="",
                    source="stubs",
                    parent=module,
                    body_start=node.lineno,
                    body_end=node.end_lineno or node.lineno,
                )
            )
    return entries


# ---------------------------------------------------------------------------
# Python source index (pycsc and official commands/samples/scripts/rig_mode)
# ---------------------------------------------------------------------------


def _parents(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    result: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            result[child] = node
    return result


def _scope_symbol(node: ast.AST | None, parents: dict[ast.AST, ast.AST], module: str) -> str:
    names: list[str] = []
    current = node
    while current is not None:
        if isinstance(current, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            names.append(_display_name(current.name))
        current = parents.get(current)
    names.reverse()
    return ".".join([module, *names]) if names else module


def _python_entries(path: Path, module: str, source: str) -> list[Entry]:
    text = _read_text(path)
    tree = ast.parse(text, filename=str(path))
    parents = _parents(tree)
    entries: list[Entry] = []
    lines = text.splitlines()

    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            parent_symbol = _scope_symbol(parents.get(node), parents, module)
            name = _display_name(node.name)
            entry = Entry(
                symbol=f"{parent_symbol}.{name}",
                name=name,
                kind="class",
                path=str(path),
                line=node.lineno,
                signature=_class_signature(node),
                doc=_docstring(node),
                source=source,
                parent=parent_symbol,
                body_start=_body_start(node),
                body_end=_body_end(node),
            )
            for child in node.body:
                if isinstance(child, ast.AnnAssign) and isinstance(child.target, ast.Name):
                    field_name = _display_name(child.target.id)
                    if field_name.startswith("_"):
                        continue
                    annotation = ast.unparse(child.annotation) if child.annotation is not None else ""
                    entry.fields.append(
                        {"name": field_name, "signature": f"{field_name}: {annotation}", "line": child.lineno}
                    )
                elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    entry.methods.append(_member(child))
            entries.append(entry)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            parent_symbol = _scope_symbol(parents.get(node), parents, module)
            name = _display_name(node.name)
            kind = "method" if _inside_class(node, parents) else "function"
            entries.append(
                Entry(
                    symbol=f"{parent_symbol}.{name}",
                    name=name,
                    kind=kind,
                    path=str(path),
                    line=node.lineno,
                    signature=_function_signature(node),
                    doc=_docstring(node),
                    source=source,
                    parent=parent_symbol,
                    body_start=_body_start(node),
                    body_end=_body_end(node),
                )
            )

    seen: set[tuple[str, str, int]] = set()
    for node in ast.walk(tree):
        name = ""
        if isinstance(node, ast.Attribute):
            name = node.attr
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            name = node.func.id
        if len(name) < 3 or name.startswith("_") or name in _BUILTINS:
            continue
        scope = _scope_symbol(node, parents, module)
        key = (scope, name, node.lineno)
        if key in seen:
            continue
        seen.add(key)
        line = lines[node.lineno - 1].strip() if node.lineno <= len(lines) else ""
        entries.append(
            Entry(
                symbol=scope,
                name=name,
                kind="usage",
                path=str(path),
                line=node.lineno,
                signature=line[:200],
                doc="",
                source=source,
                parent=scope,
            )
        )
    return entries


def _inside_class(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> bool:
    current = parents.get(node)
    while current is not None:
        if isinstance(current, ast.ClassDef):
            return True
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return False
        current = parents.get(current)
    return False


# ---------------------------------------------------------------------------
# Index cache
# ---------------------------------------------------------------------------


def _build_index(root: str) -> Index:
    install = Path(root)
    scripts = install / "resources" / "scripts"
    entries: list[Entry] = []
    skipped: list[dict] = []
    files = 0

    stub_root = scripts / "stubs"
    for path in _walk_files(stub_root / "csc", ".pyi"):
        files += 1
        try:
            entries.extend(_stub_entries(path, _module_name(path, stub_root)))
        except (OSError, SyntaxError, ValueError) as exc:
            skipped.append({"file": str(path), "error": f"{type(exc).__name__}: {exc}"})

    for group in _PYTHON_GROUPS:
        group_root = scripts / "python" / group
        for path in _walk_files(group_root, ".py"):
            files += 1
            try:
                entries.extend(_python_entries(path, _module_name(path, scripts / "python"), group))
            except (OSError, SyntaxError, ValueError) as exc:
                skipped.append({"file": str(path), "error": f"{type(exc).__name__}: {exc}"})

    if not entries:
        raise _ApiError(
            f"No .pyi or .py files were found under {scripts}. "
            "Check CASCADEUR_DIR or that the Cascadeur installation is complete."
        )

    by_symbol: dict[str, list[Entry]] = {}
    for entry in entries:
        if entry.kind in _DEF_KINDS:
            by_symbol.setdefault(entry.symbol, []).append(entry)
    return Index(root=root, entries=entries, by_symbol=by_symbol, files=files, skipped=skipped, built_at=time.time())


def _get_index() -> Index:
    root = _install_dir()
    with _CACHE_LOCK:
        index = _CACHE.get(root)
        if index is None:
            index = _build_index(root)
            _CACHE[root] = index
        return index


def _clear_cache() -> None:
    """Drop the cached index (used by tests and after replacing an install in place)."""
    with _CACHE_LOCK:
        _CACHE.clear()


# ---------------------------------------------------------------------------
# Search and describe
# ---------------------------------------------------------------------------


def _fail(error: str, hint: str | None = None, **extra) -> dict:
    result = {"ok": False, "error": error}
    if hint:
        result["hint"] = hint
    result.update(extra)
    return result


def _normalise_sources(sources) -> set[str]:
    if sources is None:
        return set()
    if isinstance(sources, str):
        sources = [sources]
    if not isinstance(sources, (list, tuple)):
        raise _ApiError("sources must be a list of source groups.")
    groups: set[str] = set()
    for raw in sources:
        key = str(raw).strip().lower()
        if not key or key == "all":
            continue
        if key == "python":
            groups.update(_PYTHON_GROUPS)
            continue
        group = _SOURCE_ALIASES.get(key)
        if group is None:
            raise _ApiError(
                f"Unknown source group {raw!r}.",
                f"Use one of: {', '.join(('all', 'python', *SOURCE_GROUPS))}.",
            )
        groups.add(group)
    return groups


def _tokens(query: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", query.lower())


def _score(entry: Entry, query: str, tokens: list[str]) -> int | None:
    if entry.symbol_l == query:
        return 1100
    if entry.name_l == query:
        return 1000
    if entry.name_l.startswith(query):
        return 900
    if query in entry.name_l:
        return 850
    if query in entry.symbol_l:
        return 800
    if all(token in entry.name_l for token in tokens):
        return 750
    if all(token in entry.symbol_l for token in tokens):
        return 700
    if query in entry.signature_l:
        return 400
    if query in entry.doc_l:
        return 300
    if all(token in entry.doc_l for token in tokens):
        return 200
    return None


def api_search(query: str = "", limit: int = 20, sources=None) -> dict:
    """Search stub and script symbols; name matches rank above docstring matches."""
    text = (query or "").strip()
    if not text:
        return _fail("query must not be empty.", "Pass a class, function, method or docstring fragment.")
    try:
        count = int(limit if limit is not None else 20)
    except (TypeError, ValueError):
        return _fail("limit must be an integer.", "Use a number between 1 and 100.")
    if count < 1:
        return _fail("limit must be at least 1.", "Use 20 for the default or up to 100.")
    count = min(count, _MAX_LIMIT)
    try:
        groups = _normalise_sources(sources)
        index = _get_index()
    except _ApiError as exc:
        return _fail(str(exc), exc.hint)

    normalised = text.lower()
    tokens = _tokens(text) or [normalised]
    ranked: list[tuple[int, Entry]] = []
    for entry in index.entries:
        if groups and entry.source not in groups:
            continue
        score = _score(entry, normalised, tokens)
        if score is None:
            continue
        ranked.append((score, entry))

    ranked.sort(key=lambda pair: (-pair[0], _SOURCE_RANK[pair[1].source], pair[1].symbol, pair[1].line))

    matches: list[dict] = []
    seen: set[tuple] = set()
    for _score_value, entry in ranked:
        if entry.source == "stubs" and entry.kind in ("class", "function", "method"):
            key = (entry.symbol, "stub-definition")
        else:
            key = (entry.symbol, entry.kind, entry.path, entry.line)
        if key in seen:
            continue
        seen.add(key)
        matches.append(
            {
                "symbol": entry.symbol,
                "file": entry.path,
                "line": entry.line,
                "signature": entry.signature,
                "kind": entry.kind,
                "source": entry.source,
            }
        )

    results = matches[:count]
    result = {
        "ok": True,
        "query": text,
        "count": len(results),
        "total": len(matches),
        "results": results,
    }
    if index.skipped:
        result["skipped_files"] = index.skipped
    if not results:
        result["hint"] = "No match. Try a shorter name or a fragment of the docstring."
    return result


def _block(entry: Entry) -> str:
    lines = _read_text(Path(entry.path)).splitlines()
    start = max(entry.body_start or entry.line, 1)
    end = min(entry.body_end or entry.line, len(lines))
    return "\n".join(lines[start - 1 : end])


def _resolve_runtime(symbol: str):
    parts = symbol.split(".")
    for size in range(len(parts), 0, -1):
        try:
            obj = importlib.import_module(".".join(parts[:size]))
        except Exception:
            continue
        try:
            for part in parts[size:]:
                obj = getattr(obj, part)
        except Exception:
            return None
        return obj
    return None


def _runtime_info(entry: Entry) -> dict:
    info: dict = {"available": False}
    try:
        importlib.import_module("csc")
    except Exception as exc:
        info["error"] = f"csc is not importable: {exc}"
        return info
    obj = _resolve_runtime(entry.symbol)
    if obj is None:
        info["error"] = "the runtime object was not found"
        return info
    info["available"] = True
    if entry.kind == "class":
        stub_names = {item["name"] for item in entry.fields} | {item["name"] for item in entry.methods}
        try:
            runtime_names = set(dir(obj))
        except Exception as exc:
            info["error"] = f"dir() failed: {exc}"
            return info
        info["missing_from_stub"] = sorted(name for name in runtime_names - stub_names if not name.startswith("_"))
        info["stub_members"] = len(stub_names)
        info["runtime_members"] = len(runtime_names)
    return info


def _find_definitions(index: Index, symbol: str) -> tuple[str | None, list[Entry], list[str]]:
    exact = index.by_symbol.get(symbol)
    if exact:
        return symbol, exact, []
    lowered = symbol.lower()
    matches = [name for name in index.by_symbol if name.lower() == lowered]
    if len(matches) == 1:
        return matches[0], index.by_symbol[matches[0]], []
    if len(matches) > 1:
        return None, [], sorted(matches)
    suffix = "." + lowered
    matches = [name for name in index.by_symbol if name.lower().endswith(suffix)]
    if len(matches) == 1:
        return matches[0], index.by_symbol[matches[0]], []
    if len(matches) > 1:
        return None, [], sorted(matches)
    return None, [], []


def api_describe(symbol: str = "") -> dict:
    """Return a symbol's full stub/source block, members and runtime-only members."""
    text = (symbol or "").strip().strip("\"'")
    if not text:
        return _fail("symbol must not be empty.", "Use a name from api_search, for example csc.view.Scene.")
    try:
        index = _get_index()
    except _ApiError as exc:
        return _fail(str(exc), exc.hint)

    _, definitions, ambiguous = _find_definitions(index, text)
    if not definitions:
        result = _fail(
            f"Symbol {text!r} was not found.",
            "Run api_search first and pass one of its symbol values.",
        )
        if ambiguous:
            result["candidates"] = ambiguous[:20]
        return result

    stub_definitions = [entry for entry in definitions if entry.source == "stubs"]
    chosen = stub_definitions or definitions
    primary = chosen[0]
    result = {
        "ok": True,
        "symbol": primary.symbol,
        "kind": primary.kind,
        "file": primary.path,
        "line": primary.line,
        "signature": primary.signature,
        "docstring": primary.doc,
        "source": "\n\n".join(_block(entry) for entry in chosen),
        "source_group": primary.source,
    }
    if len(chosen) > 1:
        result["overloads"] = len(chosen)
    if primary.kind == "class":
        result["fields"] = primary.fields
        result["methods"] = primary.methods
    runtime = _runtime_info(primary) if primary.source == "stubs" else None
    if runtime is not None:
        result["runtime"] = runtime
    return result


TOOLS = [
    {
        "name": "api_search",
        "description": "Search Cascadeur's csc type stubs, pycsc wrappers and official commands/samples/scripts for classes, "
        "functions, methods and docstrings. Use it before writing run_python code when unsure of the API. "
        "Returns matching symbols with file, line and a one-line signature; name matches come before docstring matches. "
        "Read-only, needs no GUI, and builds its index once on the first call.",
        "inputSchema": schema(
            {
                "query": {"type": "string", "description": "Class, function, method or docstring fragment to search."},
                "limit": {"type": "integer", "description": "Maximum results (default 20, max 100)."},
                "sources": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["all", "python", *SOURCE_GROUPS]},
                    "description": "Optional source groups to search; default searches all of them.",
                },
            },
            ["query"],
        ),
        "requires": [],
        "fn": api_search,
        "gui": False,
    },
    {
        "name": "api_describe",
        "description": "Show the full type-stub block (signature and docstring) of a csc class, function or method, "
        "including a class's fields and method list. Use it after api_search to learn exact signatures and member names. "
        "When Cascadeur is running, also lists members that exist at runtime but are missing from the stub. Read-only.",
        "inputSchema": schema(
            {
                "symbol": {
                    "type": "string",
                    "description": "Symbol from api_search, for example csc.tools.RenderToFile.",
                },
            },
            ["symbol"],
        ),
        "requires": [],
        "fn": api_describe,
        "gui": False,
    },
]
