"""Prepare a typed edit from the existing feature's native parameters and links."""

import math

import FreeCAD as App

from REAgent.protocol.features import DATUM_TYPES, EDIT_FEATURE_TYPES, HISTORY_COMMANDS, PRIMITIVE_COMMANDS
from REAgent.host.analysis.evidence import _geometry_evidence
from REAgent.host.validation.structure import _single_solid_summary
from REAgent.host.validation.tolerance import _ir_tolerance_contract
from REAgent.host.workspace.model import _body_objects, _depends_on, _validate_boolean_tools, _world_shape
from REAgent.host.workspace.properties import _encode, _metadata, _native_link, _native_number, _same_placement
from .boolean import _preserve_tool_placement
from .chamfer import _MODES as CHAMFER_MODES
from .datum import _resolve_reference
from .draft import _neutral_plane
from .hole import _HOLE_ENUM, _circle_profile
from .pattern import _AXIS_PARENT_PROPERTY, _native_extent_mode, _one_direction
from .pipe import _PIPE_FIXED_ENUMS
from .primitives import _validate_family
from .revolve import _reference_axis
from .thickness import _JOIN
from .support.construction import _closed_profile, _distinct_section_planes, _section_or_path, _whole_sketch_link
from .support.references import _current_faces, _dressup_edges, _role_object
from .support.transforms import _transform_axis, _transform_plane, _transform_sources


_SCALARS = {
    "extrude": {"length_mm": "Length", "reversed": "Reversed"},
    "revolve": {"angle_deg": "Angle", "reversed": "Reversed"},
    "hole": {"diameter_mm": "Diameter", "reversed": "Reversed"},
    "fillet": {"radius_mm": "Radius"},
    "chamfer": {"size_mm": "Size"},
    "draft": {"angle_deg": "Angle", "reversed": "Reversed"},
    "thickness": {"thickness_mm": "Value"},
    "pattern": {"occurrences": "Occurrences", "reversed": "Reversed"},
}

_FAMILY_PROPERTIES = {
    "extrude": ("Profile", "Type", "SideType", "UseCustomVector", "TaperAngle"),
    "revolve": ("Profile", "Type", "ReferenceAxis", "Midplane"),
    "hole": ("Profile", "DepthType", "Depth", "DrillPoint", "DrillPointAngle", "DrillForDepth",
             "HoleCutType", "HoleCutCustomValues", "HoleCutDiameter", "HoleCutDepth", "HoleCutCountersinkAngle",
             "BaseProfileType", "ThreadType", "Tapered"),
    "fillet": ("Base", "UseAllEdges"),
    "chamfer": ("Base", "ChamferType", "Size2", "FlipDirection", "UseAllEdges"),
    "draft": ("Base", "NeutralPlane", "PullDirection"),
    "thickness": ("Base", "Mode", "Join", "Reversed", "Intersection"),
    "loft": ("Profile", "Sections", "Closed", "Ruled"),
    "pipe": ("Profile", "Spine", "Sections", "AuxiliarySpine", *_PIPE_FIXED_ENUMS),
    "boolean": ("Type", "Group"),
    "mirror": ("TransformMode", "Originals", "MirrorPlane"),
    "pattern": ("TransformMode", "Originals", "Direction", "Axis", _AXIS_PARENT_PROPERTY,
                "Mode", "Length", "Angle", "Offset"),
}


def observable_properties(obj):
    command = EDIT_FEATURE_TYPES[obj.TypeId]
    if command in HISTORY_COMMANDS:
        family = HISTORY_COMMANDS[command]["domain"]
        names = (*_SCALARS.get(family, {}).values(), *_FAMILY_PROPERTIES[family], "Refine", "BaseFeature")
    elif command in PRIMITIVE_COMMANDS:
        spec = PRIMITIVE_COMMANDS[command]
        names = ("Placement", "MapMode", *(prop for _, prop, _ in spec["inputs"]),
                 *(prop for prop, _ in spec["fixed"]))
    elif command == "PartDesign_Body":
        names = ("Label", "Placement")
    else:
        names = ("Placement", "AttachmentOffset", "AttachmentSupport", "MapMode")
    return tuple(prop for prop in names if prop in obj.PropertiesList)


