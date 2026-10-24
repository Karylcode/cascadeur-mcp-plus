"""Cascadeur version compatibility checks (T54).

Cascadeur renames and removes Python API and toolbar action keys between releases. Every tool
dict declares ``requires``: dotted API paths rooted at the ``csc`` module, checked with a hasattr
chain; action keys prefixed with ``action:``; and official script modules prefixed with
``module:`` (``module:rig_mode.on:run_raw`` = the module ``rig_mode.on`` defines ``run_raw``),
checked from the files on disk without importing them. For example
``"csc.tools.RenderToFile.take_image"``, ``"action:AutoPhysicsTool.Snap to Auto Physics"`` or
``"module:rig_mode.off:run"``.

The server audits every tool once at startup and keeps the report. A tool whose entries are
missing stays in tools/list with ``UNAVAILABLE_PREFIX`` in front of its description and refuses
calls with the name of the missing entry, instead of failing halfway with AttributeError. get_status
(T54) exposes the report and runs a fresh audit with force=true, which is also how a temporary
monkeypatch is detected and then cleared again.

Action keys come from two sources. Cascadeur's live QML tree lists the actions of toolbars that
exist right now, but some actions only work through ActionManager and are never in the tree
(View.Retargeting_Copy, ObjectWatchingTool.Track selection with camera), and tool toolbars are
built on demand. So a key that is not in the tree is also looked up in the installed build's own
string tables (translations, the executable and tools.dll); only a key that neither source knows
is reported as missing. When nothing can be searched (no install folder found), such a key is
reported as unverified instead of missing, so a machine with a different layout does not hide
working tools.

Standard library only, no Cascadeur imports at module scope: the server unit tests load this
module without PySide6 or csc.
"""

from __future__ import annotations

import ast
import importlib.util
import os
import sys
import time

from .version import VERSION

TESTED_CASCADEUR_VERSION = "2026.2.3"
ACTION_PREFIX = "action:"
MODULE_PREFIX = "module:"
UNAVAILABLE_PREFIX = "Unavailable in this Cascadeur version:"

# How much of the installed build is searched for action ids when the QML tree does not list
# them: the translation catalogs first, then the program and the tool library (see
# _candidate_files). The budget keeps a startup audit well under a second on a normal install.
MAX_SCAN_BYTES = 64 * 1024 * 1024

# Behaviours that were measured on TESTED_CASCADEUR_VERSION and depend on more than an API symbol:
# window layouts, action keys, prompt texts and crash workarounds. get_status returns them so an
# agent on another version knows what to re-verify first.
VERSION_DEPENDENT = [
    {
        "area": "video export",
        "note": "render_video and render_image_sequence fill Cascadeur's File.Export.Video... window because "
        "RenderToFile.play_to_video_file/play_to_images_sequence crashed Cascadeur 2026.2.3; the window's "
        "tabs, field names and buttons are version-specific.",
    },
    {
        "area": "autoposing",
        "note": "set_pose, autoposing_enable/update and lock_controllers rely on the AutoPosing.Autolock and "
        "AutoPosingTool.* action keys plus the 'Adjust AutoPosing lock state?' prompt.",
    },
    {
        "area": "animation layers",
        "note": "list_layers and the layer editors only use layers_map(), folders_map(), all_layer_ids() and "
        "header(); Viewer.item(), Folder.children_ordered() and ItemVariant.header() corrupted the heap on "
        "2026.2.3.",
    },
    {
        "area": "video mocap",
        "note": "video_mocap walks the Load video and motion capture windows (View.Bind video, View.MocapTool); "
        "Cascadeur has no Python API for it, so changed window or button names break the flow.",
    },
    {
        "area": "cycles",
        "note": "create_cycle/remove_cycles call the Timeline cycle actions; reading csc.layers.CyclesViewer "
        "crashed Cascadeur 2026.2.3.",
    },
    {
        "area": "autophysics",
        "note": "physics_* map the AutoPhysicsTool.* actions and read QML manager flags "
        "(isAutoPhysicsEnabled, isFrozenAutoPhysics, isOnlyKeyFulcrumPoints); get_physics_state reports them.",
    },
    {
        "area": "ai inbetweening",
        "note": "ai_inbetween and generate_motion call Timeline.AI.AI on selected interval and "
        "View.Inbetweening_RunMotionGeneration; the motion generator's dialogs are version-specific.",
    },
    {
        "area": "retargeting",
        "note": "retarget_animation calls View.Retargeting_Copy/View.Retargeting_Paste, which the live QML tree "
        "may not list even when the actions work.",
    },
    {
        "area": "interface tools",
        "note": "ui_*, dialogs and screenshot drive the Qt Quick object tree; renamed objectName/property values "
        "break their selectors even when every action key still exists.",
    },
]

