"""Internal CAD tools and the single binding-free model schema projection."""

from REAgent.protocol.features import EDIT_FEATURE_TYPES, FEATURE_SPECS

from copy import deepcopy

from REAgent.protocol.observations import (DEFAULT_VIEW_DIRECTIONS, IMAGE_CONTENT_LIMITS, IMAGE_OBSERVATION_KINDS, VIEW_EVIDENCE_CONTRACT,
    VIEW_QUERY_SCHEMA, SESSION_OBSERVATION_POLICY_SCHEMA, view_direction_label)
from REAgent.protocol.definitions import LOG_READ_PROPERTIES, NAME_SCHEMA, OUTPUT_DIR_SCHEMA, TEXT_SCHEMA, VALIDATION_OPTIONS_SCHEMA, validation_items
from REAgent.protocol.operations import OBSERVATION_REQUEST_SCHEMA, OPERATION_SPECS, request_side_effect
from REAgent.schema import _branch, _schema, _string, validate_input_schema, validate_schema_definition
from REAgent.rebuild.knowledge import _ASSETS

SESSION = {"session_id": TEXT_SCHEMA}
ROLE = _string("Bound GUI role", ("source", "target"))


def tool(description, properties, required):
    return {"description": description, "input_schema": _schema(properties, required)}


def manual_comparison_contract():
    return deepcopy({"validation": VALIDATION_OPTIONS_SCHEMA, "view": VIEW_QUERY_SCHEMA,
        "default_views": list(DEFAULT_VIEW_DIRECTIONS),
        "view_labels": {name: view_direction_label(name) for name in VIEW_QUERY_SCHEMA["properties"]["views"]["items"]["enum"]}})


def prepare_comparison(options, view_request):
    if options is None and view_request is None:
        raise ValueError("Select geometry, paired views, or both")
    if options is not None:
        validate_input_schema(options, VALIDATION_OPTIONS_SCHEMA, path="comparison.options")
        validation_items(options)
        if options.get("parameter_audit"):
            raise ValueError("Parameter audit is separate from manual comparison")
    if view_request is not None:
        validate_input_schema(view_request, OBSERVATION_REQUEST_SCHEMA, path="comparison.view_request")
        if view_request["kind"] != "view" or view_request["query"].get("paired") is not True:
            raise ValueError("Manual view comparison requires one complete paired view request")
    return options, view_request


def _project_bindings(schema, hidden):
    """Remove the internal binding from this object and its same-object forms."""
    result = deepcopy(schema)
    if "properties" in schema:
        result["properties"] = {key: value for key, value in schema["properties"].items()
                                if key not in hidden}
    if "required" in schema:
        result["required"] = [key for key in schema["required"] if key not in hidden]
    if "oneOf" in schema:
        result["oneOf"] = [_project_bindings(branch, hidden) for branch in schema["oneOf"]]
    return result


