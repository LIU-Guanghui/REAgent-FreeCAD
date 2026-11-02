"""Bounded evidence points inside a trimmed face, with co-located normals."""

import math

import FreeCAD as App
import Part

from REAgent.host.numeric import _vector_dict
from REAgent.host.analysis.measurements import _shape_tolerance


def _face_surface_samples(face, contract):
    try:
        bounds = tuple(float(value) for value in face.ParameterRange)
        if len(bounds) != 4 or not all(math.isfinite(value) for value in bounds):
            raise ValueError("non_finite_uv_range")
        u0, u1, v0, v1 = bounds
        if u1 <= u0 or v1 <= v0:
            raise ValueError("degenerate_uv_range")
        kernel_tolerance = _shape_tolerance(face)
        if kernel_tolerance is None or not math.isfinite(kernel_tolerance):
            raise ValueError("face_tolerance_unavailable")
        tolerance = max(kernel_tolerance, contract["numeric_linear_epsilon_mm"])
        boundary = Part.makeCompound(list(face.Edges)) if face.Edges else None
        if boundary is None:
            raise ValueError("face_boundary_unavailable")
        samples = []
        # A finite evidence search, not an automatic modeling selection.
        fractions = (0.5, 0.25, 0.75, 0.125, 0.875)
        for fu in fractions:
            for fv in fractions:
                u, v = u0 + fu * (u1 - u0), v0 + fv * (v1 - v0)
                try:
                    if not face.isPartOfDomain(u, v):
                        continue
                    point, normal = face.valueAt(u, v), face.normalAt(u, v)
                    coords, direction = _vector_dict(point), _vector_dict(normal)
                    if not all(math.isfinite(value) for value in [*coords.values(), *direction.values()]):
                        continue
                    length = math.sqrt(sum(value * value for value in direction.values()))
                    distance = float(Part.Vertex(point).distToShape(boundary)[0])
                    on_face = float(Part.Vertex(point).distToShape(face)[0])
                    if (length <= 1e-12 or not math.isfinite(distance)
                            or distance <= 4.0 * tolerance or not math.isfinite(on_face)
                            or on_face > tolerance):
                        continue
                    samples.append({"available": True, "uv": [u, v], "point_mm": coords,
                                    "normal": {key: value / length for key, value in direction.items()},
                                    "boundary_clearance_mm": distance,
                                    "classification_tolerance_mm": tolerance,
                                    "method": "trimmed_uv_domain_and_boundary_distance"})
                except (AttributeError, RuntimeError, TypeError, ValueError):
                    continue
        return samples, None if samples else "no_reliable_interior_uv_sample"
    except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
        return [], str(exc)


def _face_surface_sample(face, contract):
    samples, reason = _face_surface_samples(face, contract)
    return samples[0] if samples else {"available": False, "unavailable_reason": reason}


def _material_side_at_sample(face, shape, sample):
    if not sample.get("available"):
        return {"classification": "unknown", "unavailable_reason": sample.get("unavailable_reason")}
    point = App.Vector(*(sample["point_mm"][axis] for axis in ("x", "y", "z")))
    normal = sample["normal"]
    tolerance = sample["classification_tolerance_mm"]
    try:
        others = [other for other in shape.Faces if not other.isSame(face)]
        if not others:
            return {"classification": "unknown", "unavailable_reason": "local_solid_clearance_unavailable"}
        clearance = float(Part.Vertex(point).distToShape(Part.makeCompound(others))[0])
        upper = min(sample["boundary_clearance_mm"], clearance) / 4.0
        offset = 4.0 * tolerance
        if not math.isfinite(upper) or upper <= offset:
            return {"classification": "unknown", "unavailable_reason": "no_safe_probe_interval"}
        probes = {}
        for sign, label in ((1.0, "positive_normal"), (-1.0, "negative_normal")):
            probe = App.Vector(*(sample["point_mm"][axis] + sign * offset * normal[axis]
                                 for axis in ("x", "y", "z")))
            probes[label] = bool(shape.isInside(probe, tolerance, False))
        if probes["positive_normal"] is True and probes["negative_normal"] is False:
            classification = "inside_on_positive_normal"
        elif probes["negative_normal"] is True and probes["positive_normal"] is False:
            classification = "inside_on_negative_normal"
        else:
            classification = "ambiguous"
        return {"classification": classification, "probes": probes, "offset_mm": offset,
                "safe_upper_offset_mm": upper, "sample": sample,
                "unavailable_reason": "probe_classifications_not_opposite" if classification == "ambiguous" else None}
    except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
        return {"classification": "unknown", "unavailable_reason": "probe_readback_failed: " + str(exc)}
