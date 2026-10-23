"""Undo, redo and MCP step history (T24).

Cascadeur's Python API has no undo/redo call; its stubs only show the action behind the menu
item (call_action("Scene.Undo")). The tools here drive those actions through ActionManager
instead of synthesizing Ctrl+Z, so a focused text field cannot swallow the shortcut.

Cascadeur writes every applied command and every undo/redo that really happened to its log::

    2026-10-24 01:52:15,189 Command: MCP: probe
    2026-10-24 01:52:15,534 'Undo' of command 'MCP: probe'
    2026-10-24 01:52:15,606 'Redo' of command 'MCP: probe'

That log is the source of truth for which step actually moved: undo()/redo() read the lines
Cascadeur wrote while the action ran and return the real step names. Cascadeur exposes no history
list, so get_undo_history() replays the same log and reports the steps MCP Plus produced through
common.edit() (names starting with "MCP: "), which is the fallback the ticket describes.

The log file is appended across Cascadeur launches (the file on this machine covers a whole day),
so replaying it from the start is only correct when every event that emptied an undo stack is
honoured. The tracker therefore starts over at:

* a new Cascadeur process: "Application executable path:" (also written by a duplicate instance
  that exits immediately, where dropping the record is the safe direction) and
  "auto deleting logs: finish" (only a real process start writes it);
* the memory manager clearing all history: "History will be cleared in every scene!" and
  "History was cleared because of:" followed by the "* <reason>" line;
* a new scene: "Command: Select default layer" (the startup untitled scene and every new tab);
* a loaded scene: "Command: load scene".

Each reset records its log timestamp and reason, and get_undo_history() reports them as
recorded_since / reset_reason. The log does not name the scene tab, so the record is conservative:
opening another scene starts a new record even when the previous tab still exists.

Only the standard library is imported at module import time, so tests/test_history.py can check
the parsing and tracking in plain Python; csc, PySide6 and common are imported by the functions
that need them.
"""

from __future__ import annotations

import os
import re
import threading
import time

UNDO_ACTION = "Scene.Undo"
REDO_ACTION = "Scene.Redo"
MCP_PREFIX = "MCP: "

LOAD_SCENE_COMMAND = "load scene"
NEW_SCENE_COMMAND = "Select default layer"
CLEAR_WARNING = "History will be cleared in every scene!"
CLEAR_MARKER = "History was cleared because of:"

# Log lines that mean every scene's undo history is gone. Matching is substring-based because the
# memory-manager warning carries a "WARNING [file:line]" prefix.
RESET_MARKERS = (
    ("Application executable path:", "Cascadeur started"),
    ("auto deleting logs: finish", "Cascadeur started"),
    (CLEAR_WARNING, "undo history is being cleared"),
    (CLEAR_MARKER, "undo history cleared"),
)
# Commands after which the scene's undo stack starts empty.
SCENE_RESETS = {
    LOAD_SCENE_COMMAND: "scene loaded",
    NEW_SCENE_COMMAND: "new scene created",
}

MAX_STEPS = 100  # undo()/redo() stop here; more than this is never useful in one call
MAX_LIMIT = 200  # get_undo_history() returns at most this many steps
MAX_TRACKED = 500  # tracked MCP steps kept in memory (oldest are dropped)
EVENT_TIMEOUT = 1.0  # seconds to wait for Cascadeur to log the undo/redo it just ran
POLL_INTERVAL = 0.02

_TIMESTAMP = r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}"
_LINE_RE = re.compile(rf"^(?P<timestamp>{_TIMESTAMP}) (?P<text>.*)$")
_UNDO_REDO_RE = re.compile(r"^'(?P<kind>Undo|Redo)' of command '(?P<name>.*)'$")
_COMMAND_PREFIX = "Command: "

_TRACKER = None


# ---------------------------------------------------------------------------
# Result helpers (the README's result shape; common.ok/common.fail need csc)
# ---------------------------------------------------------------------------


def _ok(**data):
    return {"ok": True, **data}


def _fail(error, hint=None, **data):
    result = {"ok": False, "error": str(error)}
    if hint:
        result["hint"] = str(hint)
    result.update(data)
    return result


# ---------------------------------------------------------------------------
# Reading Cascadeur's log
# ---------------------------------------------------------------------------


def _default_log_path():
    """Cascadeur's log path, taken from common (which knows the install layout)."""
    try:
        from .. import common
    except Exception:
        return None
    return getattr(common, "LOG_PATH", None)


