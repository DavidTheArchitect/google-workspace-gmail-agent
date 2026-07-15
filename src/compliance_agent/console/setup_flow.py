"""Guided setup steps derived exclusively from the shared readiness projection."""

from dataclasses import dataclass
from typing import Literal

from compliance_agent.console.capabilities import ConsoleCapabilities
from compliance_agent.console.readiness import ReadinessItem, collect_readiness
from compliance_agent.schemas.operations import RunMode
from compliance_agent.settings import Settings

SetupState = Literal["complete", "current", "locked", "not_applicable"]
_ALWAYS_APPLICABLE_STEPS = 2


@dataclass(frozen=True, slots=True)
class SetupStep:
    number: int
    title: str
    detail: str
    state: SetupState
    action_href: str | None
    action_label: str | None
    readiness: tuple[ReadinessItem, ...]


def build_setup_steps(
    settings: Settings,
    capabilities: ConsoleCapabilities | None = None,
) -> tuple[SetupStep, ...]:
    """Return five ordered, truthful guidance steps with one current step."""

    items = collect_readiness(settings, capabilities)
    groups = (
        ("Mode", (items[0],), "/setup#run-mode", "Choose a run mode"),
        ("Storage", tuple(items[1:4]), "/readiness", "Review storage"),
        (
            "Google identities",
            tuple(items[4:6]),
            "/setup#google-account",
            "Configure identities",
        ),
        (
            "Admin interface evidence",
            (items[6],),
            "/contracts",
            "Review interface evidence",
        ),
        (
            "Browser-backed capability",
            (items[7],),
            "/readiness",
            "Review browser capability",
        ),
    )
    applicable = settings.run_mode != RunMode.PLAN_ONLY
    complete = [all(not item.blocking for item in group) for _, group, _, _ in groups]
    for index in range(2, 5):
        if not applicable:
            complete[index] = True
    current_index = next(
        (
            index
            for index, done in enumerate(complete)
            if not done and (applicable or index < _ALWAYS_APPLICABLE_STEPS)
        ),
        None,
    )
    steps: list[SetupStep] = []
    for index, (title, group, href, label) in enumerate(groups):
        if index >= _ALWAYS_APPLICABLE_STEPS and not applicable:
            state: SetupState = "not_applicable"
        elif complete[index]:
            state = "complete"
        elif index == current_index:
            state = "current"
        else:
            state = "locked"
        steps.append(
            SetupStep(
                number=index + 1,
                title=title,
                detail=" ".join(item.detail for item in group),
                state=state,
                action_href=href,
                action_label=label,
                readiness=group,
            )
        )
    return tuple(steps)
