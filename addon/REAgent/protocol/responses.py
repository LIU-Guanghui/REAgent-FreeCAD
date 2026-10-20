"""Versioned metadata for canonical core success and failure responses."""

from .contract import BRIDGE_PROTOCOL_VERSION, PRODUCT_ID, request_side_effect, resolve_retry_action


def _operation_success(method, result, params):
    if method == "agent_validate" and result.get("parameter_audit", {}).get("status") in (
            "not_requested", "unavailable", "not_applicable"):
        side_effect = "none"
    elif method == "agent_close_session" and result.get("status") == "already_closed":
        side_effect = "none"
    elif isinstance(result, dict) and result.get("committed") is False:
        side_effect = "none"
    else:
        side_effect = request_side_effect(method, params)
    evidence = [{"kind": "bridge_method", "method": method}]
    if isinstance(result, dict):
        checkpoint = result.get("checkpoint_path") or result.get("saved_to")
        if checkpoint:
            evidence.append({"kind": "checkpoint", "path": checkpoint})
    return {
        "schema_version": 1,
        "bridge_protocol_version": BRIDGE_PROTOCOL_VERSION,
        "product_id": PRODUCT_ID,
        "method": method,
        "side_effect": side_effect,
        "retry_action": "do_not_retry",
        "evidence": evidence,
    }


def _operation_failure(method, exc, code):
    stage = getattr(exc, "operation_stage", "bridge_preflight")
    started = getattr(exc, "write_started", False)
    if code in ("invalid_json", "request_too_large"):
        stage, error_class = "bridge_protocol", "protocol"
    elif getattr(exc, "error_code", None):
        code = exc.error_code
        error_class = getattr(exc, "error_class", "precondition")
    elif hasattr(exc, "issues"):
        stage, error_class, code = "bridge_validation", "precondition", "invalid_request"
    elif getattr(exc, "target_state_unknown", False):
        error_class, code = "native_operation", "recovery_unknown"
    elif getattr(exc, "input_error", False):
        error_class, code = "precondition", "invalid_request"
    elif not started and isinstance(exc, (ValueError, KeyError)):
        error_class, code = "precondition", "invalid_request"
    elif not started and stage == "bridge_preflight":
        error_class, code = "precondition", "capability_unavailable"
    else:
        error_class = {"recompute": "recompute", "result_check": "validity",
                       "persistence": "persistence"}.get(stage, "native_operation")
        code = "execution_failed"
    if getattr(exc, "target_state_unknown", False):
        side_effect = "unknown"
    elif getattr(exc, "committed_side_effect", False):
        side_effect = "committed"
    elif getattr(exc, "restored", False):
        side_effect = "transient_rolled_back"
    elif not started:
        side_effect = "none"
    elif getattr(exc, "request_side_effect", None) == "none":
        side_effect = "none"
    else:
        side_effect = "unknown"
    # Unknown writes always require inspection; an exception-specific hint may
    # refine only outcomes whose side effect has already been classified.
    retry_action = resolve_retry_action(
        error_class,
        side_effect,
        explicit=getattr(exc, "retry_action", None),
    )
    evidence = [
        {"kind": "bridge_method", "method": method},
        {"kind": "exception_type", "type": exc.__class__.__name__},
    ]
    detail = {"kind": "failure", "stage": stage, "restored": bool(getattr(exc, "restored", False))}
    if hasattr(exc, "field_path"):
        detail["path"] = exc.field_path
    if hasattr(exc, "failure_detail"):
        detail["detail"] = exc.failure_detail
    if hasattr(exc, "issues"):
        detail.update(issues=exc.issues, issue_count=exc.issue_count, issues_omitted=exc.issues_omitted)
    evidence.append(detail)
    diagnostics = getattr(exc, "diagnostics", None)
    if isinstance(diagnostics, dict):
        evidence.append(diagnostics)
    return {
        "schema_version": 1,
        "bridge_protocol_version": BRIDGE_PROTOCOL_VERSION,
        "product_id": PRODUCT_ID,
        "method": method,
        "stage": stage, "code": code,
        "error_class": error_class,
        "side_effect": side_effect,
        "retry_action": retry_action,
        "evidence": evidence,
    }
