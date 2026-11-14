"""Shared Bridge identity, error categories and envelope fields."""

from REAgent.json_schema import array, object_schema, string

PRODUCT_ID = "REAgentFreeCAD"

ROLE_PORTS = {"source": 9876, "target": 9877}

CAPABILITY_VIEWS = ("status", "catalog", "operation")

RETRY_ACTIONS = frozenset({
    "retry_safe",
    "correct_request",
    "inspect_state",
    "repair_model",
    "do_not_retry",
})

def resolve_retry_action(
    error_class,
    side_effect,
    *,
    explicit=None,
    transmission_started=None,
):
    """Return the single public retry policy from bounded failure facts."""
    if side_effect == "unknown":
        return "inspect_state"
    if explicit in RETRY_ACTIONS:
        return explicit
    if error_class == "connection":
        if side_effect == "none" and transmission_started is False:
            return "retry_safe"
        return "do_not_retry"
    if error_class in ("recompute", "validity"):
        return "repair_model"
    if (
        side_effect == "none"
        and error_class in ("precondition", "protocol", "semantic")
    ):
        return "correct_request"
    return "do_not_retry"

def operation(
    domain,
    summary,
    roles,
    side_effect,
    properties=None,
    required=(),
    preconditions=(),
    limits=(),
    timeout_class="short",
):
    return {
        "domain": domain,
        "summary": summary,
        "roles": list(roles),
        "side_effect": side_effect,
        "timeout_class": timeout_class,
        "input_schema": object_schema(properties, required),
        "preconditions": list(preconditions),
        "limits": list(limits),
    }

_DOCUMENT = string("Internal FreeCAD document name")

OUTPUT_DIR_SCHEMA = string("Absolute task output directory for all session artifacts; created if missing")

SESSION_ARTIFACTS_SCHEMA = object_schema({
    "source_stem": string("Unmodified STEP filename stem"),
    "basename": string("Exclusively allocated session artifact basename"),
    "output_dir": OUTPUT_DIR_SCHEMA,
    "model_path": string("Allocated output FCStd, not proof of a save"),
    "journal_path": string("Unique reconstruction process journal"),
}, ("source_stem", "basename", "output_dir", "model_path", "journal_path"))

NAME_SCHEMA = {"type": "string", "pattern": r"^[A-Za-z][A-Za-z0-9_]{0,99}$"}

VALIDATION_OPTIONS_SCHEMA = object_schema(
    {
        "parameter_audit": {"type": "boolean", "default": False,
                            "description": "Explicit transient parameter probes with verified restoration; never implicit in diagnostics or save."},
        "measurements": {**array("Requested descriptive whole-model measurements; omitted means none",
            string("Measured quantity", ("volume", "area", "center_of_mass", "bounding_box")), maximum=4), "uniqueItems": True},
        "surface_sampling": {"type": "boolean", "default": False,
                             "description": "Explicit approximate bidirectional surface distances; never implicit"},
        "boolean_operations": {**array("Only these native operations run, each once; omitted means none",
            string("Fixed Boolean direction", ("common", "source_minus_target", "target_minus_source")), maximum=3), "uniqueItems": True},
    }
)

def validation_items(options):
    items = list(options.get("measurements", []))
    if options.get("surface_sampling", False):
        items.append("surface_sampling")
    items.extend(options.get("boolean_operations", []))
    if not items and not options.get("parameter_audit", False):
        raise ValueError("Select at least one measurement, surface sampling, Boolean or parameter audit; use agent_get_state for document state")
    return items

TEXT_SCHEMA = {"type": "string", "minLength": 1, "maxLength": 500}

LOG_READ_PROPERTIES = {
    "cursor": {"type": "string", "pattern": r"^log-[a-f0-9]{32}$"},
    "max_bytes": {"type": "integer", "minimum": 256, "maximum": 65536,
                  "description": "Maximum log payload bytes; default 16384. At most 129 extra bytes are scanned for cursor/line checks."},
}
