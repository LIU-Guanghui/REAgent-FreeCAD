"""Canonical assembled Bridge operation specifications."""

from REAgent.protocol.definitions import CAPABILITY_VIEWS, LOG_READ_PROPERTIES, SESSION_ARTIFACTS_SCHEMA, TEXT_SCHEMA, VALIDATION_OPTIONS_SCHEMA, _DOCUMENT, operation
from REAgent.protocol.features import FEATURE_SPECS
from REAgent.protocol.observations import ENTITY_QUERY_SCHEMA, FEATURE_QUERY_SCHEMA, OBSERVATION_ROLES, SELECTION_RESOLUTION_QUERY_SCHEMA, SESSION_OBSERVATION_POLICY_SCHEMA, VIEW_EVIDENCE_CONTRACT, VIEW_QUERY_SCHEMA
from REAgent.json_schema import array, branch, nullable, number, object_schema, open_object_schema, string, validate_schema_definition

_OBSERVATION_QUERIES = {
    "global": object_schema(),
    "entity": {**ENTITY_QUERY_SCHEMA, "properties": {**ENTITY_QUERY_SCHEMA["properties"],
                "edit_object": {**_DOCUMENT, "description": "Obtain writable geometry of this existing feature's actual predecessor"}}},
    "feature": FEATURE_QUERY_SCHEMA,
    "selection_resolution": SELECTION_RESOLUTION_QUERY_SCHEMA,
    "regions": object_schema({
        "region_ids": array("Optional region subset", string("Region identifier")),
    }),
    "points": object_schema({
        "points": array(
            "Model-space points in millimetres",
            {
                "type": ["array", "object"],
                "items": number("Coordinate in millimetres"),
                "minItems": 3, "maxItems": 3,
                "properties": {axis: number("Coordinate in millimetres") for axis in ("x", "y", "z")},
                "required": ["x", "y", "z"], "additionalProperties": False,
            }, minimum=1, maximum=256,
        ),
    }, ("points",)),
    "residual": {
        **object_schema(),
        "description": (
            "Explicit residual query. Computes connected missing/excess regions, counts, "
            "volumes and bounding boxes by two explicitly requested directional cuts. Per-direction failures and filtered components are reported; never implicit in validation or save."
        ),
    },
    "support_extension": object_schema({
        "faces": ENTITY_QUERY_SCHEMA["properties"]["faces"],
        "selectors": ENTITY_QUERY_SCHEMA["properties"]["selectors"],
        "support_faces": array("Supporting face handles", string("FaceN handle")),
        "radius_mm": nullable(number("Optional extension radius in millimetres")),
    }),
    "view": VIEW_QUERY_SCHEMA,
    "initial_views": object_schema({
        "width": VIEW_QUERY_SCHEMA["properties"]["width"],
        "height": VIEW_QUERY_SCHEMA["properties"]["height"],
        "output_long_edge_px": VIEW_QUERY_SCHEMA["properties"]["output_long_edge_px"],
    }),
}

OPERATION_SPECS = {
    "agent_ping": operation(
        "adapter",
        "Report adapter health, role, port, process, and active document.",
        ("source", "target"),
        "none",
    ),
    "agent_get_capabilities": operation(
        "adapter",
        "Discover the internal Bridge contract.",
        ("source", "target"),
        "none",
        {
            "view": string("Discovery depth", CAPABILITY_VIEWS),
            "operation": nullable(string("Operation identifier")),
        },
    ),
    "agent_start_session": operation(
        "session",
        "Create or resume a role-local session with the Controller-allocated artifact identity bound to the actual Document. Match explicit source_path on resume before binding. Return the current STEP revision without certifying the checkpoint's historical source.",
        ("source", "target"),
        "committed",
        {
            "document": _DOCUMENT,
            "source_path": string("Absolute STEP/STP source path"),
            "resume_path": nullable(string("Existing Target FCStd checkpoint")),
            "artifacts": SESSION_ARTIFACTS_SCHEMA,
            "linear_tolerance_mm": number("Linear tolerance in millimetres"),
            "angular_tolerance_deg": number("Angular tolerance in degrees"),
            "session_id": TEXT_SCHEMA,
        },
        ("document", "source_path", "artifacts", "session_id"),
        ("The active process role supports reconstruction.",),
        timeout_class="long",
    ) | {"session_observation_policy": SESSION_OBSERVATION_POLICY_SCHEMA},
    "agent_close_session": operation(
        "session",
        "End the exact process-bound task and release its session metadata while retaining open documents, native history and unknown-state blocks. Never save, discard, recompute or adopt another document. A matching recent receipt confirms repeated ending after a lost reply.",
        ("source", "target"),
        "committed",
        {
            "document": _DOCUMENT,
            "session_id": TEXT_SCHEMA,
        },
        ("document", "session_id"),
    ),
    "agent_get_state": operation(
        "session",
        "Read native objects, Result and transaction state without importing STEP. Report the bound Source revision and independent current-file hash availability/match; safe reads do not adopt changed source or clear recovery uncertainty.",
        ("target",),
        "none",
        {"document": _DOCUMENT},
        ("document",),
    ),
    "agent_observe": operation(
        "observation",
        "Run one role-local evidence query with native display-chain and presentation recovery checks.",
        ("source", "target"),
        "committed",
        {
            "document": _DOCUMENT,
            "kind": string(
                "Evidence query kind",
                _OBSERVATION_QUERIES,
            ),
            "query": open_object_schema("Kind-specific bounded query parameters"),
            "session_id": TEXT_SCHEMA,
        },
        ("document", "kind", "query", "session_id"),
        ("The query is read-only for the active process role.",),
        timeout_class="long",
    ) | {"query_schemas": _OBSERVATION_QUERIES,
         "view_evidence_contract": VIEW_EVIDENCE_CONTRACT,
         "kind_roles": {kind: [OBSERVATION_ROLES[kind]] for kind in ("initial_views", "feature")},
         "view_role_conditions": {"paired": OBSERVATION_ROLES["paired_view"]},
         "read_only_kinds": ["global", "entity", "feature", "selection_resolution", "regions", "points", "residual", "support_extension"]},
    "agent_validate": operation(
        "validation",
        "Compute explicitly selected geometric measurements, sampling, Booleans or transient parameter audit. At least one item is required. No task acceptance or automatic equivalence verdict; state reads use agent_get_state.",
        ("target",),
        "transient_rolled_back",
        {
            "document": _DOCUMENT,
            "options": VALIDATION_OPTIONS_SCHEMA,
            "session_id": TEXT_SCHEMA,
        },
        ("document", "options", "session_id"),
        timeout_class="long",
    ) | {"read_only_without_parameter_audit": True},

}