def _parse_line(line):
    """Return (kind, name, timestamp) for a line that changes the undo record, or None.

    kind is "command" for "Command: <name>", "undo"/"redo" for "'Undo' of command '<name>'",
    and "reset" for a marker that clears every scene's undo stack (name is then the reset
    reason). Other log lines, tracebacks included, are ignored.
    """
    match = _LINE_RE.match(line.rstrip())
    if not match:
        return None
    timestamp, text = match.group("timestamp"), match.group("text")
    if text.startswith(_COMMAND_PREFIX):
        return "command", text[len(_COMMAND_PREFIX) :].strip(), timestamp
    match = _UNDO_REDO_RE.match(text)
    if match:
        return match.group("kind").lower(), match.group("name"), timestamp
    for marker, reason in RESET_MARKERS:
        if marker in text:
            return "reset", reason, timestamp
    return None


def _clear_reason(line):
    """Return the text of the "* <reason>" line following a clear marker, or None."""
    match = _LINE_RE.match(line.rstrip())
    if not match:
        return None
    text = match.group("text")
    if not text.startswith("* "):
        return None
    return text[2:].strip()


def _read_since(path, offset):
    """Return the complete lines written after byte offset, and the offset after them.

    A half-written last line is left for the next read, so a polling caller never parses a
    truncated command name. A missing log returns ("", offset); a log that shrank (Cascadeur
    replaced it) is read again from the beginning.
    """
    if not path:
        return "", offset
    try:
        size = os.path.getsize(path)
        if size < offset:
            offset = 0
        with open(path, "rb") as handle:
            handle.seek(offset)
            data = handle.read()
    except OSError:
        return "", offset
    end = data.rfind(b"\n")
    if end < 0:
        return "", offset
    return data[: end + 1].decode("utf-8", errors="replace"), offset + end + 1


class Tracker:
    """The undo history of MCP steps, rebuilt from Cascadeur's log.

    steps holds the "MCP: " command names in application order; position counts how many of
    them are currently applied (steps[:position]). Redo re-applies steps[position], undo removes
    steps[position - 1], because Cascadeur's undo stack is a cursor over the applied commands.
    started_at and reset_reason describe where the current record began, so callers can tell
    "no MCP steps yet" from "the record was reset by a restart or by the memory manager".
    """

    def __init__(self, path=None):
        self.path = path
        self.offset = 0
        self.steps = []
        self.position = 0
        self.started_at = None
        self.reset_reason = None
        self._awaiting_clear_reason = False
        self.lock = threading.Lock()

    def log_path(self):
        return self.path if self.path is not None else _default_log_path()

    def sync(self):
        """Apply every complete new log line and return the history events it contained."""
        with self.lock:
            return self._sync()

    def _sync(self):
        text, offset = _read_since(self.log_path(), self.offset)
        if not text:
            return []
        self.offset = offset
        events = []
        for line in text.splitlines():
            if self._awaiting_clear_reason:
                self._awaiting_clear_reason = False
                reason = _clear_reason(line)
                if reason:
                    self.reset_reason = f"undo history cleared: {reason}"
                    continue
            event = _parse_line(line)
            if event is None:
                continue
            events.append(event)
            self._apply(event)
        return events

    def snapshot(self, limit):
        with self.lock:
            self._sync()
            total = len(self.steps)
            start = max(0, total - limit)
            return {
                "steps": [
                    {
                        "index": index,
                        "name": self.steps[index],
                        "state": "applied" if index < self.position else "undone",
                    }
                    for index in range(start, total)
                ],
                "position": self.position,
                "count": total,
                "truncated": start > 0,
                "next_undo": self.steps[self.position - 1] if self.position > 0 else None,
                "next_redo": self.steps[self.position] if self.position < total else None,
                "recorded_since": self.started_at,
                "reset_reason": self.reset_reason,
                "log": self.log_path(),
                "source": "Cascadeur log, MCP steps only (common.edit)",
            }

    def assume(self, kind):
        """Move the cursor without a log line; used only when the log is unreadable."""
        with self.lock:
            if kind == "undo" and self.position > 0:
                self.position -= 1
                return self.steps[self.position]
            if kind == "redo" and self.position < len(self.steps):
                name = self.steps[self.position]
                self.position += 1
                return name
            return None

    # -- events -------------------------------------------------------------

    def _apply(self, event):
        kind, name, timestamp = event
        if kind == "reset":
            self._reset(timestamp, name)
            if name == "undo history cleared":
                # The next log line is "* <reason>"; keep it for reset_reason.
                self._awaiting_clear_reason = True
        elif kind == "command":
            self._discard_redo()
            if name in SCENE_RESETS:
                # A loaded or newly created scene starts with an empty undo stack; the log cannot
                # say which tab a command belongs to, so these are the only reliable boundaries.
                self._reset(timestamp, SCENE_RESETS[name])
            elif name.startswith(MCP_PREFIX):
                if self.started_at is None:
                    self.started_at = timestamp
                    self.reset_reason = "first MCP step in the log"
                self.steps.append(name)
                self.position += 1
                self._trim()
        elif kind == "undo":
            if name.startswith(MCP_PREFIX):
                index = self._last_applied(name)
                if index is not None:
                    self.position = index
        elif kind == "redo":
            if name.startswith(MCP_PREFIX) and self.position < len(self.steps) and self.steps[self.position] == name:
                self.position += 1

    def _reset(self, timestamp, reason):
        self.steps.clear()
        self.position = 0
        self.started_at = timestamp
        self.reset_reason = reason
        self._awaiting_clear_reason = False

    def _last_applied(self, name):
        for index in range(self.position - 1, -1, -1):
            if self.steps[index] == name:
                return index
        return None

    def _discard_redo(self):
        # A new command drops the redo branch, exactly like Cascadeur's undo stack.
        if self.position < len(self.steps):
            del self.steps[self.position :]

    def _trim(self):
        overflow = len(self.steps) - MAX_TRACKED
        if overflow > 0:
            del self.steps[:overflow]
            self.position = max(0, self.position - overflow)


