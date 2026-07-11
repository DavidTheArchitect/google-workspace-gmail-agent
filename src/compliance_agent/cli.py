"""Deterministic command-line planning and audit utilities."""

import argparse
import asyncio
import importlib.metadata
import json
import platform
import sys
from collections.abc import Sequence
from pathlib import Path
from uuid import UUID

from pydantic import ValidationError

from compliance_agent.application.planning_service import (
    direct_add_plan,
    direct_list_plan,
    direct_remove_entries_plan,
    direct_remove_rule_plan,
    direct_set_notice_plan,
)
from compliance_agent.application.retention_service import AuditRetentionService
from compliance_agent.audit.export import export_redacted
from compliance_agent.audit.manifest import RunManifest, verify_manifest
from compliance_agent.audit.writer import verify_event_chain
from compliance_agent.exceptions import ComplianceAgentError
from compliance_agent.infrastructure.clock import SystemClock
from compliance_agent.llm.planner import build_planner
from compliance_agent.schemas.plan import TaskPlan
from compliance_agent.schemas.resources import AddressEntry
from compliance_agent.settings import Settings
from compliance_agent.version import __version__


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI without any auto-approval option."""

    parser = argparse.ArgumentParser(
        prog="compliance-agent",
        description=(
            "Plan and audit fail-closed Gmail blocked-sender changes. Live writes remain gated "
            "on supervised UI contract acceptance."
        ),
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("version", help="show exact runtime component versions")
    commands.add_parser("check-config", help="validate CA_ environment settings")

    natural = commands.add_parser("plan", help="create a typed plan with local Ollama")
    natural.add_argument("request")

    block = commands.add_parser("block", help="construct deterministic blocked-entry plans")
    block_commands = block.add_subparsers(dest="block_command", required=True)
    add = block_commands.add_parser("add", help="plan entry additions")
    _add_entry_arguments(add)
    add.add_argument("--notice")
    add.add_argument("--rule-id", type=UUID)
    remove = block_commands.add_parser("remove", help="plan exact-target entry removals")
    _add_entry_arguments(remove)
    remove.add_argument("--rule-id", type=UUID, required=True)
    block_commands.add_parser("list", help="plan a read-only list operation")

    rule = commands.add_parser("rule", help="construct deterministic owned-rule plans")
    rule_commands = rule.add_subparsers(dest="rule_command", required=True)
    notice = rule_commands.add_parser("set-notice", help="plan a rule-wide notice update")
    notice.add_argument("--rule-id", type=UUID, required=True)
    notice.add_argument("--notice", required=True)
    remove_rule = rule_commands.add_parser("remove", help="plan owned-rule removal")
    remove_rule.add_argument("--rule-id", type=UUID, required=True)
    remove_rule.add_argument("--remove-owned-address-list", action="store_true")

    validate = commands.add_parser("validate-plan", help="validate a TaskPlan JSON file")
    validate.add_argument("path", type=Path)

    audit = commands.add_parser("audit", help="audit integrity and redacted export utilities")
    audit_commands = audit.add_subparsers(dest="audit_command", required=True)
    verify = audit_commands.add_parser("verify", help="verify a run manifest")
    verify.add_argument("run_directory", type=Path)
    export = audit_commands.add_parser("export-redacted", help="create a shareable text export")
    export.add_argument("run_directory", type=Path)
    export.add_argument("destination", type=Path)
    prune = audit_commands.add_parser(
        "prune",
        help="list expired audit runs; delete only with --apply",
    )
    prune.add_argument("--apply", action="store_true")
    return parser


def run(arguments: Sequence[str] | None = None) -> int:  # noqa: PLR0911
    """Execute one command and return a process exit code."""

    parser = build_parser()
    args = parser.parse_args(arguments)
    try:
        if args.command == "version":
            _print_json(_version_data())
            return 0
        if args.command == "check-config":
            settings = Settings()
            _print_json(_safe_settings(settings))
            return 0
        if args.command == "plan":
            return asyncio.run(_run_natural_language_plan(args.request))
        if args.command == "block":
            plan = _block_plan(args)
            _print_plan(plan)
            return 0
        if args.command == "rule":
            plan = _rule_plan(args)
            _print_plan(plan)
            return 0
        if args.command == "validate-plan":
            plan = TaskPlan.model_validate_json(args.path.read_text(encoding="utf-8"))
            _print_plan(plan)
            return 0
        if args.command == "audit":
            return _run_audit(args)
    except (ComplianceAgentError, OSError, UnicodeError, ValidationError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    parser.error("unknown command")
    return 2  # type: ignore[unreachable]  # argparse.error terminates the process.


def main() -> None:
    """Console-script entry point."""

    raise SystemExit(run())


async def _run_natural_language_plan(request: str) -> int:
    settings = Settings()
    result = await build_planner(settings).plan(request)
    _print_plan(result.plan)
    return 0


def _block_plan(args: argparse.Namespace) -> TaskPlan:
    if args.block_command == "list":
        return direct_list_plan()
    entries = _entries(args.email, args.domain)
    if not entries:
        message = "provide at least one --email or --domain"
        raise ValueError(message)
    if args.block_command == "add":
        return direct_add_plan(entries, args.notice, args.rule_id)
    return direct_remove_entries_plan(entries, args.rule_id)


def _rule_plan(args: argparse.Namespace) -> TaskPlan:
    if args.rule_command == "set-notice":
        return direct_set_notice_plan(args.rule_id, args.notice)
    return direct_remove_rule_plan(
        args.rule_id,
        remove_owned_address_list=args.remove_owned_address_list,
    )


def _entries(emails: list[str], domains: list[str]) -> tuple[AddressEntry, ...]:
    return tuple(
        [AddressEntry(kind="email", value=value) for value in emails]
        + [AddressEntry(kind="domain", value=value) for value in domains]
    )


def _add_entry_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--email", action="append", default=[])
    parser.add_argument("--domain", action="append", default=[])


def _run_audit(args: argparse.Namespace) -> int:
    if args.audit_command == "export-redacted":
        exported = export_redacted(args.run_directory, args.destination)
        print(exported)
        return 0
    if args.audit_command == "prune":
        settings = Settings()
        service = AuditRetentionService(
            settings.audit_dir,
            SystemClock(),
            settings.audit_retention_days,
        )
        candidates = service.find_expired()
        deleted = service.delete_expired(candidates) if args.apply else ()
        _print_json(
            {
                "applied": args.apply,
                "candidate_count": len(candidates),
                "candidates": [str(candidate.path) for candidate in candidates],
                "deleted": [str(path) for path in deleted],
            }
        )
        return 0
    manifest_path = args.run_directory / "manifest.json"
    manifest = RunManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    mismatches = verify_manifest(args.run_directory, manifest)
    event_errors = verify_event_chain(args.run_directory / "run.jsonl")
    _print_json(
        {
            "valid": not mismatches and not event_errors,
            "mismatches": list(mismatches),
            "event_errors": list(event_errors),
        }
    )
    return 1 if mismatches or event_errors else 0


def _print_plan(plan: TaskPlan) -> None:
    print(plan.model_dump_json(indent=2))


def _print_json(value: object) -> None:
    print(json.dumps(value, indent=2, sort_keys=True, default=str))


def _version_data() -> dict[str, str]:
    package_names = ("agent-framework-core", "playwright", "pydantic", "openai")
    versions = {name: importlib.metadata.version(name) for name in package_names}
    versions.update({"compliance-agent": __version__, "python": platform.python_version()})
    return versions


def _safe_settings(settings: Settings) -> dict[str, object]:
    return {
        "valid": True,
        "dry_run": settings.dry_run,
        "plan_only": settings.plan_only,
        "headless": settings.headless,
        "profile_dir": str(settings.profile_dir),
        "audit_dir": str(settings.audit_dir),
        "state_dir": str(settings.state_dir),
        "expected_admin_configured": bool(settings.expected_admin_email),
        "expected_workspace_configured": bool(settings.expected_workspace_domain),
    }


if __name__ == "__main__":
    main()
