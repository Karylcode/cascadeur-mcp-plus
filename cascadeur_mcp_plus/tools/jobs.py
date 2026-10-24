"""Inspect, wait for, list and cancel background jobs (T03)."""

from __future__ import annotations

from .. import common, jobs
from ..errors import ToolError
from ._schema import schema


def _get_job(job_id: str, wait_s: float = 0):
    try:
        job = jobs.get(job_id, wait_s)
    except ToolError as error:
        return common.fail(
            error,
            hint="list_jobs shows the recent job ids; a job started before a server restart is reported as lost.",
        )
    return common.ok(**job)


def _list_jobs(limit: int = 20):
    try:
        items = jobs.recent(limit)
    except ToolError as error:
        return common.fail(error)
    return common.ok(count=len(items), jobs=items)


def _cancel_job(job_id: str):
    try:
        job = jobs.cancel(job_id)
    except ToolError as error:
        return common.fail(error, hint="Only a running job can be cancelled; get_job shows its status.")
    return common.ok(**job, cancelled=True)


TOOLS = [
    {
        "name": "get_job",
        "description": "Return the status, progress, result or error of a background job. "
        "Use it with the job_id a background tool returned: status is running, done, failed, cancelled or lost, "
        "progress is 0..1 (or null when unknown), and a failed job carries error plus traceback. "
        "wait_s waits on the HTTP thread for up to 60 s without blocking Cascadeur's UI.",
        "inputSchema": schema(
            {
                "job_id": {"type": "string", "description": "Job id returned by a background tool, e.g. 'job-3'."},
                "wait_s": {
                    "type": "number",
                    "description": "Seconds to wait for the job to finish (default 0, capped at 60).",
                },
            },
            ["job_id"],
        ),
        "requires": [],
        "handler": _get_job,
    },
    {
        "name": "list_jobs",
        "description": (
            "List recent background jobs, newest first, with their kind, status and progress. Use it to find a "
            "job id, or to check what is still running after a restart. Returns jobs with their ids, kinds, "
            "statuses and progress (limit caps the list)."
        ),
        "inputSchema": schema(
            {
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of jobs to return (default 20, capped at 100).",
                }
            }
        ),
        "requires": [],
        "handler": _list_jobs,
    },
    {
        "name": "cancel_job",
        "description": (
            "Stop a running background job and mark it cancelled. Use it when a job is stuck or no longer "
            "needed; it calls the tool's own cancel hook (for example the Stop button of a render dialog), so "
            "the server is left without running work or an open modal dialog. Returns the job's final status."
        ),
        "inputSchema": schema(
            {"job_id": {"type": "string", "description": "Job id returned by a background tool."}},
            ["job_id"],
        ),
        "requires": [],
        "fn": _cancel_job,
    },
]