def _same(actual, desired, codec):
    if codec == "placement":
        return _same_placement(actual, desired)
    if codec in ("number", "quantity"):
        a, b = _native_number(actual), _native_number(desired)
        return math.isfinite(a) and abs(a-b) <= max(1e-9, abs(b)*1e-12)
    if codec in ("link_sub", "link_sub_list"):
        def normalized(value):
            rows = [value] if codec == "link_sub" else value
            return [(obj.Name, tuple(parts) if any(parts) else ()) for obj, parts in rows if obj is not None]
        if codec == "link_sub" and (not actual or not desired):
            return _encode(actual, codec) == _encode(desired, codec)
        return normalized(actual) == normalized(desired)
    return _encode(actual, codec) == _encode(desired, codec)


class ParameterEdit:
    axis_parent_property = _AXIS_PARENT_PROPERTY

    def __init__(self, target):
        self.target = target
        self.writes = {}
        self.expected = {}
        self.new_links = []
        self.axis_parent = None
        self.install_axis_parent = False
        self.tool_placements = {}

    def add(self, prop, desired, *codecs, when_mode_changes=False):
        obj = self.target
        meta = _metadata(obj, prop, body_edit=obj.TypeId == "PartDesign::Body")
        codec = meta["codec"]
        if codec not in codecs or "value" not in meta:
            raise ValueError("Native edit cannot encode " + obj.Name + "." + prop)
        if "enum" in meta and desired not in meta["enum"]:
            raise ValueError("Unsupported native value for " + prop)
        self.expected[prop] = (codec, desired)
        if _same(getattr(obj, prop), desired, codec) and not when_mode_changes:
            return
        if not meta["writable"] and not (when_mode_changes and meta["unavailable_reason"] == "native_read_only"):
            raise ValueError("Cannot change " + obj.Name + "." + prop + " (" + str(meta["unavailable_reason"]) + ")")
        self.writes[prop] = (codec, desired, meta["value"])

    def link(self, prop, refs, *, empty=()):
        codec = _metadata(self.target, prop)["codec"]
        if codec == "link":
            if any(parts and parts != [""] for _, parts in refs):
                raise ValueError(prop + " does not accept subelements")
            desired = refs[0][0] if len(refs) == 1 else None
        elif codec == "link_sub":
            desired = refs[0] if len(refs) == 1 else None
        elif codec == "link_list":
            desired = [obj for obj, _ in refs]
        elif codec == "link_sub_list":
            desired = refs
        else:
            raise ValueError("Unsupported native reference property " + prop)
        if (codec in ("link", "link_sub") and len(refs) > 1
                or not refs and codec not in empty):
            raise ValueError("Invalid link count for " + prop)
        self.add(prop, desired, codec)
        self.new_links.extend(obj for obj, _ in refs)

    def write(self, changes):
        obj = self.target
        if self.install_axis_parent:
            obj.addProperty("App::PropertyLink", _AXIS_PARENT_PROPERTY, "REAgent",
                            "Parent Coordinate System dependency")
            setattr(obj, _AXIS_PARENT_PROPERTY, self.axis_parent)
        for prop, (codec, desired, old) in self.writes.items():
            if _same(getattr(obj, prop), desired, codec):
                continue
            if not _metadata(obj, prop, body_edit=obj.TypeId == "PartDesign::Body")["writable"]:
                raise ValueError("Native property is still read-only in requested mode: " + prop)
            changes.append((obj.Name, prop, codec, old))
            if prop == "Group" and obj.TypeId == "PartDesign::Boolean":
                obj.setObjects(desired)
                for tool, placement, original in self.tool_placements.values():
                    _preserve_tool_placement(tool, placement, original, changes)
            else:
                setattr(obj, prop, desired)

    def readback(self):
        obj = self.target
        actual_changes = {}
        for prop, (codec, desired) in self.expected.items():
            actual = getattr(obj, prop)
            if not _same(actual, desired, codec):
                raise RuntimeError("Native edit did not read back " + obj.Name + "." + prop)
            if prop in self.writes:
                original = self.writes[prop][2]
                read = _encode(actual, codec)
                if original != read:
                    actual_changes[prop] = {"before": original, "after": read}
        if self.install_axis_parent:
            if getattr(obj, _AXIS_PARENT_PROPERTY) is not self.axis_parent:
                raise RuntimeError("Native pattern axis parent did not read back")
            actual_changes[_AXIS_PARENT_PROPERTY] = {"before": None, "after": {"object": self.axis_parent.Name}}
        for tool, placement, _original in self.tool_placements.values():
            if not _same_placement(tool.getGlobalPlacement(), placement):
                raise RuntimeError("Boolean operand world Placement changed after recompute")
        if any(ref not in obj.OutList for ref in self.new_links):
            raise RuntimeError("Native edit did not retain an intended dependency")
        return actual_changes


