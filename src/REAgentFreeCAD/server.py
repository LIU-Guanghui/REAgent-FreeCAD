"""Schema-driven MCP transport for native FreeCAD operations."""

import argparse
import asyncio
import json
import time
import uuid

import anyio
from mcp import types as mcp_types
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.shared.message import SessionMessage
from mcp.types import CallToolResult, TextContent, Tool

from . import __version__
from .contract import HISTORY_COMMANDS, PRIMITIVE_COMMANDS, resolve_retry_action, validate_input_schema
from .errors import PublicError, envelope_issues, error_payload
from .execution import Execution
from .features import (
    body,
    boolean,
    chamfer,
    datum,
    draft,
    extrude,
    fillet,
    hole,
    loft,
    mirror,
    pattern,
    pipe,
    primitives,
    revolve,
    sketch,
    thickness,
)
from .log_summary import failure_summary, request_summary
from .tools_schema import TOOLS
from .view_delivery import cleanup_view_singles
from .workflow import diagnostics, discovery, history, observation, session, validation
from .workflow.context import WorkflowContext

mcp = Server("REAgentFreeCAD", version=__version__)
workflow = WorkflowContext()
_FLOW_MODULES = {
    **dict.fromkeys(("agent_ping", "agent_start_session", "agent_get_state", "agent_close_session", "agent_journal"), session),
    **dict.fromkeys(("agent_get_capabilities", "agent_get_guidance"), discovery),
    "agent_observe": observation,
    **dict.fromkeys(("agent_undo", "agent_redo"), history),
    **dict.fromkeys(("agent_validate", "agent_save"), validation),
    "agent_read_freecad_log": diagnostics,
    "PartDesign_Body": body,
    "PartDesign_NewSketch": sketch,
    "Sketcher_MapSketch": sketch,
    "Sketcher_EditSketch": sketch,
    "Sketcher_ValidateSketch": sketch,
    **dict.fromkeys(("Part_DatumPlane", "Part_DatumLine", "Part_DatumPoint",
                     "Part_CoordinateSystem"), datum),
    **dict.fromkeys(PRIMITIVE_COMMANDS, primitives),
    **{name: {"extrude": extrude, "revolve": revolve,
              "hole": hole, "boolean": boolean, "loft": loft,
              "pipe": pipe, "fillet": fillet, "chamfer": chamfer,
              "draft": draft, "thickness": thickness, "mirror": mirror,
              "pattern": pattern}[spec["domain"]]
       for name, spec in HISTORY_COMMANDS.items()},
}
if set(_FLOW_MODULES) != set(TOOLS):
    raise RuntimeError("Public workflow routes differ from the tool catalog")


def dispatch(name, args):
    if name not in TOOLS: raise PublicError("capability_unavailable", "public_discovery", "Unknown public tool")
    validate_input_schema(args, TOOLS[name]["input_schema"], path=name)
    record = workflow.record(args["session_id"]) if "session_id" in args else None
    if (record is not None and (record["startup"]["status"] != "ready" or record["closed"])
            and name not in ("agent_get_state", "agent_close_session", "agent_journal")):
        raise PublicError("session_start_incomplete", "public_preflight",
                          "Session is not active; inspect startup, close and journal facts before any modeling",
                          side_effect="unknown" if record["blocked"] else "none")
    context = workflow.call_context.get()
    if context is not None and "evidence" in args and "intent" in args:
        context["feature_evidence"] = args["evidence"]
        context["feature_intent"] = args["intent"][:400]
    started = (workflow.call_context.get() or {}).get("started", time.perf_counter())
    group = _FLOW_MODULES[name]
    return group.run(name, args, record, started, workflow)


@mcp.list_tools()
async def list_tools():
    return [Tool(name=name, description=spec["description"], inputSchema=spec["input_schema"]) for name,spec in TOOLS.items()]

@mcp.call_tool(validate_input=False)
async def call_tool(name, arguments):
    return await execution.run(name, arguments)


def _success_response(value, context):
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise PublicError("response_encoding_failed", "public_response", str(exc), side_effect=context["side_effect"]) from exc
    return CallToolResult(content=[TextContent(type="text", text=encoded), *context.get("image_content", [])])


