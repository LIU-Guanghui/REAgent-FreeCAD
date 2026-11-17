"""Native feature request definitions and cross-field relations."""

from REAgent.protocol.definitions import NAME_SCHEMA, TEXT_SCHEMA, _DOCUMENT, operation
from REAgent.protocol.observations import edge_ref_schema, face_ref_schema
from REAgent.json_schema import RequestValidationError, array, boolean, branch, conditional_form, integer, number, object_schema, string

FEATURE_SPECS = {}

DRESSUP_SCOPE = {
    "edge_limit": 64, "explicit_complete_edge_set": True, "implicit_all_edges": False,
    "mode_gaps": {
        "fillet": ["use_all_edges", "face_or_whole_feature_implicit_edges", "variable_radius"],
        "chamfer": ["use_all_edges", "face_or_whole_feature_implicit_edges", "distance_and_angle"],
    },
}

DOMAIN_EVIDENCE_SCHEMA = array(
    "Include current Target evidence in every native write. All Target IDs must match the current revision. "
    "After each confirmed write, replace the previous Target state ID with the returned evidence_id. "
    "Use agent_get_state for missing state evidence. For geometry, cite the observation_id from agent_observe. "
    "Include bound Source evidence when the workflow requires it.",
    {"type": "string", "pattern": r"^(?:evidence|target-state)-[a-f0-9]{20}$"}, 1, 16,
)

TARGET_REVISION_SCHEMA = string("Target revision derived from the cited evidence.")

FEATURE_SPECS["PartDesign_Body"] = operation(
    "body", "Create and register an additional native tool Body for a separate feature history. "
    "The Target session starts with the main Body for the result.",
    ("target",), "committed",
    {"document": _DOCUMENT, "session_id": TEXT_SCHEMA,
     "body_name": {**NAME_SCHEMA, "description": "Unique native name for the tool Body"},
     "intent": TEXT_SCHEMA, "evidence": DOMAIN_EVIDENCE_SCHEMA,
     "expected_revision": TARGET_REVISION_SCHEMA},
    ("document", "session_id", "body_name", "intent", "evidence", "expected_revision"),
    ("Current Target evidence belongs to this session; reconstruction additionally requires Source evidence.",),
    timeout_class="long",
)

FEATURE_SPECS["PartDesign_NewSketch"] = operation(
    "sketch", "Create an empty native Sketch in an owned Body. Use Sketcher_EditSketch to add its geometry and constraints.",
    ("target",), "committed",
    {"document": _DOCUMENT, "session_id": TEXT_SCHEMA,
     "body_name": {**NAME_SCHEMA, "description": "Owned receiving Body"},
     "sketch_name": {**NAME_SCHEMA, "description": "Unique native Sketch name"},
     "intent": TEXT_SCHEMA, "evidence": DOMAIN_EVIDENCE_SCHEMA,
     "expected_revision": TARGET_REVISION_SCHEMA},
    ("document", "session_id", "body_name", "sketch_name", "intent", "evidence", "expected_revision"),
    ("Current Target evidence belongs to this session; reconstruction additionally requires Source evidence.",),
    timeout_class="long",
)

FEATURE_SPECS["Sketcher_MapSketch"] = operation(
    "sketch", "First attachment of an owned empty Sketch to an Origin, Datum or Coordinate System plane in the same Body.",
    ("target",), "committed",
    {"document": _DOCUMENT, "session_id": TEXT_SCHEMA,
     "body_name": {**NAME_SCHEMA, "description": "Owned receiving Body"},
     "sketch_name": {**NAME_SCHEMA, "description": "Owned, unattached Sketch"},
     "reference": {"oneOf": [
         branch("kind", "origin_plane", {
             "role": string("Same-Body Origin plane", ("XY_Plane", "XZ_Plane", "YZ_Plane"))}),
         branch("kind", "datum_plane", {
             "datum_name": {**NAME_SCHEMA, "description": "Same-Body native Part::DatumPlane"}}),
         branch("kind", "coordinate_system_plane", {
             "datum_name": {**NAME_SCHEMA, "description": "Same-Body native Part::LocalCoordinateSystem"},
             "role": string("Stable local plane role", ("XY_Plane", "XZ_Plane", "YZ_Plane"))}),
     ]},
     "intent": TEXT_SCHEMA, "evidence": DOMAIN_EVIDENCE_SCHEMA,
     "expected_revision": TARGET_REVISION_SCHEMA},
    ("document", "session_id", "body_name", "sketch_name", "reference", "intent", "evidence", "expected_revision"),
    ("Current Target evidence belongs to this session; reconstruction also requires Source evidence; Sketch is not yet attached.",),
    timeout_class="long",
)

