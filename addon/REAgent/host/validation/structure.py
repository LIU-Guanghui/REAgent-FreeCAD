"""Read-only native structure and single-solid readiness checks."""

import math

from REAgent.host.analysis.measurements import _shape_summary
from REAgent.host.workspace.model import _owned_objects, _session_bodies, _validate_boolean_tools
from REAgent.host.workspace.state import _target_geometry_state


def _check_result(group):
    state = result_state(group)
    if state["state"] != "solid_ready":
        raise ValueError("Result is still in progress")
    return state["result_geometry"]


def result_state(group):
    """Native solid readiness does not accept a reconstruction task."""

    audit = _structure_audit(group)
    if not audit["passed"]:
        raise ValueError("Native structure check failed: " + repr(audit["failures"]))
    for obj in _owned_objects(group):
        if any(str(state).lower() in ("invalid", "error", "touched") for state in obj.State):
            raise ValueError("Native object is not ready: " + obj.Name)
    geometry = _target_geometry_state(group)
    in_progress = group.Tip is None or geometry is None or bool(audit["in_progress"])
    if geometry is not None and (not geometry["is_valid"]
                                 or not in_progress and geometry["solid_count"] != 1):
        raise ValueError("Result must contain one valid solid")
    return {"state": "in_progress" if in_progress else "solid_ready",
            "result_geometry": geometry, "structure": audit}


_REFERENCE_DATUM_TYPES = frozenset((
    "Part::DatumPlane", "Part::DatumLine", "Part::DatumPoint",
    "Part::LocalCoordinateSystem",
))


def _structure_audit(group):
    failures = []
    in_progress = []
    objects = _owned_objects(group)
    for obj in group.Document.Objects:
        if obj not in objects:
            failures.append({"object": obj.Name, "reason": "unowned_document_object"})
    bodies = _session_bodies(group)
    dependencies = {body.Name: [] for body in bodies}
    for body in bodies:
        if "AllowCompound" in body.PropertiesList and bool(body.AllowCompound):
            failures.append({"object": body.Name, "reason": "compound_body_enabled"})
        if any(child.TypeId == "PartDesign::Body" for child in body.Group):
            failures.append({"object": body.Name, "reason": "nested_body"})
        if body.Tip is not None and body.Tip not in body.Group:
            failures.append({"object": body.Name, "reason": "tip_not_in_body"})
        if body is not group and body.Tip is None:
            in_progress.append({"object": body.Name, "reason": "tool_body_without_tip"})
        if body.Tip is not None:
            shape = getattr(body, "Shape", None)
            tip_type = body.Tip.TypeId
            if tip_type == "Sketcher::SketchObject" or tip_type in _REFERENCE_DATUM_TYPES:
                in_progress.append({"object": body.Name, "reason": "non_solid_tip"})
                if shape is not None and not shape.isNull() and (
                        not shape.isValid() or len(shape.Solids) != 0):
                    failures.append({"object": body.Name, "reason": "invalid_reference_tip_shape"})
            elif shape is None or shape.isNull():
                failures.append({"object": body.Name, "reason": "body_without_shape"})
            else:
                summary = _shape_summary(shape)
                if not summary["is_valid"] or summary["solid_count"] != 1:
                    failures.append({"object": body.Name, "reason": "body_not_single_solid",
                                     "is_valid": summary["is_valid"],
                                     "solid_count": summary["solid_count"]})
    for obj in objects:
        if (obj.TypeId.startswith("Part::") and obj.TypeId not in _REFERENCE_DATUM_TYPES
                or obj.TypeId == "App::Part"):
            failures.append({"object": obj.Name, "reason": "non_body_modeling_feature"})
        if obj.TypeId in _REFERENCE_DATUM_TYPES and not any(
                obj in body.Group or any(
                    obj in getattr(parent, "OriginFeatures", [])
                    for parent in body.Group
                    if parent.TypeId == "Part::LocalCoordinateSystem"
                ) for body in bodies):
            failures.append({"object": obj.Name, "reason": "datum_not_in_owned_body"})
        if obj.TypeId == "Sketcher::SketchObject" and not any(
                consumer in objects and consumer.TypeId != "PartDesign::Body"
                for consumer in obj.InList):
            in_progress.append({"object": obj.Name, "reason": "sketch_not_used_by_feature"})
        if obj.TypeId == "PartDesign::Boolean":
            try:
                tools = list(obj.Group)
                _validate_boolean_tools(group, obj, tools)
                owner = next(body for body in bodies if obj in body.Group)
                dependencies[owner.Name].extend(tool.Name for tool in tools)
            except ValueError as exc:
                failures.append({"object": obj.Name, "reason": "invalid_boolean", "message": str(exc)[:200]})
        states = {str(s).lower() for s in obj.State}
        if states & {"invalid", "error", "touched"}:
            failures.append({"object": obj.Name, "state": list(obj.State),
                             "reason": "native_error" if states & {"invalid", "error"} else "pending_recompute"})
        if any(linked.Document is not group.Document for linked in obj.OutList):
            failures.append({"object": obj.Name, "reason": "cross_document_link"})
        if any(linked not in objects and linked is not group for linked in obj.OutList):
            failures.append({"object": obj.Name, "reason": "unowned_dependency"})
        if obj.TypeId.endswith("FeaturePython"):
            failures.append({"object": obj.Name, "reason": "non_native_feature"})
    reachable = set()
    pending = [group.Name]
    while pending:
        name = pending.pop()
        if name in reachable:
            continue
        reachable.add(name)
        pending.extend(dependencies.get(name, []))
    for body in bodies[1:]:
        if body.Name not in reachable:
            in_progress.append({"object": body.Name, "reason": "tool_body_not_used_by_result_boolean"})
    return {"status": "passed" if not failures else "failed", "passed": not failures,
            "object_count": len(objects), "failures": failures[:16], "failure_count": len(failures),
            "in_progress": in_progress[:16], "in_progress_count": len(in_progress),
            "parameter_editing_exercised": False}


def _single_solid_summary(shape, label):
    if shape is None or shape.isNull():
        raise ValueError(label + " has no native Shape")
    summary = _shape_summary(shape)
    if (not summary["is_valid"] or not summary["is_closed"]
            or summary["solid_count"] != 1
            or not math.isfinite(summary["volume_mm3"])
            or summary["volume_mm3"] <= 0):
        raise ValueError(label + " must be one closed, valid, positive-volume solid")
    return summary
