"""Address-list page gate pending supervised current-UI fixture capture."""

from compliance_agent.exceptions import UnvalidatedUiContract


class AddressListsPage:
    """Explicitly deny unvalidated address-list reads and mutations."""

    async def read_lists(self) -> None:
        """Fail closed until current list markup and semantics are fixture-tested."""

        message = "address-list parsing requires supervised sanitized UI fixtures"
        raise UnvalidatedUiContract(message)
