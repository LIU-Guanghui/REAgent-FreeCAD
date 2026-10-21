"""Public discovery lists only registered and currently callable flow tools."""

from ..contract import DOMAIN_MODE_GAPS, DRESSUP_SCOPE, OPERATION_SPECS
from ..errors import error_payload
from ..knowledge import read_knowledge, reconstruction_guidance
from ..tools_schema import EXECUTION_POLICY, TOOLS


def runtime(role, clients):
    roles = ("source", "target") if role == "both" else (role,)
    results = {}
    for name in roles:
        try:
            ping = clients[name].call("agent_ping")
            contract = clients[name].call("agent_get_capabilities", view="status")
            operations = contract.get("operation_contract", {}).get("operations", {})
            if operations != OPERATION_SPECS:
                raise ValueError("Installed Bridge contract differs from the MCP contract")
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
            domain_names = [n for n in TOOLS if OPERATION_SPECS.get(n, {}).get("domain")
                            in ("body", "sketch", "datum", "primitive", "extrude",
                                "revolve", "hole", "boolean", "loft", "pipe",
                                "fillet", "chamfer", "draft", "thickness",
                                "mirror", "pattern")]
            value = {"tools": [{"name": n, "description": s["description"], "query": {"kind": "tool", "name": n}} for n,s in TOOLS.items()],
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
            value["execution"] = EXECUTION_POLICY
        elif kind == "tool": value = {"name": query["name"], **TOOLS[query["name"]]}
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
