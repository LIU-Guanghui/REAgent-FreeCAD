"""Pure operation, role, effect and schema contract shared by MCP and Addon."""

import math

PRODUCT_ID = "REAgentFreeCAD"
ROLE_PORTS = {"source": 9876, "rebuild": 9877}

CAPABILITY_VIEWS = ("status", "catalog", "operation")
DRESSUP_SCOPE = {
    "edge_limit": 64, "explicit_complete_edge_set": True, "implicit_all_edges": False,
    "mode_gaps": {
        "fillet": ["use_all_edges", "face_or_whole_feature_implicit_edges", "variable_radius", "edit_existing_feature"],
        "chamfer": ["use_all_edges", "face_or_whole_feature_implicit_edges", "distance_and_angle", "edit_existing_feature"],
    },
}
DOMAIN_MODE_GAPS = {
    "primitive": [
        "native_attachment", "partial_angles", "skewed_cylinder_or_prism", "point_back_wedge",
    ],
    "extrude": ["face_profile", "through_all", "symmetric", "taper", "custom_direction"],
    "revolve": [
        "free_vector_axis", "construction_axis", "body_origin_axis", "symmetric", "two_angles",
    ],
    "hole": ["threads", "counterdrill", "tapered_wall", "non_circle_profile"],
    "boolean": [
        "empty_receiver", "clone_tool", "shapebinder_tool", "cross_session_tool", "multi_tool_common",
    ],
    "loft": [
        "inner_or_multiple_contours", "more_than_two_sections", "vertex_section",
        "closed_loft", "auto_section_order",
    ],
    "pipe": [
        "multiple_sections", "auxiliary_spine", "branched_or_multi_sketch_path", "picked_subedges",
        "variable_section", "automatic_path_order", "non_standard_orientation",
        "non_transformed_corner", "tangent_path_extension",
    ],
    **DRESSUP_SCOPE["mode_gaps"],
    "draft": [
        "inferred_neutral_plane", "custom_pull_direction", "edge_defined_neutral_plane",
        "automatic_face_grouping", "edit_existing_feature",
    ],
    "thickness": [
        "pipe_or_rectoverso_mode", "self_intersection_handling", "implicit_opening_faces",
        "edit_existing_feature",
    ],
    "mirror": [
        "whole_shape_mode", "inferred_mirror_plane", "sketch_axis", "nested_transform", "edit_existing_feature",
    ],
    "pattern": [
        "whole_shape_mode", "second_direction", "uneven_spacing", "sketch_axis",
        "nested_transform", "edit_existing_feature",
    ],
}

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
VIEW_CAMERA_SPECS = {
    "front": {"method": "viewFront", "camera_direction": dict(x=0., y=1., z=0.), "up_direction": dict(x=0., y=0., z=1.)},
    "back": {"method": "viewRear", "camera_direction": dict(x=0., y=-1., z=0.), "up_direction": dict(x=0., y=0., z=1.)},
    "top": {"method": "viewTop", "camera_direction": dict(x=0., y=0., z=-1.), "up_direction": dict(x=0., y=1., z=0.)},
    "bottom": {"method": "viewBottom", "camera_direction": dict(x=0., y=0., z=1.), "up_direction": dict(x=0., y=-1., z=0.)},
    "left": {"method": "viewLeft", "camera_direction": dict(x=1., y=0., z=0.), "up_direction": dict(x=0., y=0., z=1.)},
    "right": {"method": "viewRight", "camera_direction": dict(x=-1., y=0., z=0.), "up_direction": dict(x=0., y=0., z=1.)},
}
for _sx in (1, -1):
    for _sy in (1, -1):
        for _sz in (1, -1):
            _corner = "iso_x%s_y%s_z%s" % tuple("p" if s > 0 else "m" for s in (_sx, _sy, _sz))
            VIEW_CAMERA_SPECS[_corner] = {
                "method": "setCameraOrientation",
                "eye_direction": dict(zip(("x", "y", "z"), (s / math.sqrt(3) for s in (_sx, _sy, _sz)))),
                "camera_direction": dict(zip(("x", "y", "z"), (-s / math.sqrt(3) for s in (_sx, _sy, _sz)))),
                "up_direction": dict(zip(("x", "y", "z"), (s / math.sqrt(6) for s in (-_sx * _sz, -_sy * _sz, 2)))),
            }
VIEW_DIRECTIONS = tuple(VIEW_CAMERA_SPECS)
DEFAULT_VIEW_DIRECTIONS = ("front", "top", "left", "iso_xm_ym_zp")
IMAGE_OBSERVATION_KINDS = ("view", "initial_views")
OBSERVATION_ROLES = {"initial_views": "source", "paired_view": "target"}
# This limit applies independently to family, capture and journal-temp counters.
# Family numbers follow the source stem; local numbers follow the purpose suffix.
ARTIFACT_NUMBER_LIMIT = 999
ARTIFACT_SUFFIXES = {
    "model": ("", ".FCStd"),
    "journal": ("_journal", ".json"),
    "journal_tmp": ("_journal_tmp", ".json"),
    "initial_orthographic": ("_initial_orthographic", ".png"),
    "initial_corners": ("_initial_corners", ".png"),
    "paired": ("_paired", ".png"),
}
for _role in ("source", "rebuild"):
    for _direction in (*VIEW_DIRECTIONS, "section"):
        _key = _role + "_" + _direction
        ARTIFACT_SUFFIXES[_key] = ("_" + _key, ".png")


def artifact_basename(stem, number=0):
    if (not isinstance(stem, str) or not stem or stem in (".", "..")
            or any(char in stem for char in ("/", "\\", "\0"))):
        raise ValueError("Artifact basename must be one non-empty filename stem")
    if type(number) is not int or not 0 <= number <= ARTIFACT_NUMBER_LIMIT:
        raise ValueError("Artifact number must be 0 through 999")
    return stem + ("_%03d" % number if number else "")


def artifact_filename(basename, kind, number=0):
    suffix, extension = ARTIFACT_SUFFIXES[kind]
    if number and extension != ".png" and kind != "journal_tmp":
        raise ValueError("Only captures and journal temporary files have local numbers")
    return artifact_basename(artifact_basename(basename) + suffix, number) + extension


def artifact_matches(filename, basename):
    """Match this allocated basename, including local capture/temp numbers."""
    name = filename.lower()
    for kind, (suffix, extension) in ARTIFACT_SUFFIXES.items():
        prefix, extension = (basename + suffix).lower(), extension.lower()
        if name == prefix + extension:
            return True
        if extension == ".png" or kind == "journal_tmp":
            if name.startswith(prefix + "_") and name.endswith(extension):
                number = name[len(prefix) + 1:-len(extension)]
                if len(number) == 3 and number.isascii() and number.isdigit() and number != "000":
                    return True
    return False


def validate_observation_role(kind, query, role):
    key = "paired_view" if kind == "view" and query.get("paired", False) else kind
    required = OBSERVATION_ROLES.get(key)
    public_role = "target" if role == "rebuild" else role
    if required and public_role != required:
        raise ValueError("%s is available only from %s" % (key, required.title()))


INITIAL_VIEW_GROUPS = {
    "orthographic": {"columns": 3, "rows": 2, "directions": list(VIEW_DIRECTIONS[:6])},
    "corners": {"columns": 4, "rows": 2, "directions": list(VIEW_DIRECTIONS[6:])},
}
VIEW_BOARD_HEADER_HEIGHT = 36
IMAGE_CONTENT_LIMITS = {"max_images": 8, "max_image_bytes": 4 * 1024 * 1024,
                        "max_total_bytes": 12 * 1024 * 1024,
                        "max_image_pixels": 16 * 1024 * 1024}
SESSION_OBSERVATION_POLICY_SCHEMA = {
    "type": "string", "enum": ["visual", "geometry_only"], "default": "visual",
    "description": "MCP session preparation policy. visual requires complete initial Source views before native modeling. geometry_only requires explicit human task permission; never select it to bypass failed images.",
}


def initial_view_layout(width, height):
    """Compute all cells and reject oversized boards before GUI allocation."""
    for name, value in (("width", width), ("height", height)):
        if type(value) is not int or not 128 <= value <= 4096:
            raise ValueError("initial_views %s must be an integer from 128 to 4096" % name)
    result = {}
    for key, group in INITIAL_VIEW_GROUPS.items():
        board_width = group["columns"] * width
        board_height = group["rows"] * (height + VIEW_BOARD_HEADER_HEIGHT)
        pixels = board_width * board_height
        if pixels > IMAGE_CONTENT_LIMITS["max_image_pixels"]:
            raise ValueError("initial_views %s board for %sx%s is %sx%s (%s pixels), exceeding %s; lower the single-view resolution and retain all 14 directions" %
                             (key, width, height, board_width, board_height, pixels, IMAGE_CONTENT_LIMITS["max_image_pixels"]))
        cells = []
        for index, direction in enumerate(group["directions"]):
            x = index % group["columns"] * width
            y = index // group["columns"] * (height + VIEW_BOARD_HEADER_HEIGHT)
            cells.append({"direction": direction, "artifact_key": direction,
                          "image_rect_px": [x, y + VIEW_BOARD_HEADER_HEIGHT, width, height],
                          "label_rect_px": [x, y, width, VIEW_BOARD_HEADER_HEIGHT]})
        result[key] = {"rows": group["rows"], "columns": group["columns"],
                       "cell_order": "row_major", "width_px": board_width,
                       "height_px": board_height, "cells": cells}
    return result


VIEW_DISPLAY_MODES = ("wireframe", "flat_lines", "shaded")
VIEW_PROJECTIONS = ("orthographic",)
VIEW_EVIDENCE_CONTRACT = {
    "capture_identity": (
        "Family numbers follow the source stem; capture and journal-temp numbers follow the purpose suffix. "
        "Each independently selects the first available name, unnumbered then _001 through _999; "
        "the session family stays fixed after allocation. Freed names may be reused. "
        "Use returned paths and capture_id/evidence_id, not filenames or numbers, to identify captures."
    ),
    "paired": {"role": OBSERVATION_ROLES["paired_view"]},
    "mcp_image_retention": (
        "After a complete board response is prepared locally, retain boards and clean up only unchanged single PNGs "
        "from that capture. Standalone images, single-image fallback and unavailable delivery retain their files. "
        "image_delivery describes prepared image content, not later file availability. "
        "image_cleanup and top-level images[].file_status report cleanup results; absent file_status means no per-file cleanup result. "
        "Only deleted/missing entries leave views. Revisit removed singles through retained boards/layout or a new observation."
    ),
    "display_mode_default": "flat_lines",
    "display_facts": {
        "requested_display_mode": "Raw query mode; null when omitted",
        "normalized_display_mode": "wireframe/flat_lines/shaded used for object settings",
        "object_display_mode": "Native object DisplayMode readback, not PNG pixel style",
    },
    "viewport_override": "not_controlled",
    "pixel_style": "not_certified",
    "image_observation_kinds": list(IMAGE_OBSERVATION_KINDS),
    "initial_views": {"role": OBSERVATION_ROLES["initial_views"], "directions": list(VIEW_DIRECTIONS),
                      "groups": INITIAL_VIEW_GROUPS, "representation": "initial_boards",
                      "projection": "orthographic", "display_mode": "flat_lines",
                      "transparency": 0, "header_height_px": VIEW_BOARD_HEADER_HEIGHT,
                      "size_condition": "8 * width * (height + header_height_px) <= max_image_pixels; PNG bytes are checked after capture"},
}
VIEW_QUERY_FIELDS = (
    "transparency",
    "views",
    "width",
    "height",
    "display_mode",
    "projection",
    "focus_box_mm",
    "paired",
    "include_section",
    "section_axis",
)
ENTITY_QUERY_FIELDS = (
    "object",
    "faces",
    "edges",
    "selectors",
    "fields",
    "include",
    "offset",
    "limit",
)
SELECTION_RESOLUTION_QUERY_FIELDS = (
    "body_name",
    "edge_queries",
    "edge_selectors",
    "edge_refs",
    "edge_scope",
    "edge_role",
    "face_selectors",
    "face_refs",
    "face_role",
    "nearest_limit",
)


