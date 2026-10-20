"""Four bounded native Datum creators with explicit same-Body references."""

import math

import FreeCAD as App

from ..analysis.measurements import _shape_summary
from ..workspace.executor import ExecutorOperations
from ..protocol.contract import validate_feature_request
from ..workspace.model import _placement_summary, _result_shape, _session_bodies
from ..workspace.properties import _encode, _metadata, _same_placement
from .common.references import _role_object

_DATUM_TYPES = {
    "Part_DatumPlane": "Part::DatumPlane",
    "Part_DatumLine": "Part::DatumLine",
    "Part_DatumPoint": "Part::DatumPoint",
    "Part_CoordinateSystem": "Part::LocalCoordinateSystem",
}
_CS_ROLES = {
    "origin": ("Origin", "App::Point"),
    "x_axis": ("X_Axis", "App::Line"),
    "y_axis": ("Y_Axis", "App::Line"),
    "z_axis": ("Z_Axis", "App::Line"),
    "xy_plane": ("XY_Plane", "App::Plane"),
    "xz_plane": ("XZ_Plane", "App::Plane"),
    "yz_plane": ("YZ_Plane", "App::Plane"),
}


def _resolve_reference(document, body, reference, command):
    kind = reference["kind"]
    if kind.startswith("origin_"):
        origin = body.Origin
        if origin is None:
            raise ValueError("Body Origin is unavailable")
        role, type_id = {
            "origin_plane": (reference.get("role"), "App::Plane"),
            "origin_axis": (reference.get("role"), "App::Line"),
            "origin_point": ("Origin", "App::Point"),
        }[kind]
        obj = _role_object(origin, role, type_id, document)
    elif kind.startswith("datum_"):
        obj = document.getObject(reference["datum_name"])
        expected = {"datum_plane": "Part::DatumPlane",
                    "datum_line": "Part::DatumLine",
                    "datum_point": "Part::DatumPoint"}[kind]
        if (obj is None or obj.TypeId != expected or obj.Document is not document
                or obj not in body.Group):
            raise ValueError("Datum reference must be native and in the same owned Body")
    elif kind == "feature_vertex":
        obj = document.getObject(reference["feature_name"])
        if (obj is None or obj.Document is not document or obj not in body.Group
                or not obj.TypeId.startswith("PartDesign::")
                or "Shape" not in obj.PropertiesList or obj.Shape.isNull()
                or not obj.Shape.isValid()
                or len(obj.Shape.Vertexes) < reference["vertex_index"]):
            raise ValueError("Vertex reference must identify a predecessor Body feature vertex")
    else:
        raise ValueError("Unsupported Datum reference kind")

    mode = {
        ("Part_DatumPlane", "origin_plane"): "FlatFace",
        # Origin axis objects expose their axis direction as local X in the
        # target host, independent of whether the role is X, Y or Z.
        ("Part_DatumLine", "origin_axis"): "ObjectX",
        ("Part_DatumPoint", "origin_point"): "ObjectOrigin",
        ("Part_DatumPoint", "feature_vertex"): "Vertex",
        ("Part_CoordinateSystem", "origin_point"): "ObjectXY",
        ("Part_CoordinateSystem", "datum_plane"): "ObjectXY",
        ("Part_CoordinateSystem", "datum_line"): "ObjectXY",
        ("Part_CoordinateSystem", "datum_point"): "ObjectXY",
    }.get((command, kind))
    if mode is None:
        raise ValueError("Reference is not available for this Datum command")
    subelements = ["Vertex%d" % reference["vertex_index"]] if kind == "feature_vertex" else [""]
    return obj, subelements, mode


def _semantic_references(datum, document):
    children = list(getattr(datum, "OriginFeatures", []))
    if len(children) != len(_CS_ROLES):
        raise RuntimeError("Coordinate System does not expose seven native role children")
    result = {}
    for semantic, (role, type_id) in _CS_ROLES.items():
        _role_object(datum, role, type_id, document)
        result[semantic] = {"coordinate_system": datum.Name, "role": role}
    if len({child.Name for child in children}) != len(children):
        raise RuntimeError("Coordinate System native children are not distinct")
    return result


