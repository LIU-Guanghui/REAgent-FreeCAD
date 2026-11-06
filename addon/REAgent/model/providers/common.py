"""Shared model diagnostics, strict JSON and usage values; no client imports."""


from REAgent.model.messages import TextBlock
from REAgent.model.messages import Instruction
from REAgent.model.errors import ModelError


_ENDINGS = {
    "length": ("model_output_limit", "Model output budget was exhausted"),
    "max_tokens": ("model_output_limit", "Model output budget was exhausted"),
    "max_output_tokens": ("model_output_limit", "Service output or context space was exhausted"),
    "model_context_window_exceeded": ("model_context_limit", "Service context capacity was exceeded"),
    "content_filter": ("model_filtered", "Model service filtered the response"),
    "refusal": ("model_refusal", "Model service refused the request"),
    "insufficient_system_resource": ("model_service_error", "Model service ran out of resources"),
    "aborted": ("model_service_error", "Model service aborted the response"),
    "server_error": ("model_service_error", "Model service failed the response"),
    "rate_limit_exceeded": ("model_service_error", "Model service reported a rate limit"),
    "api_error": ("model_service_error", "Model service reported an internal error"),
    "overloaded_error": ("model_service_error", "Model service is overloaded"),
    "rate_limit_error": ("model_service_error", "Model service reported a rate limit"),
    "timeout_error": ("model_service_error", "Model service timed out while processing"),
}
_NORMAL_ENDINGS = {"stop", "tool_calls", "end_turn", "tool_use", "completed"}


def bearer_headers(config, key):
    return {"Authorization": "Bearer " + key}


class StreamState:
    """Latch native endings before fallible usage parsing or stream cleanup."""

    def __init__(self, identity, *, binding_controls=False):
        self.identity, self.binding_controls = identity, binding_controls
        self.usage, self.failure, self.native_reason = None, None, None
        self.protocol_completed = False
        self.diagnostics = {}

    def ending(self, reason, normal):
        safe = reason if isinstance(reason, str) and reason in _ENDINGS.keys() | _NORMAL_ENDINGS else "unrecognized"
        self.native_reason = self.failure.native_reason if self.failure is not None else safe
        if reason not in normal and self.failure is None:
            code, message = _ENDINGS.get(safe, ("model_protocol_error", "Service returned an unsupported completion reason"))
            self.failure = ModelError(code, message, native_reason=safe)

    def attach_error(self, error):
        if self.failure is not None and error is not self.failure and error.code != "request_cancelled":
            error.secondary_code = error.code
            error.code, error.args = self.failure.code, self.failure.args
        error.native_reason = error.native_reason or self.native_reason
        error.protocol_completed = self.protocol_completed
        error.diagnostics = dict(self.diagnostics)
        if error.usage is None:
            error.usage = self.usage

    def service_failure(self, reason):
        safe = reason if isinstance(reason, str) and reason in _ENDINGS else "unrecognized"
        self.diagnostics["stream_error_type"] = safe
        if self.failure is None:
            self.ending(reason, ())
            if self.failure.code == "model_protocol_error":
                self.failure.code = "model_service_error"
                self.failure.args = ("Model service reported a stream error",)

    def completed(self):
        self.protocol_completed = True
        if self.failure is not None:
            self.attach_error(self.failure)
            raise self.failure

    def detail(self):
        return {"native_reason": self.native_reason, "protocol_completed": self.protocol_completed,
                **self.diagnostics}


def token_count(value):
    if value is not None and (type(value) is not int or value < 0):
        raise ModelError("invalid_stream", "Invalid service usage count")
    return value


def usage_value(input_tokens=None, output_tokens=None, total_tokens=None, *, input_source="reported", details=None):
    values = {"input_tokens": token_count(input_tokens), "output_tokens": token_count(output_tokens),
              "total_tokens": token_count(total_tokens)}
    sources = {name: "reported" if value is not None else "unknown" for name, value in values.items()}
    if (all(value is not None for value in values.values())
            and total_tokens != input_tokens + output_tokens):
        raise ModelError("invalid_stream", "Inconsistent service usage counts")
    if input_tokens is not None:
        sources["input_tokens"] = input_source
    if total_tokens is None and input_tokens is not None and output_tokens is not None:
        values["total_tokens"] = input_tokens + output_tokens
        sources["total_tokens"] = "derived"
    return {**values, "sources": sources,
            "details": {key: token_count(value) for key, value in (details or {}).items() if value is not None}}


def standard_usage(value, *, responses=False):
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ModelError("invalid_stream", "Invalid usage object")
    incoming = value.get("input_tokens" if responses else "prompt_tokens")
    outgoing = value.get("output_tokens" if responses else "completion_tokens")
    total = value.get("total_tokens")
    input_details = value.get("input_tokens_details" if responses else "prompt_tokens_details") or {}
    output_details = value.get("output_tokens_details" if responses else "completion_tokens_details") or {}
    if not isinstance(input_details, dict) or not isinstance(output_details, dict):
        raise ModelError("invalid_stream", "Invalid usage details")
    return usage_value(incoming, outgoing, total, details={
        "cache_read_tokens": input_details.get("cached_tokens", value.get("prompt_cache_hit_tokens")),
        "cache_write_tokens": input_details.get("cache_write_tokens"),
        "cache_miss_tokens": value.get("prompt_cache_miss_tokens"),
        "reasoning_tokens": output_details.get("reasoning_tokens")})


def material_text(message):
    if isinstance(message, Instruction):
        return "system", message.text
    if isinstance(message, TextBlock):
        return message.role, message.text
    raise TypeError("Unsupported neutral model material")


def checked_calls(calls):
    if (len(calls) > 64 or len({call.id for call in calls}) != len(calls)
            or any(not isinstance(value, str) or not value for call in calls
                   for value in (call.id, call.name, call.arguments))):
        raise ModelError("invalid_stream", "Incomplete or duplicate tool call identity")
    return tuple(calls)