def _schema(properties=None, required=()):
    return {
        "type": "object",
        "properties": properties or {},
        "required": list(required),
        "additionalProperties": False,
    }


def _string(description, enum=None):
    result = {"type": "string", "description": description}
    if enum:
        result["enum"] = list(enum)
    return result


def _number(description):
    return {"type": "number", "description": description}


def _boolean(description):
    return {"type": "boolean", "description": description}


def _integer(description, minimum=None, maximum=None):
    result = {"type": "integer", "description": description}
    if minimum is not None:
        result["minimum"] = int(minimum)
    if maximum is not None:
        result["maximum"] = int(maximum)
    return result


def _object(description):
    return {"type": "object", "description": description}


def _array(description, items=None, minimum=None, maximum=None):
    result = {"type": "array", "description": description}
    if items is not None:
        result["items"] = items
    if minimum is not None:
        result["minItems"] = minimum
    if maximum is not None:
        result["maxItems"] = maximum
    return result


def _nullable(spec):
    result = dict(spec)
    result["type"] = [spec["type"], "null"]
    return result


def _check_request_fields(value, schema, field):
    """Check fields and scalar enums before dispatching to geometry handlers."""

    if not isinstance(value, dict):
        raise ValueError("%s must be an object" % field)
    properties = schema["properties"]
    allowed = sorted(properties)
    unknown = sorted(set(value) - set(properties))
    missing = sorted(set(schema.get("required", [])) - set(value))
    if unknown or missing:
        raise ValueError(
            "%s: unsupported fields %s; missing fields %s; allowed fields %s"
            % (field, unknown, missing, allowed)
        )
    for name, item in value.items():
        choices = properties[name].get("enum")
        if choices is not None and item not in choices:
            raise ValueError("%s.%s=%r; allowed values: %s" % (field, name, item, choices))


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

_FOCUS_BOX_SCHEMA = _schema(
    {
        name: _number("Finite model-space coordinate in millimetres")
        for name in (
            "x_min",
            "x_max",
            "y_min",
            "y_max",
            "z_min",
            "z_max",
        )
    },
    ("x_min", "x_max", "y_min", "y_max", "z_min", "z_max"),
)
VIEW_QUERY_SCHEMA = _schema(
    {
        "transparency": _integer("Surface transparency percent; default 0 (opaque)", 0, 100),
        "views": _array(
            "One to eight explicit canonical directions in requested order; iso names describe eye-position signs, opposite camera forward",
            _string("Canonical camera direction", VIEW_DIRECTIONS),
            minimum=1, maximum=8,
        ),
        "width": _integer("Image width in pixels", 128, 4096),
        "height": _integer("Image height in pixels", 128, 4096),
        "display_mode": {**_string(
            "Object display mode; omitted means flat_lines (lit surfaces with edges). Readback certifies object properties, not viewport overrides or PNG pixel style.",
            VIEW_DISPLAY_MODES
        ), "default": "flat_lines"},
        "projection": _string(
            "Verified camera projection", VIEW_PROJECTIONS
        ),
        "focus_box_mm": _nullable(_FOCUS_BOX_SCHEMA),
        "paired": _boolean(
            "Render Source and current Target together; requires role=%s. Each pair occupies one row"
            % OBSERVATION_ROLES["paired_view"]
        ),
        "include_section": _boolean(
            "Add one paired half-cutaway view"
        ),
        "section_axis": _string("Half-cutaway axis", ("x", "y", "z")),
    },
)
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
STABLE_SELECTOR_SCHEMA = _schema({
    "kind": _string("Entity kind", ("face", "edge", "wire")),
    "model_revision": _string("Copy from observation; do not construct"),
    "signature": _string("Copy from observation; do not construct"),
}, ("kind", "model_revision", "signature"))
EDGE_QUERY_SCHEMA = _schema({
    "curve_type": _string("Default Line", ("Line", "Circle")),
    "center_mm": _array("Measured center", _number("Coordinate"), 3, 3),
    "direction": _array("Nonzero line direction or circle normal", _number("Component"), 3, 3),
    "length_mm": {**_number("Positive edge length"), "exclusiveMinimum": 0},
    "tolerance_mm": {**_number("Positive matching tolerance; default 0.02"), "exclusiveMinimum": 0},
}, ("center_mm", "direction", "length_mm"))
_EDGE_ROLES = ("dressup_edge", "pattern_axis")
EDGE_REF_SCHEMA = _schema({
    "entity_kind": _string("Referenced kind", ("edge",)),
    "intended_role": _string("Intended use", _EDGE_ROLES),
    "owner": _schema({"role": _string("Target role", ("rebuild",)),
                      "revision": _string("Copy from semantic_ref"),
                      "object_name": NAME_SCHEMA}, ("role", "revision", "object_name")),
    "signatures": _schema({key: _string("Copy from semantic_ref") for key in ("geometry", "topology")}, ("geometry", "topology")),
    "native_hint": _nullable(_schema({"adapter": _string("Adapter", ("freecad",)),
                                     "subelement": _string("EdgeN hint")}, ("adapter", "subelement"))),
    "fallback": {"type": "null"},
    "ambiguity": _string("Never choose an ambiguous match automatically", ("reject",)),
    "last_resolution": _object("Optional diagnostic returned with semantic_ref; copy unchanged"),
}, ("entity_kind", "intended_role", "owner", "signatures"))


def _edge_ref_schema(role):
    return {**EDGE_REF_SCHEMA,
            "properties": {**EDGE_REF_SCHEMA["properties"],
                           "intended_role": _string("Explicit edge use", (role,))}}


_FACE_ROLES = ("draft_target", "draft_neutral", "thickness_opening", "mirror_plane")
FACE_REF_SCHEMA = _schema({
    "entity_kind": _string("Referenced kind", ("face",)),
    "intended_role": _string("Explicit face use", _FACE_ROLES),
    "owner": _schema({"role": _string("Target role", ("rebuild",)),
                      "revision": _string("Copy from face resolution"),
                      "object_name": NAME_SCHEMA}, ("role", "revision", "object_name")),
    "signatures": _schema({key: _string("Copy from face resolution")
                           for key in ("geometry", "topology")},
                          ("geometry", "topology")),
    "native_hint": _nullable(_schema({"adapter": _string("Adapter", ("freecad",)),
                                     "subelement": _string("FaceN hint")},
                                    ("adapter", "subelement"))),
    "fallback": {"type": "null"},
    "ambiguity": _string("Reject ambiguous matches", ("reject",)),
    "last_resolution": _object("Optional diagnostic returned with face reference"),
}, ("entity_kind", "intended_role", "owner", "signatures"))


def _face_ref_schema(role):
    return {**FACE_REF_SCHEMA,
            "properties": {**FACE_REF_SCHEMA["properties"],
                           "intended_role": _string("Explicit face use", (role,))}}
ENTITY_QUERY_SCHEMA = _schema(
    {
        "object": {
            **NAME_SCHEMA,
            "description": (
                "Target only: owned object's own geometry, including empty Sketch/Datum. "
                "Only a current solid Body Tip provides writable refs. Omit for the main "
                "Body's current Tip selection geometry; whole-result measurements use Body Shape."
            ),
        },
        "faces": _array(
            "Omitted: all faces unless selectors are supplied; []: no faces; otherwise exact FaceN subset",
            _string("Transient face handle"),
        ),
        "edges": _array(
            "Omitted: all edges unless selectors are supplied; []: no edges; otherwise exact EdgeN subset",
            _string("Transient edge handle"),
        ),
        "selectors": _array(
            "Geometry selectors copied from observation; omitted or [] adds no selection",
            STABLE_SELECTOR_SCHEMA,
        ),
        "fields": _array(
            "Top-level entity keys; default compact. Use surface/curve for details; ['*'] expands only this scope's available fields. Historical/Body objects have no writable selectors/refs. Quality flags are not exposed.",
            _string("Top-level field name; nested radius/axis values are under surface or curve"),
        ),
        "include": _array("Optional global sections; default none", _string("Section", ("wires", "topology", "stable_selection", "repetition", "relations", "semantic_regions"))),
        "offset": _integer("Zero-based offset within each selected entity kind; default 0", 0),
        "limit": _integer("Maximum entities per kind; default 50", 1, 500),
    }
)
SELECTION_RESOLUTION_QUERY_SCHEMA = _schema(
    {
        "body_name": {**NAME_SCHEMA, "description": "Owned Body whose current solid Tip supplies the edges; default main Body"},
        "edge_queries": _array(
            "LLM-supplied measured predecessor-edge queries",
            EDGE_QUERY_SCHEMA, minimum=1, maximum=64,
        ),
        "edge_selectors": _array(
            "LLM-supplied stable edge selectors",
            {**STABLE_SELECTOR_SCHEMA, "properties": {**STABLE_SELECTOR_SCHEMA["properties"], "kind": _string("Edge selector", ("edge",))}}, minimum=1, maximum=64,
        ),
        "edge_refs": _array(
            "LLM-supplied semantic current-edge references",
            EDGE_REF_SCHEMA, minimum=1, maximum=64,
        ),
        "edge_scope": _string(
            "Literal request for every predecessor edge", ("all",)
        ),
        "edge_role": _string("Intended use of resolved edges", _EDGE_ROLES),
        "face_selectors": _array(
            "Current predecessor face selectors copied from entity observation",
            {**STABLE_SELECTOR_SCHEMA,
             "properties": {**STABLE_SELECTOR_SCHEMA["properties"],
                            "kind": _string("Face selector", ("face",))}},
            minimum=1, maximum=64,
        ),
        "face_refs": _array(
            "Previously resolved explicit predecessor face references",
            FACE_REF_SCHEMA, minimum=1, maximum=64,
        ),
        "face_role": _string("Intended use of resolved faces", _FACE_ROLES),
        "nearest_limit": _integer(
            "Bounded diagnostic candidates per submitted input", 1, 5
        ),
    }
)


# Sole static truth for Bridge method names, roles, side effects, timeouts,
# schemas, and reconstruction membership. Dispatch and MCP negotiation derive from it.
OPERATION_SPECS = {
    "agent_ping": _operation(
        "adapter",
        "Report adapter health, role, port, process, and active document.",
        ("source", "rebuild"),
        "none",
        required_for=("reconstruct",),
    ),
    "agent_get_capabilities": _operation(
        "adapter",
        "Discover the internal Bridge contract.",
        ("source", "rebuild"),
        "none",
        {
            "view": _string("Discovery depth", CAPABILITY_VIEWS),
            "operation": _nullable(_string("Operation identifier")),
        },
        required_for=("reconstruct",),
    ),
    "agent_start_session": _operation(
        "session",
        "Create or resume a role-local session with the MCP-allocated artifact identity bound to the actual Document. Match explicit source_path on resume before binding. Return the current STEP revision without certifying the checkpoint's historical source.",
        ("source", "rebuild"),
        "committed",
        {
            "document": _DOCUMENT,
            "source_path": _string("Absolute STEP/STP source path"),
            "resume_path": _nullable(_string("Existing Target FCStd checkpoint")),
            "artifacts": SESSION_ARTIFACTS_SCHEMA,
            "linear_tolerance_mm": _number("Linear tolerance in millimetres"),
            "angular_tolerance_deg": _number("Angular tolerance in degrees"),
        },
        ("document", "source_path", "artifacts"),
        ("The active process role supports reconstruction.",),
        timeout_class="long",
        required_for=("reconstruct",),
    ),
    "agent_close_session": _operation(
        "session",
        "Close the exact Document object bound to the current session and role, including under write protection; never inspect, recover, save, recompute, clear history, or re-identify a same-named replacement.",
        ("source", "rebuild"),
        "committed",
        {
            "document": _DOCUMENT,
        },
        ("document",),
        required_for=("reconstruct",),
    ),
    "agent_get_state": _operation(
        "session",
        "Read Target Result, model state, checkpoint, history, and document inventory.",
        ("rebuild",),
        "none",
        {"document": _DOCUMENT},
        ("document",),
        required_for=("reconstruct",),
    ),
    "agent_observe": _operation(
        "observation",
        "Run one role-local evidence query with native display-chain and presentation recovery checks.",
        ("source", "rebuild"),
        "none",
        {
            "document": _DOCUMENT,
            "kind": _string(
                "Evidence query kind",
                (
                    "global",
                    "entity",
                    "selection_resolution",
                    "regions",
                    "points",
                    "residual",
                    "support_extension",
                    "view",
                    "initial_views",
                ),
            ),
            "query": _object("Kind-specific bounded query parameters"),
        },
        ("document", "kind", "query"),
        ("The query is read-only for the active process role.",),
        timeout_class="long",
        required_for=("reconstruct",),
    ),
    "agent_validate": _operation(
        "validation",
        "Read current Target native state; independently request descriptive measurements, sampling, Booleans or transient parameter audit. No task acceptance or automatic equivalence verdict.",
        ("rebuild",),
        "transient_rolled_back",
        {
            "document": _DOCUMENT,
            "options": VALIDATION_OPTIONS_SCHEMA,
        },
        ("document", "options"),
        timeout_class="long",
        required_for=("reconstruct",),
    ),

}

