"""Evidence-gated Admin UI contract-pack inspection and activation policy."""

import hashlib
import json
from pathlib import Path

from compliance_agent.exceptions import UnvalidatedUiContract
from compliance_agent.schemas.operations import UiContractPack


class UiContractStore:
    """Read reviewed contract packs without allowing console-side promotion."""

    def __init__(self, state_directory: Path) -> None:
        self._path = (state_directory / "ui-contract-pack.json").resolve()

    def load(self) -> UiContractPack | None:
        if not self._path.exists():
            return None
        if self._path.is_symlink():
            message = "UI contract pack cannot be a symbolic link"
            raise OSError(message)
        pack = UiContractPack.model_validate_json(self._path.read_text(encoding="utf-8"))
        if pack.status == "accepted" and pack.accepted_digest != contract_pack_digest(pack):
            message = "accepted UI contract pack digest does not match its reviewed content"
            raise UnvalidatedUiContract(message)
        return pack

    def require_accepted(self) -> UiContractPack:
        """Return exact accepted evidence or keep all live adapters unavailable."""

        pack = self.load()
        if pack is None or pack.status != "accepted":
            message = "live Admin-console adapters require an accepted UI contract pack"
            raise UnvalidatedUiContract(message)
        return pack


def contract_pack_digest(pack: UiContractPack) -> str:
    """Return the deterministic digest reviewed during out-of-band promotion."""

    value = pack.model_dump(mode="json", exclude={"accepted_digest"})
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