FEATURE_SPECS["Sketcher_ValidateSketch"] = operation(
    "sketch", "Read native Sketch constraint and geometry diagnostics without invoking repair.",
    ("target",), "none",
    {"document": _DOCUMENT, "session_id": TEXT_SCHEMA,
     "body_name": {**NAME_SCHEMA, "description": "Owned Sketch Body"},
     "sketch_name": {**NAME_SCHEMA, "description": "Owned native Sketch"},
     "tolerance_mm": {**number("Positive Sketch geometry tolerance in millimetres"),
                      "exclusiveMinimum": 0}},
    ("document", "session_id", "body_name", "sketch_name", "tolerance_mm"),
    ("No GUI editor or repair action is used.",),
)

_SKETCH_ID = {"type": "string", "pattern": r"^[A-Za-z][A-Za-z0-9_]{0,49}$"}

_SKETCH_POINT = array("Sketch-plane point in millimetres", number("Finite coordinate"), 2, 2)

_SKETCH_CONSTRUCTION = boolean("Construction geometry; omitted means false")

_SKETCH_GEOMETRY = {"oneOf": [
    branch("kind", "line", {"id": _SKETCH_ID, "start": _SKETCH_POINT,
                              "end": _SKETCH_POINT, "construction": _SKETCH_CONSTRUCTION},
            ("id", "start", "end")),
    branch("kind", "circle", {"id": _SKETCH_ID, "center": _SKETCH_POINT,
                                "radius_mm": {**number("Positive radius"), "exclusiveMinimum": 0},
                                "construction": _SKETCH_CONSTRUCTION},
            ("id", "center", "radius_mm")),
    branch("kind", "arc", {"id": _SKETCH_ID, "center": _SKETCH_POINT,
                             "radius_mm": {**number("Positive radius"), "exclusiveMinimum": 0},
                              "start_deg": number("Start circle parameter in Sketch-plane degrees; preserve direction"),
                              "end_deg": number("End circle parameter in degrees; end_deg - start_deg must be greater than 0 and less than 360"),
                             "construction": _SKETCH_CONSTRUCTION},
            ("id", "center", "radius_mm", "start_deg", "end_deg")),
    branch("kind", "ellipse", {"id": _SKETCH_ID, "center": _SKETCH_POINT,
                                 "major_radius_mm": {**number("Positive major semiaxis"), "exclusiveMinimum": 0},
                                 "minor_radius_mm": {**number("Positive minor semiaxis"), "exclusiveMinimum": 0},
                                 "direction_deg": number("Major-axis angle in the Sketch plane"),
                                 "construction": _SKETCH_CONSTRUCTION},
            ("id", "center", "major_radius_mm", "minor_radius_mm", "direction_deg")),
    branch("kind", "ellipse_arc", {"id": _SKETCH_ID, "center": _SKETCH_POINT,
                                     "major_radius_mm": {**number("Positive major semiaxis"), "exclusiveMinimum": 0},
                                     "minor_radius_mm": {**number("Positive minor semiaxis"), "exclusiveMinimum": 0},
                                     "direction_deg": number("Major-axis angle in the Sketch plane"),
                                      "start_deg": number("Start ellipse carrier parameter in degrees, not its polar angle"),
                                      "end_deg": number("End ellipse parameter in degrees; end_deg - start_deg must be greater than 0 and less than 360"),
                                     "construction": _SKETCH_CONSTRUCTION},
            ("id", "center", "major_radius_mm", "minor_radius_mm", "direction_deg", "start_deg", "end_deg")),
]}

_SKETCH_POINT_REF = object_schema({"geometry_id": _SKETCH_ID,
                             "position": string("Native Sketch point", ("start", "end", "center"))},
                            ("geometry_id", "position"))