for _name, _properties, _required, _effect in (
    ("agent_undo", {"transaction": TEXT_SCHEMA}, ("transaction",), "committed"),
    ("agent_redo", {"transaction": TEXT_SCHEMA}, ("transaction",), "committed"),
    ("agent_save", {}, (), "committed"),
):
    OPERATION_SPECS[_name] = operation("modeling" if _name != "agent_save" else "persistence",
        ("Save the structurally known Target, including an in_progress model, to its allocated model_path. "
         "Allocation excludes the resume checkpoint; only a file already saved by this session can be updated. "
         "saved_to reports an actual save, independently of geometry validation and journal persistence."
         if _name == "agent_save" else "Native " + _name.replace("_", " ")), ("target",), _effect,
        {"document": _DOCUMENT, "session_id": TEXT_SCHEMA, **_properties},
        ("document", "session_id", *_required), timeout_class="long")

OPERATION_SPECS["agent_read_freecad_log"] = operation("adapter",
    "Read only the bound instance's configured native log; no arbitrary path or configuration changes.",
    ("source", "target"), "none", LOG_READ_PROPERTIES, (), timeout_class="short")

if set(OPERATION_SPECS) & set(FEATURE_SPECS):
    raise ValueError("Duplicate Bridge operation definition")
OPERATION_SPECS.update(FEATURE_SPECS)

OPERATION_SPECS["agent_get_capabilities"]["input_schema"]["properties"]["operation"] = string("Internal method", OPERATION_SPECS)

for _spec in OPERATION_SPECS.values():
    if _spec["side_effect"] == "committed" and "body_name" in _spec["input_schema"]["properties"]:
        _spec["summary"] += (
            " Result changes are confined to the receiving Body; other Bodies retain their world geometry and Tip."
            " Editing a Body with cross-Body downstream consumers is not supported."
        )

_observe_spec = OPERATION_SPECS["agent_observe"]
if set(_observe_spec["input_schema"]["properties"]["kind"]["enum"]) != set(_observe_spec["query_schemas"]):
    raise ValueError("Observation kinds differ from query schemas")
if not set(_observe_spec["read_only_kinds"]) <= set(_observe_spec["query_schemas"]):
    raise ValueError("Read-only observation kinds exceed the declared queries")

OBSERVATION_REQUEST_SCHEMA = {"oneOf": [
    branch("kind", kind, {"query": query})
    for kind, query in OPERATION_SPECS["agent_observe"]["query_schemas"].items()
]}

def request_side_effect(method, params):
    """Classify a schema-validated request; GUI/file-producing views are writes."""
    spec = OPERATION_SPECS[method]
    if params.get("kind") in spec.get("read_only_kinds", ()):
        return "none"
    if spec.get("read_only_without_parameter_audit") and params.get("options", {}).get("parameter_audit", False) is False:
        return "none"
    return spec["side_effect"]

for _spec in OPERATION_SPECS.values():
    validate_schema_definition(_spec["input_schema"])

for _schema_value in (OBSERVATION_REQUEST_SCHEMA,):
    validate_schema_definition(_schema_value)
