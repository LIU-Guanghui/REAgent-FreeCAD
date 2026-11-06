"""Strict JSON values and detached copies, without domain dependencies."""

import json
import math
from typing import Any

class NeutralContractError(ValueError):
    """A value cannot cross the host-neutral contract boundary."""

def require_json_value(value: Any, *, path: str = "$") -> None:
    """Reject values that cannot be represented by strict neutral JSON."""

    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise NeutralContractError(f"{path} must contain a finite number")
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            require_json_value(item, path=f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise NeutralContractError(f"{path} object keys must be strings")
            require_json_value(item, path=f"{path}.{key}")
        return
    raise NeutralContractError(
        f"{path} contains non-neutral host value {type(value).__name__}"
    )

def to_json_value(value: Any) -> Any:
    """Return a detached JSON value after enforcing the neutral boundary."""

    require_json_value(value)

    def copy(item):
        if isinstance(item, (list, tuple)):
            return [copy(child) for child in item]
        if isinstance(item, dict):
            return {key: copy(child) for key, child in item.items()}
        return item

    return copy(value)

def _required_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise NeutralContractError(f"{field_name} must be a non-empty string")
    return value

def encode_json(value):
    require_json_value(value)
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))

def decode_json(text):
    def object_pairs(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("Duplicate JSON key")
            value[key] = item
        return value

    def invalid_constant(_value):
        raise ValueError("Non-finite JSON number")

    value = json.loads(text, object_pairs_hook=object_pairs, parse_constant=invalid_constant)
    require_json_value(value)
    return value
