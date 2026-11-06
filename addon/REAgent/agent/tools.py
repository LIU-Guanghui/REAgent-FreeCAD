"""Small frozen tool definitions and a fixed execution boundary, without a domain."""

from dataclasses import dataclass
from types import MappingProxyType

from REAgent.values import decode_json, encode_json


CONTEXT_TOOL_METHODS = ("directory", "check", "facts", "prepare_context", "prepare_receipt", "reply_materials")
EXECUTION_TOOL_METHODS = ("preflight", "execute", "describe", "notification", "unexecuted",
                          "interrupted", "result_state", "stage_retention", "stage_handoff",
                          "suppress_reply", "report_ready", "action_started")


def check_tools(tools):
    if not all(callable(getattr(tools, name, None)) for name in CONTEXT_TOOL_METHODS):
        raise ValueError("The tool boundary is missing a required context capability")
    if tools.directory() and not all(callable(getattr(tools, name, None)) for name in EXECUTION_TOOL_METHODS):
        raise ValueError("The tool boundary is missing a required execution capability")


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    specification: str


def freeze_tools(contributions, required):
    entries = {}
    for name, specification in contributions:
        if name in entries or name not in required:
            raise ValueError("Duplicate or unknown tool definition: " + name)
        if not isinstance(specification.get("description"), str) or "input_schema" not in specification:
            raise ValueError("Incomplete tool definition: " + name)
        entries[name] = ToolDefinition(name, encode_json(specification))
    if set(entries) != set(required):
        raise ValueError("Tool contribution coverage differs from the declared catalog")
    return MappingProxyType(entries)


def directory(registry):
    return {name: decode_json(item.specification) for name, item in registry.items()}


class EmptyTools:
    """Ordinary chat has an empty catalog and no execution resource."""

    def directory(self, action=None):
        return {}

    def check(self, *, binding_only=False):
        return None

    def facts(self, policy, action=None):
        return None

    def prepare_context(self, policy, conversation):
        return None

    def prepare_receipt(self, policy, observations, *, purpose="action"):
        return None

    def reply_materials(self, reply, rows):
        if reply.calls or rows:
            raise ValueError("No tools exist in this conversation")
        return (), ()

    def action_started(self, action, binding):
        return None

    def suppress_reply(self, reply):
        return False