# Kind-specific schemas are part of capability discovery while the top-level
# observe envelope remains shared by every evidence kind.
OPERATION_SPECS["agent_observe"]["query_schemas"] = {
    "global": _schema(),
    "entity": ENTITY_QUERY_SCHEMA,
    "selection_resolution": SELECTION_RESOLUTION_QUERY_SCHEMA,
    "regions": _schema({
        "region_ids": _array("Optional region subset", _string("Region identifier")),
    }),
    "points": _schema({
        "points": _array(
            "Model-space points in millimetres",
            {
                "type": ["array", "object"],
                "items": _number("Coordinate in millimetres"),
                "minItems": 3, "maxItems": 3,
                "properties": {axis: _number("Coordinate in millimetres") for axis in ("x", "y", "z")},
                "required": ["x", "y", "z"], "additionalProperties": False,
            }, minimum=1, maximum=256,
        ),
    }, ("points",)),
    "residual": {
        **_schema(),
        "description": (
            "Explicit residual query. Computes connected missing/excess regions, counts, "
            "volumes and bounding boxes by two explicitly requested directional cuts. Per-direction failures and filtered components are reported; never implicit in validation or save."
        ),
    },
    "support_extension": _schema({
        "faces": ENTITY_QUERY_SCHEMA["properties"]["faces"],
        "selectors": ENTITY_QUERY_SCHEMA["properties"]["selectors"],
        "support_faces": _array("Supporting face handles", _string("FaceN handle")),
        "radius_mm": _nullable(_number("Optional extension radius in millimetres")),
    }),
    "view": VIEW_QUERY_SCHEMA,
    "initial_views": _schema({
        "width": VIEW_QUERY_SCHEMA["properties"]["width"],
        "height": VIEW_QUERY_SCHEMA["properties"]["height"],
    }),
}

TEXT_SCHEMA = {"type": "string", "minLength": 1, "maxLength": 500}
LOG_READ_PROPERTIES = {
    "cursor": {"type": "string", "pattern": r"^log-[a-f0-9]{32}$"},
    "max_bytes": {"type": "integer", "minimum": 256, "maximum": 65536,
                  "description": "Maximum log payload bytes; default 16384. At most 129 extra bytes are scanned for cursor/line checks."},
}


def _branch(key, value, properties, required=None):
    return _schema({key: {"type": "string", "enum": [value]}, **properties},
                   (key, *(properties if required is None else required)))


def _conditional_form(schema, choices, required=(), excluded=()):
    """Constrain this same request object; common requirements stay in its parent."""
    properties = {key: value for key, value in schema["properties"].items()
                  if key not in excluded}
    for key, value in choices.items():
        properties[key] = {**properties[key], "enum": [value]}
    return _schema(properties, (*choices, *required))


for _name, _properties, _required, _effect in (
    ("agent_undo", {"transaction": TEXT_SCHEMA}, ("transaction",), "committed"),
    ("agent_redo", {"transaction": TEXT_SCHEMA}, ("transaction",), "committed"),
    ("agent_save", {}, (), "committed"),
):
    OPERATION_SPECS[_name] = _operation("modeling" if _name != "agent_save" else "persistence",
        "Native " + _name.replace("_", " "), ("rebuild",), _effect,
        {"document": _DOCUMENT, "session_id": TEXT_SCHEMA, **_properties},
        ("document", "session_id", *_required), timeout_class="long", required_for=("reconstruct",))
OPERATION_SPECS["agent_read_freecad_log"] = _operation("adapter",
    "Read only the bound instance's configured native log; no arbitrary path or configuration changes.",
    ("source", "rebuild"), "none", LOG_READ_PROPERTIES, (), timeout_class="short")
DOMAIN_EVIDENCE_SCHEMA = _array(
    "Source and Target evidence IDs issued by this session",
    {"type": "string", "pattern": r"^evidence-[a-f0-9]{20}$"}, 2, 16,
)
OPERATION_SPECS["PartDesign_Body"] = _operation(
    "body", "Create and register one empty native tool Body in the bound Target.",
    ("rebuild",), "committed",
    {"document": _DOCUMENT, "session_id": TEXT_SCHEMA,
     "body_name": {**NAME_SCHEMA, "description": "Unique native name for the tool Body"},
     "intent": TEXT_SCHEMA, "evidence": DOMAIN_EVIDENCE_SCHEMA,
     "expected_revision": _string("Copy the current Target revision from observation or state")},
    ("document", "session_id", "body_name", "intent", "evidence", "expected_revision"),
    ("Source and Target evidence belong to this session; Target revision is current.",),
    timeout_class="long", required_for=("reconstruct",),
)
OPERATION_SPECS["PartDesign_NewSketch"] = _operation(
    "sketch", "Create one empty native Sketch in an explicitly owned Body.",
    ("rebuild",), "committed",
    {"document": _DOCUMENT, "session_id": TEXT_SCHEMA,
     "body_name": {**NAME_SCHEMA, "description": "Owned receiving Body"},
     "sketch_name": {**NAME_SCHEMA, "description": "Unique native Sketch name"},
     "intent": TEXT_SCHEMA, "evidence": DOMAIN_EVIDENCE_SCHEMA,
     "expected_revision": _string("Copy the current Target revision from observation or state")},
    ("document", "session_id", "body_name", "sketch_name", "intent", "evidence", "expected_revision"),
    ("Source and Target evidence belong to this session; Target revision is current.",),
    timeout_class="long", required_for=("reconstruct",),
)
OPERATION_SPECS["Sketcher_MapSketch"] = _operation(
    "sketch", "First attachment of an owned empty Sketch to an Origin, Datum or Coordinate System plane in the same Body.",
    ("rebuild",), "committed",
    {"document": _DOCUMENT, "session_id": TEXT_SCHEMA,
     "body_name": {**NAME_SCHEMA, "description": "Owned receiving Body"},
     "sketch_name": {**NAME_SCHEMA, "description": "Owned, unattached Sketch"},
     "reference": {"oneOf": [
         _branch("kind", "origin_plane", {
             "role": _string("Same-Body Origin plane", ("XY_Plane", "XZ_Plane", "YZ_Plane"))}),
         _branch("kind", "datum_plane", {
             "datum_name": {**NAME_SCHEMA, "description": "Same-Body native Part::DatumPlane"}}),
         _branch("kind", "coordinate_system_plane", {
             "datum_name": {**NAME_SCHEMA, "description": "Same-Body native Part::LocalCoordinateSystem"},
             "role": _string("Stable local plane role", ("XY_Plane", "XZ_Plane", "YZ_Plane"))}),
     ]},
     "intent": TEXT_SCHEMA, "evidence": DOMAIN_EVIDENCE_SCHEMA,
     "expected_revision": _string("Copy the current Target revision from observation or state")},
    ("document", "session_id", "body_name", "sketch_name", "reference", "intent", "evidence", "expected_revision"),
    ("Source and Target evidence belong to this session; Sketch is not yet attached.",),
    timeout_class="long", required_for=("reconstruct",),
)
OPERATION_SPECS["Sketcher_ValidateSketch"] = _operation(
    "sketch", "Read native Sketch constraint and geometry diagnostics without invoking repair.",
    ("rebuild",), "none",
    {"document": _DOCUMENT, "session_id": TEXT_SCHEMA,
     "body_name": {**NAME_SCHEMA, "description": "Owned Sketch Body"},
     "sketch_name": {**NAME_SCHEMA, "description": "Owned native Sketch"},
     "tolerance_mm": {**_number("Positive Sketch geometry tolerance in millimetres"),
                      "exclusiveMinimum": 0}},
    ("document", "session_id", "body_name", "sketch_name", "tolerance_mm"),
    ("No GUI editor or repair action is used.",),
)
_SKETCH_ID = {"type": "string", "pattern": r"^[A-Za-z][A-Za-z0-9_]{0,49}$"}
_SKETCH_POINT = _array("Sketch-plane point in millimetres", _number("Finite coordinate"), 2, 2)
_SKETCH_CONSTRUCTION = _boolean("Construction geometry; omitted means false")
_SKETCH_GEOMETRY = {"oneOf": [
    _branch("kind", "line", {"id": _SKETCH_ID, "start": _SKETCH_POINT,
                              "end": _SKETCH_POINT, "construction": _SKETCH_CONSTRUCTION},
            ("id", "start", "end")),
    _branch("kind", "circle", {"id": _SKETCH_ID, "center": _SKETCH_POINT,
                                "radius_mm": {**_number("Positive radius"), "exclusiveMinimum": 0},
                                "construction": _SKETCH_CONSTRUCTION},
            ("id", "center", "radius_mm")),
    _branch("kind", "arc", {"id": _SKETCH_ID, "center": _SKETCH_POINT,
                             "radius_mm": {**_number("Positive radius"), "exclusiveMinimum": 0},
                              "start_deg": _number("Start circle parameter in Sketch-plane degrees; preserve direction"),
                              "end_deg": _number("End circle parameter in degrees; end_deg - start_deg must be greater than 0 and less than 360"),
                             "construction": _SKETCH_CONSTRUCTION},
            ("id", "center", "radius_mm", "start_deg", "end_deg")),
    _branch("kind", "ellipse", {"id": _SKETCH_ID, "center": _SKETCH_POINT,
                                 "major_radius_mm": {**_number("Positive major semiaxis"), "exclusiveMinimum": 0},
                                 "minor_radius_mm": {**_number("Positive minor semiaxis"), "exclusiveMinimum": 0},
                                 "direction_deg": _number("Major-axis angle in the Sketch plane"),
                                 "construction": _SKETCH_CONSTRUCTION},
            ("id", "center", "major_radius_mm", "minor_radius_mm", "direction_deg")),
    _branch("kind", "ellipse_arc", {"id": _SKETCH_ID, "center": _SKETCH_POINT,
                                     "major_radius_mm": {**_number("Positive major semiaxis"), "exclusiveMinimum": 0},
                                     "minor_radius_mm": {**_number("Positive minor semiaxis"), "exclusiveMinimum": 0},
                                     "direction_deg": _number("Major-axis angle in the Sketch plane"),
                                      "start_deg": _number("Start ellipse carrier parameter in degrees, not its polar angle"),
                                      "end_deg": _number("End ellipse parameter in degrees; end_deg - start_deg must be greater than 0 and less than 360"),
                                     "construction": _SKETCH_CONSTRUCTION},
            ("id", "center", "major_radius_mm", "minor_radius_mm", "direction_deg", "start_deg", "end_deg")),
]}
_SKETCH_POINT_REF = _schema({"geometry_id": _SKETCH_ID,
                             "position": _string("Native Sketch point", ("start", "end", "center"))},
                            ("geometry_id", "position"))
