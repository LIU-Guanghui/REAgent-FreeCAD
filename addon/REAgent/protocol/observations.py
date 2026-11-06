"""Observation references, directions and image layout agreements."""

from REAgent.protocol.definitions import NAME_SCHEMA
from REAgent.schema import _array, _boolean, _integer, _nullable, _number, _object, _schema, _string

VIEW_DIRECTIONS = ("front", "back", "top", "bottom", "left", "right", *tuple(
    "iso_x%s_y%s_z%s" % ("p" if x > 0 else "m", "p" if y > 0 else "m", "p" if z > 0 else "m")
    for x in (1, -1) for y in (1, -1) for z in (1, -1)))

DEFAULT_VIEW_DIRECTIONS = ("front", "top", "left", "iso_xm_ym_zp")

IMAGE_OBSERVATION_KINDS = ("view", "initial_views")

OBSERVATION_ROLES = {"initial_views": "source", "paired_view": "target", "feature": "target"}

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

IMAGE_CONTENT_LIMITS = {"max_images": 8, "max_image_bytes": 4 * 1024 * 1024,
                        "max_total_bytes": 12 * 1024 * 1024,
                        "max_image_pixels": 16 * 1024 * 1024}

SESSION_OBSERVATION_POLICY_SCHEMA = {
    "type": "string", "enum": ["visual", "geometry_only"], "default": "visual",
    "description": "Task preparation policy. visual requires complete initial Source views before native modeling. geometry_only requires explicit human task permission; never select it to bypass failed images.",
}

def view_direction_label(direction):
    if direction.startswith("iso_"):
        return "Iso (" + ", ".join(("+" if part[1] == "p" else "−") + part[0].upper()
                                  for part in direction[4:].split("_")) + ")"
    return direction[:1].upper() + direction[1:].replace("_", " ")


def _grid_layout(width, height, cells, columns, output_long_edge_px):
    for name, value in (("width", width), ("height", height)):
        if type(value) is not int or not 128 <= value <= 4096:
            raise ValueError(name + " must be an integer from 128 to 4096")
    if type(output_long_edge_px) is not int or not 512 <= output_long_edge_px <= 4096:
        raise ValueError("output_long_edge_px must be an integer from 512 to 4096")
    if width * height > IMAGE_CONTENT_LIMITS["max_image_pixels"]:
        raise ValueError("Native frame exceeds pixel capacity")
    rows = (len(cells) + columns - 1) // columns
    header = 24
    scale = min(1.0, output_long_edge_px / (columns * width),
                (output_long_edge_px - rows * header) / (rows * height))
    cell_width, cell_height = max(1, int(width * scale)), max(1, int(height * scale))
    board_width, board_height = columns * cell_width, rows * (cell_height + header)
    if board_width * board_height > IMAGE_CONTENT_LIMITS["max_image_pixels"]:
        raise ValueError("Final canvas exceeds pixel capacity")
    placed = []
    for index, (role, direction) in enumerate(cells):
        x, y = index % columns * cell_width, index // columns * (cell_height + header)
        placed.append({"role": role, "direction": direction,
                       "image_rect_px": [x, y + header, cell_width, cell_height],
                       "label_rect_px": [x, y, cell_width, header],
                       "scale_x": cell_width / width, "scale_y": cell_height / height})
    return {"rows": rows, "columns": columns, "cell_order": "row_major",
            "width_px": board_width, "height_px": board_height,
            "sampling_width_px": width, "sampling_height_px": height,
            "output_long_edge_px": output_long_edge_px, "cells": placed}


def initial_view_layout(width, height, output_long_edge_px=1536):
    return {key: _grid_layout(width, height, [("source", d) for d in group["directions"]],
                              group["columns"], output_long_edge_px)
            for key, group in INITIAL_VIEW_GROUPS.items()}


def paired_view_layout(width, height, directions, output_long_edge_px=1536):
    return _grid_layout(width, height, [(role, d) for d in directions for role in ("source", "target")],
                        2 if len(directions) <= 3 else 4, output_long_edge_px)