def _execute_tool(name, arguments, context):
    token = workflow.call_context.set(context)
    record = workflow.associated(arguments)
    if record is not None:
        workflow.progress(session_id=record["session_id"])
    try:
        value = dispatch(name, {} if arguments is None else arguments)
        value = {**value, "operation_id": context["operation_id"]}
        if record is not None: value["journal"] = record["journal"].metadata()
        response = _success_response(value, context)
        # Publish only after successful local response construction. This does
        # not certify network receipt or inspection; failures never attach images.
        pending = context.get("pending_evidence")
        if pending is not None:
            evidence_record, evidence_id, fact = pending
            cleanup_started = time.perf_counter()
            journal_ready = evidence_record["journal"].complete
            if journal_ready:
                cleanup = cleanup_view_singles(value, evidence_record["artifacts"])
            elif value.get("image_delivery", {}).get("representation") in ("initial_boards", "board"):
                cleanup = {"status": "skipped", "reason": "journal_pending", "deleted_count": 0}
            else:
                cleanup = None
            if cleanup is not None:
                value["image_cleanup"] = cleanup
                fact["image_cleanup"] = {key: item for key, item in cleanup.items() if key != "files"}
                workflow.emit(evidence_record, "observation.images_cleanup", "agent_observe",
                              {"evidence_id": evidence_id, **cleanup}, cleanup_started,
                              level="info" if cleanup["status"] == "completed" else "warning",
                              role=fact["role"], persist=journal_ready)
                value["journal"] = evidence_record["journal"].metadata()
                response = _success_response(value, context)
            previous_preparation = evidence_record["initial_source_views"]
            evidence_record["evidence_facts"][evidence_id] = fact
            try:
                evidence_record["evidence_order"].append(evidence_id)
                if fact["kind"] == "initial_views" and fact["image_delivery"]["status"] == "prepared":
                    evidence_record["initial_source_views"] = {
                        "status": "prepared", "evidence_id": evidence_id,
                        "direction_count": fact["initial_views"]["direction_count"],
                        "capture_id": fact["initial_views"]["capture_id"],
                        "scope": "local_response_preparation", "inspection": "not_attested"}
            except Exception:
                evidence_record["evidence_facts"].pop(evidence_id, None)
                if evidence_record["evidence_order"] and evidence_record["evidence_order"][-1] == evidence_id:
                    evidence_record["evidence_order"].pop()
                evidence_record["initial_source_views"] = previous_preparation
                raise
        return response
    except Exception as exc:
        if record is None:
            record = workflow.sessions.get(context.get("progress", {}).get("session_id"))
        error = {"operation_id": context["operation_id"], **error_payload(exc)}
        if record is not None:
            error["session_id"] = record["session_id"]
        if not isinstance(exc, ValueError) and getattr(exc, "operation", None) is None:
            error["side_effect"] = context["side_effect"]
            error["retry_action"] = resolve_retry_action(
                error["error_class"], error["side_effect"]
            )
        if hasattr(exc, "runtime"): error["runtime"] = exc.runtime
        query, role = _request_summary(name, arguments)
        if name != "agent_journal":
            workflow.emit(record, "operation.failed", name, failure_summary(error, query), context["started"],
                          level="error", role=role, persist=not (record and record["journal"].start_failed))
        if record is not None: error["journal"] = record["journal"].metadata()
        return CallToolResult(isError=True, content=[TextContent(type="text", text=json.dumps(error, ensure_ascii=False))])
    finally:
        workflow.call_context.reset(token)


execution = Execution(_execute_tool)


def _request_summary(name, arguments):
    if not isinstance(arguments, dict): return {}, "agent"
    summary = request_summary(name, arguments)
    request = arguments.get("request")
    role = arguments.get("role", request.get("role") if isinstance(request, dict) else None)
    if role not in ("source", "target", "both"):
        role = "target" if name in ("agent_undo", "agent_redo", "agent_save", "agent_validate",
                                     "PartDesign_Body", "PartDesign_NewSketch", "Sketcher_MapSketch",
                                     "Sketcher_EditSketch",
                                     "Sketcher_ValidateSketch",
                                     "Part_DatumPlane", "Part_DatumLine", "Part_DatumPoint",
                                     "Part_CoordinateSystem") or name in PRIMITIVE_COMMANDS or name in HISTORY_COMMANDS else "agent"
    return summary, role


async def _guard_tool_envelopes(read, write, forwarded):
    """Validate tools/call before SDK request deserialization; never access CAD here."""
    async with forwarded:
        async for item in read:
            request = item.message.root if isinstance(item, SessionMessage) else None
            if isinstance(request, mcp_types.JSONRPCRequest) and request.method == "tools/call":
                started = time.perf_counter()
                params = request.params if isinstance(request.params, dict) else {}
                arguments = params.get("arguments")
                try:
                    mcp_types.CallToolRequest.model_validate(request.model_dump())
                    if "arguments" in params and not isinstance(arguments, dict):
                        raise PublicError("invalid_request", "mcp_envelope", "tools/call arguments must be an object")
                except ValueError as exc:
                    name = params.get("name")
                    name = name if isinstance(name, str) else "tools/call"
                    record = workflow.associated(arguments)
                    error = {"operation_id": "op-" + uuid.uuid4().hex[:20], "code": "invalid_request",
                             "stage": "mcp_envelope", "message": "Invalid tools/call envelope", "side_effect": "none",
                             "error_class": "precondition",
                             "retry_action": resolve_retry_action("precondition", "none"),
                             **envelope_issues(exc)}
                    if not error["issues"]:
                        error.update(issues=[{"path": "params.arguments", "message": str(exc)[:200]}], issue_count=1)
                    query, role = _request_summary(name, arguments)
                    # Never race the worker's journal writes from the event loop.
                    if not execution.busy and name != "agent_journal":
                        workflow.emit(record, "operation.failed", name, failure_summary(error, query), started,
                                      level="error", role=role, persist=not (record and record["journal"].start_failed))
                    if record is not None: error["journal"] = record["journal"].metadata()
                    result = CallToolResult(isError=True, content=[TextContent(type="text", text=json.dumps(error, ensure_ascii=False))])
                    response = mcp_types.JSONRPCResponse(jsonrpc="2.0", id=request.id, result=result.model_dump(exclude_none=True))
                    await write.send(SessionMessage(mcp_types.JSONRPCMessage(root=response)))
                    continue
            # Invalid raw JSON/JSON-RPC arrives as Exception, without a trustworthy session.
            await forwarded.send(item)

async def _serve():
    async with stdio_server() as (read, write):
        sender, receiver = anyio.create_memory_object_stream(0)
        async with anyio.create_task_group() as group:
            group.start_soon(_guard_tool_envelopes, read, write, sender)
            try:
                await mcp.run(receiver, write, mcp.create_initialization_options())
            finally:
                group.cancel_scope.cancel()


def main():
    parser = argparse.ArgumentParser(description="Native REAgentFreeCAD MCP server")
    parser.add_argument("--timeout", type=float, default=10.0)
    args = parser.parse_args()
    for client in workflow.clients.values(): client.timeout = args.timeout
    asyncio.run(_serve())


if __name__ == "__main__": main()
