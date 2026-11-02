"""Immutable domain policy prepared from real retained results, never summaries."""

from dataclasses import dataclass

from REAgent.agent.messages import ToolOutcome
from REAgent.model.messages import AssistantTurn
from REAgent.values import decode_json, encode_json
from .tools import MODEL_COMMANDS


@dataclass(frozen=True)
class DiscoveryPolicy:
    command: str | None = None
    observations: str = "{}"

    @property
    def seen(self):
        return decode_json(self.observations)


def prepare_context(policy, conversation):
    command = None
    for unit in conversation.units:
        reply = next((item for item in unit.messages if isinstance(item, AssistantTurn)), None)
        if reply is None:
            continue
        outcomes = {item.call_id: item for item in unit.messages if isinstance(item, ToolOutcome)}
        for call in reply.calls:
            result = outcomes.get(call.id)
            if call.name != "agent_get_capabilities" or result is None or result.value["is_error"]:
                continue
            request = decode_json(call.arguments).get("request", {})
            if request.get("kind") == "tool" and request.get("name") in MODEL_COMMANDS:
                command = request["name"]
    return DiscoveryPolicy(command, policy.observations)


def prepare_receipt(policy, observations):
    seen = policy.seen
    seen.update({key: {**payload, "scope": "model_request_completed", "inspection": "not_attested"}
                 for key, payload in observations})
    return DiscoveryPolicy(policy.command, encode_json(seen))
