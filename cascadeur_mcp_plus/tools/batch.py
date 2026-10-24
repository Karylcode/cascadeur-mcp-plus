"""batch: run several tool calls in one request and return every result (T41).

A batch is a thin loop over the same code path a direct tools/call takes, so every sub-call
keeps its own GUI dispatch, timeout, settle wait, error conversion and undo step. The module
runs its handler on the HTTP thread (like other orchestration tools) and asks the server to
execute each sub-call there. The whole batch shares one time budget, checked before each
sub-call, so a long batch stops while the proxy is still waiting instead of editing the scene
after the caller has already given up.
"""

from __future__ import annotations

import json
import time
import traceback

from ..common import fail, ok
from ._schema import schema

TOOL_NAME = "batch"
DEFAULT_TIMEOUT = 110.0
MAX_TIMEOUT = 600.0
DEFAULT_CALL_TIMEOUT = 60.0


def _call_tool(name: str, arguments: dict) -> dict:
    """Run one sub-call through the server and return its raw MCP content result.

    Imported lazily: server.py imports the tool registry while starting up, so a module-level
    "from .. import server" would close the import cycle.
    """
    from .. import server

    return server.call_tool(name, arguments)


def _has_tool(name: str) -> bool:
    """Ask the server whether a tool with this name is registered."""
    from .. import server

    return server.has_tool(name)


def _now() -> float:
    """Monotonic clock; a function so tests can advance time without sleeping."""
    return time.monotonic()


def _batch_timeout(timeout):
    """Return the validated whole-batch time budget, or None when timeout is not a positive number."""
    if timeout is None:
        return DEFAULT_TIMEOUT
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        return None
    value = float(timeout)
    if value <= 0:
        return None
    return min(value, MAX_TIMEOUT)


def _call_timeout(arguments: dict) -> float:
    """Seconds a sub-call may wait, mirroring server._call_tool's timeout_arg handling."""
    try:
        return min(float(arguments.get("timeout") or DEFAULT_CALL_TIMEOUT), MAX_TIMEOUT)
    except (TypeError, ValueError):
        return DEFAULT_CALL_TIMEOUT


def _not_run(calls: list, start: int) -> list:
    """Describe the calls that did not start, so the caller knows exactly what is still pending."""
    return [{"index": index, "tool": calls[index]["tool"]} for index in range(start, len(calls))]


def _parse_text(text: str):
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return text


def _unwrap(raw) -> tuple[bool, object]:
    """Turn one raw MCP content result into (succeeded, JSON-friendly payload)."""
    if not isinstance(raw, dict):
        return True, raw
    content = raw.get("content") or []
    texts = [item.get("text", "") for item in content if isinstance(item, dict) and item.get("type") == "text"]
    images = [item for item in content if isinstance(item, dict) and item.get("type") == "image"]
    text = " ".join(part for part in texts if part).strip()
    if raw.get("isError"):
        parsed = _parse_text(text)
        if isinstance(parsed, dict):
            return False, parsed
        return False, {"error": text or "The tool failed without a message."}
    if images:
        # An image content item cannot be nested inside batch's text result; keep its metadata.
        return True, {"images": len(images), "meta": _parse_text(text) if text else None}
    if len(texts) == 1:
        return True, _parse_text(texts[0])
    if text:
        return True, {"text": text}
    return True, None


def _message(payload) -> str:
    """One-line reason for the batch-level error; the full payload stays in results[i]."""
    if isinstance(payload, dict):
        text = str(payload.get("error") or payload.get("hint") or json.dumps(payload, ensure_ascii=False))
    else:
        text = str(payload)
    lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
    if not lines:
        return "the tool failed"
    # run_python and failed callbacks report a full traceback; its last line is the exception.
    return lines[-1] if lines[0].startswith("Traceback") else lines[0]


def _validate(calls) -> dict | None:
    """Check the whole list before running anything, so a nested/short call list runs nothing."""
    if not isinstance(calls, list):
        return fail(
            f"'calls' must be a list of {{tool, arguments}} objects (got {type(calls).__name__}).",
            hint='Example: [{"tool": "get_scene_info", "arguments": {}}].',
        )
    for index, call in enumerate(calls):
        if not isinstance(call, dict):
            return fail(f"calls[{index}] must be an object with a 'tool' field (got {type(call).__name__}).")
        name = call.get("tool")
        if not isinstance(name, str) or not name.strip():
            return fail(f"calls[{index}] needs a non-empty string 'tool'.")
        if name == TOOL_NAME:
            return fail(
                f"calls[{index}] calls batch again: nested batch calls are not allowed.",
                hint="Put every step in one calls list instead of nesting batch.",
            )
        arguments = call.get("arguments")
        if arguments is not None and not isinstance(arguments, dict):
            return fail(f"calls[{index}].arguments must be an object (got {type(arguments).__name__}).")
    return None