_SKETCH_CONSTRAINT_PROPERTIES = {
    "geometry_id": _SKETCH_ID, "first_geometry": _SKETCH_ID,
    "second_geometry": _SKETCH_ID, "first_point": _SKETCH_POINT_REF,
    "second_point": _SKETCH_POINT_REF, "point": _SKETCH_POINT_REF,
    "value": _number("Distance in millimetres or angle in degrees"),
}
_SKETCH_CONSTRAINT_FIELDS = {
    "Coincident": ("first_point", "second_point"),
    "Horizontal": ("geometry_id",), "Vertical": ("geometry_id",),
    "DistanceX": ("point", "value"), "DistanceY": ("point", "value"),
    "Distance": ("geometry_id", "value"),
    "Diameter": ("geometry_id", "value"), "Radius": ("geometry_id", "value"),
    "Angle": ("geometry_id", "value"),
    "Parallel": ("first_geometry", "second_geometry"),
    "Perpendicular": ("first_geometry", "second_geometry"),
    "Tangent": ("first_geometry", "second_geometry"),
    "Equal": ("first_geometry", "second_geometry"),
}
_SKETCH_CONSTRAINT = {"oneOf": []}
for _kind, _fields in _SKETCH_CONSTRAINT_FIELDS.items():
    _constraint_properties = {key: _SKETCH_CONSTRAINT_PROPERTIES[key] for key in _fields}
    if _kind in ("Distance", "Diameter", "Radius", "Angle"):
        _constraint_properties["value"] = {
            **_constraint_properties["value"], "exclusiveMinimum": 0,
            **({"exclusiveMaximum": 360} if _kind == "Angle" else {}),
        }
    _SKETCH_CONSTRAINT["oneOf"].append(_branch("kind", _kind, _constraint_properties))
OPERATION_SPECS["Sketcher_EditSketch"] = _operation(
    "sketch", "Fill one previously empty owned Sketch once with five geometry types and explicit constraints. Shared feature preflight validates cross-field arc/ellipse_arc spans.",
    ("rebuild",), "committed",
    {"document": _DOCUMENT, "session_id": TEXT_SCHEMA,
     "body_name": {**NAME_SCHEMA, "description": "Owned Sketch Body"},
     "sketch_name": {**NAME_SCHEMA, "description": "Existing empty owned Sketch"},
     "geometry": _array("One-shot native Sketch geometry", _SKETCH_GEOMETRY, 1, 256),
     "constraints": _array("Explicit native Sketch constraints", _SKETCH_CONSTRAINT, 0, 512),
     "intent": TEXT_SCHEMA, "evidence": DOMAIN_EVIDENCE_SCHEMA,
     "expected_revision": _string("Copy the current Target revision from observation or state")},
    ("document", "session_id", "body_name", "sketch_name", "geometry", "constraints",
     "intent", "evidence", "expected_revision"),
    ("Sketch is empty and has no downstream generated feature.",),
    timeout_class="long", required_for=("reconstruct",),
)
_POSITION_MM = _array("Free Body-local position or attached support-local offset in millimetres", _number("Finite coordinate"), 3, 3)
_ROTATION_AXIS = _array("Nonzero Body-local or attached support-local rotation axis", _number("Finite component"), 3, 3)
_ORIGIN_PLANE_REFERENCE = _branch("kind", "origin_plane", {
    "role": _string("Same-Body Origin plane role", ("XY_Plane", "XZ_Plane", "YZ_Plane"))})
_ORIGIN_AXIS_REFERENCE = _branch("kind", "origin_axis", {
    "role": _string("Same-Body Origin axis role", ("X_Axis", "Y_Axis", "Z_Axis"))})
_ORIGIN_POINT_REFERENCE = _branch("kind", "origin_point", {})
def _datum_reference(kind):
    return _branch("kind", kind, {
        "datum_name": {**NAME_SCHEMA, "description": "Same-Body native Datum name"}})
_VERTEX_REFERENCE = _branch("kind", "feature_vertex", {
    "feature_name": {**NAME_SCHEMA, "description": "Same-Body predecessor feature"},
    "vertex_index": {"type": "integer", "minimum": 1,
                     "description": "Explicit one-based native vertex index"}})
for _datum_command, _datum_kind in (
    ("Part_DatumPlane", "plane"), ("Part_DatumLine", "line"),
    ("Part_DatumPoint", "point"), ("Part_CoordinateSystem", "coordinate_system"),
):
    _datum_properties = {
        "document": _DOCUMENT, "session_id": TEXT_SCHEMA,
        "body_name": {**NAME_SCHEMA, "description": "Owned receiving Body"},
        "datum_name": {**NAME_SCHEMA, "description": "Unique native Datum name"},
        "mode": _string("Free placement or reference-relative attachment", ("free", "attached")),
        "position_mm": _POSITION_MM,
        "intent": TEXT_SCHEMA, "evidence": DOMAIN_EVIDENCE_SCHEMA,
        "expected_revision": _string("Copy the current Target revision from observation or state"),
    }
    _datum_required = ("document", "session_id", "body_name", "datum_name",
                       "mode", "position_mm", "intent", "evidence", "expected_revision")
    _references = {
        "plane": [_ORIGIN_PLANE_REFERENCE],
        "line": [_ORIGIN_AXIS_REFERENCE],
        "point": [_ORIGIN_POINT_REFERENCE, _VERTEX_REFERENCE],
        "coordinate_system": [_ORIGIN_POINT_REFERENCE,
                              _datum_reference("datum_plane"),
                              _datum_reference("datum_line"),
                              _datum_reference("datum_point")],
    }
    _datum_properties["reference"] = {"oneOf": _references[_datum_kind]}
    if _datum_kind != "point":
        _datum_properties.update(axis=_ROTATION_AXIS,
                                 angle_deg=_number("Rotation around the free Body-local or attached support-local axis in degrees"))
        _datum_required += ("axis", "angle_deg")
    OPERATION_SPECS[_datum_command] = _operation(
        "datum", "Create one owned native %s Datum with free or bounded same-Body attachment." % _datum_kind,
        ("rebuild",), "committed", _datum_properties, _datum_required,
        ("Source and Target evidence belong to this session; Target revision is current.",
         "Free placement forbids reference; attached placement requires one explicit same-Body reference."),
        timeout_class="long", required_for=("reconstruct",),
    )
    _datum_schema = OPERATION_SPECS[_datum_command]["input_schema"]
    _datum_schema["oneOf"] = [
        _conditional_form(_datum_schema, {"mode": "free"}, excluded=("reference",)),
        _conditional_form(_datum_schema, {"mode": "attached"}, required=("reference",)),
    ]

# This is the single public primitive contract and the Addon property map.
# Each generated command still has its own public schema and native TypeId.
PRIMITIVE_FAMILIES = {
    "Box": {
        "inputs": (("length_mm", "Length", "positive"),
                   ("width_mm", "Width", "positive"),
                   ("height_mm", "Height", "positive")),
        "fixed": (), "oriented": False,
    },
    "Cylinder": {
        "inputs": (("radius_mm", "Radius", "positive"),
                   ("height_mm", "Height", "positive")),
        "fixed": (("Angle", 360.0), ("FirstAngle", 0.0),
                  ("SecondAngle", 0.0)), "oriented": True,
    },
    "Cone": {
        "inputs": (("radius1_mm", "Radius1", "nonnegative"),
                   ("radius2_mm", "Radius2", "nonnegative"),
                   ("height_mm", "Height", "positive")),
        "fixed": (("Angle", 360.0),), "oriented": True,
    },
    "Sphere": {
        "inputs": (("radius_mm", "Radius", "positive"),),
        "fixed": (("Angle1", -90.0), ("Angle2", 90.0),
                  ("Angle3", 360.0)), "oriented": False,
    },
    "Ellipsoid": {
        "inputs": (("radius1_mm", "Radius1", "positive"),
                   ("radius2_mm", "Radius2", "positive"),
                   ("radius3_mm", "Radius3", "positive")),
        "fixed": (("Angle1", -90.0), ("Angle2", 90.0),
                  ("Angle3", 360.0)), "oriented": True,
        "field_descriptions": {
            "radius1_mm": "Positive native Radius1 semi-axis along primitive-local Z before Placement rotation, in millimetres",
            "radius2_mm": "Positive native Radius2 semi-axis along primitive-local X before Placement rotation, in millimetres",
            "radius3_mm": "Positive native Radius3 semi-axis along primitive-local Y before Placement rotation, in millimetres",
            "axis": "Nonzero Body-local axis of the rigid Placement rotation; rotates the primitive-local semi-axes without reassigning radius numbers",
            "angle_deg": "Rigid Placement rotation angle in degrees around axis; rotates the primitive-local semi-axes without reassigning radius numbers",
        },
    },
    "Torus": {
        "inputs": (("radius1_mm", "Radius1", "positive"),
                   ("radius2_mm", "Radius2", "positive")),
        "fixed": (("Angle1", -180.0), ("Angle2", 180.0),
                  ("Angle3", 360.0)), "oriented": True,
    },
    "Prism": {
        "inputs": (("polygon", "Polygon", "polygon"),
                   ("circumradius_mm", "Circumradius", "positive"),
                   ("height_mm", "Height", "positive")),
        "fixed": (("FirstAngle", 0.0), ("SecondAngle", 0.0)),
        "oriented": True,
    },
    "Wedge": {
        "inputs": tuple((name.lower() + "_mm", name, "finite") for name in (
            "Xmin", "Xmax", "Ymin", "Ymax", "Zmin", "Zmax",
            "X2min", "X2max", "Z2min", "Z2max")),
        "fixed": (), "oriented": True,
    },
}


def _primitive_number(field, rule):
    if rule == "polygon":
        return _integer("Number of regular prism sides", 3)
    spec = _number("Finite native %s value in millimetres" % field)
    if rule == "positive":
        spec["exclusiveMinimum"] = 0
    elif rule == "nonnegative":
        spec["minimum"] = 0
    return spec


PRIMITIVE_COMMANDS = {}
for _family, _family_spec in PRIMITIVE_FAMILIES.items():
    for _operation_kind in ("Additive", "Subtractive"):
        _primitive_name = "PartDesign_%s%s" % (_operation_kind, _family)
        _primitive_properties = {
            "document": _DOCUMENT,
            "session_id": TEXT_SCHEMA,
            "body_name": {**NAME_SCHEMA, "description": "Explicit owned receiving Body"},
            "feature_name": {**NAME_SCHEMA, "description": "Unique native feature name"},
            "intent": TEXT_SCHEMA,
            "evidence": DOMAIN_EVIDENCE_SCHEMA,
            "expected_revision": _string("Current Target observation revision"),
        }
        for _field, _native, _rule in _family_spec["inputs"]:
            _primitive_properties[_field] = _primitive_number(_field, _rule)
        _position_field = "center_mm" if _family == "Sphere" else "position_mm"
        _primitive_properties[_position_field] = _array(
            "Body-local %s in millimetres" % ("sphere center" if _family == "Sphere" else "position"),
            _number("Finite coordinate"), 3, 3)
        if _family_spec["oriented"]:
            _primitive_properties["axis"] = _array(
                "Nonzero Body-local rotation axis", _number("Finite component"), 3, 3)
            _primitive_properties["angle_deg"] = _number("Body-local rotation angle in degrees")
        for _field, _description in _family_spec.get("field_descriptions", {}).items():
            _primitive_properties[_field]["description"] = _description
        _required = ("document", "session_id", "body_name", "feature_name",
                     *(item[0] for item in _family_spec["inputs"]), _position_field,
                     *(("axis", "angle_deg") if _family_spec["oriented"] else ()),
                     "intent", "evidence", "expected_revision")
        PRIMITIVE_COMMANDS[_primitive_name] = {
            "family": _family,
            "additive": _operation_kind == "Additive",
            "type_id": "PartDesign::%s%s" % (_operation_kind, _family),
            "inputs": _family_spec["inputs"],
            "fixed": _family_spec["fixed"],
            "oriented": _family_spec["oriented"],
            "position_field": _position_field,
        }
        OPERATION_SPECS[_primitive_name] = _operation(
            "primitive", "Create one native %s %s in an explicitly owned Body." %
            (_operation_kind.lower(), _family.lower()),
            ("rebuild",), "committed", _primitive_properties, _required,
            ("Current Source and Target evidence and Target revision are required.",
             "Subtraction requires a prior valid single solid in the receiving Body."),
            ("Only the full, unslanted, unattached supported native form is supported.",),
            timeout_class="long", required_for=("reconstruct",),
        )

