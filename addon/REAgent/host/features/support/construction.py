"""Shared Body construction, feature preflight and native result readback."""

import FreeCAD as App
import Part

from REAgent.host.workspace.properties import _valid_name
from REAgent.host.validation.structure import _single_solid_summary, result_state
from REAgent.host.workspace.model import _enforce_single_solid_body, _session_bodies, _world_shape
from REAgent.host.workspace.properties import _metadata, _native_link


def _history_context(document, group, request, *, require_solid, sketch=False):
    body = document.getObject(request["body_name"])
    name = request["feature_name"]
    if body not in _session_bodies(group):
        raise ValueError("History feature Body is not owned by this session")
    if document.getObject(name) is not None:
        raise ValueError("History feature name already exists: " + name)
    result_state(group)
    shape = getattr(body, "Shape", None)
    prior = None
    if body.Tip is not None and body.Tip.TypeId not in (
            "Sketcher::SketchObject", "Part::DatumPlane", "Part::DatumLine",
            "Part::DatumPoint", "Part::LocalCoordinateSystem"):
        prior = _single_solid_summary(shape, "Receiving Body")
    elif shape is not None and not shape.isNull() and len(shape.Solids):
        raise ValueError("Receiving Body has a solid without a solid Tip")
    if require_solid and prior is None:
        raise ValueError("Subtractive history feature requires a prior valid single solid")
    profile = None
    if sketch:
        profile = _closed_profile(document, body, request["sketch_name"])
    return body, profile, body.Tip


def _closed_profile(document, body, name):
    profile = document.getObject(name)
    if (profile is None or profile.TypeId != "Sketcher::SketchObject"
            or profile.Document is not document or profile not in body.Group):
        raise ValueError("Profile must be an existing Sketch in the same owned Body")
    if profile.GeometryCount == 0 or not any(
            not profile.getConstruction(i) for i in range(profile.GeometryCount)):
        raise ValueError("Profile Sketch has no non-construction geometry")
    wires = list(getattr(profile.Shape, "Wires", []))
    if (profile.Shape.isNull() or not profile.Shape.isValid()
            or not wires or not all(wire.isClosed() for wire in wires)):
        raise ValueError("Profile Sketch has no valid closed native wires")
    return profile


def _set_profile(feature, sketch):
    meta = _metadata(feature, "Profile")
    if not meta["writable"] or meta["codec"] not in ("link", "link_sub"):
        raise ValueError("Native Profile is not a writable Sketch reference")
    feature.Profile = sketch


def _profile_readback(feature, sketch):
    if _native_link(feature.Profile) is not sketch or sketch not in feature.OutList:
        raise RuntimeError("Native Profile did not retain its Sketch dependency")


def _shape_readback(document, body, feature, name, type_id, prior_tip):
    if (document.getObject(name) is not feature or feature.Name != name
            or feature.TypeId != type_id or feature not in body.Group
            or body.Tip is not feature or feature is prior_tip):
        raise RuntimeError("Native history feature identity, ownership or Tip differs")
    feature_shape = _single_solid_summary(feature.Shape, "History feature")
    body_shape = _single_solid_summary(_world_shape(body), "Receiving Body")
    if abs(feature_shape["volume_mm3"] - body_shape["volume_mm3"]) > max(
            1e-9, body_shape["volume_mm3"] * 1e-12):
        raise RuntimeError("Native feature and Body volume summaries disagree")
    return body_shape


def _section_or_path(document, body, name, *, closed, path=False):
    """Read one pre-existing whole Sketch wire without changing its geometry."""
    sketch = document.getObject(name)
    if (sketch is None or sketch.TypeId != "Sketcher::SketchObject"
            or sketch.Document is not document or sketch not in body.Group):
        raise ValueError("Loft/Pipe input must be an existing Sketch in the same owned Body")
    geometry = [sketch.Geometry[i] for i in range(sketch.GeometryCount)
                if not sketch.getConstruction(i)]
    if not geometry:
        raise ValueError("Loft/Pipe Sketch has no non-construction geometry")
    if path and any(type(item).__name__ not in ("LineSegment", "ArcOfCircle")
                    for item in geometry):
        raise ValueError("Pipe path supports only native line segments and circular arcs")
    shape = sketch.Shape
    wires = list(getattr(shape, "Wires", []))
    if (shape.isNull() or not shape.isValid() or len(wires) != 1
            or not wires[0].isValid() or wires[0].Length <= 1e-7
            or len(shape.Edges) != len(wires[0].Edges)
            or len(shape.Edges) != len(geometry)):
        raise ValueError("Loft/Pipe Sketch must form exactly one continuous native wire")
    if closed and not wires[0].isClosed():
        raise ValueError("Loft/Pipe section must have one closed outer wire")
    if closed:
        try:
            face = Part.Face(wires[0])
            if not face.isValid() or face.Area <= 1e-14:
                raise ValueError("Section has no valid planar area")
        except Exception as exc:
            raise ValueError("Loft/Pipe section is not one valid planar outer contour") from exc
    if any(str(state).lower() in ("touched", "invalid", "error") for state in sketch.State):
        raise ValueError("Loft/Pipe Sketch has pending native errors")
    return sketch, wires[0]


def _distinct_section_planes(first, second):
    first_frame, second_frame = first.Placement, second.Placement
    normal = first_frame.Rotation.multVec(App.Vector(0, 0, 1))
    other = second_frame.Rotation.multVec(App.Vector(0, 0, 1))
    if (normal.cross(other).Length <= 1e-9
            and abs((second_frame.Base - first_frame.Base).dot(normal)) <= 1e-7):
        raise ValueError("Loft sections must lie on different Sketch planes")


def _whole_sketch_link(value, sketch):
    if value is sketch:
        return True
    if isinstance(value, (tuple, list)) and len(value) == 2 and value[0] is sketch:
        return all(part == "" for part in value[1])
    return False


def create_body(document, name, *, main=False):
    """Create one native Body inside the caller's existing transaction."""
    name = _valid_name(name, "body")
    if document.getObject(name) is not None:
        raise ValueError("Body name already exists: " + name)
    body = document.addObject("PartDesign::Body", name)
    if body.Name != name or body.TypeId != "PartDesign::Body":
        raise RuntimeError("Native Body creation did not retain its requested identity")
    _enforce_single_solid_body(body)
    if main:
        body.Label = "Rebuild"
        body.addProperty("App::PropertyStringList", "REAgentBodyNames", "REAgent")
        body.setEditorMode("REAgentBodyNames", 1)
    return body