_report: dict | None = None


# ---------------------------------------------------------------------------
# Version
# ---------------------------------------------------------------------------


def cascadeur_version() -> str | None:
    """Cascadeur's version, preferring the program version the log prints (2026.2.3).

    csc.SystemVariables.git_version() is the one that matches what users see; Qt's
    applicationVersion() and the executable's version resource both say 2026.2.0.16863 on
    Cascadeur 2026.2.3, so they are only fallbacks.
    """
    try:
        version = str(__import__("csc").SystemVariables.git_version()).strip()
        if version:
            return version
    except Exception:
        pass
    try:
        from PySide6 import QtCore

        version = QtCore.QCoreApplication.applicationVersion()
    except Exception:
        version = None
    if version:
        return str(version).strip() or None
    return _executable_version()


def _executable_version() -> str | None:
    """Version of the running process's executable on Windows (e.g. '2026.2.3'), else None."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes

        class _VersionInfo(ctypes.Structure):
            _fields_ = [
                ("signature", ctypes.c_uint32),
                ("struct_version", ctypes.c_uint32),
                ("file_version_ms", ctypes.c_uint32),
                ("file_version_ls", ctypes.c_uint32),
                ("product_version_ms", ctypes.c_uint32),
                ("product_version_ls", ctypes.c_uint32),
                ("file_flags_mask", ctypes.c_uint32),
                ("file_flags", ctypes.c_uint32),
                ("file_os", ctypes.c_uint32),
                ("file_type", ctypes.c_uint32),
                ("file_subtype", ctypes.c_uint32),
                ("file_date_ms", ctypes.c_uint32),
                ("file_date_ls", ctypes.c_uint32),
            ]

        path = ctypes.create_unicode_buffer(32768)
        if not ctypes.windll.kernel32.GetModuleFileNameW(None, path, len(path)):
            return None
        size = ctypes.windll.version.GetFileVersionInfoSizeW(path.value, None)
        if not size:
            return None
        data = ctypes.create_string_buffer(size)
        if not ctypes.windll.version.GetFileVersionInfoW(path.value, 0, size, data):
            return None
        pointer = ctypes.c_void_p()
        length = ctypes.c_uint()
        if not ctypes.windll.version.VerQueryValueW(data, "\\", ctypes.byref(pointer), ctypes.byref(length)):
            return None
        info = ctypes.cast(pointer, ctypes.POINTER(_VersionInfo)).contents
        major, minor, patch = info.file_version_ms >> 16, info.file_version_ms & 0xFFFF, info.file_version_ls >> 16
        return f"{major}.{minor}.{patch}"
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Requirement entries
# ---------------------------------------------------------------------------


def validate_requires(entries) -> list[str]:
    """Return entries as a list, raising ValueError for anything tools/list cannot check.

    An entry is an API path ("csc." + dot separated names, for example "csc.Guid" or
    "csc.tools.RenderToFile.take_image"), "action:" + an action key (an actionId such as
    "Scene.Undo"), or "module:" + an official script module with an optional function name
    ("module:rig_mode.on:run_raw"). The registry calls this for every tool that declares requires,
    so a typo fails at load time instead of silently skipping the check.
    """
    if isinstance(entries, (str, bytes)) or not isinstance(entries, (list, tuple)):
        raise ValueError(f"requires must be a list of strings (got {type(entries).__name__})")
    cleaned = []
    for entry in entries:
        if not isinstance(entry, str) or not entry.strip():
            raise ValueError(f"requires entries must be non-empty strings (got {entry!r})")
        text = entry.strip()
        if text.startswith(ACTION_PREFIX):
            if not text[len(ACTION_PREFIX) :].strip():
                raise ValueError(f"requires entry {text!r} has no action key")
        elif text.startswith(MODULE_PREFIX):
            names = text[len(MODULE_PREFIX) :].split(":")
            dotted = names[0]
            if len(names) > 2 or not dotted or any(not part.isidentifier() for part in dotted.split(".")):
                raise ValueError(
                    f"requires entry {text!r} must be 'module:<dotted name>' or 'module:<name>:<function>'"
                )
            if len(names) == 2 and not names[1].isidentifier():
                raise ValueError(f"requires entry {text!r} has a function name that is not an identifier")
        elif text.startswith("csc."):
            parts = text.split(".")
            if len(parts) < 2 or any(not part for part in parts):
                raise ValueError(
                    f"requires entry {text!r} must be a dotted csc API path, for example csc.glb.process_import"
                )
        else:
            raise ValueError(f"requires entry {text!r} must start with 'csc.', {ACTION_PREFIX!r} or {MODULE_PREFIX!r}")
        cleaned.append(text)
    return cleaned


def _split(entries) -> tuple[list[str], list[str]]:
    """(API paths, action keys) from a requires list; module entries are checked separately."""
    apis, actions = [], []
    for entry in entries or ():
        if entry.startswith(ACTION_PREFIX):
            actions.append(entry[len(ACTION_PREFIX) :])
        elif entry.startswith(MODULE_PREFIX):
            continue
        else:
            apis.append(entry)
    return apis, actions


# ---------------------------------------------------------------------------
# Script module checks (no import: importing an official script can run code)
# ---------------------------------------------------------------------------


def _script_module_file(dotted: str) -> str | None:
    """The source file of a dotted module found on sys.path, or None; nothing is imported."""
    parts = dotted.split(".")
    try:
        spec = importlib.util.find_spec(parts[0])
    except (ImportError, ValueError):
        return None
    if spec is None:
        return None
    if len(parts) == 1:
        return spec.origin if spec.origin and os.path.isfile(spec.origin) else None
    locations = list(spec.submodule_search_locations or [])
    for depth, part in enumerate(parts[1:], start=1):
        found = None
        for location in locations:
            for candidate in (os.path.join(location, part + ".py"), os.path.join(location, part, "__init__.py")):
                if os.path.isfile(candidate):
                    found = candidate
                    break
            if found:
                break
        if found is None:
            return None
        if depth == len(parts) - 1:
            return found
        if not found.endswith("__init__.py"):
            return None
        locations = [os.path.dirname(found)]
    return None


def script_module_provides(entry: str) -> bool:
    """True when 'module:<name>[:<function>]' exists in this build: the file is there and, when a
    function is named, the module source defines it at top level. The module is never imported."""
    name, _, function = entry[len(MODULE_PREFIX) :].partition(":")
    path = _script_module_file(name)
    if path is None:
        return False
    if not function:
        return True
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            tree = ast.parse(handle.read(), filename=path)
    except (OSError, SyntaxError, ValueError):
        return False
    return any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function for node in tree.body
    )


# ---------------------------------------------------------------------------
# API checks
# ---------------------------------------------------------------------------


def api_exists(path: str, module=None) -> bool:
    """True when every part of a dotted csc path exists in this Cascadeur build."""
    if not isinstance(path, str) or not path.startswith("csc."):
        return False
    if module is None:
        try:
            import csc
        except Exception:
            return False
        module = csc
    node = module
    for part in path[len("csc.") :].split("."):
        if not hasattr(node, part):
            return False
        node = getattr(node, part)
    return True


# ---------------------------------------------------------------------------
# Action checks
# ---------------------------------------------------------------------------


def interface_actions() -> set[str] | None:
    """Every actionId in Cascadeur's live QML tree, or None when the tree cannot be read."""
    try:
        from . import ui

        listing = ui.list_actions(include_hidden=True)
    except Exception:
        return None
    if not isinstance(listing, dict):
        return None
    actions = listing.get("actions")
    if not actions:
        return None
    found = set()
    for entry in actions:
        if isinstance(entry, dict) and isinstance(entry.get("actionId"), str):
            found.add(entry["actionId"])
    return found or None