_SKETCH_CONSTRAINT_PROPERTIES = {
    "geometry_id": _SKETCH_ID, "first_geometry": _SKETCH_ID,
    "second_geometry": _SKETCH_ID, "first_point": _SKETCH_POINT_REF,
    "second_point": _SKETCH_POINT_REF, "point": _SKETCH_POINT_REF,
    "value": number("Distance in millimetres or angle in degrees"),
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
    _SKETCH_CONSTRAINT["oneOf"].append(branch("kind", _kind, _constraint_properties))

FEATURE_SPECS["Sketcher_EditSketch"] = operation(
    "sketch", "Populate an empty owned Sketch with geometry and constraints in one operation.",
    ("target",), "committed",
    {"document": _DOCUMENT, "session_id": TEXT_SCHEMA,
     "body_name": {**NAME_SCHEMA, "description": "Owned Sketch Body"},
     "sketch_name": {**NAME_SCHEMA, "description": "Existing empty owned Sketch"},
     "geometry": array("One-shot native Sketch geometry", _SKETCH_GEOMETRY, 1, 256),
     "constraints": array("Explicit native Sketch constraints", _SKETCH_CONSTRAINT, 0, 512),
     "intent": TEXT_SCHEMA, "evidence": DOMAIN_EVIDENCE_SCHEMA,
     "expected_revision": TARGET_REVISION_SCHEMA},
    ("document", "session_id", "body_name", "sketch_name", "geometry", "constraints",
     "intent", "evidence", "expected_revision"),
    ("Sketch is empty and has no downstream generated feature.",),
    timeout_class="long",
)

_POSITION_MM = array("Free Body-local position or attached support-local offset in millimetres", number("Finite coordinate"), 3, 3)

_ROTATION_AXIS = array("Nonzero Body-local or attached support-local rotation axis", number("Finite component"), 3, 3)

_ORIGIN_PLANE_REFERENCE = branch("kind", "origin_plane", {
    "role": string("Same-Body Origin plane role", ("XY_Plane", "XZ_Plane", "YZ_Plane"))})

_ORIGIN_AXIS_REFERENCE = branch("kind", "origin_axis", {
    "role": string("Same-Body Origin axis role", ("X_Axis", "Y_Axis", "Z_Axis"))})

_ORIGIN_POINT_REFERENCE = branch("kind", "origin_point", {})

def _datum_reference(kind):
    return branch("kind", kind, {
        "datum_name": {**NAME_SCHEMA, "description": "Same-Body native Datum name"}})

_VERTEX_REFERENCE = branch("kind", "feature_vertex", {
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
        "mode": string("Free placement or reference-relative attachment", ("free", "attached")),
        "position_mm": _POSITION_MM,
        "intent": TEXT_SCHEMA, "evidence": DOMAIN_EVIDENCE_SCHEMA,
        "expected_revision": TARGET_REVISION_SCHEMA,
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
                                 angle_deg=number("Rotation around the free Body-local or attached support-local axis in degrees"))
        _datum_required += ("axis", "angle_deg")
    FEATURE_SPECS[_datum_command] = operation(
        "datum", "Create one owned native %s Datum with free or bounded same-Body attachment." % _datum_kind,
        ("target",), "committed", _datum_properties, _datum_required,
        ("Current Target evidence belongs to this session; reconstruction additionally requires Source evidence.",
         "Free placement forbids reference; attached placement requires one explicit same-Body reference."),
        timeout_class="long",
    )
    _datum_schema = FEATURE_SPECS[_datum_command]["input_schema"]
    _datum_schema["oneOf"] = [
        conditional_form(_datum_schema, {"mode": "free"}, excluded=("reference",)),
        conditional_form(_datum_schema, {"mode": "attached"}, required=("reference",)),
    ]

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
        return integer("Number of regular prism sides", 3)
    spec = number("Finite native %s value in millimetres" % field)
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
            "expected_revision": TARGET_REVISION_SCHEMA,
        }
        for _field, _native, _rule in _family_spec["inputs"]:
            _primitive_properties[_field] = _primitive_number(_field, _rule)
        _position_field = "center_mm" if _family == "Sphere" else "position_mm"
        _primitive_properties[_position_field] = array(
            "Body-local %s in millimetres" % ("sphere center" if _family == "Sphere" else "position"),
            number("Finite coordinate"), 3, 3)
        if _family_spec["oriented"]:
            _primitive_properties["axis"] = array(
                "Nonzero Body-local rotation axis", number("Finite component"), 3, 3)
            _primitive_properties["angle_deg"] = number("Body-local rotation angle in degrees")
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
        FEATURE_SPECS[_primitive_name] = operation(
            "primitive", "Create one native %s %s in an explicitly owned Body." %
            (_operation_kind.lower(), _family.lower()),
            ("target",), "committed", _primitive_properties, _required,
            ("Current Target evidence and revision are required; reconstruction also requires Source evidence.",
             "Subtraction requires a prior valid single solid in the receiving Body."),
            ("Only the full, unslanted, unattached supported native form is supported.",),
            timeout_class="long",
        )

HISTORY_COMMANDS = {
    "PartDesign_Pad": {
        "domain": "extrude", "type_id": "PartDesign::Pad", "additive": True,
        "field_descriptions": {
            "reversed": "Pad grows along the positive Sketch normal for false, negative for true.",
        },
    },
    "PartDesign_Pocket": {
        "domain": "extrude", "type_id": "PartDesign::Pocket", "additive": False,
        "field_descriptions": {
            "reversed": (
                "Pocket cuts along the negative Sketch normal for false, positive for true. "
                "Choose the material side from the actual Sketch and predecessor."
            ),
        },
    },
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
    "expected_revision": TARGET_REVISION_SCHEMA,
}

_HISTORY_REQUIRED = ("document", "session_id", "body_name", "feature_name",
                     "intent", "evidence", "expected_revision")

_SKETCH_PROFILE = {**NAME_SCHEMA, "description": "Existing Sketch in the receiving Body with valid closed non-construction geometry"}

_POSITIVE_LENGTH = {**number("Positive native length in millimetres"), "exclusiveMinimum": 0}

_REVOLUTION_AXIS = {"oneOf": [
    object_schema({"kind": string("Sketch horizontal or vertical axis", ("sketch_axis",)),
             "axis": string("Native Sketch axis", ("horizontal", "vertical"))},
            ("kind", "axis")),
    object_schema({"kind": string("Same-Body Datum Line", ("datum_line",)),
             "datum_name": NAME_SCHEMA}, ("kind", "datum_name")),
    object_schema({"kind": string("Same-Body Coordinate System semantic axis", ("coordinate_system_axis",)),
             "coordinate_system_name": NAME_SCHEMA,
             "role": string("Native Coordinate System axis role", ("X_Axis", "Y_Axis", "Z_Axis"))},
            ("kind", "coordinate_system_name", "role")),
]}

_TRANSFORM_SOURCES = array(
    "One to 16 caller-ordered native additive/subtractive features from the current Body history",
    NAME_SCHEMA, 1, 16)

_PATTERN_AXIS = {"oneOf": [
    object_schema({"kind": string("Same-Body Origin axis", ("origin_axis",)),
             "role": string("Origin axis role", ("X_Axis", "Y_Axis", "Z_Axis"))},
            ("kind", "role")),
    object_schema({"kind": string("Current predecessor straight edge", ("predecessor_edge",)),
             "edge_ref": edge_ref_schema("pattern_axis")},
            ("kind", "edge_ref")),
    object_schema({"kind": string("Same-Body Datum Line", ("datum_line",)),
             "datum_name": NAME_SCHEMA}, ("kind", "datum_name")),
    object_schema({"kind": string("Same-Body Coordinate System semantic child axis", ("coordinate_system_axis",)),
             "coordinate_system_name": NAME_SCHEMA,
             "role": string("Coordinate System axis role", ("X_Axis", "Y_Axis", "Z_Axis"))},
            ("kind", "coordinate_system_name", "role")),
]}

for _command, _spec in HISTORY_COMMANDS.items():
    _properties = dict(_HISTORY_COMMON)
    _required = _HISTORY_REQUIRED
    if _spec["domain"] in ("extrude", "revolve", "hole"):
        _properties["sketch_name"] = _SKETCH_PROFILE
        _required += ("sketch_name",)
    if _spec["domain"] == "extrude":
        _properties.update(
            length_mm={
                **_POSITIVE_LENGTH,
                "description": (
                    "Full generating length from the Sketch plane in millimetres, "
                    "including portions later trimmed by dress-ups or cuts."
                ),
            },
            reversed=boolean("Explicit native direction reversal"),
        )
        _required += ("length_mm", "reversed")
    elif _spec["domain"] == "revolve":
        _properties.update(axis=_REVOLUTION_AXIS,
                           angle_deg={**number("Native rotation angle in degrees"),
                                      "exclusiveMinimum": 0, "maximum": 360},
                           reversed=boolean("Explicit native direction reversal"))
        _required += ("axis", "angle_deg", "reversed")
    elif _spec["domain"] == "hole":
        _properties.update(
            diameter_mm=_POSITIVE_LENGTH,
            depth_mode=string("Finite depth or through all", ("dimension", "through_all")),
            depth_mm={**_POSITIVE_LENGTH, "description": "Finite depth in millimetres from the Sketch plane. Angled mode includes the conical tip (native DrillForDepth=true); measure to the tip, not just the cylindrical wall."},
            drill_point=string("Finite-depth bottom", ("flat", "angled")),
            drill_point_angle_deg={**number("Included angled drill-tip angle in degrees"),
                                   "exclusiveMinimum": 0, "exclusiveMaximum": 180},
            mouth_mode=string("Hole entrance", ("none", "counterbore", "countersink")),
            mouth_diameter_mm={**_POSITIVE_LENGTH, "description": "Entrance diameter in millimetres; must exceed diameter_mm."},
            mouth_depth_mm={**_POSITIVE_LENGTH, "description": "Counterbore entrance depth from the Sketch plane in millimetres; forbidden for countersink/none."},
            mouth_angle_deg={**number("Included countersink angle in degrees"),
                            "exclusiveMinimum": 0, "exclusiveMaximum": 180},
            reversed=boolean("Hole removal direction: false follows the negative Sketch normal, true the positive normal. Establish material side in the actual Sketch frame."),
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
            edge_refs=array("One to 64 explicit current Target predecessor edges",
                             edge_ref_schema("dressup_edge"), 1, DRESSUP_SCOPE["edge_limit"]),
        )
        _required += ("predecessor_name", "edge_refs")
        if _spec["domain"] == "fillet":
            _properties["radius_mm"] = _POSITIVE_LENGTH
            _required += ("radius_mm",)
        else:
            _properties.update(
                mode=string("Native Chamfer mode", ("equal_distance", "two_distances")),
                size_mm=_POSITIVE_LENGTH,
                size2_mm=_POSITIVE_LENGTH,
                flip_direction=boolean("Explicit two-distance direction flip"),
            )
            _required += ("mode", "size_mm")
    elif _spec["domain"] in ("draft", "thickness"):
        role = "draft_target" if _spec["domain"] == "draft" else "thickness_opening"
        _properties.update(
            predecessor_name={**NAME_SCHEMA, "description": "Current solid Tip of the owned Body"},
            face_refs=array("One to 64 explicit current Target predecessor faces",
                             face_ref_schema(role), 1, 64),
        )
        _required += ("predecessor_name", "face_refs")
        if _spec["domain"] == "draft":
            _properties.update(
                neutral_plane={"oneOf": [
                    object_schema({"kind": string("Current predecessor planar face", ("predecessor_face",)),
                             "face_ref": face_ref_schema("draft_neutral")},
                            ("kind", "face_ref")),
                    object_schema({"kind": string("Same-Body native origin plane", ("origin_plane",)),
                             "role": string("Origin plane role", ("XY_Plane", "XZ_Plane", "YZ_Plane"))},
                            ("kind", "role")),
                ]},
                angle_deg={**number("Positive native draft angle in degrees"),
                           "exclusiveMinimum": 0, "exclusiveMaximum": 90},
                reversed=boolean("Explicit native draft direction reversal"),
            )
            _required += ("neutral_plane", "angle_deg", "reversed")
        else:
            _properties.update(
                thickness_mm=_POSITIVE_LENGTH,
                direction=string("Thickness material side", ("inward", "outward")),
                join=string("Native corner join", ("arc", "intersection")),
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
                object_schema({"kind": string("Same-Body native Origin plane", ("origin_plane",)),
                         "role": string("Origin plane role", ("XY_Plane", "XZ_Plane", "YZ_Plane"))},
                        ("kind", "role")),
                object_schema({"kind": string("Current predecessor planar face", ("predecessor_face",)),
                         "face_ref": face_ref_schema("mirror_plane")},
                        ("kind", "face_ref")),
                _mirror_datum_plane,
            ]}
            _required += ("mirror_plane",)
        else:
            _properties.update(
                axis=_PATTERN_AXIS,
                reversed=boolean("Explicit native pattern direction reversal"),
                extent_mode=string("Preserve overall extent or per-step offset semantics",
                                    ("overall", "offset")),
                occurrences=integer("Occurrences including the original", 2, 64),
            )
            _required += ("axis", "reversed", "extent_mode", "occurrences")
            if _command == "PartDesign_LinearPattern":
                _properties["distance_mm"] = _POSITIVE_LENGTH
                _required += ("distance_mm",)
            else:
                _properties["angle_deg"] = {
                    **number("Positive native overall or per-step angle in degrees; offset × (occurrences - 1) must be less than 360"),
                    "exclusiveMinimum": 0, "maximum": 360,
                }
                _required += ("angle_deg",)
    else:
        _properties.update(
            tool_body_names=array("Ordered registered tool Bodies; exactly one for Common, 1-16 for Cut/Fuse", NAME_SCHEMA, 1, 16),
            mode=string("Native Boolean operation", ("Fuse", "Cut", "Common")),
        )
        _required += ("tool_body_names", "mode")
    for _field, _description in _spec.get("field_descriptions", {}).items():
        _properties[_field] = {**_properties[_field], "description": _description}
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
    FEATURE_SPECS[_command] = operation(
        _spec["domain"], "Create one native %s feature in an explicitly owned Body." % _command,
        ("target",), "committed", _properties, _required,
        ("Current Target evidence and revision are required; reconstruction also requires Source evidence.",
         "Native references must belong to this Target session."),
        _limits, timeout_class="long",
    )

