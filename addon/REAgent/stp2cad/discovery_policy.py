"""Immutable domain policy prepared from real retained results, never summaries."""

from dataclasses import dataclass

from REAgent.json_values import decode_json, encode_json
from .tools import MODEL_COMMANDS


@dataclass(frozen=True)
class DiscoveryPolicy:
    command: str | None = None
    observations: str = "{}"
    type_id: str | None = None

    @property
    def seen(self):
        return decode_json(self.observations)


def prepare_context(policy, tool_results):
    command = type_id = None
    for call, result in tool_results:
        if call.name != "agent_get_capabilities" or result.value["is_error"]:
            continue
        request = decode_json(call.arguments).get("request", {})
        if request.get("kind") == "tool" and request.get("name") in MODEL_COMMANDS:
            retained = result.value["result"]
            if retained.get("name") == request["name"] and "input_schema" in retained:
                command, type_id = retained["name"], retained.get("type_id")
    return DiscoveryPolicy(command, policy.observations, type_id)


def prepare_receipt(policy, observations, *, purpose="action"):
    if purpose != "action":
        return policy
    seen = policy.seen
    for key, payload in observations:
        seen[key] = {**payload, "scope": "model_request_completed", "inspection": "not_attested"}
    return DiscoveryPolicy(policy.command, encode_json(seen), policy.type_id)