# Native history commands share public formats and conditional forms;
# actual geometry, ownership and native readback remain in domain handlers.
HISTORY_COMMANDS = {
    "PartDesign_Pad": {"domain": "extrude", "type_id": "PartDesign::Pad", "additive": True},
    "PartDesign_Pocket": {"domain": "extrude", "type_id": "PartDesign::Pocket", "additive": False},
    "PartDesign_Revolution": {"domain": "revolve", "type_id": "PartDesign::Revolution", "additive": True},
    "PartDesign_Groove": {"domain": "revolve", "type_id": "PartDesign::Groove", "additive": False},
    "PartDesign_Hole": {"domain": "hole", "type_id": "PartDesign::Hole", "additive": False},
    "PartDesign_Boolean": {"domain": "boolean", "type_id": "PartDesign::Boolean"},
    "PartDesign_AdditiveLoft": {"domain": "loft", "type_id": "PartDesign::AdditiveLoft", "additive": True},
    "PartDesign_SubtractiveLoft": {"domain": "loft", "type_id": "PartDesign::SubtractiveLoft", "additive": False},
    "PartDesign_AdditivePipe": {"domain": "pipe", "type_id": "PartDesign::AdditivePipe", "additive": True},
    "PartDesign_SubtractivePipe": {"domain": "pipe", "type_id": "PartDesign::SubtractivePipe", "additive": False},
    "PartDesign_Fillet": {"domain": "fillet", "type_id": "PartDesign::Fillet"},
    "PartDesign_Chamfer": {"domain": "chamfer", "type_id": "PartDesign::Chamfer"},
    "PartDesign_Draft": {"domain": "draft", "type_id": "PartDesign::Draft"},
    "PartDesign_Thickness": {"domain": "thickness", "type_id": "PartDesign::Thickness"},
    "PartDesign_Mirrored": {"domain": "mirror", "type_id": "PartDesign::Mirrored"},
    "PartDesign_LinearPattern": {"domain": "pattern", "type_id": "PartDesign::LinearPattern"},
    "PartDesign_PolarPattern": {"domain": "pattern", "type_id": "PartDesign::PolarPattern"},
}
_HISTORY_COMMON = {
    "document": _DOCUMENT, "session_id": TEXT_SCHEMA,
    "body_name": {**NAME_SCHEMA, "description": "Explicit owned receiving Body"},
    "feature_name": {**NAME_SCHEMA, "description": "Unique native feature name"},
    "intent": TEXT_SCHEMA, "evidence": DOMAIN_EVIDENCE_SCHEMA,
    "expected_revision": _string("Current Target observation revision"),
}
_HISTORY_REQUIRED = ("document", "session_id", "body_name", "feature_name",
                     "intent", "evidence", "expected_revision")
_SKETCH_PROFILE = {**NAME_SCHEMA, "description": "Existing Sketch in the receiving Body"}
_POSITIVE_LENGTH = {**_number("Positive native length in millimetres"), "exclusiveMinimum": 0}
_REVOLUTION_AXIS = {"oneOf": [
    _schema({"kind": _string("Sketch horizontal or vertical axis", ("sketch_axis",)),
             "axis": _string("Native Sketch axis", ("horizontal", "vertical"))},
            ("kind", "axis")),
    _schema({"kind": _string("Same-Body Datum Line", ("datum_line",)),
             "datum_name": NAME_SCHEMA}, ("kind", "datum_name")),
    _schema({"kind": _string("Same-Body Coordinate System semantic axis", ("coordinate_system_axis",)),
             "coordinate_system_name": NAME_SCHEMA,
             "role": _string("Native Coordinate System axis role", ("X_Axis", "Y_Axis", "Z_Axis"))},
            ("kind", "coordinate_system_name", "role")),
]}
_TRANSFORM_SOURCES = _array(
    "One to 16 caller-ordered native additive/subtractive features from the current Body history",
    NAME_SCHEMA, 1, 16)
_PATTERN_AXIS = {"oneOf": [
    _schema({"kind": _string("Same-Body Origin axis", ("origin_axis",)),
             "role": _string("Origin axis role", ("X_Axis", "Y_Axis", "Z_Axis"))},
            ("kind", "role")),
    _schema({"kind": _string("Current predecessor straight edge", ("predecessor_edge",)),
             "edge_ref": _edge_ref_schema("pattern_axis")},
            ("kind", "edge_ref")),
    _schema({"kind": _string("Same-Body Datum Line", ("datum_line",)),
             "datum_name": NAME_SCHEMA}, ("kind", "datum_name")),
    _schema({"kind": _string("Same-Body Coordinate System semantic child axis", ("coordinate_system_axis",)),
             "coordinate_system_name": NAME_SCHEMA,
             "role": _string("Coordinate System axis role", ("X_Axis", "Y_Axis", "Z_Axis"))},
            ("kind", "coordinate_system_name", "role")),
]}
for _command, _spec in HISTORY_COMMANDS.items():
    _properties = dict(_HISTORY_COMMON)
    _required = _HISTORY_REQUIRED
    if _spec["domain"] in ("extrude", "revolve", "hole"):
        _properties["sketch_name"] = _SKETCH_PROFILE
        _required += ("sketch_name",)
    if _spec["domain"] == "extrude":
        _properties.update(length_mm={**_POSITIVE_LENGTH, "description": "Full generating length from the Sketch plane in millimetres, including portions later trimmed by dress-ups or cuts."},
                           reversed=_boolean("Explicit native direction reversal"))
        _required += ("length_mm", "reversed")
    elif _spec["domain"] == "revolve":
        _properties.update(axis=_REVOLUTION_AXIS,
                           angle_deg={**_number("Native rotation angle in degrees"),
                                      "exclusiveMinimum": 0, "maximum": 360},
                           reversed=_boolean("Explicit native direction reversal"))
        _required += ("axis", "angle_deg", "reversed")
    elif _spec["domain"] == "hole":
        _properties.update(
            diameter_mm=_POSITIVE_LENGTH,
            depth_mode=_string("Finite depth or through all", ("dimension", "through_all")),
            depth_mm={**_POSITIVE_LENGTH, "description": "Finite depth in millimetres from the Sketch plane. Angled mode includes the conical tip (native DrillForDepth=true); measure to the tip, not just the cylindrical wall."},
            drill_point=_string("Finite-depth bottom", ("flat", "angled")),
            drill_point_angle_deg={**_number("Included angled drill-tip angle in degrees"),
                                   "exclusiveMinimum": 0, "exclusiveMaximum": 180},
            mouth_mode=_string("Hole entrance", ("none", "counterbore", "countersink")),
            mouth_diameter_mm={**_POSITIVE_LENGTH, "description": "Entrance diameter in millimetres; must exceed diameter_mm."},
            mouth_depth_mm={**_POSITIVE_LENGTH, "description": "Counterbore entrance depth from the Sketch plane in millimetres; forbidden for countersink/none."},
            mouth_angle_deg={**_number("Included countersink angle in degrees"),
                            "exclusiveMinimum": 0, "exclusiveMaximum": 180},
            reversed=_boolean("Hole removal direction: false follows the negative Sketch normal, true the positive normal. Establish material side in the actual Sketch frame."),
        )
        _required += ("diameter_mm", "depth_mode", "mouth_mode", "reversed")
    elif _spec["domain"] == "loft":
        _properties.update(
            profile_sketch_name={**NAME_SCHEMA, "description": "First ordered closed section Sketch in the receiving Body"},
            section_sketch_name={**NAME_SCHEMA, "description": "Second ordered closed section Sketch in the receiving Body"},
        )
        _required += ("profile_sketch_name", "section_sketch_name")
    elif _spec["domain"] == "pipe":
        _properties.update(
            profile_sketch_name={**NAME_SCHEMA, "description": "Single closed section Sketch in the receiving Body"},
            path_sketch_name={**NAME_SCHEMA, "description": "Single continuous whole-path Sketch in the receiving Body"},
        )
        _required += ("profile_sketch_name", "path_sketch_name")
    elif _spec["domain"] in ("fillet", "chamfer"):
        _properties.update(
            predecessor_name={**NAME_SCHEMA, "description": "Current solid Tip of the owned Body"},
            edge_refs=_array("One to 64 explicit current Target predecessor edges",
                             _edge_ref_schema("dressup_edge"), 1, DRESSUP_SCOPE["edge_limit"]),
        )
        _required += ("predecessor_name", "edge_refs")
        if _spec["domain"] == "fillet":
            _properties["radius_mm"] = _POSITIVE_LENGTH
            _required += ("radius_mm",)
        else:
            _properties.update(
                mode=_string("Native Chamfer mode", ("equal_distance", "two_distances")),
                size_mm=_POSITIVE_LENGTH,
                size2_mm=_POSITIVE_LENGTH,
                flip_direction=_boolean("Explicit two-distance direction flip"),
            )
            _required += ("mode", "size_mm")
    elif _spec["domain"] in ("draft", "thickness"):
        role = "draft_target" if _spec["domain"] == "draft" else "thickness_opening"
        _properties.update(
            predecessor_name={**NAME_SCHEMA, "description": "Current solid Tip of the owned Body"},
            face_refs=_array("One to 64 explicit current Target predecessor faces",
                             _face_ref_schema(role), 1, 64),
        )
        _required += ("predecessor_name", "face_refs")
        if _spec["domain"] == "draft":
            _properties.update(
                neutral_plane={"oneOf": [
                    _schema({"kind": _string("Current predecessor planar face", ("predecessor_face",)),
                             "face_ref": _face_ref_schema("draft_neutral")},
                            ("kind", "face_ref")),
                    _schema({"kind": _string("Same-Body native origin plane", ("origin_plane",)),
                             "role": _string("Origin plane role", ("XY_Plane", "XZ_Plane", "YZ_Plane"))},
                            ("kind", "role")),
                ]},
                angle_deg={**_number("Positive native draft angle in degrees"),
                           "exclusiveMinimum": 0, "exclusiveMaximum": 90},
                reversed=_boolean("Explicit native draft direction reversal"),
            )
            _required += ("neutral_plane", "angle_deg", "reversed")
        else:
            _properties.update(
                thickness_mm=_POSITIVE_LENGTH,
                direction=_string("Thickness material side", ("inward", "outward")),
                join=_string("Native corner join", ("arc", "intersection")),
            )
            _required += ("thickness_mm", "direction", "join")
    elif _spec["domain"] in ("mirror", "pattern"):
        _properties.update(
            predecessor_name={**NAME_SCHEMA, "description": "Current valid solid Tip of the owned Body"},
            source_feature_names=_TRANSFORM_SOURCES,
        )
        _required += ("predecessor_name", "source_feature_names")
        if _spec["domain"] == "mirror":
            _mirror_datum_plane = _datum_reference("datum_plane")
            _mirror_datum_plane["properties"]["datum_name"]["description"] = "Same-Body native Part::DatumPlane"
            _properties["mirror_plane"] = {"oneOf": [
                _schema({"kind": _string("Same-Body native Origin plane", ("origin_plane",)),
                         "role": _string("Origin plane role", ("XY_Plane", "XZ_Plane", "YZ_Plane"))},
                        ("kind", "role")),
                _schema({"kind": _string("Current predecessor planar face", ("predecessor_face",)),
                         "face_ref": _face_ref_schema("mirror_plane")},
                        ("kind", "face_ref")),
                _mirror_datum_plane,
            ]}
            _required += ("mirror_plane",)
        else:
            _properties.update(
                axis=_PATTERN_AXIS,
                reversed=_boolean("Explicit native pattern direction reversal"),
                extent_mode=_string("Preserve overall extent or per-step offset semantics",
                                    ("overall", "offset")),
                occurrences=_integer("Occurrences including the original", 2, 64),
            )
            _required += ("axis", "reversed", "extent_mode", "occurrences")
            if _command == "PartDesign_LinearPattern":
                _properties["distance_mm"] = _POSITIVE_LENGTH
                _required += ("distance_mm",)
            else:
                _properties["angle_deg"] = {
                    **_number("Positive native overall or per-step angle in degrees; offset × (occurrences - 1) must be less than 360"),
                    "exclusiveMinimum": 0, "maximum": 360,
                }
                _required += ("angle_deg",)
    else:
        _properties.update(
            tool_body_names=_array("Ordered registered tool Bodies; exactly one for Common, 1-16 for Cut/Fuse", NAME_SCHEMA, 1, 16),
            mode=_string("Native Boolean operation", ("Fuse", "Cut", "Common")),
        )
        _required += ("tool_body_names", "mode")
    _limits = ()
    if _spec["domain"] == "loft":
        _limits = ("Exactly two distinct same-Body Sketches, caller-ordered, each with one closed outer contour on a different plane; Closed, Ruled and Refine are false.",)
    elif _spec["domain"] == "pipe":
        _limits = ("One closed same-Body profile and one continuous whole-path line/arc Sketch; Constant/Standard/Transformed with Refine false, no extra sections or auxiliary path.",)
    elif _spec["domain"] == "fillet":
        _limits = ("One positive radius on 1-64 explicit exact current predecessor edges, including the complete explicit set; UseAllEdges is false, no implicit whole-feature/face or variable-radius mode.",)
    elif _spec["domain"] == "chamfer":
        _limits = ("1-64 explicit exact current predecessor edges may include the complete set; UseAllEdges is false. Equal distance requires only size_mm; two distances also requires size2_mm and explicit flip_direction. No implicit whole-feature/face or distance-and-angle mode.",)
    elif _spec["domain"] == "draft":
        _limits = ("Explicit current predecessor faces and one planar neutral face or same-Body origin plane; no inferred neutral plane or custom pull direction.",)
    elif _spec["domain"] == "thickness":
        _limits = ("Explicit current predecessor opening faces; Skin mode, Arc/Intersection join and intersection-handling false only.",)
    elif _spec["domain"] == "mirror":
        _limits = ("Transform tool shapes only; 1-16 ordered same-Body native tool features; explicit Origin plane, current predecessor planar face or same-Body Datum Plane with same-Body attachment supports throughout its Datum/Coordinate System support chain. No nested transform.",)
    elif _spec["domain"] == "pattern":
        _limits = ("Transform tool shapes only; 1-16 ordered source features, one explicit axis, 2-64 occurrences, overall/offset mode. No second direction, uneven spacing or nested transform.",)
    OPERATION_SPECS[_command] = _operation(
        _spec["domain"], "Create one native %s feature in an explicitly owned Body." % _command,
        ("rebuild",), "committed", _properties, _required,
        ("Current Source and Target evidence and Target revision are required.",
         "Native references must belong to this Target session."),
        _limits, timeout_class="long", required_for=("reconstruct",),
    )
