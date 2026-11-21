"""Internal CAD tools and the single binding-free model schema projection."""

from REAgent.protocol.features import EDIT_FEATURE_TYPES, FEATURE_SPECS

from copy import deepcopy

from REAgent.protocol.observations import (DEFAULT_VIEW_DIRECTIONS, IMAGE_CONTENT_LIMITS, IMAGE_OBSERVATION_KINDS, VIEW_EVIDENCE_CONTRACT,
    VIEW_QUERY_SCHEMA, SESSION_OBSERVATION_POLICY_SCHEMA, view_direction_label)
from REAgent.protocol.definitions import (
    LOG_READ_PROPERTIES, NAME_SCHEMA, OBSERVATION_ID_SCHEMA, OUTPUT_DIR_SCHEMA,
    TEXT_SCHEMA, VALIDATION_OPTIONS_SCHEMA, validation_items,
)
from REAgent.protocol.operations import OPERATION_SPECS, request_side_effect
from REAgent.json_schema import array, branch, integer, number, object_schema, string, validate_input_schema, validate_schema_definition
from REAgent.cad.command_names import FIXED_FLOW_TOOLS, FIXED_TOOLS, MODEL_COMMANDS
from REAgent.cad.knowledge import _ASSETS

SESSION = {"session_id": TEXT_SCHEMA}
ROLE = string("Bound GUI role", ("source", "target"))
_OBSERVATION_QUERIES = OPERATION_SPECS["agent_observe"]["query_schemas"]


def tool(description, properties, required):
    return {"description": description, "input_schema": object_schema(properties, required)}


def observation_query(arguments):
    properties = _OBSERVATION_QUERIES[arguments["kind"]]["properties"]
    return {key: arguments[key] for key in properties if key in arguments}


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
        validate_input_schema(view_request, VIEW_QUERY_SCHEMA, path="comparison.view_request")
        if view_request.get("paired") is not True:
            raise ValueError("Manual view comparison requires one complete paired view request")
    return options, view_request


def _project_bindings(schema, hidden):
    """Remove the internal binding from this object and its same-object forms."""
    result = deepcopy(schema)
    if "properties" in result:
        result["properties"] = {key: value for key, value in result["properties"].items()
                                if key not in hidden}
    if "required" in schema:
        result["required"] = [key for key in schema["required"] if key not in hidden]
    if "oneOf" in schema:
        result["oneOf"] = [_project_bindings(branch, hidden) for branch in schema["oneOf"]]
    return result


