"""Native property metadata, codecs and read-only Sketch content facts."""

import math

import FreeCAD as App

from REAgent.host.workspace.model import _is_origin_object
import re

_CODECS = {
    "App::PropertyBool": "bool", "App::PropertyInteger": "integer",
    "App::PropertyIntegerConstraint": "integer", "App::PropertyFloat": "number",
    "App::PropertyFloatConstraint": "number", "App::PropertyString": "string",
    "App::PropertyEnumeration": "string", "App::PropertyVector": "vector",
    "App::PropertyPlacement": "placement", "App::PropertyLink": "link",
    "App::PropertyLinkSub": "link_sub", "App::PropertyLinkList": "link_list",
    "App::PropertyLinkSubList": "link_sub_list",
    **{name: "quantity" for name in ("App::PropertyLength", "App::PropertyDistance",
        "App::PropertyAngle", "App::PropertyQuantity", "App::PropertyQuantityConstraint",
        "App::PropertyArea", "App::PropertyVolume")},
}

_INTERNAL = {
    "Shape", "Proxy", "ExpressionEngine", "Group", "Tip", "Result", "Origin",
    "PlacementList", "Visibility", "Label2", "SupportTransform", "REAgentBodyNames",
    "BaseFeature",
}


def _native_number(value):
    return float(value.Value) if hasattr(value, "Value") else float(value)


def _metadata(obj, prop, *, body_edit=False):
    if prop not in obj.PropertiesList:
        raise ValueError("Unknown native property " + obj.Name + "." + prop)
    typ = obj.getTypeIdOfProperty(prop)
    codec = _CODECS.get(typ)
    status = list(obj.getPropertyStatus(prop))
    editor = obj.getEditorMode(prop)
    readonly = ("ReadOnly" in status or "Immutable" in status or "Output" in status
                or editor == 1 or (isinstance(editor, (tuple, list)) and "ReadOnly" in editor))
    driven = any(pair[0] == prop or pair[0].startswith(prop + ".")
                 for pair in getattr(obj, "ExpressionEngine", []))
    root = obj.Document.getObject("REAgentWorkspace")
    origin_owned = root is not None and _is_origin_object(root, obj)
    boolean_tools = obj.TypeId == "PartDesign::Boolean" and prop == "Group"
    reason = (
        "workspace_read_only" if obj is root and not (body_edit and prop in ("Label", "Placement"))
        else "origin_read_only" if origin_owned
        else "internal_or_output" if prop in _INTERNAL and not boolean_tools
        else "native_read_only" if readonly
        else "expression_driven" if driven
        else "unsupported_codec" if not codec
        else None
    )
    result = {"name": prop, "type_id": typ, "codec": codec, "writable": reason is None,
              "unavailable_reason": reason, "documentation": obj.getDocumentationOfProperty(prop),
              "status": status}
    if typ == "App::PropertyEnumeration":
        result["enum"] = list(obj.getEnumerationsOfProperty(prop))
    if codec:
        try:
            result["value"] = _encode(getattr(obj, prop), codec)
        except Exception as exc:
            result["value_status"] = "not_encodable"
            result["value_error"] = {"step": "metadata_encode", "exception_type": type(exc).__name__,
                                     "message": str(exc)[:200]}
            if result["writable"]:
                result.update(writable=False, unavailable_reason="restoration_value_unavailable")
    return result


def _encode(value, codec):
    if codec in ("bool", "integer", "number", "string"):
        return value
    if codec == "quantity":
        # Quantity.representation emits a round-trip number followed by the native
        # internal unit expression (unlike Unit.representation's debug description).
        unit = repr(App.Units.Quantity(1.0, value.Unit)).partition(" ")[2]
        number = float(value.Value)
        if not math.isfinite(number) or len(unit) > 64:
            raise ValueError("Quantity cannot be represented by the public contract")
        return {"value": number, "unit": unit}
    if codec == "vector":
        return [value.x, value.y, value.z]
    if codec == "placement":
        return {"position": _encode(value.Base, "vector"), "axis": _encode(value.Rotation.Axis, "vector"),
                "angle_deg": math.degrees(value.Rotation.Angle)}
    if codec == "link":
        return {"object": value.Name} if value is not None else None
    if codec == "link_sub":
        if not value or value[0] is None:
            return None
        return {"object": {"object": value[0].Name}, "subelements": list(value[1])}
    if codec == "link_list":
        return [_encode(v, "link") for v in value]
    if codec == "link_sub_list":
        return [_encode(v, "link_sub") for v in value]
    raise ValueError("Unsupported property value")


def _property_readback(obj, prop, codec, requested):
    return _encode(requested, codec), _encode(getattr(obj, prop), codec)


