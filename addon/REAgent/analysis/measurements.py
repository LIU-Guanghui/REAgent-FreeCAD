"""Geometry bounds independent of display tessellation; no Shape mutation."""

import math

from ..runtime import _vector_dict


def _shape_summary(shape):
    if shape is None or shape.isNull():
        raise ValueError("STEP source contains a null shape")
    bound_box = _geometry_bound_box(shape)
    center = _shape_center_of_mass(shape)
    return {
        "shape_type": str(shape.ShapeType),
        "is_valid": bool(shape.isValid()),
        "is_closed": bool(shape.isClosed()),
        "solid_count": len(shape.Solids),
        "shell_count": len(shape.Shells),
        "face_count": len(shape.Faces),
        "edge_count": len(shape.Edges),
        "vertex_count": len(shape.Vertexes),
        "volume_mm3": float(shape.Volume),
        "area_mm2": float(shape.Area),
        "bounding_box_mm": {
            "x_min": float(bound_box.XMin),
            "y_min": float(bound_box.YMin),
            "z_min": float(bound_box.ZMin),
            "x_length": float(bound_box.XLength),
            "y_length": float(bound_box.YLength),
            "z_length": float(bound_box.ZLength),
        },
        "bounding_box_method": "brep_optimal_without_triangulation_or_tolerance_expansion",
        "bounding_box_tightness": "not_guaranteed_for_trimmed_geometry",
        "center_of_mass_mm": _vector_dict(center) if center is not None else None,
        "center_of_mass_unavailable_reason": "mass_properties_unavailable" if center is None else None,
    }


def _shape_center_of_mass(shape):
    center = _mass_center(shape)
    if center is not None:
        return center

    solids = list(shape.Solids)
    volumes = [float(solid.Volume) for solid in solids]
    total_volume = sum(volumes)
    if (solids and all(math.isfinite(volume) and volume > 0 for volume in volumes)
            and math.isfinite(total_volume) and total_volume > 0):
        solid_centers = [_mass_center(solid) for solid in solids]
        if all(center is not None for center in solid_centers):
            coordinates = [sum(float(getattr(center, axis)) * volume
                               for volume, center in zip(volumes, solid_centers)) / total_volume
                           for axis in ("x", "y", "z")]
            if all(math.isfinite(value) for value in coordinates):
                return _VectorSummary(*coordinates)

    return None


def _mass_center(shape):
    for property_name in ("CenterOfGravity", "CenterOfMass"):
        try:
            center = getattr(shape, property_name)
            if all(math.isfinite(float(getattr(center, axis))) for axis in ("x", "y", "z")):
                return center
        except (AttributeError, RuntimeError, TypeError, ValueError):
            pass
    return None


class _VectorSummary:
    def __init__(self, x, y, z):
        self.x = x
        self.y = y
        self.z = z


def _geometry_bound_box(shape):
    if shape is None or shape.isNull():
        raise ValueError("Geometry bounds require a non-null Shape")
    try:
        box = shape.optimalBoundingBox(False, False)
        values = [float(getattr(box, axis + suffix))
                  for axis in ("X", "Y", "Z") for suffix in ("Min", "Max", "Length")]
    except Exception as exc:
        raise ValueError("B-Rep optimal bounds unavailable: " + str(exc)) from exc
    if not all(math.isfinite(value) for value in values) or any(
            float(getattr(box, axis + "Length")) < 0 for axis in ("X", "Y", "Z")):
        raise ValueError("B-Rep optimal bounds are non-finite or inverted")
    return box


def _shape_summary_consistent(expected, actual):
    """Numeric noise only, independent of Source/Target modeling tolerances."""
    if expected is None or actual is None:
        return expected is actual
    exact = ("shape_type", "is_valid", "is_closed", "solid_count", "shell_count",
             "face_count", "edge_count", "vertex_count")
    if any(expected.get(key) != actual.get(key) for key in exact):
        return False
    box = expected["bounding_box_mm"]
    scale = math.sqrt(sum(box[axis + "_length"] ** 2 for axis in ("x", "y", "z")))
    linear = max(1e-7, scale * 1e-12)
    for key, floor in (("volume_mm3", 1e-9), ("area_mm2", 1e-9)):
        delta = abs(expected[key] - actual[key])
        if not math.isfinite(delta) or delta > max(floor, abs(expected[key]) * 1e-12):
            return False
    for key in box:
        delta = abs(box[key] - actual["bounding_box_mm"][key])
        if not math.isfinite(delta) or delta > linear:
            return False
    left, right = expected.get("center_of_mass_mm"), actual.get("center_of_mass_mm")
    if left is None or right is None:
        return left is right
    return all(abs(left[axis] - right[axis]) <= linear for axis in ("x", "y", "z"))


def _bound_box_summary(bound_box):
    return {
        "x_min": float(bound_box.XMin),
        "y_min": float(bound_box.YMin),
        "z_min": float(bound_box.ZMin),
        "x_length": float(bound_box.XLength),
        "y_length": float(bound_box.YLength),
        "z_length": float(bound_box.ZLength),
    }


def _shape_tolerance(shape):
    try:
        return float(shape.getTolerance(1))
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return None