def _tracker():
    global _TRACKER
    if _TRACKER is None:
        _TRACKER = Tracker()
    return _TRACKER


def reset(path=None):
    """Forget the tracked log position and start over (tests, or a replaced log file)."""
    global _TRACKER
    _TRACKER = Tracker(path)


def _log_available(tracker):
    path = tracker.log_path()
    return bool(path) and os.path.exists(path)


# ---------------------------------------------------------------------------
# Driving Cascadeur's undo/redo actions
# ---------------------------------------------------------------------------


def _call_action(key):
    """Run one Cascadeur action (GUI thread)."""
    import csc

    csc.app.get_application().get_action_manager().call_action(key)


def _run_action(key):
    """Run an action on Cascadeur's GUI thread through the dispatcher (tests replace this)."""
    from .. import dispatch

    dispatch.call_on_gui(_call_action, {"key": key}, 30.0)


def _wait_for_event(tracker, kind):
    """Poll the log until Cascadeur logs an undo/redo of the expected kind (or time runs out).

    Polling happens on the HTTP thread, never on the GUI thread, and it is what makes an empty
    undo stack distinguishable from a log line that was flushed a few milliseconds late.
    """
    deadline = time.monotonic() + EVENT_TIMEOUT
    while True:
        events = tracker.sync()
        if any(event_kind == kind for event_kind, _, _ in events):
            return events
        if time.monotonic() >= deadline:
            return events
        time.sleep(POLL_INTERVAL)


def _perform(kind, steps):
    """Undo or redo up to steps commands and return the names Cascadeur actually moved."""
    if isinstance(steps, bool):
        return _fail("steps must be a positive integer.", "Use undo() for one step, or undo(steps=3).")
    try:
        requested = int(steps)
    except (TypeError, ValueError):
        return _fail("steps must be a positive integer.", "Use undo() for one step, or undo(steps=3).")
    if requested < 1:
        return _fail("steps must be a positive integer.", "Use undo() for one step, or undo(steps=3).")
    count = min(requested, MAX_STEPS)

    action = UNDO_ACTION if kind == "undo" else REDO_ACTION
    verb = "undone" if kind == "undo" else "redone"
    tracker = _tracker()
    tracker.sync()
    names = []
    fallback = False

    for _ in range(count):
        logged = _log_available(tracker)
        try:
            _run_action(action)
        except Exception as exc:
            return _fail(
                f"Cascadeur could not run the {kind} action {action!r}.",
                hint="Check that a scene is open, that no modal dialog is blocking Cascadeur, and read get_log.",
                action=kind,
                detail=str(exc),
            )
        if logged:
            events = _wait_for_event(tracker, kind)
            matched = [name for event_kind, name, _ in events if event_kind == kind]
            if not matched:
                break
            names.extend(matched)
        else:
            name = tracker.assume(kind)
            if name is None:
                break
            names.append(name)
            fallback = True

    if not names:
        return _fail(
            f"Nothing to {kind}.",
            hint=(
                "The scene's undo stack is empty; make a change first. "
                "The tracked record starts at "
                f"{tracker.started_at or 'the start of the log'} ({tracker.reset_reason or 'no reset seen'})."
                if kind == "undo"
                else "There are no undone steps to redo; undo something first."
            ),
            action=kind,
            requested=count,
        )

    result = _ok(action=kind, requested=count, count=len(names), steps=names)
    notes = []
    if requested > MAX_STEPS:
        notes.append(f"steps is capped at {MAX_STEPS}.")
    if len(names) < count:
        notes.append(f"Only {len(names)} of {count} step(s) could be {verb}; the stack has no more.")
    if fallback:
        result["source"] = "MCP Plus's own record"
        notes.append("Cascadeur's log is unreadable, so the step names come from MCP Plus's own record.")
    if notes:
        result["hint"] = " ".join(notes)
    return result


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


