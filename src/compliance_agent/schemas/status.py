"""Closed terminal run statuses."""

from enum import StrEnum


class RunStatus(StrEnum):
    """Authoritative outcomes; narrative text cannot change these values."""

    NO_CHANGE_REQUIRED = "no_change_required"
    CONFIRMATION_REJECTED = "confirmation_rejected"
    APPLIED_UI_VERIFIED = "applied_ui_verified"
    APPLIED_PENDING_PROPAGATION = "applied_pending_propagation"
    PARTIALLY_APPLIED = "partially_applied"
    FAILED_UNCHANGED = "failed_unchanged"
    INDETERMINATE = "indeterminate"
    UNSUPPORTED = "unsupported"