def _candidate_files() -> list[str]:
    """Files whose string tables can prove an action key exists, cheapest first."""
    roots = []
    try:
        from . import paths

        roots = [str(root) for root in paths.install_dirs()]
    except Exception:
        roots = []
    translations = []
    programs = []
    for root in roots:
        folder = os.path.join(root, "resources", "translations")
        if os.path.isdir(folder):
            try:
                names = sorted(os.listdir(folder))
            except OSError:
                names = []
            translations.extend(os.path.join(folder, name) for name in names if name.endswith(".qm"))
        for name in (
            "tools.dll",
            "tools.so",
            "tools.dylib",
            "presenter_lib.dll",
            "cascadeur.exe",
            "Cascadeur.exe",
            "Cascadeur",
            "cascadeur",
        ):
            candidate = os.path.join(root, name)
            if os.path.isfile(candidate):
                programs.append(candidate)
    executable = getattr(sys, "executable", "") or ""
    if executable and os.path.isfile(executable):
        programs.insert(0, os.path.abspath(executable))
    ordered = []
    seen = set()
    for candidate in translations + programs:
        key = os.path.normcase(os.path.abspath(candidate))
        if key not in seen and os.path.isfile(candidate):
            seen.add(key)
            ordered.append(os.path.abspath(candidate))
    return ordered


