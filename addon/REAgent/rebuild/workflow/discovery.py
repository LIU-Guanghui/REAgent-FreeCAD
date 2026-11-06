"""Public discovery lists only registered and currently callable flow tools."""

from REAgent.protocol.features import DOMAIN_MODE_GAPS, DRESSUP_SCOPE, EDIT_FEATURE_TYPES
from REAgent.protocol.operations import OPERATION_SPECS
from REAgent.rebuild.errors import error_payload
from REAgent.values import encode_json
from REAgent.rebuild.knowledge import read_knowledge, reconstruction_guidance
from REAgent.rebuild.tools import MODEL_COMMANDS, MODEL_TOOLS, model_tool


def runtime(role, clients):
    roles = ("source", "target") if role == "both" else (role,)
    results = {}
    for name in roles:
        try:
            ping = clients[name].call("agent_ping")
            contract = clients[name].call("agent_get_capabilities", view="status")
            operations = contract.get("operation_contract", {}).get("operations", {})
            if operations != OPERATION_SPECS:
                raise ValueError("Installed Bridge contract differs from the Controller contract")
            clients[name].install_operation_contract(contract["operation_contract"])
            results[name] = {"available": True, "instance": ping, "native_log_source": contract.get("native_log_source")}
        except Exception as exc:
            error = error_payload(exc)
            if not getattr(exc, "operation", None):
                error.update(code="capability_unavailable", stage="capability_discovery")
            results[name] = {"available": False, "error": error}
    return results


def run(name, args, record, started, ctx):
    _runtime = lambda role: runtime(role, ctx.clients)
    _emit = ctx.emit
    if name == "agent_get_guidance":
        value = read_knowledge(args["knowledge_id"]) if "knowledge_id" in args else reconstruction_guidance()
        _emit(record, "guidance.read", name, {"knowledge_id": args.get("knowledge_id"), "status": "read"}, started, role="agent")
        return value
    if name == "agent_get_capabilities":
        query = args["request"]
        kind = query["kind"]
        if kind == "catalog":
            domain_names = list(MODEL_COMMANDS)
            value = {"tools": [{"name": n, "description": (OPERATION_SPECS[n]["summary"] if n in MODEL_COMMANDS else model_tool(n)["description"]),
                                "query": {"kind": "tool", "name": n, **({"type_id": "observed native TypeId"} if n == "EditFeature" else {})}} for n in MODEL_TOOLS],
                     "domain_commands": {"status": "implementation_pending_user_model_review", "available": len(domain_names),
                                         "names": domain_names,
                                         "mode_gaps": [{"domain": domain, "unsupported": list(gaps)}
                                                       for domain, gaps in DOMAIN_MODE_GAPS.items()],
                                         "verification_gap": "Native host probes are not product command-chain, recovery, save or GUI validation; current Dev runtime gaps remain unverified."}}
            value["workspace"] = {
                "type_id": "PartDesign::Body",
                "name": "REAgentWorkspace",
                "label": "Rebuild",
                "origin": "native",
                "result": "main_body_tip",
                "tool_body_registry": "non_link_names",
                "body_result": "single_solid",
                "independent_solids": "native_partdesign_boolean",
                "placement_values": "body_local",
                "geometry_observations_and_comparison": "document_global",
                "document_container_policy": "main_body_or_registered_tool_body",
            }
            value["dressup_scope"] = DRESSUP_SCOPE
            value["editable_native_types"] = list(EDIT_FEATURE_TYPES)
        elif kind == "tool":
            type_id = query.get("type_id")
            value = {"name": query["name"], **model_tool(query["name"], type_id=type_id)}
            if type_id is not None:
                value["type_id"] = type_id
                if len(encode_json(value).encode("utf-8")) > 16000:
                    raise ValueError("Complete edit contract exceeds the discovery budget")
        elif kind == "runtime": value = _runtime(query["role"])
        else: raise ValueError("Unknown capability query")
        summary = {"request": query, "status": "available"}
        if kind == "runtime":
            summary["instances"] = {role: {k: item[k] for k in ("available", "error") if k in item} for role,item in value.items()}
            if not all(item["available"] for item in value.values()): summary["status"] = "unavailable"
        _emit(record, "capabilities.read", name, summary, started,
              level="info" if summary["status"] == "available" else "warning", role=query.get("role", "agent"))
        return value
    raise ValueError("Unknown tool in workflow group")


def contribute():
    return {name: run for name in ('agent_get_capabilities', 'agent_get_guidance')}