TOOLS = {
    "agent_read_freecad_log": tool("Read a bounded tail or cursor increment of the selected instance's configured native log for diagnosis. Does not prove which call caused a line. Never changes logging configuration.",
                             {**SESSION, "role": ROLE, **LOG_READ_PROPERTIES}, ("role",)),
    "agent_ping": tool("Read both bound GUI instances without changing window state or focus. Window presentation does not determine connectivity; never starts FreeCAD.", {}, ()),
    "agent_get_capabilities": tool("Discover model-callable tools and bound runtime status; native command support is listed only after implementation.",
                             {"request": {}, **SESSION}, ("request",)),
    "agent_get_guidance": tool("Read a knowledge card or its catalog; cards are guidance, not executable parameter contracts.",
                         {"knowledge_id": _string("Knowledge card", [a.knowledge_id for a in _ASSETS]), **SESSION}, ()),
    "agent_start_session": tool("Create or resume a bound workspace and match both STEP revisions. Allocate all artifacts from the source filename in output_dir and persist the real journal before CAD creation. Resume explicitly matches source_path and saves to a new file. visual requires complete initial Source views before modeling; geometry_only requires explicit human task permission. Never change policy to bypass failed views.",
                          {}, ()),
    "agent_get_state": tool("Read actual objects, Result, native undo/redo and a bounded page of current-task evidence summaries; raw image manifests are internal.",
                            {**SESSION, "evidence_offset": {"type": "integer", "minimum": 0},
                             "evidence_limit": {"type": "integer", "minimum": 1, "maximum": 128}}, ("session_id",)),
    "agent_read_evidence": tool("Read the complete registered final PNG group without CAD access. artifact_key selects its group; initial views return both boards. Revalidate original path, hash and dimensions. Target revisions are historical. Never restores missing files or clears write blocks.",
                               {**SESSION, "evidence_id": TEXT_SCHEMA, "artifact_key": TEXT_SCHEMA},
                               ("session_id", "evidence_id", "artifact_key")),
    "agent_journal": tool(
        "Read original bounded session events or explicitly flush them to the bound output_dir and unique journal path. "
        "No CAD access; available in failed startup, unknown native write and closed/journal_pending states when the worker is idle. "
        "An ended session is released after Source cleanup and its journal are confirmed; human End task/Stop can abandon unconfirmed Target cleanup. "
        "Recorded event loss or cleanup residue cannot be recovered by flush. Never infers model recovery or migrates the log.", {}, ()),
    "agent_observe": tool("Get role-local revision-bound evidence. "
                    f"Image capture kinds ({', '.join(IMAGE_OBSERVATION_KINDS)}) must be the only tool call in a response, "
                    "even for Source; captures temporarily change presentation and write PNG files. "
                    "Only independent pure queries may share a response. "
                    "Source initial_views deterministically captures all six orthogonal and eight corner directions with one native camera scale and attaches both final labeled boards; each board records its final pixel scale; inspect all 14 before modeling and choose additional local views afterward. Native width/height controls sampling; output_long_edge_px only caps final PNG size without upsampling. Pixel/allocation limits precede capture; encoded byte limits follow it. Never omit directions to fit. Ordinary view selects up to eight directions; paired attaches one complete board with adjacent roles; oversized groups are unavailable, never partial. Image preparation certifies neither receipt nor inspection. Evidence is published after local success-response construction. Non-image queries remain readable under write blocks.",
                    {**SESSION, "role": ROLE, "request": OBSERVATION_REQUEST_SCHEMA, "purpose": TEXT_SCHEMA},
                    ("session_id", "role", "request")),
    "agent_undo": tool("Undo the named committed REAgent transaction only when it is the native stack top and owned by this session; never traverses user history.",
                 {**SESSION, "transaction": TEXT_SCHEMA}, ("session_id", "transaction")),
    "agent_redo": tool("Redo the named committed REAgent transaction only when it is the native stack top and owned by this session.",
                 {**SESSION, "transaction": TEXT_SCHEMA}, ("session_id", "transaction")),
    "agent_validate": tool(OPERATION_SPECS["agent_validate"]["summary"],
                     {**SESSION, "options": VALIDATION_OPTIONS_SCHEMA}, ("session_id", "options")),
    "agent_finish_reconstruction": tool(
        "Declare reconstruction ready for delivery using the current model validation and complete paired views already adopted in a model request. Sole call; no CAD mutation. Returns the authoritative limitations and a stable delivery receipt. A separate text-only report follows.",
        {**SESSION, "validation_operation_id": TEXT_SCHEMA, "paired_evidence_id": TEXT_SCHEMA,
         "notes": TEXT_SCHEMA}, ("session_id", "validation_operation_id", "paired_evidence_id")),
    "agent_save": tool(OPERATION_SPECS["agent_save"]["summary"],
                 SESSION, ("session_id",)),
    "agent_close_session": tool(OPERATION_SPECS["agent_close_session"]["summary"]
        + " Human End task/Stop also ends the local task when Target release fails or Target creation is unconfirmed: "
          "abandon its local binding and report remote cleanup as unconfirmed. Source cleanup and journal completion remain required. "
          "Never rebind a replacement Target, replay writes, close native documents or infer model recovery.", SESSION, ("session_id",)),
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
for _domain_name in FEATURE_SPECS:
    _bridge = OPERATION_SPECS[_domain_name]["input_schema"]
    TOOLS[_domain_name] = {
        "description": OPERATION_SPECS[_domain_name]["summary"],
        "input_schema": _project_bindings(_bridge, {"document"}),
    }
TOOLS["agent_get_state"]["description"] += (
    " Session responses include allocated artifacts independently of native checkpoint_path (null before first save). Failed startup and closed journal retention use one local state shape with native_state=not_queried."
)
TOOLS["agent_observe"]["description"] += " " + VIEW_EVIDENCE_CONTRACT["image_retention"]
# Journal flush includes an explicit output path; keep the whole control human-only.
HUMAN_ONLY = frozenset(("agent_start_session", "agent_save", "agent_close_session", "agent_journal"))
if not HUMAN_ONLY <= TOOLS.keys():
    raise RuntimeError("Human-only tools differ from the internal tool catalog")
MODEL_COMMANDS = tuple(name for name in TOOLS if not name.startswith("agent_"))
MODEL_ONLY = {
    "agent_resume_reconstruction": tool(
        "As the sole call, hand this user's explicit decision to continue reconstruction to the session. Only available in a new Target action. Reference the latest original UserText generation shown in current facts. Never infer consent from your own question, history or a summary. No CAD access.",
        {"user_requirement_generation": {"type": "integer", "minimum": 0}},
        ("user_requirement_generation",)),
    "agent_execute_command": tool(
        "Execute the ONE currently discovered native command using its exact returned schema. Discovery must have completed on an earlier model request. Human controls and arbitrary code are forbidden.",
        {"command": _string("Discovered native command", MODEL_COMMANDS), "arguments": {"type": "object"}},
        ("command", "arguments")),
    "agent_set_evidence_retention": tool(
        "As the sole call, retain a complete currently loaded image group until the next successfully committed summary checkpoint, or release that explicit retention. Does not read pixels, change CAD evidence or bypass mandatory first delivery. Read a released group first.",
        {"evidence_id": TEXT_SCHEMA, "policy": _string("Context retention", ("until_checkpoint", "release"))},
        ("evidence_id", "policy")),
}
MODEL_TOOLS = (*tuple(name for name in TOOLS if name not in HUMAN_ONLY), *MODEL_ONLY)
_capability_queries = [
    _branch("kind", "catalog", {}),
    _branch("kind", "tool", {"name": _string("Model tool", tuple(n for n in MODEL_TOOLS if n != "EditFeature"))}),
    _branch("kind", "tool", {"name": _string("Native in-place edit", ("EditFeature",)),
                            "type_id": _string("Observed native TypeId", tuple(EDIT_FEATURE_TYPES))}),
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
for spec in (*TOOLS.values(), *MODEL_ONLY.values()):
    validate_schema_definition(spec["input_schema"])


def model_schema(name, *, type_id=None):
    """Hide only this request object's bindings, including its oneOf forms."""
    if name not in MODEL_TOOLS:
        raise ValueError("Tool is not available to the model: %s" % name)
    spec = MODEL_ONLY[name] if name in MODEL_ONLY else TOOLS[name]
    schema = spec["input_schema"]
    if type_id is not None:
        if name != "EditFeature" or type_id not in EDIT_FEATURE_TYPES:
            raise ValueError("Type-specific discovery requires an editable native TypeId")
        # Each full native branch already contains the common top-level guards.
        schema = next(branch for branch in schema["oneOf"]
                      if branch["properties"]["type_id"]["enum"] == [type_id])
    return _project_bindings(schema, {"document", "session_id"})


def model_tool(name, *, type_id=None):
    return {**(MODEL_ONLY[name] if name in MODEL_ONLY else TOOLS[name]),
            "input_schema": model_schema(name, type_id=type_id)}


def model_tools():
    return {name: model_tool(name) for name in MODEL_TOOLS
            if name not in MODEL_COMMANDS}


def model_side_effect(name, arguments):
    if name not in MODEL_TOOLS:
        raise ValueError("Tool is not available to the model")
    if name == "agent_execute_command":
        name, arguments = arguments["command"], arguments["arguments"]
    if name not in OPERATION_SPECS:
        return "none"
    native = arguments["request"] if name == "agent_observe" else arguments
    return request_side_effect(name, native)


def _binding_keys(schema):
    keys = set(schema.get("properties", {})) & {"document", "session_id"}
    for branch in schema.get("oneOf", ()):
        keys.update(_binding_keys(branch))
    return keys


for _name in MODEL_TOOLS:
    validate_schema_definition(model_schema(_name))
