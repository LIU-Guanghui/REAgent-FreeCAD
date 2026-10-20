"""Public MCP contracts; Bridge envelopes are never exposed as public tools."""

from .contract import (
    HISTORY_COMMANDS,
    IMAGE_CONTENT_LIMITS,
    LOG_READ_PROPERTIES,
    NAME_SCHEMA,
    OBSERVATION_REQUEST_SCHEMA,
    OPERATION_SPECS,
    OUTPUT_DIR_SCHEMA,
    PRIMITIVE_COMMANDS,
    TEXT_SCHEMA,
    VALIDATION_OPTIONS_SCHEMA,
    VIEW_EVIDENCE_CONTRACT,
    SESSION_OBSERVATION_POLICY_SCHEMA,
    _branch,
    _schema,
    _string,
    validate_schema_definition,
)
from .knowledge import _ASSETS

SESSION = {"session_id": TEXT_SCHEMA}
EXECUTION_POLICY = {
    "serialization": "one_worker_per_mcp_process",
    "response_wait_seconds": 30.0,
    "retained_operations": 8,
    "retention_scope": (
        "Eight tool executions, not eight models or sessions. Oldest completed operations are evicted when a new execution starts; "
        "local operation queries do not consume slots. Retrieve deferred results before starting further work. "
        "An absent ID is not proof that its operation did not execute."
    ),
    "running_result": "Query agent_get_state with operation_id; never repeat the operation.",
    "lost_response": "agent_get_state with no arguments lists retained operations in this MCP process.",
    "cancellation": "Ends the response wait, not the worker or native operation; writes remain serialized.",
    "busy_exception": "Only agent_get_state without session_id reads published operation snapshots while busy. Journal read/flush wait for an idle worker; a 30-second response timeout does not end busy.",
}
ROLE = _string("Bound GUI role", ("source", "target"))


def tool(description, properties, required):
    return {"description": description, "input_schema": _schema(properties, required)}


def _public_native_schema(schema):
    """Remove the internal binding from this object and its same-object forms."""
    result = dict(schema)
    if "properties" in schema:
        result["properties"] = {key: value for key, value in schema["properties"].items()
                                if key != "document"}
    if "required" in schema:
        result["required"] = [key for key in schema["required"] if key != "document"]
    if "oneOf" in schema:
        result["oneOf"] = [_public_native_schema(branch) for branch in schema["oneOf"]]
    return result


TOOLS = {
    "agent_read_freecad_log": tool("Read a bounded tail or cursor increment of the selected instance's configured native log for diagnosis. Does not prove which call caused a line. Never changes logging configuration.",
                             {**SESSION, "role": ROLE, **LOG_READ_PROPERTIES}, ("role",)),
    "agent_ping": tool("Read both bound GUI instances without changing window state or focus. Window presentation does not determine connectivity; never starts FreeCAD.", {}, ()),
    "agent_get_capabilities": tool("Discover registered public tools and bound runtime status; native command support is listed only after implementation.",
                             {"request": {}, **SESSION}, ("request",)),
    "agent_get_guidance": tool("Read a knowledge card or its catalog; cards are guidance, not executable parameter contracts.",
                         {"knowledge_id": _string("Knowledge card", [a.knowledge_id for a in _ASSETS]), **SESSION}, ()),
    "agent_start_session": tool("Create or resume a bound workspace and match both STEP revisions. Allocate all artifacts from the source filename in output_dir and persist the real journal before CAD creation. Resume explicitly matches source_path and saves to a new file. visual requires complete initial Source views before modeling; geometry_only requires explicit human task permission. Never change policy to bypass failed views.",
                          {}, ()),
    "agent_get_state": tool("Read actual objects, Result, native undo/redo, bounded current-session evidence IDs and historical validation.", SESSION, ("session_id",)),
    "agent_journal": tool(
        "Read original bounded session events or explicitly flush them to the bound output_dir and unique journal path. "
        "No CAD access; available in failed startup, unknown native write and closed/journal_pending states when the worker is idle. "
        "A fully closed session with no unconfirmed roles is released once its journal is complete. "
        "Recorded event loss or cleanup residue cannot be recovered by flush. Never infers model recovery or migrates the log.", {}, ()),
    "agent_observe": tool("Get role-local revision-bound evidence. Source initial_views deterministically captures all six orthogonal and eight corner directions at one scale and attaches both labeled boards; inspect all 14 before modeling and choose additional local views afterward. Initial size limits include board labels; lower resolution to retry, never omit directions. Ordinary view selects up to eight directions; paired attaches a full board or complete registered pairs when over budget. Image preparation certifies neither receipt nor inspection. Evidence is published after local success-response construction. Non-image queries remain readable under write blocks.",
                    {**SESSION, "role": ROLE, "request": OBSERVATION_REQUEST_SCHEMA, "purpose": TEXT_SCHEMA},
                    ("session_id", "role", "request")),
    "agent_undo": tool("Undo the named committed REAgent transaction only when it is the native stack top and owned by this session; never traverses user history.",
                 {**SESSION, "transaction": TEXT_SCHEMA}, ("session_id", "transaction")),
    "agent_redo": tool("Redo the named committed REAgent transaction only when it is the native stack top and owned by this session.",
                 {**SESSION, "transaction": TEXT_SCHEMA}, ("session_id", "transaction")),
    "agent_validate": tool("Read current Target native state by default; measurements, approximate surface sampling, fixed-direction Booleans and parameter audit are independent explicit choices. Descriptive results never accept the task. Without parameter_audit this is read-only; an unready document is reported without recomputing it.",
                     {**SESSION, "options": VALIDATION_OPTIONS_SCHEMA}, ("session_id",)),
    "agent_save": tool(OPERATION_SPECS["agent_save"]["summary"],
                 SESSION, ("session_id",)),
    "agent_close_session": tool("Close only the process-bound session documents with explicit discard_unsaved=true, including while Target writes are blocked. It never saves, recomputes, aborts, clears history, or re-identifies a same-named document. Partial two-role close facts are retained until each bound document is confirmed.",
                          {**SESSION, "discard_unsaved": {"type": "boolean", "enum": [True]}}, ("session_id", "discard_unsaved")),
}
_start = {"document": NAME_SCHEMA,
          "observation_policy": SESSION_OBSERVATION_POLICY_SCHEMA,
          "source_path": TEXT_SCHEMA, "output_dir": OUTPUT_DIR_SCHEMA,
          "linear_tolerance_mm": {"type": "number", "exclusiveMinimum": 0},
          "angular_tolerance_deg": {"type": "number", "exclusiveMinimum": 0}}
