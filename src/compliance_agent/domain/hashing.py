"""Canonical Unicode-normalized SHA-256 hashing for confirmation preconditions."""

import hashlib
import json
import unicodedata
from collections.abc import Mapping, Sequence

from pydantic import BaseModel

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]


def canonical_json(model: BaseModel) -> str:
    """Serialize a model with stable keys and order-independent collections."""

    value = _canonicalize(model.model_dump(mode="json", exclude_none=False))
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def canonical_hash(model: BaseModel) -> str:
    """Return the lowercase SHA-256 digest of a canonical model representation."""

    return hashlib.sha256(canonical_json(model).encode("utf-8")).hexdigest()


def _canonicalize(value: object) -> JsonValue:
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, Mapping):
        return {str(key): _canonicalize(item) for key, item in sorted(value.items())}
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        items = [_canonicalize(item) for item in value]
        return sorted(items, key=lambda item: json.dumps(item, sort_keys=True, ensure_ascii=False))
    message = f"value is not canonical JSON data: {type(value).__name__}"
    raise TypeError(message)
