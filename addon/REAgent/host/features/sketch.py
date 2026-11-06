"""Bounded native Sketch creation, attachment, edit and read-only diagnosis."""

import math

import FreeCAD as App
import Part
import Sketcher

from REAgent.host.process_state import PROCESS
from REAgent.protocol.features import validate_feature_relations
from REAgent.host.validation.tolerance import _ir_tolerance_contract
from REAgent.host.workspace.model import _depends_on, _owned_objects, _session_bodies, _target_state
from REAgent.host.workspace.properties import _metadata
from REAgent.host.workspace.recovery import _block
from REAgent.host.workspace.state import _target_view_revision

_POINT_POSITIONS = {"start": 1, "end": 2, "center": 3}


def _geometry_preflight(geometry, epsilon):
    by_id = {}
    for item in geometry:
        identifier = item["id"]
        if identifier in by_id:
            raise ValueError("Duplicate Sketch geometry ID: " + identifier)
        kind = item["kind"]
        if kind == "line":
            if math.dist(item["start"], item["end"]) <= epsilon:
                raise ValueError("Sketch line is degenerate: " + identifier)
        else:
            radius = item.get("radius_mm")
            if radius is not None and radius <= epsilon:
                raise ValueError("Sketch circle/arc radius is degenerate: " + identifier)
        if kind in ("ellipse", "ellipse_arc"):
            major, minor = item["major_radius_mm"], item["minor_radius_mm"]
            if minor <= epsilon or major <= minor + epsilon:
                raise ValueError("Sketch ellipse requires distinct positive semiaxes")
        by_id[identifier] = item
    return by_id


def _point_constraint_ref(ref, by_id):
    item = by_id.get(ref["geometry_id"])
    if item is None:
        raise ValueError("Unknown Sketch point geometry ID")
    position = ref["position"]
    if position == "center":
        if item["kind"] not in ("circle", "arc", "ellipse", "ellipse_arc"):
            raise ValueError("This Sketch geometry has no center point")
    elif item["kind"] not in ("line", "arc", "ellipse_arc"):
        raise ValueError("This Sketch geometry has no start/end point")
    return ref["geometry_id"], _POINT_POSITIONS[position]


def _constraints_preflight(constraints, by_id):
    for item in constraints:
        kind = item["kind"]
        if kind == "Coincident":
            first = _point_constraint_ref(item["first_point"], by_id)
            second = _point_constraint_ref(item["second_point"], by_id)
            if first == second:
                raise ValueError("Coincident constraint cannot reference one point twice")
        elif kind in ("DistanceX", "DistanceY"):
            _point_constraint_ref(item["point"], by_id)
        elif kind in ("Horizontal", "Vertical", "Distance", "Angle"):
            if by_id.get(item["geometry_id"], {}).get("kind") != "line":
                raise ValueError(kind + " requires a line")
        elif kind in ("Diameter", "Radius"):
            if by_id.get(item["geometry_id"], {}).get("kind") not in ("circle", "arc"):
                raise ValueError(kind + " requires a circle or circular arc")
        else:
            first = by_id.get(item["first_geometry"])
            second = by_id.get(item["second_geometry"])
            if first is None or second is None or first is second:
                raise ValueError("Pair constraint requires two distinct known geometries")
            if kind in ("Parallel", "Perpendicular") and (
                    first["kind"] != "line" or second["kind"] != "line"):
                raise ValueError(kind + " requires two lines")
            if kind == "Equal" and not (
                    first["kind"] == second["kind"] == "line"
                    or first["kind"] in ("circle", "arc")
                    and second["kind"] in ("circle", "arc")):
                raise ValueError("Equal supports two lines or two circular curves")


def _native_geometry(item):
    vector = lambda point: App.Vector(point[0], point[1], 0)
    kind = item["kind"]
    if kind == "line":
        return Part.LineSegment(vector(item["start"]), vector(item["end"]))
    center = vector(item["center"])
    if kind in ("circle", "arc"):
        circle = Part.Circle(center, App.Vector(0, 0, 1), item["radius_mm"])
        return (circle if kind == "circle" else Part.ArcOfCircle(
            circle, math.radians(item["start_deg"]), math.radians(item["end_deg"])))
    direction = math.radians(item["direction_deg"])
    cos_a, sin_a = math.cos(direction), math.sin(direction)
    major, minor = item["major_radius_mm"], item["minor_radius_mm"]
    ellipse = Part.Ellipse(
        center + App.Vector(major * cos_a, major * sin_a, 0),
        center + App.Vector(-minor * sin_a, minor * cos_a, 0), center,
    )
    return (ellipse if kind == "ellipse" else Part.ArcOfEllipse(
        ellipse, math.radians(item["start_deg"]), math.radians(item["end_deg"])))