def single_view_layout(width, height, role, direction, output_long_edge_px=1536):
    return _grid_layout(width, height, [(role, direction)], 1, output_long_edge_px)


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
    "image_retention": (
        "One final PNG per board or single view is shared by model delivery, GUI and registered readback. "
        "Only owned temporary native frames are removed; cleanup residue is reported. "
        "Initial views return both complete boards and paired views return the complete board. "
        "Preparation does not attest model receipt or inspection. Missing files are never recreated by readback."
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
    "output": {"default_long_edge_px": 1536, "minimum_long_edge_px": 512, "maximum_long_edge_px": 4096,
               "sampling": "width/height control native sampling; output_long_edge_px only caps final size; no upsampling",
               "limits": "Check pixels/allocation and count before rendering; actual PNG bytes after encoding; reject the entire group"},
    "initial_views": {"role": OBSERVATION_ROLES["initial_views"], "directions": list(VIEW_DIRECTIONS),
                      "groups": INITIAL_VIEW_GROUPS, "projection": "orthographic", "display_mode": "flat_lines",
                      "transparency": 0, "header_height_px": 24},
}

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
        "width": _integer("Native sampling width in pixels", 128, 4096),
        "height": _integer("Native sampling height in pixels", 128, 4096),
        "output_long_edge_px": _integer("Final longest-edge cap, default 1536; no upsampling. Raise native width/height too when more sampling is needed", 512, 4096),
        "display_mode": {**_string(
            "Object display mode; omitted means flat_lines (lit surfaces with edges). Readback certifies object properties, not viewport overrides or PNG pixel style.",
            VIEW_DISPLAY_MODES
        ), "default": "flat_lines"},
        "projection": _string(
            "Verified camera projection", VIEW_PROJECTIONS
        ),
        "focus_box_mm": _nullable(_FOCUS_BOX_SCHEMA),
        "paired": _boolean(
            "Render Source and current Target together; requires role=%s. Pairs remain adjacent; up to two pairs per row"
            % OBSERVATION_ROLES["paired_view"]
        ),
        "include_section": _boolean(
            "Add one paired half-cutaway view"
        ),
        "section_axis": _string("Half-cutaway axis", ("x", "y", "z")),
    },
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

ENTITY_FIELDS = {
    "face": ("name", "index", "surface_type", "orientation", "area_mm2", "center_mm",
             "center_unavailable_reason", "surface_sample", "parameter_range", "wire_count",
             "edge_count", "bounding_box_mm", "surface", "adjacent_faces"),
    "edge": ("name", "index", "curve_type", "orientation", "length_mm", "center_mm",
             "center_unavailable_reason", "endpoints_mm", "midpoint_mm", "parameter_range",
             "closed", "periodic", "curve", "adjacent_faces", "local_tolerance_mm"),
}

def entity_record(kind, values):
    """Keep produced base entity fields identical to capability discovery."""
    if set(values) != set(ENTITY_FIELDS[kind]):
        raise ValueError("Entity output differs from declared " + kind + " fields")
    return values

FEATURE_OBSERVATION_BUDGET_BYTES = 16 * 1024

FEATURE_QUERY_SCHEMA = _schema({
    "object": NAME_SCHEMA,
    "sketch_page": _schema({
        "section": _string("Sketch element page", ("geometry", "constraints")),
        "offset": _integer("Zero-based first native index", 0),
        "limit": _integer("Page size", 1, 16),
    }, ("section",)),
    "vertex_page": _schema({
        "offset": _integer("Zero-based page offset; returned VertexN indices are one-based", 0),
        "limit": _integer("Page size", 1, 64),
    }),
}, ("object",))

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
            "Top-level entity keys; default compact. Face: " + ", ".join(ENTITY_FIELDS["face"]) + ". Edge: " + ", ".join(ENTITY_FIELDS["edge"]) + ". Use surface/curve for nested details. ['*'] discovers this scope's actual fields; stable_selector/semantic_ref require a writable current predecessor. Historical/Body/empty objects have no writable refs; quality flags are not exposed.",
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