class DatumOperations(ExecutorOperations):
    def _datum_plane(self, params):
        return self._create_datum(params, "Part_DatumPlane")

    def _datum_line(self, params):
        return self._create_datum(params, "Part_DatumLine")

    def _datum_point(self, params):
        return self._create_datum(params, "Part_DatumPoint")

    def _coordinate_system(self, params):
        return self._create_datum(params, "Part_CoordinateSystem")

    def _create_datum(self, params, command):

        name, body_name = params["datum_name"], params["body_name"]
        type_id = _DATUM_TYPES[command]
        prior_tip = [None]
        expected_offset = [None]
        prior_shape = [None]
        support_object = [None]
        support_subelements = [None]
        map_mode = [None]

        def preflight(document, group, request):
            validate_feature_request(command, request)
            body = document.getObject(body_name)
            if body not in _session_bodies(group):
                raise ValueError("Datum Body is not owned by this session")
            if document.getObject(name) is not None:
                raise ValueError("Datum name already exists: " + name)
            if request["mode"] == "attached":
                support_object[0], support_subelements[0], map_mode[0] = _resolve_reference(
                    document, body, request["reference"], command)
            if command != "Part_DatumPoint":
                axis = request["axis"]
                if math.sqrt(sum(component * component for component in axis)) < 1e-12:
                    raise ValueError("Datum rotation axis must be nonzero")
                rotation = App.Rotation(App.Vector(*axis), request["angle_deg"])
            else:
                rotation = App.Rotation()
            expected_offset[0] = App.Placement(App.Vector(*request["position_mm"]), rotation)
            prior_tip[0] = body.Tip
            shape = _result_shape(group)
            prior_shape[0] = _shape_summary(shape) if shape is not None and not shape.isNull() else None

        def mutate(document, group, request, changes):
            body = document.getObject(body_name)
            datum = document.addObject(type_id, name)
            if datum.TypeId != type_id or datum.Name != name:
                raise RuntimeError("Native Datum type or identity differs from the contract")
            body.addObject(datum)
            if request["mode"] == "free":
                placement = _metadata(datum, "Placement")
                if not placement["writable"] or placement["codec"] != "placement":
                    raise ValueError("Native Datum Placement is not writable")
                datum.Placement = expected_offset[0]
            else:
                support = _metadata(datum, "AttachmentSupport")
                mode = _metadata(datum, "MapMode")
                offset = _metadata(datum, "AttachmentOffset")
                if (not support["writable"] or support["codec"] != "link_sub_list"
                        or not mode["writable"] or mode["codec"] != "string"
                        or map_mode[0] not in mode.get("enum", [])
                        or not offset["writable"] or offset["codec"] != "placement"):
                    raise ValueError("Native Datum attachment properties are not writable")
                datum.AttachmentSupport = (support_object[0], support_subelements[0])
                datum.MapMode = map_mode[0]
                datum.AttachmentOffset = expected_offset[0]
            return datum

        def readback(document, group, datum, _state):
            shape = _result_shape(group)
            current = _shape_summary(shape) if shape is not None and not shape.isNull() else None
            if current != prior_shape[0]:
                raise RuntimeError("Datum creation changed the main Body result Shape")
            body = document.getObject(body_name)
            if (document.getObject(name) is not datum or datum.TypeId != type_id
                    or datum not in body.Group or body.Tip is not prior_tip[0]):
                raise RuntimeError("Native Datum identity, ownership or Tip did not read back")
            if params["mode"] == "free":
                if not _same_placement(datum.Placement, expected_offset[0]):
                    raise RuntimeError("Native Datum free placement did not read back")
            else:
                support = _metadata(datum, "AttachmentSupport")["value"]
                mode = _metadata(datum, "MapMode")["value"]
                if (support != [{"object": {"object": support_object[0].Name},
                                 "subelements": support_subelements[0]}]
                        or mode != map_mode[0]
                        or not _same_placement(datum.AttachmentOffset, expected_offset[0])):
                    raise RuntimeError("Native Datum attachment did not read back exactly")
            result = {"datum": datum.Name, "body": body.Name,
                      "type_id": datum.TypeId, "mode": params["mode"],
                      "placement": _placement_summary(datum)}
            if params["mode"] == "attached":
                result["reference"] = {"kind": params["reference"]["kind"],
                                       "object": support_object[0].Name,
                                       "subelements": support_subelements[0]}
                result["map_mode"] = map_mode[0]
                result["attachment_offset"] = _encode(datum.AttachmentOffset, "placement")
            if command == "Part_CoordinateSystem":
                result["semantic_references"] = _semantic_references(datum, document)
                result["semantic_references_status"] = "native_role_readback"
            return result

        return self._execute_native_command(
            params, command=command, preflight=preflight,
            mutate=mutate, readback=readback,
        )