def _native_constraint(item, indices):
    kind = item["kind"]
    index = lambda identifier: indices[identifier]
    point = lambda ref: [index(ref["geometry_id"]), _POINT_POSITIONS[ref["position"]]]
    if kind == "Coincident":
        args = point(item["first_point"]) + point(item["second_point"])
    elif kind in ("DistanceX", "DistanceY"):
        args = point(item["point"]) + [item["value"]]
    elif kind in ("Horizontal", "Vertical"):
        args = [index(item["geometry_id"])]
    elif kind in ("Distance", "Diameter", "Radius", "Angle"):
        value = math.radians(item["value"]) if kind == "Angle" else item["value"]
        args = [index(item["geometry_id"]), value]
    else:
        args = [index(item["first_geometry"]), index(item["second_geometry"])]
    return Sketcher.Constraint(kind, *args)


class SketchOperations:
    def __init__(self, *, execute_native):
        self.execute_native = execute_native

    def _new_sketch(self, params):
        name = params["sketch_name"]
        body_name = params["body_name"]
        prior_tip = [None]

        def preflight(document, group, request):
            body = document.getObject(body_name)
            if body not in _session_bodies(group):
                raise ValueError("Sketch Body is not owned by this session")
            if document.getObject(name) is not None:
                raise ValueError("Sketch name already exists: " + name)
            prior_tip[0] = body.Tip

        def mutate(document, group, request, changes):
            body = document.getObject(body_name)
            sketch = body.newObject("Sketcher::SketchObject", name)
            return sketch

        def readback(document, group, sketch, PROCESS):
            body = document.getObject(body_name)
            if (sketch.Name != name or sketch.TypeId != "Sketcher::SketchObject"
                    or sketch not in body.Group or document.getObject(name) is not sketch
                    or body.Tip is not prior_tip[0]
                    or sketch.GeometryCount != 0 or sketch.ConstraintCount != 0):
                raise RuntimeError("New native Sketch did not read back as empty in its Body")
            return {"sketch": sketch.Name, "body": body.Name,
                    "type_id": sketch.TypeId, "geometry_count": 0,
                    "constraint_count": 0, "sketch_state": "in_progress"}

        return self.execute_native(
            params, command="PartDesign_NewSketch", preflight=preflight,
            mutate=mutate, readback=readback,
        )

    def _map_sketch(self, params):

        name, body_name = params["sketch_name"], params["body_name"]
        prior_tip = [None]
        original = {}
        reference_object = [None]
        reference_fact = [None]
        reference_mode = ["FlatFace"]

        def preflight(document, group, request):
            body = document.getObject(body_name)
            sketch = document.getObject(name)
            if body not in _session_bodies(group) or sketch is None or sketch not in body.Group:
                raise ValueError("Sketch and reference must belong to the same owned Body")
            if (sketch.TypeId != "Sketcher::SketchObject" or sketch.GeometryCount != 0
                    or sketch.ConstraintCount != 0):
                raise ValueError("First attachment requires an empty native Sketch")
            reference = request["reference"]
            if reference["kind"] == "origin_plane":
                role = reference["role"]
                if body.Origin is None:
                    raise ValueError("Body Origin is unavailable")
                matches = [feature for feature in body.Origin.OriginFeatures
                           if getattr(feature, "Role", None) == role]
                if len(matches) != 1 or matches[0].Document is not document:
                    raise ValueError("Body Origin does not have one stable plane role: " + role)
                reference_object[0] = matches[0]
            elif reference["kind"] == "datum_plane":
                datum = document.getObject(reference["datum_name"])
                if (datum is None or datum.Document is not document
                        or datum.TypeId != "Part::DatumPlane" or datum not in body.Group):
                    raise ValueError("Sketch Datum Plane must be a native reference in its owned Body")
                if _depends_on(datum, sketch):
                    raise ValueError("Sketch attachment would create a dependency cycle")
                reference_object[0] = datum
            elif reference["kind"] == "coordinate_system_plane":
                datum = document.getObject(reference["datum_name"])
                if (datum is None or datum.Document is not document
                        or datum.TypeId != "Part::LocalCoordinateSystem" or datum not in body.Group):
                    raise ValueError("Sketch Coordinate System must be in its owned Body")
                matches = [child for child in getattr(datum, "OriginFeatures", [])
                           if getattr(child, "Role", None) == reference["role"]]
                if (len(matches) != 1 or matches[0].TypeId != "App::Plane"
                        or matches[0].Document is not document):
                    raise ValueError("Coordinate System does not have one native plane for this role")
                if _depends_on(matches[0], sketch) or _depends_on(datum, sketch):
                    raise ValueError("Sketch attachment would create a dependency cycle")
                # The child plane is local to the Coordinate System; attaching
                # directly to it discards the parent's placement.
                reference_object[0] = datum
                reference_mode[0] = {"XY_Plane": "ObjectXY",
                                     "XZ_Plane": "ObjectXZ",
                                     "YZ_Plane": "ObjectYZ"}[reference["role"]]
            else:
                raise ValueError("Sketch reference kind is unavailable")
            reference_fact[0] = {"kind": reference["kind"]}
            if "role" in reference:
                reference_fact[0]["role"] = reference["role"]
            if reference["kind"] == "coordinate_system_plane":
                reference_fact[0]["coordinate_system"] = reference["datum_name"]
            else:
                reference_fact[0]["object"] = reference_object[0].Name
            support = _metadata(sketch, "AttachmentSupport")
            mode = _metadata(sketch, "MapMode")
            if (not support["writable"] or support["codec"] not in ("link_sub", "link_sub_list")
                    or not mode["writable"] or mode["codec"] != "string"
                    or reference_mode[0] not in mode.get("enum", [])):
                raise ValueError("Host Sketch attachment properties are not confirmed writable")
            if support["value"] not in (None, []) or mode["value"] != "Deactivated":
                raise ValueError("Sketch already has an attachment")
            prior_tip[0] = body.Tip
            original.update(AttachmentSupport=support, MapMode=mode)

        def mutate(document, group, request, changes):
            sketch = document.getObject(name)
            for prop in ("AttachmentSupport", "MapMode"):
                meta = original[prop]
                changes.append((name, prop, meta["codec"], meta["value"]))
            sketch.AttachmentSupport = (reference_object[0], [""])
            sketch.MapMode = reference_mode[0]
            return sketch

        def readback(document, group, sketch, PROCESS):
            body = document.getObject(body_name)
            mode = _metadata(sketch, "MapMode")
            support = _metadata(sketch, "AttachmentSupport")
            links = support["value"]
            if support["codec"] == "link_sub":
                links = [links]
            if (mode["value"] != reference_mode[0] or body.Tip is not prior_tip[0]
                    or links != [{"object": {"object": reference_object[0].Name},
                                  "subelements": [""]}]):
                raise RuntimeError("Sketch reference attachment did not read back exactly")
            if reference_fact[0]["kind"] == "coordinate_system_plane":
                role = reference_fact[0]["role"]
                local_normal = {"XY_Plane": App.Vector(0, 0, 1),
                                "XZ_Plane": App.Vector(0, -1, 0),
                                "YZ_Plane": App.Vector(1, 0, 0)}[role]
                expected_normal = reference_object[0].Placement.Rotation.multVec(local_normal)
                actual_normal = sketch.Placement.Rotation.multVec(App.Vector(0, 0, 1))
                if ((sketch.Placement.Base - reference_object[0].Placement.Base).Length > 1e-8
                        or (actual_normal - expected_normal).Length > 1e-8):
                    raise RuntimeError("Sketch did not inherit the Coordinate System plane")
            return {"sketch": sketch.Name, "body": body.Name,
                    "reference": reference_fact[0], "map_mode": mode["value"]}

        return self.execute_native(
            params, command="Sketcher_MapSketch", preflight=preflight,
            mutate=mutate, readback=readback,
        )

    def _edit_sketch(self, params):
        name, body_name = params["sketch_name"], params["body_name"]
        prior_tip = [None]
        epsilon = [None]

        def preflight(document, group, request):
            validate_feature_relations("Sketcher_EditSketch", request)
            body = document.getObject(body_name)
            sketch = document.getObject(name)
            if (body not in _session_bodies(group) or sketch is None
                    or sketch.TypeId != "Sketcher::SketchObject"
                    or sketch not in body.Group):
                raise ValueError("Sketch edit requires an owned native Sketch")
            if sketch.GeometryCount != 0 or sketch.ConstraintCount != 0:
                raise ValueError("Sketch edit is one-shot and requires an empty Sketch")
            if getattr(sketch, "ExternalGeometry", []):
                raise ValueError("Sketch with external geometry is not in the supported edit scope")
            if any(consumer in _owned_objects(group) and consumer is not body
                   for consumer in sketch.InList):
                raise ValueError("Sketch already has a downstream dependent feature")
            epsilon[0] = _ir_tolerance_contract(group)["numeric_linear_epsilon_mm"]
            by_id = _geometry_preflight(request["geometry"], epsilon[0])
            _constraints_preflight(request["constraints"], by_id)
            prior_tip[0] = body.Tip

        def mutate(document, group, request, changes):
            sketch = document.getObject(name)
            indices = {}
            geometry_types = {}
            for item in request["geometry"]:
                geometry = _native_geometry(item)
                index = sketch.addGeometry(geometry, item.get("construction", False))
                if not isinstance(index, int) or isinstance(index, bool) or index < 0:
                    raise RuntimeError("Native Sketch geometry returned an invalid index")
                indices[item["id"]] = index
                geometry_types[item["id"]] = type(geometry).__name__
            if len(set(indices.values())) != len(indices):
                raise RuntimeError("Native Sketch geometry indices are not unique")
            for item in request["constraints"]:
                sketch.addConstraint(_native_constraint(item, indices))
            return sketch, indices, geometry_types

        def readback(document, group, created, state):
            if state["state"] != "in_progress":
                raise RuntimeError("An unused edited Sketch must remain an intermediate")
            sketch, indices, geometry_types = created
            body = document.getObject(body_name)
            if (body.Tip is not prior_tip[0]
                    or sketch.GeometryCount != len(params["geometry"])
                    or sketch.ConstraintCount != len(params["constraints"])
                    or list(indices.values()) != list(range(len(params["geometry"])))):
                raise RuntimeError("Sketch content counts, IDs or Body Tip did not read back")
            if not sketch.evaluateConstraints():
                raise ValueError("Native Sketch constraints are invalid")
            if sketch.detectDegeneratedGeometries(epsilon[0]) != 0:
                raise ValueError("Native Sketch contains degenerate geometry")
            for item in params["geometry"]:
                index = indices[item["id"]]
                if (type(sketch.Geometry[index]).__name__ != geometry_types[item["id"]]
                        or bool(sketch.getConstruction(index)) != item.get("construction", False)):
                    raise RuntimeError("Native Sketch geometry type or construction changed")
            actual_kinds = [str(item.Type) for item in sketch.Constraints]
            requested_kinds = [item["kind"] for item in params["constraints"]]
            if actual_kinds != requested_kinds:
                raise RuntimeError("Native Sketch constraint kinds changed unexpectedly")
            return {"sketch": sketch.Name, "body": body.Name,
                    "geometry_count": sketch.GeometryCount,
                    "constraint_count": sketch.ConstraintCount,
                    "geometry_ids": indices,
                    "fully_constrained": bool(sketch.FullyConstrained)}

        return self.execute_native(
            params, command="Sketcher_EditSketch", preflight=preflight,
            mutate=mutate, readback=readback,
        )

    def _validate_sketch(self, params):

        document, _source, group, config = _target_state(params["document"])
        binding = PROCESS.SESSION_BINDINGS.get(params["session_id"])
        if (binding is None or binding.get("document") is not document
                or binding.get("role") != "rebuild"):
            raise ValueError("Sketch diagnosis requires the exact bound Target session")
        body = document.getObject(params["body_name"])
        sketch = document.getObject(params["sketch_name"])
        if (body not in _session_bodies(group) or sketch is None
                or sketch.TypeId != "Sketcher::SketchObject" or sketch not in body.Group):
            raise ValueError("Sketch diagnosis requires an owned native Sketch")
        if document.HasPendingTransaction:
            raise ValueError("Finish the active transaction before Sketch diagnosis")

        def snapshot():
            return (list(document.UndoNames), list(document.RedoNames),
                    bool(document.HasPendingTransaction), sketch.GeometryCount,
                    sketch.ConstraintCount, [str(state) for state in sketch.State],
                    _target_view_revision(document, group, config))

        before = snapshot()
        try:
            missing = sketch.detectMissingPointOnPointConstraints(params["tolerance_mm"])
            degenerate = sketch.detectDegeneratedGeometries(params["tolerance_mm"])
            constraints_valid = sketch.evaluateConstraints()
            if (not isinstance(missing, int) or isinstance(missing, bool) or missing < 0
                    or not isinstance(degenerate, int) or isinstance(degenerate, bool)
                    or degenerate < 0 or not isinstance(constraints_valid, bool)):
                raise RuntimeError("Native Sketch diagnosis returned unexpected types")
            return_value = {
                "sketch": sketch.Name, "body": body.Name,
                "tolerance_mm": params["tolerance_mm"],
                "geometry_count": sketch.GeometryCount,
                "constraint_count": sketch.ConstraintCount,
                "fully_constrained": bool(sketch.FullyConstrained),
                "missing_point_coincidence_count": missing,
                "degenerated_geometry_count": degenerate,
                "constraint_references_valid": constraints_valid,
                "unavailable_categories": ["self_intersection", "open_wire_classification"],
                "repair_performed": False,
            }
        finally:
            try:
                unchanged = snapshot() == before
            except Exception:
                unchanged = False
            if not unchanged:
                raise _block(document, "Read-only Sketch diagnosis changed or could not confirm native state")
        return return_value

    def contribute(self):
        return {
            'PartDesign_NewSketch': self._new_sketch,
            'Sketcher_MapSketch': self._map_sketch,
            'Sketcher_EditSketch': self._edit_sketch,
            'Sketcher_ValidateSketch': self._validate_sketch,
        }
