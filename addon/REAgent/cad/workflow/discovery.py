"""List available tools, read input schemas and inspect bound runtime status."""

from REAgent.protocol.features import DRESSUP_SCOPE, EDIT_FEATURE_TYPES
from REAgent.protocol.operations import OPERATION_SPECS
from REAgent.cad.errors import error_payload
from REAgent.cad.action_policy import allowed_tools, model_spec
from REAgent.cad.knowledge import knowledge_assets, read_knowledge, reconstruction_guidance
from REAgent.cad.tools import MODEL_COMMANDS, MODEL_TOOLS


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
    workflow = ctx.spec
    execution = ctx.call_context.get().get("execution") or {}
    model = execution.get("actor") == "model"
    scope = execution.get("scope") if model else None
    if name == "agent_get_guidance":
        knowledge_id = args.get("knowledge_id")
        if knowledge_id is not None and knowledge_id not in {asset.knowledge_id for asset in knowledge_assets(workflow)}:
            raise ValueError("Knowledge card is unavailable for the current workflow")
        value = read_knowledge(knowledge_id) if knowledge_id else reconstruction_guidance(workflow)
        _emit(record, "guidance.read", name, {"knowledge_id": args.get("knowledge_id"), "status": "read"}, started, role="agent")
        return value
    if name == "agent_get_capabilities":
        query = args["request"]
        kind = query["kind"]
        if kind == "catalog":
            allowed = allowed_tools(workflow, scope)
            value = {"tools": [{"name": n, "description": (OPERATION_SPECS[n]["summary"] if n in MODEL_COMMANDS else model_spec(workflow, scope, n)["description"]),
                                "query": {"kind": "tool", "name": n, **({"type_id": "observed native TypeId"} if n == "EditFeature" else {})}}
                               for n in MODEL_TOOLS if n in allowed]}
            value["workspace"] = {
                "type_id": "PartDesign::Body",
                "name": "REAgentWorkspace",
                "label": "Target",
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
            tool_name = query["name"]
            type_id = query.get("type_id")
            value = {"name": tool_name, **model_spec(workflow, scope, tool_name, type_id=type_id)}
            if type_id is not None:
                value["type_id"] = type_id
        elif kind == "runtime":
            if query["role"] != "target" and not workflow.native_source:
                raise ValueError("This CAD workflow binds only Target")
            value = _runtime(query["role"])
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
