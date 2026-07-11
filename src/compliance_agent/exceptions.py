"""Explicit failures used at application and infrastructure boundaries."""


class ComplianceAgentError(Exception):
    """Base class for expected application failures."""


class AmbiguousTarget(ComplianceAgentError):
    """Raised when a plan does not identify exactly one safe target."""


class OwnershipNotEstablished(ComplianceAgentError):
    """Raised when visible and local ownership evidence do not agree."""


class RootOuNotConfirmed(ComplianceAgentError):
    """Raised when the root organizational unit is not positively identified."""


class StaleConfirmation(ComplianceAgentError):
    """Raised when approved hashes no longer match current execution inputs."""


class SelectorNotFound(ComplianceAgentError):
    """Raised when a required locator resolves to no elements."""


class SelectorAmbiguous(ComplianceAgentError):
    """Raised when a mutation-capable locator resolves to several elements."""


class UnknownPageState(ComplianceAgentError):
    """Raised when browser identity cannot be established."""


class UnvalidatedUiContract(ComplianceAgentError):
    """Raised when live behavior is gated on supervised UI observation."""


class MutationOutcomeUnknown(ComplianceAgentError):
    """Raised when a mutation may have reached Google but its response was not observed."""


class AuditWriteFailure(ComplianceAgentError):
    """Raised when required audit evidence cannot be persisted."""


class PlannerFailure(ComplianceAgentError):
    """Raised when the natural-language planner cannot return a valid typed plan."""


class RunLockUnavailable(ComplianceAgentError):
    """Raised when another compliance-agent process owns the run lock."""
