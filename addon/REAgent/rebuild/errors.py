"""Bounded Controller and Bridge failure facts."""

from REAgent.protocol.definitions import resolve_retry_action


class PublicError(ValueError):
    def __init__(self, code, stage, message, *, side_effect="none"):
        super().__init__(message)
        self.code, self.stage, self.side_effect = code, stage, side_effect


def error_payload(exc, *, default_stage="public_execution"):
    operation = getattr(exc, "operation", None)
    transmission_started = None
    explicit_retry_action = None
    if operation is not None and operation.error is not None:
        error = operation.error
        result = {"code": error.code, "stage": "bridge_execution", "message": error.message,
                  "side_effect": operation.side_effect, "retry_action": operation.retry_action,
                  "error_class": error.error_class, "origin_method": operation.method}
        explicit_retry_action = operation.retry_action
        if error.error_class == "connection": result["stage"] = "bridge_transport"
        if error.error_class == "protocol": result["stage"] = "bridge_protocol"
        for item in operation.evidence:
            if item.get("kind") in ("failure", "input_validation"):
                result["stage"] = item.get("stage", "bridge_validation")
                for key in ("path", "issues", "issue_count", "issues_omitted", "restored", "detail"):
                    if key in item: result[key] = item[key]
            elif item.get("kind") == "transport":
                transmission_started = item.get("transmission_started")
                result["transmission_started"] = transmission_started
            elif item.get("kind") == "recovery":
                result["recovery"] = {k: item[k] for k in ("original_error", "recovery_error", "detail") if k in item}
            elif item.get("kind") in ("edge_selection_resolution", "face_selection_resolution"):
                result["selection_resolution"] = item
    else:
        invalid = isinstance(exc, ValueError)
        error_class = getattr(
            exc, "error_class", "precondition" if invalid else "internal"
        )
        result = {"code": getattr(exc, "code", "invalid_request" if invalid else "internal_error"),
                  "stage": getattr(exc, "stage", "public_validation" if invalid else default_stage),
                  "message": str(exc), "side_effect": getattr(exc, "side_effect", "none"),
                  "error_class": error_class}
        explicit_retry_action = getattr(exc, "retry_action", None)
        if hasattr(exc, "issues"):
            result.update(issues=exc.issues, issue_count=exc.issue_count, issues_omitted=exc.issues_omitted)
        if hasattr(exc, "field_path"):
            result["path"] = exc.field_path
        if hasattr(exc, "file_error"):
            result["file_error"] = exc.file_error
    if hasattr(exc, "close_results"):
        result["close_results"] = exc.close_results
        result["side_effect"] = getattr(exc, "close_side_effect", result["side_effect"])
    result["retry_action"] = resolve_retry_action(
        result["error_class"],
        result["side_effect"],
        explicit=explicit_retry_action,
        transmission_started=transmission_started,
    )
    message = result["message"]
    result["message"] = message[:500]
    if len(message) > 500: result["message_characters_omitted"] = len(message)-500
    return result