def _fixed(obj, values):
    for prop, expected in values.items():
        if prop in obj.PropertiesList:
            actual = getattr(obj, prop)
            if isinstance(expected, (int, float)) and not isinstance(expected, bool):
                actual = _native_number(actual)
            if actual != expected:
                raise ValueError("Unsupported native mode: " + obj.Name + "." + prop)


def _placement(plan, changes, prop, *, position_field="position_mm", base=None):
    if not set(changes) & {position_field, "axis", "angle_deg"} and base is None:
        return
    placement = base if base is not None else getattr(plan.target, prop)
    position = App.Vector(*changes[position_field]) if position_field in changes else placement.Base
    rotation = placement.Rotation
    if "axis" in changes or "angle_deg" in changes:
        axis = App.Vector(*changes["axis"]) if "axis" in changes else rotation.Axis
        angle = changes.get("angle_deg", math.degrees(rotation.Angle))
        if not math.isfinite(axis.Length) or axis.Length <= 1e-12:
            raise ValueError("Rotation axis must be finite and nonzero")
        rotation = App.Rotation(axis, angle)
    plan.add(prop, App.Placement(position, rotation), "placement", when_mode_changes=base is not None)


def _sketch_support(document, body, ref):
    origin = body.Origin
    if ref["kind"] == "origin_plane":
        if origin is None:
            raise ValueError("Body Origin is unavailable")
        return _role_object(origin, ref["role"], "App::Plane", document), [""], "FlatFace"
    support = document.getObject(ref["datum_name"])
    is_plane = ref["kind"] == "datum_plane"
    type_id = "Part::DatumPlane" if is_plane else "Part::LocalCoordinateSystem"
    if support is None or support not in body.Group or support.TypeId != type_id:
        raise ValueError("Sketch attachment requires a native plane in the same Body")
    if is_plane:
        return support, [""], "FlatFace"
    _role_object(support, ref["role"], "App::Plane", document)
    return support, [""], {"XY_Plane": "ObjectXY", "XZ_Plane": "ObjectXZ",
                            "YZ_Plane": "ObjectYZ"}[ref["role"]]


def _edit_attachment(plan, document, body, command, changes):
    obj = plan.target
    if command == "Sketcher_EditSketch":
        request = changes.get("attachment", {})
    else:
        request = changes
    supported = {
        "Sketcher_EditSketch": ("FlatFace", "ObjectXY", "ObjectXZ", "ObjectYZ"),
        "Part_DatumPlane": ("FlatFace",), "Part_DatumLine": ("ObjectX",),
        "Part_DatumPoint": ("ObjectOrigin", "Vertex"), "Part_CoordinateSystem": ("ObjectXY",),
    }
    if obj.MapMode != "Deactivated" and obj.MapMode not in supported[command]:
        raise ValueError("Existing native attachment mode is outside the supported form")
    before_mode = "free" if obj.MapMode == "Deactivated" else "attached"
    mode = request.get("mode", before_mode)
    ref = request.get("reference")
    if ref is not None and mode != "attached":
        raise ValueError("Reference requires attached mode")
    if mode == "attached" and ref is None and before_mode == "free":
        raise ValueError("Switching to attached requires an explicit reference")
    if mode == "free" and before_mode != mode:
        plan.add("MapMode", "Deactivated", "string")
        plan.link("AttachmentSupport", [], empty=("link_sub", "link_sub_list"))
    elif ref is not None:
        resolved = (_sketch_support(document, body, ref) if command == "Sketcher_EditSketch"
                    else _resolve_reference(document, body, ref, command))
        support, parts, map_mode = resolved
        plan.link("AttachmentSupport", [(support, parts)])
        plan.add("MapMode", map_mode, "string")
    elif mode == "attached" and not obj.AttachmentSupport:
        raise ValueError("Native attachment has no explicit support")
    if (command == "Sketcher_EditSketch" and (mode != before_mode or ref is not None)
            and (obj.ExternalGeometry or len(getattr(obj, "ExternalGeo", ())) > 2)):
        raise ValueError("Cannot switch attachment mode with existing external geometry")
    # Detaching preserves the current local placement. Attaching from free starts
    # with a zero support-local offset unless the user explicitly supplies one.
    switched_base = (obj.Placement if mode == "free" else App.Placement()) if mode != before_mode else None
    _placement(plan, changes, "AttachmentOffset" if mode == "attached" else "Placement", base=switched_base)


