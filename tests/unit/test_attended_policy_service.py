"""Behavior tests for the complete Reflex-to-Google attended lifecycle."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest

from compliance_agent.application.attended_policy_service import (
    AttendedPolicyService,
    PendingAttendedRuns,
)
from compliance_agent.application.audit_catalog import AuditCatalog
from compliance_agent.browser.admin_agent_driver import (
    AdminBrowserApplyResult,
    AdminBrowserPermit,
    _validate_admin_permit,
)
from compliance_agent.browser.states import AdminPageState
from compliance_agent.domain.desired_state import calculate_desired_state
from compliance_agent.domain.diff import calculate_change_set
from compliance_agent.domain.hashing import canonical_hash
from compliance_agent.exceptions import AmbiguousTarget, StaleConfirmation
from compliance_agent.infrastructure.filesystem import OwnershipStore
from compliance_agent.llm import readiness as readiness_module
from compliance_agent.llm.readiness import _ollama_native_endpoint, require_local_model
from compliance_agent.schemas.compliance import (
    AdvancedContentLocation,
    AdvancedContentMatch,
    AdvancedMatchType,
    ContentComplianceRuleDraft,
    ExpressionCombiner,
    GeneratedRejectionNotice,
    MessageDirection,
    OrganizationalUnitRef,
    PersonaProfile,
)
from compliance_agent.schemas.operations import RunMode
from compliance_agent.schemas.plan import (
    CreateBlockedSenderRule,
    CreateContentComplianceRule,
    SetBlockedSenderRuleEnabled,
    TaskPlan,
    UpdateBlockedSenderRule,
)
from compliance_agent.schemas.resources import AddressEntry
from compliance_agent.schemas.state import BlockedSenderState
from compliance_agent.schemas.status import RunStatus
from compliance_agent.settings import Settings
from tests.conftest import OWNERSHIP_ID, PREFIX, domain, owned_state, registry_for


class _FakeBrowserSession:
    read_count = 0
    drift_on_second_read = False
    fail_during_apply = False

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        pass

    async def __aenter__(self) -> "_FakeBrowserSession":
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def read_blocked_sender_state(
        self,
        expected: BlockedSenderState,
        **_kwargs: object,
    ) -> BlockedSenderState:
        type(self).read_count += 1
        if type(self).drift_on_second_read and type(self).read_count == 2:
            return expected.model_copy(update={"unmanaged_rule_names": ("Manual drift",)})
        return expected

    async def apply_blocked_sender_change(
        self,
        _change: object,
        _permit: object,
    ) -> AdminBrowserApplyResult:
        if type(self).fail_during_apply:
            message = "simulated uncertain browser write"
            raise RuntimeError(message)
        return AdminBrowserApplyResult(
            completed=True,
            steps=("typed-save",),
            final_page_state=AdminPageState.BLOCKED_SENDERS_SECTION,
        )


def _settings(tmp_path: Path, mode: RunMode) -> Settings:
    return Settings(
        run_mode=mode,
        expected_admin_email="admin@example.com",
        expected_workspace_domain="example.com",
        profile_dir=tmp_path / "profile",
        state_dir=tmp_path / "state",
        audit_dir=tmp_path / "audit",
    )


def _standard_plan() -> TaskPlan:
    return TaskPlan(
        status="plan",
        actions=(
            CreateBlockedSenderRule(
                entries=(AddressEntry(kind="domain", value="blocked.example"),),
                bypass_entries=(AddressEntry(kind="email", value="safe@blocked.example"),),
                rejection_notice="Mail refused by policy.",
            ),
        ),
    )


@pytest.mark.asyncio
async def test_local_model_readiness_requires_declared_vision(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class _Response:
        def __init__(self, capabilities: list[str]) -> None:
            self._capabilities = capabilities

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, list[str]]:
            return {"capabilities": self._capabilities}

    class _Client:
        def __init__(self, capabilities: list[str]) -> None:
            self._capabilities = capabilities

        async def __aenter__(self) -> "_Client":
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def post(self, endpoint: str, **_kwargs: object) -> _Response:
            assert endpoint == "http://localhost:11434/api/show"
            return _Response(self._capabilities)

    settings = _settings(tmp_path, RunMode.PLAN_ONLY)
    monkeypatch.setattr(
        readiness_module.httpx2,
        "AsyncClient",
        lambda **_kwargs: _Client(["completion", "vision"]),
    )
    await require_local_model(settings, "gemma4:12b", require_vision=True)

    monkeypatch.setattr(
        readiness_module.httpx2,
        "AsyncClient",
        lambda **_kwargs: _Client(["completion"]),
    )
    with pytest.raises(RuntimeError, match="vision capability"):
        await require_local_model(settings, "gemma4:12b", require_vision=True)
    assert (
        _ollama_native_endpoint("http://localhost:11434/v1", "/api/show")
        == "http://localhost:11434/api/show"
    )


@pytest.mark.asyncio
async def test_local_model_readiness_hides_provider_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class _Client:
        async def __aenter__(self) -> "_Client":
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def post(self, *_args: object, **_kwargs: object) -> object:
            message = "provider internals"
            raise OSError(message)

    monkeypatch.setattr(readiness_module.httpx2, "AsyncClient", lambda **_kwargs: _Client())
    with pytest.raises(RuntimeError, match="local Ollama model") as raised:
        await require_local_model(
            _settings(tmp_path, RunMode.PLAN_ONLY),
            "missing-model",
            require_vision=False,
        )
    assert "provider internals" not in str(raised.value)


def test_audit_catalog_surfaces_interrupted_run(tmp_path: Path) -> None:
    run_id = "a" * 32
    orphan = tmp_path / "runs" / f"20260716T120000Z-{run_id}"
    orphan.mkdir(parents=True)
    (orphan / "run.jsonl").write_text("", encoding="utf-8")

    summary = AuditCatalog(tmp_path).list_runs()[0]

    assert summary.run_id == run_id
    assert summary.status is RunStatus.INDETERMINATE
    assert not summary.integrity_valid
    assert "manifest" in summary.integrity_errors[0]


def test_atomic_blocked_sender_update_replaces_every_editable_field() -> None:
    state = owned_state(entries=(domain("old.example"),))
    plan = TaskPlan(
        status="plan",
        actions=(
            UpdateBlockedSenderRule(
                target_rule_id=OWNERSHIP_ID,
                entries=(domain("new.example"),),
                bypass_entries=(AddressEntry(kind="email", value="safe@new.example"),),
                rejection_notice="Updated rejection.",
                enabled=False,
            ),
        ),
    )
    bypass_id = UUID("663c82a1-2d36-42cd-ae97-85ee319bb21d")
    desired = calculate_desired_state(
        state,
        plan,
        registry_for(),
        (bypass_id,),
        PREFIX,
    ).desired_state

    assert desired.rules[0].rejection_notice == "Updated rejection."
    assert not desired.rules[0].enabled
    lists = {address_list.display_name: address_list for address_list in desired.address_lists}
    primary_name = desired.rules[0].address_list_names[0]
    bypass_name = desired.rules[0].bypass_address_list_names[0]
    assert lists[primary_name].entries[0].value == "new.example"
    assert lists[bypass_name].entries[0].value == "safe@new.example"
    assert desired.rules[0].bypass_address_list_names == (bypass_name,)
    with pytest.raises(ValueError, match="same address"):
        UpdateBlockedSenderRule(
            target_rule_id=OWNERSHIP_ID,
            entries=(domain("overlap.example"),),
            bypass_entries=(domain("overlap.example"),),
        )


def test_standard_create_and_toggle_preserve_explicit_enabled_state() -> None:
    created = calculate_desired_state(
        BlockedSenderState(),
        TaskPlan(
            status="plan",
            actions=(
                CreateBlockedSenderRule(
                    entries=(domain("disabled.example"),),
                    enabled=False,
                ),
            ),
        ),
        registry_for().model_copy(update={"resources": ()}),
        (OWNERSHIP_ID,),
        PREFIX,
    ).desired_state
    assert not created.rules[0].enabled

    disabled = calculate_desired_state(
        owned_state(entries=(domain("existing.example"),)),
        TaskPlan(
            status="plan",
            actions=(
                SetBlockedSenderRuleEnabled(
                    target_rule_id=OWNERSHIP_ID,
                    enabled=False,
                ),
            ),
        ),
        registry_for(),
        (),
        PREFIX,
    ).desired_state
    assert not disabled.rules[0].enabled

    inherited_base = owned_state(entries=(domain("inherited.example"),))
    inherited = inherited_base.model_copy(
        update={"rules": (inherited_base.rules[0].model_copy(update={"inherited": True}),)}
    )
    with pytest.raises(AmbiguousTarget, match="inherited blocked-sender rules"):
        calculate_desired_state(
            inherited,
            TaskPlan(
                status="plan",
                actions=(
                    SetBlockedSenderRuleEnabled(
                        target_rule_id=OWNERSHIP_ID,
                        enabled=False,
                    ),
                ),
            ),
            registry_for(),
            (),
            PREFIX,
        )


def test_admin_permit_binds_approval_policy_identity_and_ou() -> None:
    before = BlockedSenderState()
    after = calculate_desired_state(
        before,
        _standard_plan(),
        registry_for().model_copy(update={"resources": ()}),
        (OWNERSHIP_ID, UUID("663c82a1-2d36-42cd-ae97-85ee319bb21d")),
        PREFIX,
    ).desired_state
    change = calculate_change_set(before, after)
    permit = AdminBrowserPermit(
        approval_id="approval",
        plan_hash="a" * 64,
        before_state_hash=canonical_hash(before),
        change_set_hash=canonical_hash(change),
        target_ou="/",
        target_ownership_id=OWNERSHIP_ID,
        surface="blocked_senders",
        approved=True,
    )

    _validate_admin_permit(change, permit, surface="blocked_senders")
    with pytest.raises(StaleConfirmation):
        _validate_admin_permit(
            change,
            permit.model_copy(update={"approved": False}),
            surface="blocked_senders",
        )
    with pytest.raises(StaleConfirmation):
        _validate_admin_permit(
            change,
            permit.model_copy(update={"target_ou": "/Other"}),
            surface="blocked_senders",
        )


@pytest.mark.asyncio
async def test_live_entry_only_update_keeps_policy_identity_for_approval(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "compliance_agent.application.attended_policy_service.PlaywrightAdminAgentSession",
        _FakeBrowserSession,
    )
    _FakeBrowserSession.read_count = 0
    _FakeBrowserSession.drift_on_second_read = False
    _FakeBrowserSession.fail_during_apply = False
    settings = _settings(tmp_path, RunMode.LIVE)
    current = owned_state(entries=(domain("old.example"),))
    record = (
        registry_for()
        .resources[0]
        .model_copy(
            update={
                "rule_snapshot": current.rules[0],
                "address_list_snapshot": current.address_lists[0],
            }
        )
    )
    OwnershipStore(settings.state_dir).save(
        registry_for().model_copy(update={"resources": (record,)})
    )
    service = AttendedPolicyService(PendingAttendedRuns())
    plan = TaskPlan(
        status="plan",
        actions=(
            UpdateBlockedSenderRule(
                target_rule_id=OWNERSHIP_ID,
                entries=(domain("new.example"),),
                rejection_notice="Mail rejected.",
            ),
        ),
    )

    preview = await service.preview(settings, plan)

    assert preview.standard_change is not None
    assert preview.standard_change.rules_to_update == ()
    assert len(preview.standard_change.address_lists_to_update) == 1
    result = await service.execute(
        preview.run_id,
        phrase=preview.approval_phrase or "",
        acknowledged=True,
    )
    assert result.verified


@pytest.mark.asyncio
async def test_live_attended_run_previews_approves_verifies_and_commits(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "compliance_agent.application.attended_policy_service.PlaywrightAdminAgentSession",
        _FakeBrowserSession,
    )
    _FakeBrowserSession.read_count = 0
    _FakeBrowserSession.drift_on_second_read = False
    settings = _settings(tmp_path, RunMode.LIVE)
    service = AttendedPolicyService(PendingAttendedRuns())

    preview = await service.preview(settings, _standard_plan())
    assert preview.has_mutations
    assert preview.approval_phrase is not None
    assert preview.standard_change is not None
    assert len(preview.standard_change.rules_to_create) == 1

    result = await service.execute(
        preview.run_id,
        phrase=preview.approval_phrase,
        acknowledged=True,
    )
    assert result.status == "completed"
    assert result.verified
    registry = OwnershipStore(settings.state_dir).load()
    assert registry.resources[0].rule_snapshot is not None
    assert registry.address_lists[0].address_list_snapshot is not None
    run_directories = tuple((settings.audit_dir / "runs").iterdir())
    assert len(run_directories) == 1
    assert (run_directories[0] / "manifest.json").is_file()
    assert "approval_phrase" not in (run_directories[0] / "preview.json").read_text(
        encoding="utf-8"
    )


@pytest.mark.asyncio
async def test_live_attended_run_detects_drift_before_any_write(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "compliance_agent.application.attended_policy_service.PlaywrightAdminAgentSession",
        _FakeBrowserSession,
    )
    _FakeBrowserSession.read_count = 0
    _FakeBrowserSession.drift_on_second_read = True
    _FakeBrowserSession.fail_during_apply = False
    settings = _settings(tmp_path, RunMode.LIVE)
    service = AttendedPolicyService(PendingAttendedRuns())
    preview = await service.preview(settings, _standard_plan())

    result = await service.execute(
        preview.run_id,
        phrase=preview.approval_phrase or "",
        acknowledged=True,
    )
    assert result.status == "drifted"
    assert not result.verified
    assert OwnershipStore(settings.state_dir).load().resources == ()


@pytest.mark.asyncio
async def test_live_exception_after_apply_begins_finalizes_indeterminate_audit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "compliance_agent.application.attended_policy_service.PlaywrightAdminAgentSession",
        _FakeBrowserSession,
    )
    _FakeBrowserSession.read_count = 0
    _FakeBrowserSession.drift_on_second_read = False
    _FakeBrowserSession.fail_during_apply = True
    settings = _settings(tmp_path, RunMode.LIVE)
    service = AttendedPolicyService(PendingAttendedRuns())
    preview = await service.preview(settings, _standard_plan())

    with pytest.raises(RuntimeError, match="simulated uncertain"):
        await service.execute(
            preview.run_id,
            phrase=preview.approval_phrase or "",
            acknowledged=True,
        )

    run_directory = next((settings.audit_dir / "runs").iterdir())
    manifest = (run_directory / "manifest.json").read_text(encoding="utf-8")
    assert '"final_status": "indeterminate"' in manifest
    _FakeBrowserSession.fail_during_apply = False


@pytest.mark.asyncio
async def test_expired_live_approval_finalizes_rejected_audit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "compliance_agent.application.attended_policy_service.PlaywrightAdminAgentSession",
        _FakeBrowserSession,
    )
    _FakeBrowserSession.read_count = 0
    _FakeBrowserSession.drift_on_second_read = False
    pending = PendingAttendedRuns()
    settings = _settings(tmp_path, RunMode.LIVE)
    service = AttendedPolicyService(pending)
    preview = await service.preview(settings, _standard_plan())
    stored = pending.get(preview.run_id)
    assert stored is not None
    stored.preview = stored.preview.model_copy(
        update={"expires_at": datetime.now(UTC) - timedelta(seconds=1)}
    )

    with pytest.raises(ValueError, match="expired"):
        await service.execute(
            preview.run_id,
            phrase=preview.approval_phrase or "",
            acknowledged=True,
        )

    run_directory = next((settings.audit_dir / "runs").iterdir())
    manifest = (run_directory / "manifest.json").read_text(encoding="utf-8")
    assert '"final_status": "confirmation_rejected"' in manifest


def test_content_compliance_plan_keeps_reject_action_and_regex_shape() -> None:
    notice = GeneratedRejectionNotice(
        text="The clockwork postmaster declined this message under the header policy.",
        policy_category="header-policy",
        policy_id="GW-200",
        persona=PersonaProfile(
            fictional_role="clockwork postmaster",
            traits=("courteous", "precise"),
            voice="warm and concise",
            motif="brass gears",
            seed=200,
        ),
    )
    rule = ContentComplianceRuleDraft(
        target_ou=OrganizationalUnitRef(path="/Finance"),
        directions=(MessageDirection.INBOUND,),
        combiner=ExpressionCombiner.ANY,
        expressions=(
            AdvancedContentMatch(
                location=AdvancedContentLocation.FULL_HEADERS,
                match_type=AdvancedMatchType.MATCHES_REGEX,
                value=r"(?i)^x-risk:\s*high$",
                regex_description="Risk header",
            ),
        ),
        rejection_notice=notice,
    )
    plan = TaskPlan(
        status="plan",
        actions=(CreateContentComplianceRule(rule=rule),),
    )
    assert plan.actions[0].type == "create_content_compliance_rule"
    assert plan.actions[0].rule.rejection_notice.text == notice.text
    assert plan.actions[0].rule.expressions[0].value == r"(?i)^x-risk:\s*high$"