OPERATION_SPECS["PartDesign_Boolean"]["input_schema"]["oneOf"] = [
    {"type": "object", "properties": {
        "mode": {"type": "string", "enum": ["Fuse", "Cut"]},
        "tool_body_names": {"type": "array", "minItems": 1, "maxItems": 16},
    }, "required": ["mode", "tool_body_names"]},
    {"type": "object", "properties": {
        "mode": {"type": "string", "enum": ["Common"]},
        "tool_body_names": {"type": "array", "minItems": 1, "maxItems": 1},
    }, "required": ["mode", "tool_body_names"]},
]
_hole_schema = OPERATION_SPECS["PartDesign_Hole"]["input_schema"]
_hole_schema["oneOf"] = [
    _conditional_form(_hole_schema, {**depth_choices, "mouth_mode": mouth},
                      (*depth_required, *mouth_required), (*depth_excluded, *mouth_excluded))
    for depth_choices, depth_required, depth_excluded in (
        ({"depth_mode": "dimension", "drill_point": "flat"}, ("depth_mm",),
         ("drill_point_angle_deg",)),
        ({"depth_mode": "dimension", "drill_point": "angled"},
         ("depth_mm", "drill_point_angle_deg"), ()),
        ({"depth_mode": "through_all"}, (),
         ("depth_mm", "drill_point", "drill_point_angle_deg")),
    )
    for mouth, mouth_required, mouth_excluded in (
        ("none", (), ("mouth_diameter_mm", "mouth_depth_mm", "mouth_angle_deg")),
        ("counterbore", ("mouth_diameter_mm", "mouth_depth_mm"), ("mouth_angle_deg",)),
        ("countersink", ("mouth_diameter_mm", "mouth_angle_deg"), ("mouth_depth_mm",)),
    )
]
_chamfer_schema = OPERATION_SPECS["PartDesign_Chamfer"]["input_schema"]
_chamfer_schema["oneOf"] = [
    _conditional_form(_chamfer_schema, {"mode": "equal_distance"},
                      excluded=("size2_mm", "flip_direction")),
    _conditional_form(_chamfer_schema, {"mode": "two_distances"},
                      required=("size2_mm", "flip_direction")),
]
OPERATION_SPECS["agent_observe"]["side_effect"] = "committed"
OPERATION_SPECS["agent_observe"]["view_evidence_contract"] = VIEW_EVIDENCE_CONTRACT
OPERATION_SPECS["agent_observe"]["kind_roles"] = {"initial_views": [OBSERVATION_ROLES["initial_views"]]}
OPERATION_SPECS["agent_observe"]["view_role_conditions"] = {"paired": OBSERVATION_ROLES["paired_view"]}
OPERATION_SPECS["agent_start_session"]["session_observation_policy"] = SESSION_OBSERVATION_POLICY_SCHEMA
OPERATION_SPECS["agent_save"]["summary"] = (
    "Save the structurally known Target, including an in_progress model, to its allocated model_path. "
    "Allocation excludes the resume checkpoint; only a file already saved by this session can be updated. "
    "saved_to reports an actual save, independently of geometry validation and journal persistence."
)
OPERATION_SPECS["agent_observe"]["read_only_kinds"] = [
    "global", "entity", "selection_resolution", "regions", "points", "residual", "support_extension"]
OPERATION_SPECS["agent_validate"]["read_only_without_parameter_audit"] = True
OPERATION_SPECS["agent_get_capabilities"]["input_schema"]["properties"]["operation"] = _string("Internal method", OPERATION_SPECS)
OPERATION_SPECS["agent_get_state"]["summary"] = "Read native objects, Result and transaction state without importing STEP. Report the bound Source revision and independent current-file hash availability/match; safe reads do not adopt changed source or clear recovery uncertainty."
for _spec in OPERATION_SPECS.values():
    if _spec["side_effect"] == "committed" and "body_name" in _spec["input_schema"]["properties"]:
        _spec["summary"] += (
            " Result changes are confined to the receiving Body; other Bodies retain their world geometry and Tip."
            " Editing a Body with cross-Body downstream consumers is not supported."
        )
OPERATION_SPECS["agent_validate"]["input_schema"]["properties"]["session_id"] = TEXT_SCHEMA
OPERATION_SPECS["agent_close_session"]["input_schema"]["properties"]["session_id"] = TEXT_SCHEMA
OPERATION_SPECS["agent_start_session"]["input_schema"]["properties"]["session_id"] = TEXT_SCHEMA
OPERATION_SPECS["agent_start_session"]["input_schema"]["required"].append("session_id")
OPERATION_SPECS["agent_observe"]["input_schema"]["properties"]["session_id"] = TEXT_SCHEMA
OPERATION_SPECS["agent_observe"]["input_schema"]["required"].append("session_id")
OBSERVATION_REQUEST_SCHEMA = {"oneOf": [
    _branch("kind", kind, {"query": query})
    for kind, query in OPERATION_SPECS["agent_observe"]["query_schemas"].items()
]}


class RequestValidationError(ValueError):
    def __init__(self, issues):
        self.issue_count = len(issues)
        self.issues = issues[:16]
        self.issues_omitted = max(0, self.issue_count - len(self.issues))
        super().__init__("Input does not match the published contract")


def validate_schema_definition(schema):
    """Reject keywords this interpreter cannot enforce, including nested schemas."""
    known = {"type", "properties", "required", "additionalProperties", "items", "oneOf", "enum",
             "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "minItems", "maxItems",
             "minLength", "maxLength", "pattern", "description", "default", "title", "uniqueItems"}
    unknown = set(schema) - known
    if unknown:
        raise ValueError("Unsupported schema keywords: " + ", ".join(sorted(unknown)))
    for child in schema.get("properties", {}).values():
        validate_schema_definition(child)
    for child in schema.get("oneOf", []):
        validate_schema_definition(child)
    if "items" in schema:
        validate_schema_definition(schema["items"])


def validate_input_schema(value, schema, *, path="request", partial=False):
    """Strict finite JSON validation; no coercion, inserted defaults or CAD I/O."""
    import math
    import re
    issues = []
    def fail(loc, reason, **details):
        issues.append({"path": loc[:500], "message": reason, **details,
                       **({"path_characters_omitted": len(loc)-500} if len(loc)>500 else {})})
    def visit(item, spec, loc, skip_required=False):
        if "oneOf" in spec:
            branches = spec["oneOf"]
            # Branch selection is diagnostic only: shortcuts must never weaken
            # oneOf exclusivity or skip sibling constraints.
            visit(item, {k: v for k, v in spec.items() if k != "oneOf"}, loc, skip_required)
            successful = 0
            for branch in branches:
                before = len(issues)
                visit(item, branch, loc)
                successful += len(issues) == before
                del issues[before:]
            if successful == 1:
                return
            if successful > 1:
                fail(loc, "Matches more than one supported form"); return
            if isinstance(item, dict) and isinstance(item.get("request"), dict):
                kind = item["request"].get("kind")
                matching = [b for b in branches if kind in b.get("properties", {}).get("request", {})
                            .get("properties", {}).get("kind", {}).get("enum", [])]
                if len(matching) == 1:
                    visit(item, matching[0], loc); return
            if isinstance(item, dict):
                # Complete discriminator narrowing before field-presence diagnostics.
                for key in ("kind", "op", "mode", "depth_mode", "mouth_mode", "drill_point"):
                    tagged = [b for b in branches if b.get("properties", {}).get(key, {}).get("enum")]
                    if len(tagged) == len(branches):
                        allowed = [v for b in tagged for v in b["properties"][key]["enum"]]
                        if item.get(key) not in allowed:
                            values = list(dict.fromkeys(allowed))
                            fail(loc + "." + key, "Unsupported or missing discriminator",
                                 allowed_values=values[:16], allowed_values_omitted=max(0,len(values)-16)); return
                        matches_tag = [b for b in tagged if item[key] in b["properties"][key]["enum"]]
                        branches = matches_tag
                if len(branches) == 1:
                    visit(item, branches[0], loc); return
                explicit = [b for b in branches if any(k in item and all(k not in other.get("properties", {})
                            for other in branches if other is not b) for k in b.get("properties", {}))]
                if len(explicit) == 1:
                    visit(item, explicit[0], loc); return
                required_match = [b for b in branches if b.get("properties")
                                  and set(b.get("required", [])) <= set(item)]
                if len(required_match) == 1:
                    visit(item, required_match[0], loc); return
                object_forms = [b for b in branches if b.get("type") in (None, "object")]
                if len(object_forms) == 1:
                    visit(item, object_forms[0], loc); return
            fail(loc, "Must match exactly one supported form",
                 required_forms=[b.get("required", []) for b in branches][:8])
            return
        typ = spec.get("type")
        types = [typ] if isinstance(typ, str) else typ
        matches = {"null": item is None, "object": isinstance(item, dict), "array": isinstance(item, list),
                   "string": isinstance(item, str), "boolean": isinstance(item, bool),
                   "integer": isinstance(item, int) and not isinstance(item, bool),
                   "number": isinstance(item, (float, int)) and not isinstance(item, bool)}
        if types and not any(matches.get(t, False) for t in types):
            fail(loc, "expected " + repr(types)); return
        if "enum" in spec and item not in spec["enum"]:
            fail(loc, "Unsupported value", allowed_values=spec["enum"][:16],
                 allowed_values_omitted=max(0,len(spec["enum"])-16)); return
        if isinstance(item, dict):
            props = spec.get("properties", {})
            if spec.get("additionalProperties") is False:
                for key in sorted(set(item) - set(props)):
                    fail(loc + "." + key, "unknown field")
            if not skip_required:
                for key in spec.get("required", []):
                    if key not in item: fail(loc + "." + key, "required")
            for key in props.keys() & item.keys(): visit(item[key], props[key], loc + "." + key)
        elif isinstance(item, list):
            if len(item) < spec.get("minItems", 0) or len(item) > spec.get("maxItems", len(item)):
                fail(loc, "array length outside supported range")
            if spec.get("uniqueItems") and any(child in item[:index] for index, child in enumerate(item)):
                fail(loc, "array items must be unique")
            for i, child in enumerate(item):
                if "items" in spec: visit(child, spec["items"], loc + "[%d]" % i)
        elif isinstance(item, str):
            if len(item) < spec.get("minLength", 0) or len(item) > spec.get("maxLength", len(item)):
                fail(loc, "string length outside supported range")
            if "pattern" in spec and not re.search(spec["pattern"], item): fail(loc, "invalid format")
        elif isinstance(item, (int, float)) and not isinstance(item, bool):
            if isinstance(item, float) and not math.isfinite(item): fail(loc, "must be finite")
            for key, invalid in (("minimum", item < spec.get("minimum", item)),
                                 ("maximum", item > spec.get("maximum", item)),
                                 ("exclusiveMinimum", "exclusiveMinimum" in spec and item <= spec["exclusiveMinimum"]),
                                 ("exclusiveMaximum", "exclusiveMaximum" in spec and item >= spec["exclusiveMaximum"])):
                if invalid: fail(loc, "violates " + key)
    visit(value, schema, path, partial)
    if issues: raise RequestValidationError(issues)


