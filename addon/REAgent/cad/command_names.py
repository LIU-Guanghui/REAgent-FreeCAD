"""Authoritative pure command names shared across CLI, application, and CAD tools without loading schemas or knowledge."""

from REAgent.protocol.features import FEATURE_SPECS

FIXED_FLOW_TOOLS = (
    "agent_read_freecad_log",
    "agent_ping",
    "agent_get_capabilities",
    "agent_get_knowledge",
    "agent_open_session",
    "agent_get_state",
    "agent_read_image",
    "agent_manage_journal",
    "agent_observe",
    "agent_undo",
    "agent_redo",
    "agent_validate",
    "agent_save",
    "agent_close_session",
)
MODEL_COMMANDS = tuple(FEATURE_SPECS)
FIXED_TOOLS = (*FIXED_FLOW_TOOLS, *MODEL_COMMANDS)

if len(FIXED_FLOW_TOOLS) != 14 or set(FIXED_FLOW_TOOLS) & set(MODEL_COMMANDS):
    raise RuntimeError("Fixed-mode command names overlap or differ from expected count")
