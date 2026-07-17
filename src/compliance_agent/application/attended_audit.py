"""Durable hash-chained audit package for Reflex attended policy runs."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from compliance_agent.audit.manifest import RunManifest, digest_artifacts
from compliance_agent.audit.writer import RunAuditWriter
from compliance_agent.domain.hashing import canonical_hash
from compliance_agent.infrastructure.runtime_metadata import collect_manifest_metadata
from compliance_agent.schemas.events import AuditEvent

if TYPE_CHECKING:
    from pydantic import BaseModel

    from compliance_agent.application.attended_policy_service import (
        AttendedExecutionResult,
        AttendedPolicyPreview,
    )
    from compliance_agent.llm.group_chat import GroupChatTranscript
    from compliance_agent.schemas.plan import TaskPlan
    from compliance_agent.schemas.status import RunStatus
    from compliance_agent.settings import Settings


class AttendedRunAudit:
    """Write only typed evidence; browser screenshots and credentials are never persisted."""

    def __init__(
        self,
        settings: Settings,
        *,
        run_id: str,
        started_at: datetime,
    ) -> None:
        timestamp = started_at.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
        self._writer = RunAuditWriter(
            settings.audit_dir / "runs" / f"{timestamp}-{run_id}"
        )
        self._settings = settings
        self._run_id = run_id
        self._started_at = started_at
        self._metadata = collect_manifest_metadata(started_at, settings)
        self._finalized = False

    def record_review(
        self,
        plan: TaskPlan,
        transcript: GroupChatTranscript,
        *,
        model_tag: str,
    ) -> None:
        """Persist the sanitized specialist verdicts bound to the exact typed plan."""

        payload = {
            "plan_hash": canonical_hash(plan),
            "model_tag": model_tag,
            "participants": list(transcript.participants),
            "max_rounds": transcript.max_rounds,
            "messages": [message.model_dump(mode="json") for message in transcript.messages],
            "outcome": "passed",
        }
        self._writer.write_text("plan.json", _json(plan))
        self._writer.write_text("agent-review.json", _json_value(payload))
        self._append(
            "agent_review_completed",
            "microsoft_agent_framework_group_chat",
            "passed",
            plan_hash=canonical_hash(plan),
        )

    def record_preview(self, preview: AttendedPolicyPreview) -> None:
        self._writer.write_text("plan.json", _json(preview.plan))
        preview_payload = preview.model_dump(mode="json", exclude={"approval_phrase"})
        self._writer.write_text("preview.json", _json_value(preview_payload))
        before = preview.standard_before or preview.compliance_before
        after = preview.standard_after or preview.compliance_after
        change = preview.standard_change or preview.compliance_change
        if before is None or after is None or change is None:
            message = "attended preview omitted audit evidence"
            raise ValueError(message)
        self._writer.write_text("before.json", _json(before))
        self._writer.write_text("expected_after.json", _json(after))
        self._writer.write_text("change_set.json", _json(change))
        self._append(
            "preview_ready",
            "reflex_console",
            "ready" if preview.has_mutations else "no_change",
            preview=preview,
        )

    def finalize(
        self,
        status: RunStatus,
        *,
        result: AttendedExecutionResult | None = None,
    ) -> None:
        if self._finalized:
            return
        if result is not None:
            self._writer.write_text("execution_result.json", _json(result))
        self._append(
            "run_finalized",
            "attended_policy_service",
            status.value,
            result=result,
        )
        ended_at = datetime.now(UTC)
        manifest = RunManifest(
            **self._metadata.model_dump(),
            end_time=ended_at,
            final_status=status,
            artifacts=digest_artifacts(self._writer.run_directory),
        )
        self._writer.write_text("manifest.json", _json(manifest))
        self._finalized = True

    def _append(  # noqa: PLR0913 - explicit audit correlations.
        self,
        event_type: str,
        component: str,
        outcome: str,
        *,
        preview: AttendedPolicyPreview | None = None,
        result: AttendedExecutionResult | None = None,
        plan_hash: str | None = None,
    ) -> None:
        self._writer.append(
            AuditEvent(
                run_id=self._run_id,
                sequence=self._writer.next_sequence,
                timestamp=datetime.now(UTC),
                event_type=event_type,
                component=component,
                outcome=outcome,
                plan_hash=preview.plan_hash if preview is not None else plan_hash,
                before_state_hash=(
                    preview.before_state_hash
                    if preview is not None
                    else result.current_before_hash
                    if result is not None
                    else None
                ),
                change_set_hash=preview.change_set_hash if preview is not None else None,
            )
        )


def _json(model: BaseModel) -> str:
    return _json_value(model.model_dump(mode="json"))


def _json_value(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"
