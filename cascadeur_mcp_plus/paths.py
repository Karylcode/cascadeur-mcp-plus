"""Cascadeur's paths on Windows, macOS and Linux (T55).

Only a few paths differ between the platforms the in-app server runs on: Cascadeur's per-user
data folder (settings and logs), its log file and its installation directory. This module
resolves them from sys.platform at call time, so no Win32 or macOS-only code is imported on
another platform, and every location can be overridden with an environment variable when a
different layout is needed:

* CASCADEUR_DATA: Cascadeur's per-user data folder
* CASCADEUR_LOG: the log file itself
* CASCADEUR_HOME, CASCADEUR_DIR: the installation directory

The Windows layout is the one verified on a real installation (%LOCALAPPDATA%\\Nekki
Limited\\Cascadeur). The macOS and Linux defaults follow the same Qt
QStandardPaths::AppDataLocation convention that the Windows folder matches, and the
installation candidates are best guesses (Cascadeur for Linux is a folder the user unpacks).
None of the macOS/Linux paths has been verified on a real installation yet, so CASCADEUR_DATA,
CASCADEUR_LOG and CASCADEUR_HOME take precedence when a default is wrong.

Installation folders are tried in this order: the explicit CASCADEUR_HOME/CASCADEUR_DIR
overrides, the folders of the Cascadeur that is running this code (its csc package, the
executable and sys.prefix), and finally the platform's usual install folders. The running
program must win: with two Cascadeur versions installed, its stubs, templates and resources are
the ones that belong to the scene.

Standard library only: the CLI package cannot import this module (it lives inside the runtime
that Cascadeur loads), so it keeps its own copy of the same rules in cascadeur_mcp_plus_cli.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ORG = "Nekki Limited"
APP = "Cascadeur"
LOG_NAME = "cascadeur_log.log"


def is_windows() -> bool:
    return sys.platform == "win32"


def is_macos() -> bool:
    return sys.platform == "darwin"


def _env_path(*names: str) -> Path | None:
    for name in names:
        value = os.environ.get(name)
        if value:
            return Path(value).expanduser()
    return None


def local_app_data() -> Path:
    """The per-user application-data root that holds Cascadeur's own folder.

    Windows: %LOCALAPPDATA% (or ~/AppData/Local)
    macOS:   ~/Library/Application Support
    Linux:   $XDG_DATA_HOME (or ~/.local/share)

    Cascadeur keeps everything in <this folder>/Nekki Limited/Cascadeur; callers that know the
    org/app layout themselves (the mocap model, for example) use this root.
    """
    if is_windows():
        return _env_path("LOCALAPPDATA") or Path.home() / "AppData" / "Local"
    if is_macos():
        return Path.home() / "Library" / "Application Support"
    return _env_path("XDG_DATA_HOME") or Path.home() / ".local" / "share"


def data_dir() -> Path:
    """Cascadeur's per-user data folder. settings.json and the log folder live here.

    Windows: %LOCALAPPDATA%\\Nekki Limited\\Cascadeur, the folder Cascadeur itself uses.
    macOS:   ~/Library/Application Support/Nekki Limited/Cascadeur
    Linux:   $XDG_DATA_HOME/Nekki Limited/Cascadeur (usually ~/.local/share/...)
    CASCADEUR_DATA overrides all three.
    """
    override = _env_path("CASCADEUR_DATA")
    if override is not None:
        return override
    return local_app_data() / ORG / APP


def log_path() -> str:
    """Cascadeur's log file, the one the log-reading tools and edit() tail.

    CASCADEUR_LOG names the file itself (useful when logs are enabled at another location);
    otherwise it is "logs/cascadeur_log.log" inside data_dir().
    """
    override = _env_path("CASCADEUR_LOG")
    if override is not None:
        return str(override)
    return str(data_dir() / "logs" / LOG_NAME)


def running_install_dirs() -> list[Path]:
    """Installation folders of the Cascadeur that is running this code, most specific first.

    csc's own file comes first (it sits inside the installation on every platform), then the
    executable's folder and sys.prefix. Inside an AppImage, a macOS .app bundle or the Windows
    install tree these all point into the same installation; the callers that look for
    resources/ walk upwards, so either of them is enough.
    """
    candidates: list[Path] = []
    try:
        import csc

        file = getattr(csc, "__file__", None)
    except Exception:
        file = None
    if file:
        candidates.append(Path(file).resolve().parent)
    executable = getattr(sys, "executable", "")
    if executable:
        candidates.append(Path(executable).resolve().parent)
    prefix = getattr(sys, "prefix", "")
    if prefix:
        candidates.append(Path(prefix).resolve())
    unique: list[Path] = []
    for candidate in candidates:
        if candidate not in unique:
            unique.append(candidate)
    return unique


def _platform_install_dirs() -> list[Path]:
    """The platform's usual Cascadeur install folders, used only when nothing better matches."""
    candidates: list[Path] = []
    if is_windows():
        for variable in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
            base = os.environ.get(variable)
            if base:
                candidates.append(Path(base) / APP)
                candidates.append(Path(base) / "Programs" / APP)
    elif is_macos():
        candidates.append(Path("/Applications") / f"{APP}.app" / "Contents")
        candidates.append(Path("/Applications") / f"{APP}.app" / "Contents" / "MacOS")
        candidates.append(Path.home() / "Applications" / f"{APP}.app" / "Contents")
    else:
        candidates.append(Path("/opt") / APP.lower())
        candidates.append(Path("/opt") / APP)
        candidates.append(Path("/usr/local") / APP)
        candidates.append(Path.home() / APP)
    return candidates


def install_dirs() -> list[Path]:
    """Candidate Cascadeur installation folders, in the order they should be tried.

    CASCADEUR_HOME and CASCADEUR_DIR (explicit overrides) come first, then the running
    program's own folders (running_install_dirs), and the platform's usual install folders
    last. The running program must beat the platform defaults: with two Cascadeur versions
    installed, its stubs and templates are the ones that belong to the open scene (T55-fix).
    """
    candidates: list[Path] = []
    candidates.extend(path for path in (_env_path("CASCADEUR_HOME"), _env_path("CASCADEUR_DIR")) if path)
    candidates.extend(running_install_dirs())
    candidates.extend(_platform_install_dirs())
    unique: list[Path] = []
    for candidate in candidates:
        if candidate not in unique:
            unique.append(candidate)
    return unique
