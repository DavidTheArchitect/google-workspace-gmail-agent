"""Protected follow-up records that distinguish UI persistence from mail enforcement."""

from datetime import datetime, timedelta
from pathlib import Path
from typing import cast

from compliance_agent.infrastructure.protected_json import ProtectedJsonStore
from compliance_agent.schemas.operations import PropagationRecord


class PropagationService:
    """Create and update evidence-linked propagation follow-up records."""

    def __init__(self, state_directory: Path) -> None:
        self._store = ProtectedJsonStore(state_directory / "propagation.json")

    def list(self) -> tuple[PropagationRecord, ...]:
        records = cast(
            "tuple[PropagationRecord, ...]",
            self._store.load(PropagationRecord),
        )
        return tuple(
            sorted(
                records,
                key=lambda item: item.created_at,
                reverse=True,
            )
        )

    def create_pending(
        self,
        run_id: str,
        now: datetime,
        *,
        recheck_after: timedelta = timedelta(hours=24),
    ) -> PropagationRecord:
        loaded = cast("tuple[PropagationRecord, ...]", self._store.load(PropagationRecord))
        records = {record.run_id: record for record in loaded}
        record = PropagationRecord(
            run_id=run_id,
            created_at=now,
            updated_at=now,
            due_at=now + recheck_after,
        )
        records[run_id] = record
        ordered: tuple[PropagationRecord, ...] = tuple(
            sorted(records.values(), key=lambda item: item.run_id)
        )
        self._store.save(ordered)
        return record

    def record_ui_recheck(
        self,
        run_id: str,
        recheck_run_id: str,
        now: datetime,
    ) -> PropagationRecord:
        return self._replace(
            run_id,
            now,
            status="ui_reconfirmed",
            ui_recheck_run_id=recheck_run_id,
        )

    def record_mail_flow(
        self,
        run_id: str,
        mail_flow_audit_run_id: str,
        now: datetime,
    ) -> PropagationRecord:
        return self._replace(
            run_id,
            now,
            status="mail_flow_verified",
            mail_flow_audit_run_id=mail_flow_audit_run_id,
        )

    def _replace(self, run_id: str, now: datetime, **changes: object) -> PropagationRecord:
        loaded = cast("tuple[PropagationRecord, ...]", self._store.load(PropagationRecord))
        records = {record.run_id: record for record in loaded}
        current = records.get(run_id)
        if current is None:
            message = f"propagation record does not exist: {run_id}"
            raise ValueError(message)
        updated = current.model_copy(update={"updated_at": now, **changes})
        updated = PropagationRecord.model_validate(updated.model_dump())
        records[run_id] = updated
        ordered: tuple[PropagationRecord, ...] = tuple(
            sorted(records.values(), key=lambda item: item.run_id)
        )
        self._store.save(ordered)
        return updated