def undo(steps=1):
    """Undo the last step(s) through Cascadeur's Scene.Undo action."""
    return _perform("undo", steps)


def redo(steps=1):
    """Redo the last undone step(s) through Cascadeur's Scene.Redo action."""
    return _perform("redo", steps)


def get_undo_history(limit=20):
    """Return the recent MCP steps (common.edit names) and where the record starts."""
    if isinstance(limit, bool):
        return _fail("limit must be a positive integer.", "Use 20 for the default or up to 200.")
    try:
        count = int(limit)
    except (TypeError, ValueError):
        return _fail("limit must be a positive integer.", "Use 20 for the default or up to 200.")
    if count < 1:
        return _fail("limit must be at least 1.", "Use 20 for the default or up to 200.")
    count = min(count, MAX_LIMIT)

    tracker = _tracker()
    data = tracker.snapshot(count)
    if not _log_available(tracker):
        data["warning"] = (
            "Cascadeur's log is not readable, so the history is empty or stale; "
            "check common.LOG_PATH and Cascadeur's log settings."
        )
    elif not data["steps"] and data.get("recorded_since"):
        data["note"] = (
            f"The record starts at {data['recorded_since']} ({data['reset_reason']}); "
            "MCP steps from before that are no longer in any undo stack."
        )
    return _ok(**data)


TOOLS = [
    {
        "name": "undo",
        "description": "Undo the last scene change(s) with Cascadeur's own Scene.Undo action, called through "
        "ActionManager (not a synthetic Ctrl+Z), so a focused text field cannot swallow it. "
        "Use it to back out a tool call that went wrong. Returns action, requested, count and steps: the names "
        "of the steps Cascadeur actually undid, in the order they were undone (read from its log). "
        "If the undo stack runs out, fewer steps are undone and hint says so. steps is capped at 100.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "steps": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "How many undo steps to take (default 1, max 100).",
                }
            },
            "required": [],
        },
        "handler": undo,
    },
    {
        "name": "redo",
        "description": "Redo the last undone scene change(s) with Cascadeur's own Scene.Redo action through "
        "ActionManager. Use it after undo when the undone change was the wanted one. "
        "Returns action, requested, count and steps: the names of the steps Cascadeur actually redid, in the "
        "order they were redone (read from its log). If nothing is left to redo, ok is false and hint says why. "
        "steps is capped at 100.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "steps": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "How many steps to redo (default 1, max 100).",
                }
            },
            "required": [],
        },
        "handler": redo,
    },
    {
        "name": "get_undo_history",
        "description": "List the recent steps of the current scene's undo history, oldest first, each marked applied "
        "or undone, plus the current position and where the record starts. Only steps MCP Plus produced through "
        "common.edit (names starting 'MCP: ') are listed, because Cascadeur has no Python API for its full history: "
        "the data is rebuilt from Cascadeur's log. The record starts over when Cascadeur starts, when its memory "
        "manager clears all history, and when a scene is created or loaded; recorded_since and reset_reason say "
        "which event began the current record (MCP steps from before it are not in any undo stack). position counts "
        "applied MCP steps; next_undo and next_redo name the MCP step the next undo/redo would move. "
        "limit caps the returned steps (default 20, max 200).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Maximum number of steps to return (default 20, max 200).",
                }
            },
            "required": [],
        },
        "fn": get_undo_history,
        "gui": False,
    },
]