FEATURE_SPECS["PartDesign_Boolean"]["input_schema"]["oneOf"] = [
    {"type": "object", "properties": {
        "mode": {"type": "string", "enum": ["Fuse", "Cut"]},
        "tool_body_names": {"type": "array", "minItems": 1, "maxItems": 16},
    }, "required": ["mode", "tool_body_names"]},
    {"type": "object", "properties": {
        "mode": {"type": "string", "enum": ["Common"]},
        "tool_body_names": {"type": "array", "minItems": 1, "maxItems": 1},
    }, "required": ["mode", "tool_body_names"]},
]

_hole_schema = FEATURE_SPECS["PartDesign_Hole"]["input_schema"]

_hole_schema["oneOf"] = [
    conditional_form(_hole_schema, {**depth_choices, "mouth_mode": mouth},
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

_chamfer_schema = FEATURE_SPECS["PartDesign_Chamfer"]["input_schema"]

_chamfer_schema["oneOf"] = [
    conditional_form(_chamfer_schema, {"mode": "equal_distance"},
                      excluded=("size2_mm", "flip_direction")),
    conditional_form(_chamfer_schema, {"mode": "two_distances"},
                      required=("size2_mm", "flip_direction")),
]


DATUM_TYPES = {
    "Part_DatumPlane": "Part::DatumPlane", "Part_DatumLine": "Part::DatumLine",
    "Part_DatumPoint": "Part::DatumPoint", "Part_CoordinateSystem": "Part::LocalCoordinateSystem",
}

_INDEX = integer("Zero-based native Sketch index from the current observation", 0)
# Existing indexes are zero-based; new IDs belong only to this edit.
_SKETCH_ADDRESS = {"oneOf": [
    branch("kind", "existing", {"index": _INDEX}),
    branch("kind", "new", {"id": _SKETCH_ID}),
]}


def _sketch_edit_constraint(branch):
    properties = dict(branch["properties"])
    for key in ("geometry_id", "first_geometry", "second_geometry"):
        if key in properties:
            properties[key] = _SKETCH_ADDRESS
    for key in ("first_point", "second_point", "point"):
        if key in properties:
            properties[key] = object_schema({"geometry": _SKETCH_ADDRESS,
                                       "position": _SKETCH_POINT_REF["properties"]["position"]},
                                      ("geometry", "position"))
    return {**branch, "properties": properties}


def _compact_sketch_form(value):
    if isinstance(value, list):
        return [_compact_sketch_form(item) for item in value]
    if isinstance(value, dict):
        return {key: _compact_sketch_form(item) for key, item in value.items() if key != "description"}
    return value


def _sketch_constraint_forms():
    forms = {}
    for source in _SKETCH_CONSTRAINT["oneOf"]:
        branch = _compact_sketch_form(_sketch_edit_constraint(source))
        fields = branch["properties"]
        kinds = fields["kind"].pop("enum")
        key = repr(branch)
        if key not in forms:
            forms[key] = branch
        forms[key]["properties"]["kind"].setdefault("enum", []).extend(kinds)
    return list(forms.values())


_SKETCH_OPERATIONS = _compact_sketch_form({"oneOf": [
    branch("op", "add_geometry", {"geometry": _SKETCH_GEOMETRY}),
    branch("op", "delete_geometry", {"index": _INDEX}),
    branch("op", "move_point", {"index": _INDEX,
                                 "position": _SKETCH_POINT_REF["properties"]["position"],
                                 "point_mm": _SKETCH_POINT}),
    branch("op", "set_radius", {"index": _INDEX, "radius_mm": _POSITIVE_LENGTH}),
    branch("op", "set_length", {"index": _INDEX, "length_mm": _POSITIVE_LENGTH}),
    branch("op", "construction", {"index": _INDEX, "enabled": boolean("Construction geometry")}),
    branch("op", "add_constraint", {"constraint": {"oneOf": _sketch_constraint_forms()}}),
    branch("op", "delete_constraint", {"index": _INDEX}),
    branch("op", "set_constraint_value", {"index": _INDEX,
        "value": number("Driving dimensional value in millimetres or degrees")}),
    branch("op", "constraint_active", {"index": _INDEX, "enabled": boolean("Constraint enabled")}),
    branch("op", "constraint_driving", {"index": _INDEX, "enabled": boolean("Driving constraint")}),
]})

EDIT_FEATURE_TYPES = {
    "PartDesign::Body": "PartDesign_Body",
    "Sketcher::SketchObject": "Sketcher_EditSketch",
    **{type_id: name for name, type_id in DATUM_TYPES.items()},
    **{spec["type_id"]: name for name, spec in PRIMITIVE_COMMANDS.items()},
    **{spec["type_id"]: name for name, spec in HISTORY_COMMANDS.items()},
}

_EDIT_PLACEMENT = {"position_mm": _POSITION_MM, "axis": _ROTATION_AXIS,
                   "angle_deg": number("Rotation angle in degrees")}
_EDIT_COMMON = {
    "document": _DOCUMENT, "session_id": TEXT_SCHEMA, "body_name": NAME_SCHEMA,
    "object_name": {**NAME_SCHEMA, "description": "Existing native Target object"},
    "intent": TEXT_SCHEMA, "evidence": DOMAIN_EVIDENCE_SCHEMA,
    "expected_revision": TARGET_REVISION_SCHEMA,
}


def _edit_fields(type_id, command):
    if type_id == "PartDesign::Body":
        return {"label": {"type": "string", "minLength": 1, "maxLength": 120}, **_EDIT_PLACEMENT}
    if type_id == "Sketcher::SketchObject":
        return {**_EDIT_PLACEMENT, "attachment": {"oneOf": [
            branch("mode", "free", {}),
            branch("mode", "attached", {"reference":
                FEATURE_SPECS["Sketcher_MapSketch"]["input_schema"]["properties"]["reference"]}),
        ]}, "operations": array(
            "Use observed zero-based indexes or new geometry IDs in this edit. Set radius on an unconstrained circle or arc, or length on an unconstrained line. Use mm or degrees for driving dimensions.",
                                  _SKETCH_OPERATIONS, 1, 64)}
    hidden = set(_EDIT_COMMON) | {"feature_name", "datum_name", "predecessor_name"}
    fields = {key: value for key, value in FEATURE_SPECS[command]["input_schema"]["properties"].items()
              if key not in hidden}
    return fields


FEATURE_SPECS["EditFeature"] = operation(
    "edit", "Edit an observed existing Target object in place using its native type contract.",
    ("target",), "committed", _EDIT_COMMON, tuple(_EDIT_COMMON),
    ("Keep native identity and Body Tip. Re-observe the object and any replacement subelements at the current revision.",),
    timeout_class="long",
)
_edit_schema = FEATURE_SPECS["EditFeature"]["input_schema"]
_edit_schema["properties"].update(type_id=string("Observed native TypeId", tuple(EDIT_FEATURE_TYPES)),
                                  changes={"type": "object"})
_edit_schema["required"] += ["type_id", "changes"]
_edit_schema["oneOf"] = [
    object_schema({**_EDIT_COMMON, "type_id": string("Observed native TypeId", (type_id,)),
             "changes": object_schema(_edit_fields(type_id, command))},
            _edit_schema["required"])
    for type_id, command in EDIT_FEATURE_TYPES.items()
]


def validate_feature_relations(command, request, *, path=None):
    """Cross-field relations after schema validation; never reads CAD state."""
    path = path or command
    if command == "EditFeature" and not request["changes"]:
        raise RequestValidationError([{"path": path + ".changes", "message": "Specify at least one change"}])
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
