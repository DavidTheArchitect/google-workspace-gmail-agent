"""Atomic protected JSON persistence for small operator indexes."""

import json
import os
import tempfile
from pathlib import Path

from pydantic import BaseModel


class ProtectedJsonStore:
    """Load and atomically replace one validated tuple of models."""

    def __init__(self, path: Path) -> None:
        self._path = path.resolve()

    def load[T: BaseModel](self, model_type: type[T]) -> tuple[T, ...]:
        if not self._path.exists():
            return ()
        if self._path.is_symlink():
            message = f"protected JSON store cannot be a symbolic link: {self._path}"
            raise OSError(message)
        raw = json.loads(self._path.read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            message = "protected JSON collection must be an array"
            raise TypeError(message)
        return tuple(model_type.model_validate(item) for item in raw)

    def save(self, values: tuple[BaseModel, ...]) -> None:
        self._path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._path.parent.chmod(0o700)
        descriptor, temporary_name = tempfile.mkstemp(
            dir=self._path.parent,
            prefix=f".{self._path.name}.",
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(
                    json.dumps(
                        [value.model_dump(mode="json") for value in values],
                        ensure_ascii=False,
                        indent=2,
                        sort_keys=True,
                    )
                )
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(self._path)
            self._path.chmod(0o600)
        except OSError:
            temporary.unlink(missing_ok=True)
            raise