TOOLS = {
    "agent_read_freecad_log": tool("Read a bounded tail or cursor increment of the selected instance's configured native log for diagnosis. Does not prove which call caused a line. Never changes logging configuration.",
                             {**SESSION, "role": ROLE, **LOG_READ_PROPERTIES}, ("role",)),
    "agent_ping": tool("Read the task's available GUI roles without changing window state or focus.", {}, ()),
    "agent_get_capabilities": tool(
        "List available commands and tools, read an exact input schema by name, or check bound runtime status. "
        "A native command lookup returns the input_schema for agent_execute_command.arguments.",
        {"request": {}, **SESSION}, ("request",)),
    "agent_get_knowledge": tool("Read a knowledge card or its catalog; cards are guidance, not executable parameter contracts.",
                         {"knowledge_id": string("Knowledge card", [a.knowledge_id for a in _ASSETS]), **SESSION}, ()),
    "agent_open_session": tool("Start the selected CAD workflow and allocate its artifacts before native workspace creation.", {}, ()),
    "agent_get_state": tool(
        "Read current Target objects, result, native undo/redo and a page of registered evidence. "
        "A current Target state readback supplies a target-state evidence_id for native writes.",
        {**SESSION, "evidence_offset": {"type": "integer", "minimum": 0},
         "evidence_limit": {"type": "integer", "minimum": 1, "maximum": 128}}, ("session_id",)),
    "agent_read_image": {
        "description": (
            "Read one registered image without CAD access: either a user-bound reference image by 1-based index "
            "(image2cad), or a registered observation PNG group by evidence_id (shown as observation_id in model results) "
            "and artifact_key (initial views return both boards). Observation reads revalidate original path, hash and "
            "dimensions; Target revisions are historical and never restore missing files or clear write blocks. "
            "Independent image reads can share a response."
        ),
        "input_schema": {
            "type": "object",
            "oneOf": [
                object_schema({
                    **SESSION,
                    "index": integer("One-based reference image index from task inputs", 1),
                    "region": array(
                        "Optional [left, top, right, bottom] in the displayed image",
                        {**number("Normalized coordinate"), "minimum": 0, "maximum": 1},
                        minimum=4, maximum=4,
                    ),
                    "long_edge_px": integer(
                        "Render or resize the view within this edge in pixels, default 2048",
                        512, 2560,
                    ),
                }, ("session_id", "index")),
                object_schema({
                    **SESSION,
                    "evidence_id": {
                        **TEXT_SCHEMA,
                        "description": "Registered image group ID, shown as observation_id in model results.",
                    },
                    "artifact_key": TEXT_SCHEMA,
                }, ("session_id", "evidence_id", "artifact_key")),
            ],
        },
    },
    "agent_manage_journal": tool(
        "Read original bounded session events or explicitly flush them to the bound output_dir and unique journal path. "
        "No CAD access; available in failed startup, unknown native write and closed/journal_pending states when the worker is idle. "
        "An ended session is released after Source cleanup and its journal are confirmed; human End task/Stop can abandon unconfirmed Target cleanup. "
        "Recorded event loss or cleanup residue cannot be recovered by flush. Never infers model recovery or migrates the log.", {}, ()),
    "agent_observe": tool("Get role-local revision-bound evidence. "
                    f"Image capture kinds ({', '.join(IMAGE_OBSERVATION_KINDS)}) must be the only tool call in a response, "
                    "even for Source; captures temporarily change presentation and write PNG files. "
                    "Only independent pure queries may share a response. "
                    "Source initial_views deterministically captures all six orthogonal and eight corner directions with one native camera scale and attaches both final labeled boards; each board records its final pixel scale; inspect all 14 before modeling and choose additional local views afterward. Native width/height controls sampling; output_long_edge_px only caps final PNG size without upsampling. Pixel/allocation limits precede capture; encoded byte limits follow it. Never omit directions to fit. Ordinary view selects up to eight directions; paired attaches one complete board with adjacent roles; oversized groups are unavailable, never partial. Image preparation certifies neither receipt nor inspection. Evidence is published after local success-response construction. Non-image queries remain readable under write blocks.",
                     {}, ()),
    "agent_undo": tool("Undo the named committed REAgent transaction only when it is the native stack top and owned by this session; never traverses user history.",
                 {**SESSION, "transaction": OPERATION_SPECS["agent_undo"]["input_schema"]["properties"]["transaction"]}, ("session_id", "transaction")),
    "agent_redo": tool("Redo the named committed REAgent transaction only when it is the native stack top and owned by this session.",
                 {**SESSION, "transaction": OPERATION_SPECS["agent_redo"]["input_schema"]["properties"]["transaction"]}, ("session_id", "transaction")),
    "agent_validate": tool(OPERATION_SPECS["agent_validate"]["summary"],
                     {**SESSION, "options": VALIDATION_OPTIONS_SCHEMA}, ("session_id", "options")),
    "agent_finish_reconstruction": tool(
        "Declare reconstruction ready for delivery using the current model geometric comparison and complete paired views already adopted in a model request. Sole call; no CAD mutation. Returns the authoritative limitations and a stable delivery receipt. A separate text-only report follows.",
        {**SESSION, "validation_operation_id": TEXT_SCHEMA,
         "paired_evidence_id": {**TEXT_SCHEMA, "description": "Copy the observation_id from the current complete paired view."},
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
          "inputs": array("Bound image references prepared in the Source worker", object_schema({
              "index": integer("One-based input index", 1), "filename": TEXT_SCHEMA,
              "path": TEXT_SCHEMA, "revision": TEXT_SCHEMA,
          }, ("index", "filename", "path", "revision")), minimum=1),
          "linear_tolerance_mm": {"type": "number", "exclusiveMinimum": 0},
          "angular_tolerance_deg": {"type": "number", "exclusiveMinimum": 0}}
TOOLS["agent_open_session"]["input_schema"] = object_schema(_start, ("output_dir",))
TOOLS["agent_manage_journal"]["input_schema"] = {"oneOf": [
    object_schema({**SESSION, "action": string("Journal action", ("read",)),
             "after_sequence": {"type": "integer", "minimum": 0},
             "limit": {"type": "integer", "minimum": 1, "maximum": 100}}, ("session_id", "action")),
    object_schema({**SESSION, "action": string("Journal action", ("flush",)),
             "output_dir": OUTPUT_DIR_SCHEMA}, ("session_id", "action", "output_dir")),
]}
for _domain_name in FEATURE_SPECS:
    _bridge = OPERATION_SPECS[_domain_name]["input_schema"]
    TOOLS[_domain_name] = {
        "description": OPERATION_SPECS[_domain_name]["summary"],
        "input_schema": _project_bindings(_bridge, {"document", "expected_revision"}),
    }
TOOLS["agent_get_state"]["description"] += (
    " Session responses include allocated artifacts independently of native checkpoint_path (null before first save). Failed startup and closed journal retention use one local state shape with native_state=not_queried."
)
TOOLS["agent_observe"]["input_schema"] = {"type": "object", "oneOf": [
    branch("kind", kind, {**SESSION, "role": ROLE, "purpose": TEXT_SCHEMA, **query["properties"]},
           ("session_id", "role", *query.get("required", ())))
    for kind, query in _OBSERVATION_QUERIES.items()
]}
TOOLS["agent_observe"]["description"] += " " + VIEW_EVIDENCE_CONTRACT["image_retention"]
# Journal flush includes an explicit output path; keep the whole control human-only.
HUMAN_ONLY = frozenset(("agent_open_session", "agent_save", "agent_close_session", "agent_manage_journal"))
if not HUMAN_ONLY <= TOOLS.keys():
    raise RuntimeError("Human-only tools differ from the internal tool catalog")
if (
    set(FIXED_FLOW_TOOLS) != {name for name in TOOLS if name.startswith("agent_") and name != "agent_finish_reconstruction"}
    or set(MODEL_COMMANDS) != {name for name in TOOLS if not name.startswith("agent_")}
    or len(FIXED_FLOW_TOOLS) != 14
    or set(FIXED_FLOW_TOOLS) & set(MODEL_COMMANDS)
):
    raise RuntimeError("Fixed-mode tool sets differ from the internal tool catalog")
MODEL_ONLY = {
    "agent_execute_command": tool(
        "Execute a native CAD command with its arguments. Use agent_get_capabilities to read the command's input_schema. "
        "Submit a write as the only call and wait for its result.",
        {"command": string("Native command name", MODEL_COMMANDS),
         "arguments": {"type": "object", "additionalProperties": True}},
        ("command", "arguments")),
    "agent_resume_reconstruction": tool(
        "As the sole call, hand this user's explicit decision to continue reconstruction to the session. Only available in a new Target action. Reference the latest original UserText generation shown in current facts. Never infer consent from your own question, history or a summary. No CAD access.",
        {"user_requirement_generation": {"type": "integer", "minimum": 0}},
        ("user_requirement_generation",)),
    "agent_set_evidence_retention": tool(
        "As the sole call, retain a complete currently loaded image group until the next successfully committed summary checkpoint, or release that explicit retention. Does not read pixels, change CAD evidence or bypass mandatory first delivery. Read a released group first.",
        {"evidence_id": {**TEXT_SCHEMA, "description": "Copy the observation_id of the loaded registered image group."},
         "policy": string("Context retention", ("until_checkpoint", "release"))},
        ("evidence_id", "policy")),
}
MODEL_TOOLS = (*tuple(name for name in TOOLS if name not in HUMAN_ONLY), *MODEL_ONLY)
_capability_queries = [
    branch("kind", "catalog", {}),
    branch("kind", "tool", {"name": string(
        "Tool or command name",
        tuple(dict.fromkeys(n for n in (*FIXED_TOOLS, *MODEL_TOOLS) if n != "EditFeature")),
    )}),
    branch("kind", "tool", {"name": string("Native in-place edit", ("EditFeature",)),
                            "type_id": string("Observed native TypeId", tuple(EDIT_FEATURE_TYPES))}),
    branch("kind", "runtime", {"role": string("Role", ("source", "target", "both"))}),
]
TOOLS["agent_get_capabilities"]["input_schema"] = {"type": "object", "oneOf": [
    object_schema({"request": query, **SESSION},
            ("request",))
    for query in _capability_queries
]}
TOOLS["agent_observe"]["image_content_limits"] = dict(IMAGE_CONTENT_LIMITS)
TOOLS["agent_observe"]["view_evidence_contract"] = VIEW_EVIDENCE_CONTRACT
TOOLS["agent_observe"]["description"] += " paired=true requires role=%s." % VIEW_EVIDENCE_CONTRACT["paired"]["role"]
for spec in (*TOOLS.values(), *MODEL_ONLY.values()):
    validate_schema_definition(spec["input_schema"])


def _projected_schema(spec, name, *, type_id=None, hidden=None):
    schema = spec["input_schema"]
    if type_id is not None:
        if name != "EditFeature" or type_id not in EDIT_FEATURE_TYPES:
            raise ValueError("An EditFeature schema requires a supported native TypeId")
        # Each full native branch already contains the common top-level guards.
        schema = next(branch for branch in schema["oneOf"]
                      if branch["properties"]["type_id"]["enum"] == [type_id])
    return _project_bindings(schema, {"document", "session_id"} if hidden is None else hidden)


def fixed_tool(name, *, type_id=None):
    """Project one fixed-mode tool or native command without mutating TOOLS."""
    if name not in FIXED_TOOLS:
        raise ValueError("Tool is not available in fixed mode: %s" % name)
    hidden = {"session_id"} if name == "agent_open_session" else None
    spec = {**TOOLS[name], "input_schema": _projected_schema(TOOLS[name], name, type_id=type_id, hidden=hidden)}
    if name == "agent_open_session":
        props = dict(spec["input_schema"]["properties"])
        props.pop("inputs", None)
        props["paths"] = array(
            "One to six existing absolute reference image paths (image2cad)",
            TEXT_SCHEMA,
            minimum=1,
            maximum=6,
        )
        spec["input_schema"] = {**spec["input_schema"], "properties": props}
    if name == "agent_get_capabilities":
        schema = spec["input_schema"]
        forms = []
        for form in schema["oneOf"]:
            props = form["properties"]["request"]["properties"]
            if "name" in props:
                props["name"]["enum"] = [item for item in props["name"]["enum"] if item in FIXED_TOOLS]
            forms.append(form)
        spec["input_schema"] = {**schema, "oneOf": forms}
    return spec


def model_schema(name, *, type_id=None):
    """Hide only this request object's bindings, including its oneOf forms."""
    if name not in MODEL_TOOLS:
        raise ValueError("Tool is not available to the model: %s" % name)
    spec = MODEL_ONLY[name] if name in MODEL_ONLY else TOOLS[name]
    return _projected_schema(spec, name, type_id=type_id)


def model_tool(name, *, type_id=None):
    return {**(MODEL_ONLY[name] if name in MODEL_ONLY else TOOLS[name]),
            "input_schema": model_schema(name, type_id=type_id)}


def finish_tool(workflow, *, model=False):
    if workflow.compares_stp:
        spec = TOOLS["agent_finish_reconstruction"]
    else:
        fields = {
            "requirement": {**TEXT_SCHEMA, "maxLength": 240,
                            "description": "Expected value or behavior with units and applicable tolerances. "
                                           "For a directional dimension, name one model axis or reference-view direction."},
            "source": {**TEXT_SCHEMA, "maxLength": 1024,
                       "description": "Brief source citation such as a drawing callout, image index, STL section or view, or short user quote"},
            "observation_id": {**OBSERVATION_ID_SCHEMA,
                               "description": "Copy observation_id from the matching agent_observe result for the current Target revision."},
            "observed": {**TEXT_SCHEMA, "maxLength": 320,
                         "description": "Actual Target readback in the requirement's direction and any discrepancy or uncertainty. "
                                        "For a derived distance, give the observed coordinates and their difference."},
            "verdict": string("Assessment based on the cited evidence", ("matches", "differs", "unconfirmed")),
        }
        required_fields = tuple(fields)
        description = ("The receipt includes the Controller's native single-solid check. "
                       "Check the input's shape, dimensions and feature requirements against current Target observations. "
                       "Obtain any missing observations at the final Target revision. "
                       "Sole call. Put detailed explanations in the separate final report.")
        for kinds in workflow.delivery_queries:
            description += " Cite current " + " or ".join(kind.replace("_", " ") for kind in kinds) + " evidence."
        if workflow.require_dimension_readback:
            fields["measurement"] = object_schema({
                "nominal": number("Drawing nominal for the directly measured dimension in the cited unit"),
                "unit": {**TEXT_SCHEMA, "maxLength": 64,
                         "description": "Copy the unit alongside the chosen readback_path"},
                "readback_path": array(
                    "Copy a readback_path from dimension_readbacks in the cited Target observation. "
                    "Choose the entry that directly measures the drawing dimension.",
                    {"oneOf": [{**TEXT_SCHEMA, "maxLength": 120}, integer("Zero-based array index", 0)]},
                    minimum=1, maximum=12),
                "tolerance": object_schema({
                    "lower": number("Lower deviation from the nominal"),
                    "upper": number("Upper deviation from the nominal"),
                }, ("lower", "upper")),
            }, ("nominal", "unit", "readback_path"))
            description += (" Cite the drawing callout and include at least one direct Target measurement. "
                            "Use the stated drawing tolerances. For a derived dimension, give the observed values "
                            "and calculation in observed and omit measurement for that check.")
        check = object_schema(fields, required_fields)
        spec = tool(description, {**SESSION,
            "checks": array("Input requirements and their Target observations", check, minimum=1, maximum=32),
            "notes": {**TEXT_SCHEMA, "description": "Brief unresolved points. Omit when there are none."}},
            ("session_id", "checks"))
    if model:
        return {**spec, "input_schema": _project_bindings(spec["input_schema"], {"session_id"})}
    return spec


def model_side_effect(name, arguments):
    if name not in OPERATION_SPECS:
        return "none"
    return request_side_effect(name, arguments)


def binding_keys(schema):
    keys = set(schema.get("properties", {})) & {"document", "session_id"}
    for branch in schema.get("oneOf", ()):
        keys.update(binding_keys(branch))
    return keys