def validate_feature_request(command, request, *, path=None):
    """Shared feature format and finite numeric relations; never reads CAD state."""
    path = path or command
    validate_input_schema(request, OPERATION_SPECS[command]["input_schema"], path=path)
    if command == "Sketcher_EditSketch":
        for index, item in enumerate(request["geometry"]):
            if item["kind"] in ("arc", "ellipse_arc"):
                span = item["end_deg"] - item["start_deg"]
                if not 0 < span < 360:
                    raise RequestValidationError([{
                        "path": path + ".geometry[%d].end_deg" % index,
                        "message": "end_deg - start_deg must be greater than 0 and less than 360; preserve path direction",
                    }])
    if (command == "PartDesign_Hole" and request["mouth_mode"] != "none"
            and request["mouth_diameter_mm"] <= request["diameter_mm"]):
        raise RequestValidationError([{
            "path": path + ".mouth_diameter_mm",
            "message": "Entrance diameter must exceed diameter_mm",
        }])


# Neutral guidance templates are separate from input schemas and never execute CAD.
def _example_binding(name):
    return {"$binding": name}


def _example_value(check_value, en, zh):
    return {"check_value": check_value, "en": en, "zh": zh}


def _example_reference(kind, role):
    # These declaration-only values are never rendered into cards or issued as evidence.
    return {"entity_kind": kind, "intended_role": role,
            "owner": {"role": "rebuild", "revision": "declaration-only", "object_name": "Predecessor"},
            "signatures": {"geometry": "declaration-only", "topology": "declaration-only"}}


FEATURE_EXAMPLE_BINDINGS = {
    "session_id": _example_value("DeclarationSession", "Current returned session ID.", "当前返回的会话ID。"),
    "body_name": _example_value("ReceivingBody", "Actual owned receiving Body name.", "实际会话所属接收Body的名称。"),
    "new_body_name": _example_value("NewToolBody", "Choose an unused native name for the new tool Body.", "为新工具Body选择未占用的原生名称。"),
    "feature_name": _example_value("NewFeature", "Choose an unused native name for this new feature.", "为本次新特征选择未占用的原生名称。"),
    "datum_name": _example_value("NewDatum", "Choose an unused native Datum name.", "选择未占用的原生Datum名称。"),
    "intent": _example_value("Measured modeling intent", "Explain this operation's measured geometric purpose.", "说明本次操作有测量依据的几何意图。"),
    "source_evidence_id": _example_value("evidence-00000000000000000000", "Source evidence actually issued in this session.", "本会话实际签发的Source证据。"),
    "target_evidence_id": _example_value("evidence-11111111111111111111", "Current applicable Target evidence actually issued in this session.", "本会话实际签发、当前适用的Target证据。"),
    "target_revision": _example_value("declaration-only", "Current Target revision returned by observation/state.", "观察或状态实际返回的当前Target修订。"),
    "sketch_name": _example_value("ExistingSketch", "Actual owned empty Sketch name returned by creation/state.", "创建或状态实际返回的会话所属空Sketch名称。"),
    "profile_sketch_name": _example_value("ProfileSketch", "Actual same-Body closed profile Sketch name.", "实际同Body闭合截面Sketch名称。"),
    "section_sketch_name": _example_value("SectionSketch", "Actual second ordered section on a distinct plane.", "不同平面上实际第二个有序截面名称。"),
    "path_sketch_name": _example_value("PathSketch", "Actual same-Body continuous whole-path Sketch name.", "实际同Body连续完整路径Sketch名称。"),
    "profile_center_x_mm": _example_value(3.0, "Signed circle center X relative to this Sketch's own origin and X axis, mm; convert coordinates from other frames first.", "圆心相对该Sketch自身原点和X轴的有符号局部X坐标，单位mm；其他坐标系的测量先换算。"),
    "profile_center_y_mm": _example_value(4.0, "Signed circle center Y relative to this Sketch's own origin and Y axis, mm; convert coordinates from other frames first.", "圆心相对该Sketch自身原点和Y轴的有符号局部Y坐标，单位mm；其他坐标系的测量先换算。"),
    "profile_radius_mm": _example_value(2.0, "Measured circle radius, mm; same circle as profile_diameter_mm.", "测得的圆半径，单位mm；与profile_diameter_mm对应同一个圆。"),
    "profile_diameter_mm": _example_value(4.0, "Measured diameter of that circle, mm.", "同一圆的测量直径，单位mm。"),
    "body_position_mm": _example_value([0.0, 0.0, 0.0], "Measured feature position in the receiving Body's local coordinates, mm.", "测得的特征在接收Body局部坐标系中的位置，单位mm。"),
    "datum_attachment_offset_mm": _example_value([0.0, 0.0, 0.0], "Measured translation in the Datum's attachment coordinate system, mm.", "测得的Datum在附着坐标系中的平移偏移，单位mm。"),
    "datum_attachment_axis": _example_value([0.0, 0.0, 1.0], "Chosen nonzero rotation axis in the Datum's attachment coordinate system.", "在Datum附着坐标系中明确选择的非零旋转轴。"),
    "datum_attachment_angle_deg": _example_value(0.0, "Measured rotation about datum_attachment_axis, degrees.", "测得的绕datum_attachment_axis的旋转角，单位度。"),
    "length_mm": _example_value(20.0, "Measured full generating length, mm; include portions later obscured by dress-ups.", "测得的完整生成长度，单位mm；包括后来被修饰遮挡的部分。"),
    "width_mm": _example_value(12.0, "Measured intrinsic width in the chosen local frame, mm.", "选定局部坐标系中测得的固有宽度，单位mm。"),
    "height_mm": _example_value(8.0, "Measured intrinsic height in the chosen local frame, mm.", "选定局部坐标系中测得的固有高度，单位mm。"),
    "reverse_direction": _example_value(False, "Choose from this feature's actual frame and material/rotation direction.", "按本特征实际坐标系及材料/旋转方向选择。"),
    "rotation_axis_role": _example_value("vertical", "Chosen horizontal/vertical axis of the actual profile Sketch.", "实际截面Sketch中明确选择的horizontal/vertical轴。"),
    "rotation_angle_deg": _example_value(180.0, "Measured sweep angle about the chosen axis, degrees.", "绕选定轴测得的旋转范围，单位度。"),
    "hole_diameter_mm": _example_value(4.0, "Measured shaft diameter, mm.", "测得的孔身直径，单位mm。"),
    "hole_depth_mm": _example_value(10.0, "Measured Sketch-plane-to-tip depth, mm, for this angled mode.", "本斜钻尖模式从Sketch平面到钻尖的测量深度，单位mm。"),
    "drill_angle_deg": _example_value(118.0, "Measured included drill-tip angle, degrees.", "测得的钻尖夹角，单位度。"),
    "mouth_diameter_mm": _example_value(8.0, "Measured entrance diameter greater than the shaft diameter, mm.", "测得的大于孔身直径的入口直径，单位mm。"),
    "mouth_angle_deg": _example_value(90.0, "Measured included countersink angle, degrees.", "测得的沉头夹角，单位度。"),
    "tool_body_names": _example_value(["ToolBody"], "Actual registered tool Body names; this Common example uses exactly one.", "实际登记的工具Body名称；本Common示例只使用一个。"),
    "predecessor_name": _example_value("Predecessor", "Actual current solid Tip of the receiving Body.", "接收Body实际当前实体Tip的名称。"),
    "edge_refs": _example_value([_example_reference("edge", "dressup_edge")], "Copy complete exact edge references resolved for this current predecessor.", "复制为当前前驱解析的完整精确边引用。"),
    "draft_face_refs": _example_value([_example_reference("face", "draft_target")], "Copy current exact draft_target face references.", "复制当前精确draft_target面引用。"),
    "opening_face_refs": _example_value([_example_reference("face", "thickness_opening")], "Copy current exact thickness_opening face references.", "复制当前精确thickness_opening面引用。"),
    "fillet_radius_mm": _example_value(1.0, "Measured blend radius, mm.", "测得的圆角半径，单位mm。"),
    "chamfer_size_mm": _example_value(1.0, "Measured first chamfer distance, mm.", "测得的第一倒角距离，单位mm。"),
    "chamfer_size2_mm": _example_value(2.0, "Measured second chamfer distance, mm.", "测得的第二倒角距离，单位mm。"),
    "flip_direction": _example_value(False, "Set True or False for native FlipDirection in two_distances; verify the adjacent-face distance assignment.", "在two_distances中为原生FlipDirection明确选择True或False，并核对相邻面的距离分配。"),
    "neutral_plane_role": _example_value("XY_Plane", "Chosen same-Body Origin plane defining the neutral level.", "明确选择定义中性位置的同Body原点平面。"),
    "draft_angle_deg": _example_value(5.0, "Measured draft angle and material side, degrees.", "测得的拔模角及材料侧，角度单位度。"),
    "thickness_mm": _example_value(1.0, "Measured wall thickness, mm.", "测得的壁厚，单位mm。"),
    "thickness_direction": _example_value("inward", "Choose inward/outward from the observed material side.", "按观察到的材料侧选择inward/outward。"),
    "mirror_plane_role": _example_value("YZ_Plane", "Chosen same-Body Origin plane matching the observed symmetry.", "符合观测对称性的明确同Body原点平面。"),
    "source_feature_names": _example_value(["SeedFeature"], "Actual caller-ordered additive/subtractive seed feature names.", "实际按调用者顺序排列的加料/减料种子特征名称。"),
    "pattern_axis_role": _example_value("X_Axis", "Chosen same-Body Origin axis matching the observed repetition.", "符合观测重复方向的明确同Body原点轴。"),
    "pattern_offset_mm": _example_value(6.0, "Measured per-step offset in this offset-mode example, mm.", "本offset模式示例中测得的每步间距，单位mm。"),
    "occurrences": _example_value(3, "Observed total occurrences including the original.", "观察到的总实例数，包含原件。"),
}


def _feature_example(tool, arguments, preconditions, readback, name_field="feature_name"):
    common = {"session_id": _example_binding("session_id"),
              "body_name": _example_binding("body_name"),
              "intent": _example_binding("intent"),
              "evidence": [_example_binding("source_evidence_id"), _example_binding("target_evidence_id")],
              "expected_revision": _example_binding("target_revision")}
    if name_field:
        common[name_field] = _example_binding(name_field)
    return {"tool": tool, "arguments": {**common, **arguments},
            "preconditions": {"en": preconditions[0], "zh": preconditions[1]},
            "readback": {"en": readback[0], "zh": readback[1]}}