def _contains(blob: bytes, text: str) -> bool:
    """True when the text appears as UTF-8 or in either UTF-16 order (Qt .qm files use UTF-16)."""
    for encoded in (text.encode("utf-8"), text.encode("utf-16-be"), text.encode("utf-16-le")):
        if encoded in blob:
            return True
    return False


def _search_build(keys) -> tuple[set[str], list[str]]:
    """Keys found in the installed build's string tables, plus the files that were searched.

    Stops as soon as every key is found or MAX_SCAN_BYTES has been read, so a healthy install
    reads the small translation catalogs and the program only.
    """
    remaining = set(keys)
    found: set[str] = set()
    scanned: list[str] = []
    total = 0
    if not remaining:
        return found, scanned
    for path in _candidate_files():
        if not remaining:
            break
        try:
            size = os.path.getsize(path)
        except OSError:
            continue
        if total + size > MAX_SCAN_BYTES:
            break
        try:
            with open(path, "rb") as handle:
                blob = handle.read()
        except OSError:
            continue
        total += len(blob)
        scanned.append(path)
        for key in list(remaining):
            if _contains(blob, key):
                found.add(key)
                remaining.discard(key)
    return found, scanned


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


def audit(tools) -> dict:
    """Check every tool's requires against this build; returns the report (no caching).

    tools is an iterable of tool dicts. API paths are checked with hasattr; action keys are
    looked up in the live QML tree and, when absent there, in the installed build's strings.
    """
    requirements = [(tool.get("name"), list(tool.get("requires") or ())) for tool in tools]
    total = sum(len(entries) for _name, entries in requirements)
    interface = interface_actions()
    wanted_actions = sorted(
        {key for _name, entries in requirements for key in _split(entries)[1]},
    )
    unresolved = [key for key in wanted_actions if interface is None or key not in interface]
    build_found, scanned = _search_build(unresolved) if unresolved else (set(), [])

    unavailable: dict[str, list[str]] = {}
    checked_actions = 0
    verified_from_interface = 0
    verified_from_build: set[str] = set()
    unverified: set[str] = set()
    for name, entries in requirements:
        missing = []
        for entry in entries:
            if entry.startswith(MODULE_PREFIX):
                if not script_module_provides(entry):
                    missing.append(entry)
                continue
            if not entry.startswith(ACTION_PREFIX):
                if not api_exists(entry):
                    missing.append(entry)
                continue
            checked_actions += 1
            key = entry[len(ACTION_PREFIX) :]
            if interface is not None and key in interface:
                verified_from_interface += 1
            elif key in build_found:
                verified_from_build.add(key)
            elif scanned:
                missing.append(entry)
            else:
                unverified.add(key)
        if missing:
            unavailable[name] = missing
    return {
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "cascadeur_version": None,  # filled by refresh()
        "tested_cascadeur_version": TESTED_CASCADEUR_VERSION,
        "tools": len(requirements),
        "requirements": total,
        "action_requirements": checked_actions,
        "unavailable": unavailable,
        "actions": {
            "interface": None if interface is None else len(interface),
            "verified_in_interface": verified_from_interface,
            "verified_in_installation": sorted(verified_from_build),
            "unverified": sorted(unverified),
            "files_scanned": scanned,
        },
    }