def _enum_key(mapping, native):
    for key, value in mapping.items():
        if value == native:
            return key
    raise ValueError("Unsupported native mode " + str(native))


def _mode_values(changes, previous, mode_key, prop, names):
    """Read current active fields, requiring explicit values when activating new ones."""
    mode = changes.get(mode_key, previous)
    allowed = set(names[mode])
    all_fields = {key for group in names.values() for key in group}
    if (set(changes) & all_fields) - allowed:
        raise ValueError("Parameters do not apply to " + mode_key + "=" + mode)
    values = {}
    for field in names[mode]:
        if field in changes:
            values[field] = changes[field]
        elif field in names[previous]:
            values[field] = prop(field)
        else:
            raise ValueError("Changing " + mode_key + " requires " + field)
    return mode, values


def _hole_parameters(plan, changes):
    obj = plan.target
    _fixed(obj, {"ThreadType": "None", "BaseProfileType": 2, "Tapered": False, "Refine": False})
    depth_before = _enum_key(_HOLE_ENUM["DepthType"], obj.DepthType)
    depth = changes.get("depth_mode", depth_before)
    point_before = _enum_key(_HOLE_ENUM["DrillPoint"], obj.DrillPoint) if depth_before == "dimension" else None
    if depth == "dimension":
        point = changes.get("drill_point", point_before)
        if point is None:
            raise ValueError("Finite depth requires drill_point")
        if depth_before != depth and "depth_mm" not in changes:
            raise ValueError("Switching to finite depth requires depth_mm")
        if point == "angled":
            if point_before != point and "drill_point_angle_deg" not in changes:
                raise ValueError("Switching to angled drill tip requires drill_point_angle_deg")
        elif "drill_point_angle_deg" in changes:
            raise ValueError("Flat drill point does not accept an angle")
    elif set(changes) & {"depth_mm", "drill_point", "drill_point_angle_deg"}:
        raise ValueError("Through-all Hole has no finite depth or drill tip")
    else:
        point = None
    if point_before == "angled" and point != "angled" and "DrillForDepth" in obj.PropertiesList:
        plan.add("DrillForDepth", False, "bool")
    if depth_before != depth:
        plan.add("DepthType", _HOLE_ENUM["DepthType"][depth], "string")
    if point is not None:
        if "depth_mm" in changes:
            plan.add("Depth", changes["depth_mm"], "quantity", "number", when_mode_changes=depth != depth_before)
        if point != point_before:
            plan.add("DrillPoint", _HOLE_ENUM["DrillPoint"][point], "string", when_mode_changes=depth != depth_before)
        if "drill_point_angle_deg" in changes:
            plan.add("DrillPointAngle", changes["drill_point_angle_deg"], "quantity", "number",
                     when_mode_changes=point != point_before)
    drill_for_depth = point == "angled"
    if drill_for_depth or "DrillForDepth" in obj.PropertiesList:
        if depth == depth_before and point == point_before:
            _fixed(obj, {"DrillForDepth": drill_for_depth})
        elif "DrillForDepth" not in plan.expected:
            plan.add("DrillForDepth", drill_for_depth, "bool", when_mode_changes=True)
    native = {"mouth_diameter_mm": "HoleCutDiameter", "mouth_depth_mm": "HoleCutDepth",
              "mouth_angle_deg": "HoleCutCountersinkAngle"}
    mouth_before = _enum_key(_HOLE_ENUM["HoleCutType"], obj.HoleCutType)
    mouth, values = _mode_values(changes, mouth_before, "mouth_mode", lambda field: _native_number(getattr(obj, native[field])),
        {"none": (), "counterbore": ("mouth_diameter_mm", "mouth_depth_mm"),
         "countersink": ("mouth_diameter_mm", "mouth_angle_deg")})
    if mouth != "none" and values["mouth_diameter_mm"] <= changes.get("diameter_mm", _native_number(obj.Diameter)):
        raise ValueError("Hole entrance diameter must exceed the bore diameter")
    if mouth != mouth_before:
        if mouth == "none":
            plan.add("HoleCutCustomValues", False, "bool")
        plan.add("HoleCutType", _HOLE_ENUM["HoleCutType"][mouth], "string")
        if mouth != "none":
            plan.add("HoleCutCustomValues", True, "bool", when_mode_changes=True)
    else:
        _fixed(obj, {"HoleCutCustomValues": mouth != "none"})
    for field, value in values.items():
        if field in changes or mouth != mouth_before:
            plan.add(native[field], value, "number", "quantity", when_mode_changes=mouth != mouth_before)


