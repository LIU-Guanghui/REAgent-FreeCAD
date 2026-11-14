"""JSON Bridge result and actionable error envelopes."""

from dataclasses import dataclass, field
from typing import Any, Mapping
from REAgent.json_values import require_json_value, require_text, to_json_value
from .definitions import RETRY_ACTIONS

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
            raise ValueError("unsupported operation error class")
        require_text(self.code, "code")
        require_text(self.message, "message")
        if self.side_effect not in SIDE_EFFECTS:
            raise ValueError("unsupported operation side effect")
        if self.retry_action not in RETRY_ACTIONS:
            raise ValueError("unsupported operation retry action")
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
            raise ValueError("operation ok must be a boolean")
        require_text(self.method, "method")
        require_json_value(self.value, path="value")
        if self.side_effect not in SIDE_EFFECTS:
            raise ValueError("unsupported operation side effect")
        if self.retry_action not in RETRY_ACTIONS:
            raise ValueError("unsupported operation retry action")
        _validate_evidence(self.evidence)
        if self.ok == (self.error is not None):
            raise ValueError("successful results cannot contain an error")
        if self.error is not None and not isinstance(self.error, OperationError):
            raise ValueError("operation error must use OperationError")

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
        raise ValueError("operation evidence must be a tuple")
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise ValueError("operation evidence entries must be objects")
        require_json_value(item, path=f"evidence[{index}]")

def _evidence(value: Any) -> tuple[dict[str, Any], ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError("operation evidence must be a list")
    normalized = tuple(dict(item) if isinstance(item, dict) else item for item in value)
    _validate_evidence(normalized)
    return normalized