def refresh(tools=None, version: str | None = None) -> dict:
    """Audit tools (default: the registry's tools) and cache the report; returns the report."""
    global _report
    if tools is None:
        from .tools import load_tools

        tools = load_tools()
    tool_list = list(tools)
    if version is None:
        version = cascadeur_version()
    report = audit(tool_list)
    report["cascadeur_version"] = version
    report["version_matches_tested"] = version == TESTED_CASCADEUR_VERSION
    _report = report
    return report


def report() -> dict | None:
    """The cached report from the last refresh, or None before the first one."""
    return _report


def unavailable(name: str) -> list[str]:
    """The requires entries this build is missing for a tool (empty when it is available)."""
    cached = _report or {}
    missing = (cached.get("unavailable") or {}).get(name)
    return list(missing or [])


def describe(tool: dict) -> str:
    """The description tools/list shows: prefixed with what is missing when the tool is unavailable."""
    description = tool.get("description", "")
    missing = unavailable(tool.get("name"))
    if not missing:
        return description
    return f"{UNAVAILABLE_PREFIX} missing {', '.join(missing)}. {description}"


def refusal(name: str, missing=None) -> dict:
    """The tool result for calling an unavailable tool: names the missing requirements."""
    entries = list(missing if missing is not None else unavailable(name))
    detail = ", ".join(entries) or "an API this build does not provide"
    return {
        "ok": False,
        "error": f"{UNAVAILABLE_PREFIX} {name} needs {detail}, which this Cascadeur build does not provide.",
        "hint": "get_status lists every unavailable tool and what is missing; the other tools still work.",
        "missing": entries,
    }


def version_note() -> str:
    """One sentence about version compatibility, for get_status and the MCP instructions."""
    cached = _report or {}
    version = cached.get("cascadeur_version")
    if not version:
        return "The Cascadeur version could not be read; compatibility checks may be incomplete."
    if cached.get("version_matches_tested"):
        return f"Cascadeur {version} is the version this build was tested with."
    return (
        f"Cascadeur {version} is not the tested version ({TESTED_CASCADEUR_VERSION}); behaviour that depends on "
        "the version (video export, AutoPosing lock, animation layers, motion capture) may have changed."
    )


def instructions(text: str) -> str:
    """Append the version reminder to the MCP instructions when Cascadeur is not the tested version."""
    cached = _report or {}
    version = cached.get("cascadeur_version")
    if not version or cached.get("version_matches_tested"):
        return text
    return (
        f"{text} This Cascadeur is {version}, which this build was not tested with ({TESTED_CASCADEUR_VERSION}): "
        "version-dependent behaviour (video export, AutoPosing lock, animation layers, motion capture) may have "
        "changed, and get_status lists the tools this build had to disable."
    )


def status() -> dict:
    """The get_status payload: versions, checked tool counts and what is unavailable."""
    cached = _report or {}
    missing = cached.get("unavailable") or {}
    unavailable_list = [
        {"tool": name, "missing": list(entries), "reason": f"missing {', '.join(entries)}"}
        for name, entries in sorted(missing.items())
    ]
    return {
        "ok": True,
        "mcp_plus_version": VERSION,
        "cascadeur_version": cached.get("cascadeur_version"),
        "tested_cascadeur_version": TESTED_CASCADEUR_VERSION,
        "version_tested": bool(cached.get("version_matches_tested")),
        "version_note": version_note(),
        "tools": cached.get("tools"),
        "requirements": cached.get("requirements"),
        "checked_at": cached.get("checked_at"),
        "unavailable_count": len(unavailable_list),
        "unavailable": unavailable_list,
        "actions": cached.get("actions"),
        "version_dependent": VERSION_DEPENDENT,
    }