def _chamfer_parameters(plan, changes):
    obj = plan.target
    before = _enum_key(CHAMFER_MODES, obj.ChamferType)
    native = {"size2_mm": "Size2", "flip_direction": "FlipDirection"}
    mode, values = _mode_values(changes, before, "mode", lambda field: getattr(obj, native[field]),
                               {"equal_distance": (), "two_distances": tuple(native)})
    if mode != before:
        if mode == "equal_distance" and bool(obj.FlipDirection):
            plan.add("FlipDirection", False, "bool")
        plan.add("ChamferType", CHAMFER_MODES[mode], "string")
    if "size2_mm" in changes:
        plan.add("Size2", values["size2_mm"], "quantity", "number", when_mode_changes=mode != before)
    if "flip_direction" in changes:
        plan.add("FlipDirection", values["flip_direction"], "bool", when_mode_changes=mode != before)


def _pattern_parameters(plan, changes, linear):
    obj = plan.target
    _one_direction(obj)
    modes = {key: _native_extent_mode(obj, key) for key in ("overall", "offset")}
    before = _enum_key(modes, obj.Mode)
    mode = changes.get("extent_mode", before)
    field = "distance_mm" if linear else "angle_deg"
    prop = ("Length" if linear else "Angle") if mode == "overall" else "Offset"
    if mode != before:
        if field not in changes:
            raise ValueError("Changing the pattern extent mode requires " + field)
        plan.add("Mode", modes[mode], "string")
    value = changes[field] if field in changes else _native_number(getattr(obj, prop))
    count = changes.get("occurrences", int(obj.Occurrences))
    if not 2 <= count <= 64 or value <= 0 or (not linear and (
            value > 360 or mode == "offset" and value * (count-1) >= 360)):
        raise ValueError("Pattern extent and occurrence count are incompatible")
    if field in changes:
        plan.add(prop, value, "number", "quantity", when_mode_changes=mode != before)


