"""Explicit browser page states."""

from enum import StrEnum


class AdminPageState(StrEnum):
    """Known identity states; unrecognized UI is always UNKNOWN."""

    LOGIN_REQUIRED = "login_required"
    ACCOUNT_CHOOSER = "account_chooser"
    TWO_STEP_VERIFICATION = "two_step_verification"
    GMAIL_SPAM_SETTINGS = "gmail_spam_settings"
    BLOCKED_SENDERS_SECTION = "blocked_senders_section"
    BLOCKED_SENDER_RULE_EDITOR = "blocked_sender_rule_editor"
    ADDRESS_LIST_PICKER = "address_list_picker"
    ADDRESS_LIST_EDITOR = "address_list_editor"
    SAVE_PENDING = "save_pending"
    UNKNOWN = "unknown"
