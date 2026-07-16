"""Protected audit boundaries for advanced Gmail UI runs."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from compliance_agent.schemas.events import AuditEvent

if TYPE_CHECKING:
    from pathlib import Path

    from compliance_agent.browser.pages.content_compliance import (
        ComplianceBrowserPermit,
        ComplianceBrowserRunResult,
    )
    from compliance_agent.infrastructure.clock import Clock
    from compliance_agent.schemas.compliance_operations import ComplianceDryRunResult


class ComplianceAuditWriter(Protocol):
    @property
    def next_sequence(self) -> int: ...

    def write_text(self, relative_path: str, content: str) -> Path: ...

    def append(self, event: AuditEvent) -> AuditEvent: ...


class ComplianceAuditService:
    """Persist exact hashes and redacted browser outcomes in the chained run log."""

    def __init__(self, writer: ComplianceAuditWriter, clock: Clock, run_id: str) -> None:
        self._writer = writer
        self._clock = clock
        self._run_id = run_id

    def record_preview(self, preview: ComplianceDryRunResult) -> None:
        """Persist the complete typed preview before approval."""

        self._writer.write_text("compliance-preview.json", preview.model_dump_json(indent=2))
        self._append(
            event_type="compliance_preview_ready",
            outcome=preview.status,
            plan_hash=preview.plan_hash,
            before_state_hash=preview.before_state_hash,
            change_set_hash=preview.change_set_hash,
        )

    def record_approval(self, permit: ComplianceBrowserPermit) -> None:
        """Record immutable approval facts without persisting the typed phrase."""

        self._append(
            event_type="compliance_approval_consumed",
            outcome="approved",
            plan_hash=permit.plan_hash,
            before_state_hash=permit.before_state_hash,
            change_set_hash=permit.change_set_hash,
            target_ou=permit.target_ou,
            correlation_id=permit.approval_id,
        )

    def record_browser_result(
        self,
        result: ComplianceBrowserRunResult,
        permit: ComplianceBrowserPermit,
    ) -> None:
        """Persist step decisions and verification status, excluding page snapshots."""

        safe_result = result.model_dump(exclude={"final_snapshot"})
        self._writer.write_text(
            "compliance-browser-result.json",
            result.__class__.model_validate(
                safe_result | {"final_snapshot": "redacted"}
            ).model_dump_json(indent=2),
        )
        self._append(
            event_type="compliance_browser_verified",
            outcome="verified" if result.verified else "not_verified",
            plan_hash=permit.plan_hash,
            before_state_hash=permit.before_state_hash,
            change_set_hash=permit.change_set_hash,
            target_ou=permit.target_ou,
            correlation_id=permit.approval_id,
            error_code=None if result.verified else "compliance_readback_mismatch",
        )

    def _append(  # noqa: PLR0913 - mirrors the immutable audit event fields.
        self,
        *,
        event_type: str,
        outcome: str,
        plan_hash: str | None,
        before_state_hash: str | None,
        change_set_hash: str | None,
        target_ou: str | None = None,
        correlation_id: str | None = None,
        error_code: str | None = None,
    ) -> None:
        self._writer.append(
            AuditEvent(
                run_id=self._run_id,
                sequence=self._writer.next_sequence,
                timestamp=self._clock.now(),
                event_type=event_type,
                component="content_compliance_browser",
                outcome=outcome,
                plan_hash=plan_hash,
                before_state_hash=before_state_hash,
                change_set_hash=change_set_hash,
                target_ou=target_ou,
                correlation_id=correlation_id,
                error_code=error_code,
            )
        )