TOOLS["agent_start_session"]["input_schema"] = {"type": "object", "oneOf": [
    _schema(_start, ("source_path", "output_dir")),
    _schema({"document": NAME_SCHEMA, "resume_path": TEXT_SCHEMA, "source_path": TEXT_SCHEMA,
             "observation_policy": SESSION_OBSERVATION_POLICY_SCHEMA,
             "output_dir": OUTPUT_DIR_SCHEMA}, ("resume_path", "source_path", "output_dir")),
]}
TOOLS["agent_journal"]["input_schema"] = {"oneOf": [
    _schema({**SESSION, "action": _string("Journal action", ("read",)),
             "after_sequence": {"type": "integer", "minimum": 0},
             "limit": {"type": "integer", "minimum": 1, "maximum": 100}}, ("session_id", "action")),
    _schema({**SESSION, "action": _string("Journal action", ("flush",)),
             "output_dir": OUTPUT_DIR_SCHEMA}, ("session_id", "action", "output_dir")),
]}
for _domain_name in ("PartDesign_Body", "PartDesign_NewSketch", "Sketcher_MapSketch",
                     "Sketcher_EditSketch", "Sketcher_ValidateSketch",
                     "Part_DatumPlane", "Part_DatumLine", "Part_DatumPoint",
                     "Part_CoordinateSystem", *PRIMITIVE_COMMANDS,
                     *HISTORY_COMMANDS):
    _bridge = OPERATION_SPECS[_domain_name]["input_schema"]
    TOOLS[_domain_name] = {
        "description": OPERATION_SPECS[_domain_name]["summary"],
        "input_schema": _public_native_schema(_bridge),
    }
TOOLS["agent_get_state"]["input_schema"] = {"type": "object", "oneOf": [
    _schema(SESSION, ("session_id",)),
    _schema({"operation_id": TEXT_SCHEMA}, ("operation_id",)),
    _schema({}, ()),
]}
TOOLS["agent_get_state"]["description"] += (
    " With operation_id, read retained execution progress/outcome without CAD access. "
    "With no arguments, list the last %d operations in this MCP process to recover a lost response. "
    "A running operation is not rolled back; never repeat its write."
) % EXECUTION_POLICY["retained_operations"]
TOOLS["agent_get_state"]["description"] += (
    " Session responses include allocated artifacts independently of native checkpoint_path (null before first save). Failed startup and closed journal retention use one local state shape with native_state=not_queried."
)
TOOLS["agent_observe"]["description"] += " " + VIEW_EVIDENCE_CONTRACT["mcp_image_retention"]
TOOLS["agent_start_session"]["description"] += (
    " Calls taking over %g seconds return running with operation_id; query agent_get_state for the outcome and session_id."
) % EXECUTION_POLICY["response_wait_seconds"]
_capability_queries = [
    _branch("kind", "catalog", {}),
    _branch("kind", "tool", {"name": _string("Public tool", TOOLS)}),
    _branch("kind", "runtime", {"role": _string("Role", ("source", "target", "both"))}),
]
TOOLS["agent_get_capabilities"]["input_schema"] = {"type": "object", "oneOf": [
    _schema({"request": query, **SESSION},
            ("request",))
    for query in _capability_queries
]}
TOOLS["agent_observe"]["image_content_limits"] = dict(IMAGE_CONTENT_LIMITS)
TOOLS["agent_observe"]["view_evidence_contract"] = VIEW_EVIDENCE_CONTRACT
TOOLS["agent_observe"]["description"] += " paired=true requires role=%s." % VIEW_EVIDENCE_CONTRACT["paired"]["role"]
for spec in TOOLS.values():
    validate_schema_definition(spec["input_schema"])
