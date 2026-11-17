"""Agent-facing tool calls and execution boundary."""

from dataclasses import dataclass

CONTEXT_TOOL_METHODS = ("directory", "check", "facts", "prepare_receipt", "reply_materials")
EXECUTION_TOOL_METHODS = ("preflight", "execute", "describe", "notification", "unexecuted",
                          "interrupted", "result_state", "stage_retention", "stage_handoff",
                          "suppress_reply", "report_ready", "action_started")


def check_tools(tools):
    if not all(callable(getattr(tools, name, None)) for name in CONTEXT_TOOL_METHODS):
        raise ValueError("The tool boundary is missing a required context capability")
    if tools.directory() and not all(callable(getattr(tools, name, None)) for name in EXECUTION_TOOL_METHODS):
        raise ValueError("The tool boundary is missing a required execution capability")


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: str


def project_calls(calls):
    return tuple(ToolCall(call.id, call.name, call.arguments) for call in calls)


class EmptyTools:
    """Ordinary chat has an empty catalog and no execution resource."""

    def directory(self, action=None):
        return {}

    def check(self, *, binding_only=False):
        return None

    def facts(self, policy, action=None):
        return None

    def prepare_receipt(self, policy, observations, *, purpose="action"):
        return None

    def reply_materials(self, calls, rows):
        if calls or rows:
            raise ValueError("No tools exist in this conversation")
        return (), ()

    def action_started(self, action, binding):
        return None

    def suppress_reply(self, calls):
        return False