def sketch_content_state(sketch):
    """Persistent Sketch contents for revision/recovery, not a replay payload."""
    def scalar(value):
        if hasattr(value, "x") and hasattr(value, "y") and hasattr(value, "z"):
            return [float(value.x), float(value.y), float(value.z)]
        if isinstance(value, (str, bool, int, float)):
            return value
        return str(value)

    def geometry_row(geometry):
        row = {"type_id": str(getattr(geometry, "TypeId", type(geometry).__name__))}
        for key in ("StartPoint", "EndPoint", "Center", "Axis", "Radius",
                    "MajorRadius", "MinorRadius", "FirstParameter",
                    "LastParameter", "AngleXU"):
            if hasattr(geometry, key):
                row[key] = scalar(getattr(geometry, key))
        if hasattr(geometry, "Ellipse"):
            row["ellipse"] = geometry_row(geometry.Ellipse)
        if hasattr(geometry, "getPoles"):
            row["poles"] = [scalar(point) for point in geometry.getPoles()]
            row["knots"] = list(geometry.getKnots())
            row["weights"] = list(geometry.getWeights())
            row["multiplicities"] = list(geometry.getMultiplicities())
            row["degree"] = int(geometry.Degree)
            row["periodic"] = bool(geometry.isPeriodic())
        return row

    constraints = []
    expressions = dict(sketch.ExpressionEngine)
    for index, constraint in enumerate(sketch.Constraints):
        row = {}
        for key in ("Type", "First", "FirstPos", "Second", "SecondPos",
                    "Third", "ThirdPos", "Value", "Name", "IsDriving", "IsActive"):
            if hasattr(constraint, key):
                row[key] = scalar(getattr(constraint, key))
        row["active"] = bool(sketch.getActive(index))
        name = row.get("Name")
        row["expression"] = expressions.get("Constraints[" + str(index) + "]")
        if name:
            row["expression"] = expressions.get("Constraints." + name, row["expression"])
        if "Value" in row:
            row["value_unit"] = "rad" if row.get("Type", "").startswith("Angle") else "mm" if row.get("Type") in (
                "Distance", "DistanceX", "DistanceY", "Diameter", "Radius", "DistanceViaPoint") else None
        row["driving"] = bool(sketch.getDriving(index)) if row.get("value_unit") else None
        constraints.append(row)
    geometry_rows = []
    for index, geometry in enumerate(sketch.Geometry):
        row = geometry_row(geometry)
        row["construction"] = bool(sketch.getConstruction(index))
        geometry_rows.append(row)
    external_rows = [{"index": -index-1, **geometry_row(geometry)}
                     for index, geometry in enumerate(getattr(sketch, "ExternalGeo", ())) if index >= 2]
    return {"geometry": geometry_rows, "constraints": constraints,
            "external_geometry": _encode(sketch.ExternalGeometry, "link_sub_list"),
            "external_indices": external_rows}


def _native_link(value):
    if isinstance(value, (tuple, list)):
        return value[0] if value else None
    return value


def _set_enum(feature, name, expected):
    meta = _metadata(feature, name)
    if (not meta["writable"] or meta["codec"] != "string"
            or expected not in meta.get("enum", [])):
        raise ValueError("Native enumeration is unavailable: " + name + "=" + expected)
    setattr(feature, name, expected)


def _set_bool(feature, name, expected):
    meta = _metadata(feature, name)
    if not meta["writable"] or meta["codec"] != "bool":
        raise ValueError("Native Boolean property is unavailable: " + name)
    setattr(feature, name, bool(expected))


def _set_number(feature, name, expected):
    meta = _metadata(feature, name)
    if not meta["writable"] or meta["codec"] not in ("quantity", "number"):
        raise ValueError("Native numeric property is unavailable: " + name)
    setattr(feature, name, expected)


def _number_readback(feature, name, expected):
    actual = _native_number(getattr(feature, name))
    if not math.isfinite(actual) or abs(actual - expected) > max(1e-9, abs(expected) * 1e-12):
        raise RuntimeError("Native numeric property did not read back: " + name)
    return actual


def _fixed_dressup_false(feature, prop):
    if prop not in feature.PropertiesList:
        return
    if feature.getTypeIdOfProperty(prop) != "App::PropertyBool":
        raise ValueError("Native dress-up fixed option is not Boolean: " + prop)
    if any(name == prop or name.startswith(prop + ".")
           for name, _expression in getattr(feature, "ExpressionEngine", [])):
        raise ValueError("Native dress-up fixed option is expression-driven: " + prop)
    status = list(feature.getPropertyStatus(prop))
    editor = feature.getEditorMode(prop)
    readonly = ("ReadOnly" in status or "Immutable" in status or "Output" in status
                or editor == 1 or (isinstance(editor, (tuple, list)) and "ReadOnly" in editor))
    if not readonly:
        setattr(feature, prop, False)
    if bool(getattr(feature, prop)):
        raise RuntimeError("Native dress-up fixed option did not read back: " + prop)


def _same_placement(actual, intended):
    translation_error = (actual.Base - intended.Base).Length
    q_actual, q_intended = actual.Rotation.Q, intended.Rotation.Q
    rotation_error = min(
        max(abs(a - b) for a, b in zip(q_actual, q_intended)),
        max(abs(a + b) for a, b in zip(q_actual, q_intended)),
    )
    return translation_error <= 1e-8 and rotation_error <= 1e-10


def _add_read_only_string(obj, name, value, group):
    obj.addProperty("App::PropertyString", name, group)
    setattr(obj, name, value)
    obj.setEditorMode(name, 1)


_NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,99}$")


def _valid_name(value, field):
    if not isinstance(value, str) or not _NAME_PATTERN.match(value):
        raise ValueError(
            "%s must start with a letter and contain only letters, numbers, "
            "or underscores (maximum 100 characters)" % field
        )
    return value