FEATURE_EXAMPLES = {
    "body": _feature_example("PartDesign_Body", {"body_name": _example_binding("new_body_name")},
        ("Use when the construction needs a new independent tool Body.", "构造需要新的独立工具Body时使用。"),
        ("Use the returned Body name and updated revision in later calls.", "后续调用使用实际返回的Body名称及更新修订。"), None),
    "sketch": _feature_example("Sketcher_EditSketch", {
        "geometry": [{"kind": "circle", "id": "section_circle",
                      "center": [_example_binding("profile_center_x_mm"), _example_binding("profile_center_y_mm")],
                      "radius_mm": _example_binding("profile_radius_mm")}],
        "constraints": [
            {"kind": "DistanceX", "point": {"geometry_id": "section_circle", "position": "center"}, "value": _example_binding("profile_center_x_mm")},
            {"kind": "DistanceY", "point": {"geometry_id": "section_circle", "position": "center"}, "value": _example_binding("profile_center_y_mm")},
            {"kind": "Diameter", "geometry_id": "section_circle", "value": _example_binding("profile_diameter_mm")},
        ]},
        ("The owned Sketch is empty; convert measured coordinates into its local XY frame before using the same center values for geometry and DistanceX/DistanceY.", "会话所属Sketch为空；圆心测量先换算到该Sketch自身XY坐标系，再将相同值用于几何与DistanceX/DistanceY定位。"),
        ("Check actual solver diagnostics and the intended profile before generating a feature.", "生成特征前检查实际求解诊断及截面意图。"), "sketch_name"),
    "datum": _feature_example("Part_DatumPlane", {
        "mode": "attached", "reference": {"kind": "origin_plane", "role": "XY_Plane"},
        "position_mm": _example_binding("datum_attachment_offset_mm"), "axis": _example_binding("datum_attachment_axis"),
        "angle_deg": _example_binding("datum_attachment_angle_deg")},
        ("This example attaches to the receiving Body's own XY plane; choose the intended support and frame.", "本例附着到接收Body自身XY平面；按意图选择支承和坐标系。"),
        ("Check returned support, attachment frame and placement; the Datum is not a solid.", "检查返回的支承、附着坐标系和Placement；Datum不是实体。"), "datum_name"),
    "primitives": _feature_example("PartDesign_AdditiveBox", {
        "length_mm": _example_binding("length_mm"), "width_mm": _example_binding("width_mm"),
        "height_mm": _example_binding("height_mm"), "position_mm": _example_binding("body_position_mm")},
        ("Use when measured planar bounds support a native box in this Body frame.", "测得的平面边界支持在该Body坐标系使用原生Box时采用。"),
        ("Check native dimensions, Body Tip and returned shape facts.", "检查原生尺寸、Body Tip及返回的形状事实。")),
    "extrude": _feature_example("PartDesign_Pad", {
        "sketch_name": _example_binding("profile_sketch_name"), "length_mm": _example_binding("length_mm"),
        "reversed": _example_binding("reverse_direction")},
        ("A same-Body closed Sketch and measured constant-direction extent support this Pad.", "同Body闭合Sketch及测得的恒定方向范围支持该Pad。"),
        ("Check profile, full native length, direction and Body Tip.", "检查截面、完整原生长度、方向及Body Tip。")),
    "revolve": _feature_example("PartDesign_Revolution", {
        "sketch_name": _example_binding("profile_sketch_name"),
        "axis": {"kind": "sketch_axis", "axis": _example_binding("rotation_axis_role")},
        "angle_deg": _example_binding("rotation_angle_deg"), "reversed": _example_binding("reverse_direction")},
        ("The closed profile and chosen Sketch axis explain the measured rotational shape.", "闭合截面和明确Sketch轴能够解释测得的旋转形状。"),
        ("Check the actual axis link, angle, direction and resulting Tip.", "检查实际轴链接、角度、方向及结果Tip。")),
    "hole": _feature_example("PartDesign_Hole", {
        "sketch_name": _example_binding("profile_sketch_name"), "diameter_mm": _example_binding("hole_diameter_mm"),
        "depth_mode": "dimension", "depth_mm": _example_binding("hole_depth_mm"),
        "drill_point": "angled", "drill_point_angle_deg": _example_binding("drill_angle_deg"),
        "mouth_mode": "countersink", "mouth_diameter_mm": _example_binding("mouth_diameter_mm"),
        "mouth_angle_deg": _example_binding("mouth_angle_deg"), "reversed": _example_binding("reverse_direction")},
        ("An owned circle-only profile and prior solid support this finite angled-tip countersunk Hole; establish entry and material direction.", "会话所属纯圆截面和既有实体支持本有限深度斜钻尖沉头Hole；确认入口和材料方向。"),
        ("Check native modes and direction plus each intended opening/bottom in the actual geometry.", "检查原生模式和方向，并在实际几何中核对每个预期孔口/孔底。")),
    "boolean": _feature_example("PartDesign_Boolean", {
        "mode": "Common", "tool_body_names": _example_binding("tool_body_names")},
        ("The main receiving Body and exactly one registered solid tool Body support the intended intersection.", "主接收Body及一个已登记实体工具Body支持预期交集。"),
        ("Check actual operands, native operation and resulting receiving Body Tip.", "检查实际操作数、原生操作及接收Body结果Tip。")),
    "loft": _feature_example("PartDesign_AdditiveLoft", {
        "profile_sketch_name": _example_binding("profile_sketch_name"), "section_sketch_name": _example_binding("section_sketch_name")},
        ("Two caller-ordered closed same-Body sections on distinct planes explain the transition.", "不同平面上两个调用者排序的同Body闭合截面能够解释过渡。"),
        ("Check ordered section links and the native result.", "检查有序截面链接及原生结果。")),
    "pipe": _feature_example("PartDesign_AdditivePipe", {
        "profile_sketch_name": _example_binding("profile_sketch_name"), "path_sketch_name": _example_binding("path_sketch_name")},
        ("A closed section and one continuous same-Body line/arc path explain this sweep; establish the start and direction.", "闭合截面及同Body单条连续直线/圆弧路径能够解释扫掠；确认起点和方向。"),
        ("Check profile/path links, fixed native transport modes and the result.", "检查截面/路径链接、固定原生搬运模式及结果。")),
    "fillet": _feature_example("PartDesign_Fillet", {
        "predecessor_name": _example_binding("predecessor_name"), "edge_refs": _example_binding("edge_refs"),
        "radius_mm": _example_binding("fillet_radius_mm")},
        ("Resolve the intended edge set on the current solid predecessor with dressup_edge use.", "按dressup_edge用途解析当前实体前驱的目标边集。"),
        ("Check the exact native Base links, radius and valid resulting Tip.", "检查精确原生Base链接、半径及有效结果Tip。")),
    "chamfer": _feature_example("PartDesign_Chamfer", {
        "predecessor_name": _example_binding("predecessor_name"), "edge_refs": _example_binding("edge_refs"),
        "mode": "two_distances", "size_mm": _example_binding("chamfer_size_mm"),
        "size2_mm": _example_binding("chamfer_size2_mm"), "flip_direction": _example_binding("flip_direction")},
        ("Current resolved edges and two measured adjacent-face distances support two_distances. For equal_distance, omit size2_mm and flip_direction.", "当前解析的边集及两个相邻面测量距离支持two_distances；equal_distance省略size2_mm和flip_direction。"),
        ("Check native mode, side assignment, both sizes and Base links.", "检查原生模式、侧面分配、两项尺寸及Base链接。")),
    "draft": _feature_example("PartDesign_Draft", {
        "predecessor_name": _example_binding("predecessor_name"), "face_refs": _example_binding("draft_face_refs"),
        "neutral_plane": {"kind": "origin_plane", "role": _example_binding("neutral_plane_role")},
        "angle_deg": _example_binding("draft_angle_deg"), "reversed": _example_binding("reverse_direction")},
        ("Resolve current draft_target faces and explicitly choose the neutral plane and material direction.", "解析当前draft_target面，明确选择中性平面和材料方向。"),
        ("Check native face/neutral references, angle, direction and result.", "检查原生面/中性引用、角度、方向及结果。")),
    "thickness": _feature_example("PartDesign_Thickness", {
        "predecessor_name": _example_binding("predecessor_name"), "face_refs": _example_binding("opening_face_refs"),
        "thickness_mm": _example_binding("thickness_mm"), "direction": _example_binding("thickness_direction"), "join": "arc"},
        ("Current opening-face references, wall measurements and material side support hollowing.", "当前开口面引用、壁厚测量及材料侧支持抽壳。"),
        ("Check actual openings, wall side, thickness, join and native result.", "检查实际开口、壁厚侧、厚度、连接及原生结果。")),
    "mirror": _feature_example("PartDesign_Mirrored", {
        "predecessor_name": _example_binding("predecessor_name"), "source_feature_names": _example_binding("source_feature_names"),
        "mirror_plane": {"kind": "origin_plane", "role": _example_binding("mirror_plane_role")}},
        ("Choose actual same-Body seed features and an explicit plane supported by symmetry evidence.", "选择实际同Body种子特征及有对称证据支持的明确平面。"),
        ("Check ordered source links, plane and resulting instances.", "检查有序源链接、平面及结果实例。")),
    "pattern": _feature_example("PartDesign_LinearPattern", {
        "predecessor_name": _example_binding("predecessor_name"), "source_feature_names": _example_binding("source_feature_names"),
        "axis": {"kind": "origin_axis", "role": _example_binding("pattern_axis_role")},
        "extent_mode": "offset", "distance_mm": _example_binding("pattern_offset_mm"),
        "occurrences": _example_binding("occurrences"), "reversed": _example_binding("reverse_direction")},
        ("Measured repetition supports these actual seed features, one direction and a per-step offset.", "重复测量支持这些实际种子特征、单方向及每步间距。"),
        ("Check native offset semantics, total occurrences and actual instances.", "检查原生间距语义、总实例数及实际实例。")),
}


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
__all__ = [
    'ARTIFACT_NUMBER_LIMIT',
    'ARTIFACT_SUFFIXES',
    'CAPABILITY_VIEWS',
    'DOMAIN_EVIDENCE_SCHEMA',
    'DOMAIN_MODE_GAPS',
    'DRESSUP_SCOPE',
    'EDGE_QUERY_SCHEMA',
    'EDGE_REF_SCHEMA',
    'ENTITY_QUERY_FIELDS',
    'ENTITY_QUERY_SCHEMA',
    'FACE_REF_SCHEMA',
    'FEATURE_EXAMPLE_BINDINGS',
    'FEATURE_EXAMPLES',
    'HISTORY_COMMANDS',
    'LOG_READ_PROPERTIES',
    'NAME_SCHEMA',
    'OBSERVATION_REQUEST_SCHEMA',
    'OBSERVATION_ROLES',
    'OPERATION_SPECS',
    'OUTPUT_DIR_SCHEMA',
    'PRIMITIVE_COMMANDS',
    'PRIMITIVE_FAMILIES',
    'PRODUCT_ID',
    'RETRY_ACTIONS',
    'ROLE_PORTS',
    'RequestValidationError',
    'SELECTION_RESOLUTION_QUERY_FIELDS',
    'SELECTION_RESOLUTION_QUERY_SCHEMA',
    'STABLE_SELECTOR_SCHEMA',
    'TEXT_SCHEMA',
    'VALIDATION_OPTIONS_SCHEMA',
    'VIEW_DIRECTIONS',
    'VIEW_DISPLAY_MODES',
    'VIEW_EVIDENCE_CONTRACT',
    'VIEW_PROJECTIONS',
    'VIEW_QUERY_FIELDS',
    'VIEW_QUERY_SCHEMA',
    'VIEW_CAMERA_SPECS',
    'DEFAULT_VIEW_DIRECTIONS',
    'IMAGE_OBSERVATION_KINDS',
    'INITIAL_VIEW_GROUPS',
    'VIEW_BOARD_HEADER_HEIGHT',
    'IMAGE_CONTENT_LIMITS',
    'SESSION_OBSERVATION_POLICY_SCHEMA',
    'SESSION_ARTIFACTS_SCHEMA',
    'artifact_basename',
    'artifact_filename',
    'artifact_matches',
    'initial_view_layout',
    '_branch',
    '_schema',
    '_string',
    'request_side_effect',
    'resolve_retry_action',
    'validate_input_schema',
    'validate_observation_role',
    'validate_feature_request',
    'validate_schema_definition',
]
