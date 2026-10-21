"""JSON-safe Bridge operation results and actionable errors.

All values crossing this boundary must be JSON-safe.  In particular, FreeCAD
documents, shapes, vectors, placements, and other host objects are never valid
contract values.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Mapping

from .contract import RETRY_ACTIONS

ERROR_CLASSES = frozenset(
    {
        "connection",
        "precondition",
        "native_operation",
        "recompute",
        "validity",
        "semantic",
        "persistence",
        "protocol",
    }
)
SIDE_EFFECTS = frozenset(
    {"none", "transient_rolled_back", "committed", "unknown"}
)
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
    if isinstance(value, tuple):
        return [to_json_value(item) for item in value]
    if isinstance(value, list):
        return [to_json_value(item) for item in value]
    if isinstance(value, dict):
        return {key: to_json_value(item) for key, item in value.items()}
    return value


def _required_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise NeutralContractError(f"{field_name} must be a non-empty string")
    return value


@dataclass(frozen=True)
class OperationError:
    """One actionable failure without host-specific exception types."""

    error_class: str
    code: str
    message: str
    side_effect: str
    retry_action: str
    evidence: tuple[dict[str, Any], ...] = ()

    def __post_init__(self) -> None:
        if self.error_class not in ERROR_CLASSES:
            raise NeutralContractError("unsupported operation error class")
        _required_text(self.code, "code")
        _required_text(self.message, "message")
        if self.side_effect not in SIDE_EFFECTS:
            raise NeutralContractError("unsupported operation side effect")
        if self.retry_action not in RETRY_ACTIONS:
            raise NeutralContractError("unsupported operation retry action")
        _validate_evidence(self.evidence)

    def to_dict(self) -> dict[str, Any]:
        return {
            "error_class": self.error_class,
            "code": self.code,
            "message": self.message,
            "side_effect": self.side_effect,
            "retry_action": self.retry_action,
            "evidence": to_json_value(self.evidence),
        }


@dataclass(frozen=True)
class OperationResult:
    """One result from the canonical Bridge response contract."""

    ok: bool
    method: str
    value: Any = None
    side_effect: str = "none"
    retry_action: str = "do_not_retry"
    evidence: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    error: OperationError | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.ok, bool):
            raise NeutralContractError("operation ok must be a boolean")
        _required_text(self.method, "method")
        require_json_value(self.value, path="value")
        if self.side_effect not in SIDE_EFFECTS:
            raise NeutralContractError("unsupported operation side effect")
        if self.retry_action not in RETRY_ACTIONS:
            raise NeutralContractError("unsupported operation retry action")
        _validate_evidence(self.evidence)
        if self.ok == (self.error is not None):
            raise NeutralContractError("successful results cannot contain an error")
        if self.error is not None and not isinstance(self.error, OperationError):
            raise NeutralContractError("operation error must use OperationError")

    @classmethod
    def success(
        cls,
        method: str,
        value: Any,
        metadata: Mapping[str, Any],
    ) -> "OperationResult":
        return cls(
            ok=True,
            method=method,
            value=value,
            side_effect=str(metadata["side_effect"]),
            retry_action=str(metadata["retry_action"]),
            evidence=_evidence(metadata["evidence"]),
        )

    @classmethod
    def failure(
        cls,
        method: str,
        *,
        error_class: str,
        code: str,
        message: str,
        side_effect: str,
        retry_action: str,
        evidence: Any = None,
    ) -> "OperationResult":
        normalized_evidence = _evidence(evidence)
        error = OperationError(
            error_class=error_class,
            code=code,
            message=message,
            side_effect=side_effect,
            retry_action=retry_action,
            evidence=normalized_evidence,
        )
        return cls(
            ok=False,
            method=method,
            side_effect=side_effect,
            retry_action=retry_action,
            evidence=normalized_evidence,
            error=error,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "method": self.method,
            "value": to_json_value(self.value),
            "side_effect": self.side_effect,
            "retry_action": self.retry_action,
            "evidence": to_json_value(self.evidence),
            "error": self.error.to_dict() if self.error is not None else None,
        }


def _validate_evidence(value: Any) -> None:
    if not isinstance(value, tuple):
        raise NeutralContractError("operation evidence must be a tuple")
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise NeutralContractError("operation evidence entries must be objects")
        require_json_value(item, path=f"evidence[{index}]")


def _evidence(value: Any) -> tuple[dict[str, Any], ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise NeutralContractError("operation evidence must be a list")
    normalized = tuple(dict(item) if isinstance(item, dict) else item for item in value)
    _validate_evidence(normalized)
    return normalized


__all__ = [
    "ERROR_CLASSES",
    "NeutralContractError",
    "OperationError",
    "OperationResult",
    "RETRY_ACTIONS",
    "SIDE_EFFECTS",
    "require_json_value",
    "to_json_value",
]
