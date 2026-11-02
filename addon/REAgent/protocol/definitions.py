"""Shared Bridge identity, error categories and envelope fields."""

from REAgent.schema import _array, _schema, _string

PRODUCT_ID = "REAgentFreeCAD"

ROLE_PORTS = {"source": 9876, "rebuild": 9877}

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

def _operation(
    domain,
    summary,
    roles,
    side_effect,
    properties=None,
    required=(),
    preconditions=(),
    limits=(),
    timeout_class="short",
    required_for=(),
):
    return {
        "domain": domain,
        "summary": summary,
        "roles": list(roles),
        "side_effect": side_effect,
        "timeout_class": timeout_class,
        "required_for": list(required_for),
        "input_schema": _schema(properties, required),
        "preconditions": list(preconditions),
        "limits": list(limits),
    }

_DOCUMENT = _string("Internal FreeCAD document name")

OUTPUT_DIR_SCHEMA = _string("Absolute task output directory for all session artifacts; created if missing")

SESSION_ARTIFACTS_SCHEMA = _schema({
    "source_stem": _string("Unmodified STEP filename stem"),
    "basename": _string("Exclusively allocated session artifact basename"),
    "output_dir": OUTPUT_DIR_SCHEMA,
    "model_path": _string("Allocated output FCStd, not proof of a save"),
    "journal_path": _string("Unique reconstruction process journal"),
}, ("source_stem", "basename", "output_dir", "model_path", "journal_path"))

NAME_SCHEMA = {"type": "string", "pattern": r"^[A-Za-z][A-Za-z0-9_]{0,99}$"}

VALIDATION_OPTIONS_SCHEMA = _schema(
    {
        "parameter_audit": {"type": "boolean", "default": False,
                            "description": "Explicit transient parameter probes with verified restoration; never implicit in diagnostics or save."},
        "measurements": {**_array("Requested descriptive whole-model measurements; omitted means none",
            _string("Measured quantity", ("volume", "area", "center_of_mass", "bounding_box")), maximum=4), "uniqueItems": True},
        "surface_sampling": {"type": "boolean", "default": False,
                             "description": "Explicit approximate bidirectional surface distances; never implicit"},
        "boolean_operations": {**_array("Only these native operations run, each once; omitted means none",
            _string("Fixed Boolean direction", ("common", "source_minus_target", "target_minus_source")), maximum=3), "uniqueItems": True},
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
