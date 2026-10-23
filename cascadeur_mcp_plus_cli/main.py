"""The cascadeur-mcp-plus command line: serve, setup, doctor and uninstall.

    uvx cascadeur-mcp-plus serve          run the stdio proxy (what MCP clients start)
    uvx cascadeur-mcp-plus setup          show and apply client registrations
    uvx cascadeur-mcp-plus doctor         report Cascadeur, both ports and the versions
    uvx cascadeur-mcp-plus uninstall      show and remove the registrations

Registration changes are always printed first and written only after confirmation; every
file is backed up next to itself first. Run with --dry-run to see the changes without any
write, or with --yes to skip the prompt in scripts.
"""

from __future__ import annotations

import argparse
import sys

from . import version
from . import clients, doctor, serve


def _select(client: str) -> list[str]:
    return list(clients.CLIENT_KEYS) if client == "all" else [client]


def _print_plans(plans: list[clients.Plan]) -> None:
    for plan in plans:
        print(clients.describe(plan))
        print()


def _confirmation() -> bool:
    if not sys.stdin or not sys.stdin.isatty():
        print("No interactive terminal: re-run with --yes to apply these changes.")
        return False
    try:
        answer = input("Apply these changes? [y/N] ")
    except EOFError:
        return False
    return answer.strip().lower() in ("y", "yes")


def _targets(plans: list[clients.Plan], client: str, uninstall: bool = False) -> list[clients.Plan]:
    """Plans worth applying; with --client all, only clients that are present."""
    todo = [plan for plan in plans if plan.changes]
    if client != "all":
        return todo
    targets = []
    for plan in todo:
        present = plan.installed or (
            uninstall and plan.kind == "file" and plan.path is not None and plan.path.is_file()
        )
        if present:
            targets.append(plan)
        else:
            print(f"{plan.label}: not detected, skipped")
    return targets


def run_setup(client: str, dry_run: bool, assume_yes: bool, command_override: str | None) -> int:
    launch = clients.launch_command(command_override)
    print(f"Cascadeur MCP Plus {version()} - registering {clients.SERVER_NAME} with MCP clients")
    print(f"  clients will start: {clients.format_argv(launch.argv())}")
    if launch.note:
        print(f"  command source: {launch.note}")
    print()
    try:
        plans = [clients.plan_setup(key, launch) for key in _select(client)]
    except clients.CliError as exc:
        print(f"error: {exc}")
        return 1
    _print_plans(plans)
    todo = _targets(plans, client)
    if not dry_run and client == "claude-code" and not plans[0].installed:
        print('error: Claude Code is not installed (no "claude" on PATH); nothing was changed.')
        return 1
    if client == "all" and not any(plan.installed for plan in plans):
        print("No supported MCP client was detected; use --client <name> to write a config anyway.")
    if not todo:
        print("Nothing to do.")
        if dry_run:
            print("Dry run: no files were written and no commands were run.")
        return 0
    if dry_run:
        print("Dry run: no files were written and no commands were run.")
        return 0
    if not assume_yes and not _confirmation():
        print("Cancelled: nothing was changed.")
        return 1
    failures = 0
    for plan in todo:
        try:
            print(f"- {clients.apply_plan(plan)}")
        except (clients.CliError, OSError) as exc:
            failures += 1
            print(f"- {plan.label}: failed: {exc}")
    if failures:
        print(f"{failures} change(s) failed; see the messages above.")
        return 1
    print(f"Applied {len(todo)} change(s). Restart the MCP client so it picks up the server.")
    return 0


def run_uninstall(client: str, dry_run: bool, assume_yes: bool) -> int:
    print(f"Cascadeur MCP Plus {version()} - removing {clients.SERVER_NAME}")
    print()
    try:
        plans = [clients.plan_uninstall(key) for key in _select(client)]
    except clients.CliError as exc:
        print(f"error: {exc}")
        return 1
    _print_plans(plans)
    todo = _targets(plans, client, uninstall=True)
    if not todo:
        print("Nothing to remove.")
        if dry_run:
            print("Dry run: no files were written and no commands were run.")
        return 0
    if dry_run:
        print("Dry run: no files were written and no commands were run.")
        return 0
    if not assume_yes and not _confirmation():
        print("Cancelled: nothing was changed.")
        return 1
    failures = 0
    for plan in todo:
        try:
            print(f"- {clients.apply_plan(plan)}")
        except clients.CliError as exc:
            text = str(exc).lower()
            if plan.kind == "command" and ("no mcp server" in text or "not found" in text or "does not exist" in text):
                print(f"- {plan.label}: not registered ({exc})")
                continue
            failures += 1
            print(f"- {plan.label}: failed: {exc}")
        except OSError as exc:
            failures += 1
            print(f"- {plan.label}: failed: {exc}")
    if failures:
        print(f"{failures} change(s) failed; see the messages above.")
        return 1
    print(f"Applied {len(todo)} change(s).")
    return 0


def _add_registration_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--client",
        choices=[*clients.CLIENT_KEYS, "all"],
        default="all",
        help="which MCP client to change (default: all detected clients)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the exact changes and exit without writing files or running commands",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="apply the changes without asking for confirmation (for scripts)",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cascadeur-mcp-plus",
        description="Install, register and run the Cascadeur MCP Plus stdio proxy.",
    )
    parser.add_argument("--version", action="version", version=f"cascadeur-mcp-plus {version()}")
    sub = parser.add_subparsers(dest="command", metavar="command", required=True)

    sub.add_parser("serve", help="run the stdio MCP proxy (MCP clients start this)")

    setup_parser = sub.add_parser("setup", help="register Cascadeur MCP Plus with installed MCP clients")
    _add_registration_options(setup_parser)
    setup_parser.add_argument(
        "--command",
        dest="command_override",
        metavar="PATH",
        help="executable MCP clients should run instead of the detected one",
    )

    doctor_parser = sub.add_parser("doctor", help="report the Cascadeur install, both ports and the versions")
    doctor_parser.add_argument("--json", action="store_true", help="print machine-readable JSON")

    uninstall_parser = sub.add_parser("uninstall", help="remove the Cascadeur MCP Plus registration")
    _add_registration_options(uninstall_parser)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "serve":
        return serve.run()
    if args.command == "doctor":
        return doctor.run(json_output=args.json)
    if args.command == "setup":
        return run_setup(args.client, args.dry_run, args.yes, args.command_override)
    if args.command == "uninstall":
        return run_uninstall(args.client, args.dry_run, args.yes)
    return 2