def _history_parameters(plan, document, group, body, prior, command, changes):
    obj = plan.target
    family = HISTORY_COMMANDS[command]["domain"]
    _fixed(obj, {"Refine": False, "Suppressed": False})
    for field, prop in _SCALARS.get(family, {}).items():
        if field in changes:
            value = changes[field]
            codecs = ("bool",) if type(value) is bool else ("integer",) if prop == "Occurrences" else ("quantity", "number")
            plan.add(prop, value, *codecs)
    if family in ("extrude", "revolve", "hole"):
        existing = _native_link(obj.Profile)
        if existing is None or not _whole_sketch_link(obj.Profile, existing):
            raise ValueError("Native profile is not a whole Sketch")
        profile = _closed_profile(document, body, changes.get("sketch_name", existing.Name))
        if "sketch_name" in changes:
            plan.link("Profile", [(profile, [])])
        if family == "extrude":
            _fixed(obj, {"Type": "Length", "SideType": "One side", "UseCustomVector": False, "TaperAngle": 0})
        elif family == "revolve":
            _fixed(obj, {"Type": "Angle", "Midplane": False})
            if "axis" in changes:
                axis, element = _reference_axis(document, body, profile, changes["axis"])
                plan.link("ReferenceAxis", [(axis, [element])])
            elif _native_link(obj.ReferenceAxis) is existing and profile is not existing:
                plan.link("ReferenceAxis", [(profile, list(obj.ReferenceAxis[1]))])
        else:
            _circle_profile(profile)
            _hole_parameters(plan, changes)
    elif family in ("loft", "pipe"):
        _section_parameters(plan, document, body, family, changes)
    elif family == "boolean":
        _boolean_parameters(plan, document, group, changes)
    else:
        if prior is None:
            raise ValueError("Native feature has no confirmed solid predecessor")
        _predecessor_parameters(plan, document, group, body, prior, family, command, changes)


def _section_parameters(plan, document, body, family, changes):
    obj = plan.target
    loft = family == "loft"
    if loft and len(obj.Sections) != 1:
        raise ValueError("Only two-section Loft is supported")
    _fixed(obj, {"Closed": False, "Ruled": False} if loft else
           {**_PIPE_FIXED_ENUMS, "Sections": [], "AuxiliarySpine": None,
            "SpineTangent": False, "AuxiliarySpineTangent": False})
    prop = "Sections" if loft else "Spine"
    field = "section_sketch_name" if loft else "path_sketch_name"
    raw = obj.Sections[0] if loft else obj.Spine
    first, second = _native_link(obj.Profile), _native_link(raw)
    if (first is None or second is None or not _whole_sketch_link(obj.Profile, first)
            or not _whole_sketch_link(raw, second)):
        raise ValueError("Native Loft/Pipe references must be whole Sketches")
    first, _ = _section_or_path(document, body, changes.get("profile_sketch_name", first.Name), closed=True)
    second, _ = _section_or_path(document, body, changes.get(field, second.Name), closed=loft, path=not loft)
    if first is second:
        raise ValueError("Loft/Pipe requires two distinct Sketches")
    if loft:
        _distinct_section_planes(first, second)
    if "profile_sketch_name" in changes:
        plan.link("Profile", [(first, [])])
    if field in changes:
        plan.link(prop, [(second, [])])


def _boolean_parameters(plan, document, group, changes):
    obj = plan.target
    tools = ([document.getObject(name) for name in changes["tool_body_names"]]
             if "tool_body_names" in changes else list(obj.Group))
    mode = changes.get("mode", obj.Type)
    if any(tool is None for tool in tools):
        raise ValueError("Requested Boolean tool Body does not exist")
    if mode not in ("Fuse", "Cut", "Common") or not 1 <= len(tools) <= (1 if mode == "Common" else 16):
        raise ValueError("Boolean mode and tool count are incompatible")
    _validate_boolean_tools(group, obj, tools)
    for tool in tools:
        _single_solid_summary(_world_shape(tool), "Boolean tool " + tool.Name)
    if "mode" in changes:
        plan.add("Type", mode, "string")
    if "tool_body_names" in changes:
        plan.link("Group", [(tool, []) for tool in tools])
        if "Group" in plan.writes:
            plan.tool_placements = {
                tool.Name: (tool, tool.getGlobalPlacement(), _metadata(tool, "Placement")["value"])
                for tool in [*obj.Group, *tools]
            }


