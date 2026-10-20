"""Bounded native log diagnostics without inferred CAD timing."""

import os

from ..errors import error_payload
from .discovery import runtime as discover_runtime


def run(name, args, record, started, ctx):
    _emit = ctx.emit
    _clients = ctx.clients
    if name == "agent_read_freecad_log":
        role = args["role"]
        runtime_state = discover_runtime(role, ctx.clients)
        if not runtime_state[role]["available"]:
            value = {"available": False, "status": "instance_unavailable", "error": runtime_state[role]["error"]}
        else:
            value = _clients[role].call(name, **{k: args[k] for k in ("cursor", "max_bytes") if k in args})
            peer = "source" if role == "target" else "target"
            try:
                peer_source = _clients[peer].call("agent_get_capabilities", view="status")["native_log_source"]
                path = value.get("source", {}).get("path")
                peer_path = peer_source.get("path")
                if path and peer_path:
                    value["shared_source"] = os.path.normcase(os.path.realpath(path)) == os.path.normcase(os.path.realpath(peer_path))
                    value["shared_source_status"] = "checked_configured_paths"
                else:
                    value["shared_source_status"] = "peer_or_selected_path_unavailable"
                value["peer_source"] = peer_source
            except Exception as exc:
                value["shared_source_status"] = "peer_unavailable"
                value["peer_error"] = error_payload(exc)
        summary = {k: value[k] for k in ("available", "status", "source", "offset_start", "offset_end", "payload_bytes",
                   "cursor_reset", "shared_source", "shared_source_status", "error") if k in value}
        _emit(record, "diagnostics.read", name, summary, started, role=role,
              level="info" if value["available"] else "warning")
        return value
    raise ValueError("Unknown tool in workflow group")