def batch(calls: list, stop_on_error: bool = True, timeout=None) -> dict:
    """Run calls in order within one time budget; return each result and what did not run."""
    if not isinstance(stop_on_error, bool):
        return fail(
            f"'stop_on_error' must be true or false (got {stop_on_error!r}).",
            hint="The default is true: the batch stops at the first failed call.",
        )
    budget = _batch_timeout(timeout)
    if budget is None:
        return fail(
            f"'timeout' must be a positive number of seconds (got {timeout!r}).",
            hint=f"The whole batch defaults to {DEFAULT_TIMEOUT:g} s and is capped at {MAX_TIMEOUT:g} s.",
        )
    invalid = _validate(calls)
    if invalid is not None:
        return invalid

    started = _now()
    results = []
    failed = []
    for index, call in enumerate(calls):
        name = call["tool"]
        arguments = call.get("arguments") or {}
        if not _has_tool(name):
            # Check the name up front: a KeyError raised inside a tool must keep its traceback.
            succeeded = False
            payload = {
                "error": f"Unknown tool: {name}",
                "hint": "Use tools/list to see the available tool names.",
            }
        else:
            remaining = budget - (_now() - started)
            required = _call_timeout(arguments)
            if remaining < required:
                return fail(
                    f"batch did not start call {index} ({name}): {max(0.0, remaining):.1f} s of the "
                    f"{budget:g} s batch timeout left, but the call may take up to {required:g} s "
                    "(its own timeout, or the 60 s default).",
                    hint="Increase 'timeout' (max 600 s), give the sub-call a smaller 'timeout', or split "
                    "the batch. 'results' has the completed calls and 'not_run' lists the calls that did "
                    "not start.",
                    stopped_at=index,
                    not_run=_not_run(calls, index),
                    results=results,
                )
            try:
                succeeded, payload = _unwrap(_call_tool(name, arguments))
            except Exception:
                succeeded = False
                payload = {"error": traceback.format_exc()}
        results.append({"index": index, "tool": name, "ok": succeeded, "result": payload})
        if not succeeded:
            failed.append(index)
            if stop_on_error:
                return fail(
                    f"batch stopped at call {index} ({name}): {_message(payload)}",
                    hint=f"results[{index}] has the full error. Fix it, or set stop_on_error=false "
                    "to run the remaining calls anyway.",
                    stopped_at=index,
                    results=results,
                )

    if failed:
        positions = ", ".join(str(index) for index in failed)
        return fail(
            f"{len(failed)} of {len(calls)} calls failed (at {positions}); all calls ran because stop_on_error=false.",
            hint="results[i].ok and results[i].result show what each call returned.",
            failed=failed,
            results=results,
        )
    return ok(count=len(calls), results=results)


TOOLS = [
    {
        "name": "batch",
        "description": (
            "Run several tool calls in order and return each result with one request. "
            "Use it for repetitive workflows such as seeking a frame, setting a pose and adding a key, "
            "instead of one MCP round trip per step. Every call runs exactly like a direct tools/call, so a "
            "scene change is wrapped by that tool in its own undo step: one Ctrl+Z undoes one call, not the "
            "whole batch. Calls run in order; stop_on_error=true (default) stops at the first failure and "
            "reports its index, and stop_on_error=false runs the remaining calls. Nested batch calls are "
            "rejected. Background job tools return their usual job_id instead of waiting. "
            "The whole batch shares a time budget: 'timeout' seconds (default 110, capped at 600). Before "
            "each call the batch compares the remaining budget with that call's own timeout (or 60 s when "
            "it has none); if the call cannot fit, it does not start and the result lists the completed "
            "calls in 'results' plus 'not_run'. A call that already started is not interrupted. "
            "Returns {ok, count, results: [{index, tool, ok, result}]}; when a call fails or the budget "
            "runs out, ok is false and the completed results are still included."
        ),
        "inputSchema": schema(
            {
                "calls": {
                    "type": "array",
                    "description": "Tool calls in execution order.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "tool": {
                                "type": "string",
                                "description": "Tool name to call (cannot be 'batch').",
                            },
                            "arguments": {
                                "type": "object",
                                "description": "Arguments for that tool (default {}).",
                            },
                        },
                        "required": ["tool"],
                    },
                },
                "stop_on_error": {
                    "type": "boolean",
                    "description": "Stop at the first failed call and return the completed results "
                    "(default true); false runs the remaining calls too.",
                },
                "timeout": {
                    "type": "number",
                    "description": "Time budget for the whole batch in seconds (default 110, max 600). "
                    "Before each call the remaining budget is compared with that call's own timeout "
                    "(or 60 s when it has none); calls that cannot fit are returned in 'not_run'.",
                },
            },
            ["calls"],
        ),
        "requires": [
            "csc.Guid",
            "csc.math.OrthogonalTransform",
            "csc.math.Quaternion",
            "csc.math.Rotation",
            "csc.math.ScaledTransform",
            "csc.model.ObjectId",
        ],
        "handler": batch,
    },
]