def _predecessor_parameters(plan, document, group, body, prior, family, command, changes):
    obj = plan.target
    shape = _world_shape(prior)
    _single_solid_summary(shape, "Edit predecessor")
    request = {**changes, "predecessor_name": prior.Name}
    if family in ("fillet", "chamfer", "draft", "thickness"):
        _fixed(obj, {"UseAllEdges": False, "SupportTransform": False})
        if not obj.Base or obj.Base[0] is not prior or not obj.Base[1]:
            raise ValueError("Existing dress-up has no explicit predecessor subelements")
    if family in ("fillet", "chamfer"):
        if "edge_refs" in changes:
            plan.link("Base", [(prior, _dressup_edges(body, group, request, prior, shape=shape))])
        if family == "chamfer":
            _chamfer_parameters(plan, changes)
    elif family in ("draft", "thickness"):
        role = "draft_target" if family == "draft" else "thickness_opening"
        if "face_refs" in changes:
            faces, _ = _current_faces(body, group, request, prior, changes["face_refs"], role, "face_refs", shape=shape)
            plan.link("Base", [(prior, faces)])
        if family == "draft":
            _fixed(obj, {"PullDirection": None})
            if "neutral_plane" in changes:
                evidence = _geometry_evidence(shape, _ir_tolerance_contract(group),
                                              include_relations=False, include_repetition=False)
                plan.link("NeutralPlane", [_neutral_plane(document, body, group, prior, request, evidence)])
        else:
            _fixed(obj, {"Mode": "Skin", "Intersection": False})
            if "direction" in changes:
                plan.add("Reversed", changes["direction"] == "inward", "bool")
            if "join" in changes:
                plan.add("Join", _JOIN[changes["join"]], "string")
    else:
        _fixed(obj, {"TransformMode": "Features"})
        sources = _transform_sources(document, body, prior, changes.get(
            "source_feature_names", [source.Name for source in obj.Originals]))
        if "source_feature_names" in changes:
            plan.link("Originals", [(source, []) for source in sources])
        if family == "mirror" and "mirror_plane" in changes:
            plan.link("MirrorPlane", [_transform_plane(document, body, group, prior, request, shape=shape)])
        if family == "pattern":
            linear = command == "PartDesign_LinearPattern"
            if "axis" in changes:
                link, parent = _transform_axis(document, body, group, prior, request, shape=shape)
                plan.link("Direction" if linear else "Axis", [link])
                if _AXIS_PARENT_PROPERTY in obj.PropertiesList:
                    plan.link(_AXIS_PARENT_PROPERTY, [(parent, [])] if parent else [], empty=("link",))
                elif parent is not None:
                    plan.install_axis_parent = True
                    plan.axis_parent = parent
                    plan.new_links.append(parent)
            _pattern_parameters(plan, changes, linear)


def prepare_parameters(document, group, body, obj, prior, changes):
    command = EDIT_FEATURE_TYPES[obj.TypeId]
    plan = ParameterEdit(obj)
    if command == "PartDesign_Body":
        if "label" in changes:
            if not changes["label"].strip():
                raise ValueError("Body label cannot be blank")
            plan.add("Label", changes["label"], "string")
        _placement(plan, changes, "Placement")
    elif command in PRIMITIVE_COMMANDS:
        spec = PRIMITIVE_COMMANDS[command]
        _fixed(obj, {**dict(spec["fixed"]), "MapMode": "Deactivated"})
        if getattr(obj, "AttachmentSupport", None) or getattr(obj, "Support", None):
            raise ValueError("Native primitive has an attachment")
        complete = {field: changes.get(field, _native_number(getattr(obj, prop)))
                    for field, prop, _ in spec["inputs"]}
        _validate_family(complete, spec["family"])
        for field, prop, rule in spec["inputs"]:
            if field in changes:
                plan.add(prop, changes[field], *(("integer",) if rule == "polygon" else ("quantity", "number")))
        _placement(plan, changes, "Placement", position_field=spec["position_field"])
    elif command in DATUM_TYPES or command == "Sketcher_EditSketch":
        _edit_attachment(plan, document, body, command, changes)
    else:
        _history_parameters(plan, document, group, body, prior, command, changes)
    members = _body_objects(body)
    for dependency in plan.new_links:
        if (dependency is obj or _depends_on(dependency, obj)
                or dependency not in members and command != "PartDesign_Boolean"):
            raise ValueError("Replacement reference is outside its scope or creates a dependency cycle")
    return plan
