"""Explicit descriptive measurements, surface sampling and directional Booleans."""

import math
import time

import Part

from ..analysis.evidence import _geometry_evidence, _semantic_regions
from ..analysis.math3d import _point_distance
from ..analysis.measurements import (_bound_box_summary, _geometry_bound_box,
                                     _shape_center_of_mass)
from ..runtime import _vector_dict


def _shape_sample_points(shape, deflection, maximum):
    points = []
    for vertex in list(getattr(shape, "Vertexes", [])):
        point = getattr(vertex, "Point", None)
        if point is not None:
            points.append(point)
    try:
        tessellated, _triangles = shape.tessellate(deflection)
        points.extend(tessellated)
    except (AttributeError, RuntimeError, TypeError, ValueError):
        pass
    if not points:
        return []
    unique = []
    seen = set()
    for point in points:
        try:
            key = (
                round(float(point.x), 12),
                round(float(point.y), 12),
                round(float(point.z), 12),
            )
        except (AttributeError, TypeError, ValueError):
            continue
        if key not in seen:
            seen.add(key)
            unique.append(point)
    if len(unique) <= maximum:
        return unique
    stride = float(len(unique) - 1) / float(maximum - 1)
    return [unique[int(round(index * stride))] for index in range(maximum)]


def _directed_surface_distance(shape, target, deflection, maximum):
    points = _shape_sample_points(shape, deflection, maximum)
    if not points or not hasattr(Part, "Vertex"):
        return {"available": False, "max_mm": None, "sample_count": 0}
    boundary = target
    try:
        faces = list(target.Faces)
        if faces and hasattr(Part, "makeCompound"):
            boundary = Part.makeCompound(faces)
    except (AttributeError, RuntimeError, TypeError, ValueError):
        boundary = target
    maximum_distance = 0.0
    measured = 0
    try:
        for point in points:
            distance = float(Part.Vertex(point).distToShape(boundary)[0])
            if not math.isfinite(distance) or distance < 0:
                return {"available": False, "max_mm": None, "sample_count": measured}
            maximum_distance = max(maximum_distance, distance)
            measured += 1
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return {"available": False, "max_mm": None, "sample_count": measured}
    return {
        "available": True,
        "max_mm": maximum_distance,
        "sample_count": measured,
    }


def _bidirectional_surface_distance(source_shape, rebuild_shape, contract):
    deflection = contract["surface_sampling_deflection_mm"]
    maximum = int(contract["max_surface_samples_per_direction"])
    source_to_rebuild = _directed_surface_distance(
        source_shape, rebuild_shape, deflection, maximum
    )
    rebuild_to_source = _directed_surface_distance(
        rebuild_shape, source_shape, deflection, maximum
    )
    available = (
        source_to_rebuild["available"]
        and rebuild_to_source["available"]
    )
    return {
        "method": "tessellated_directed_point_to_boundary_faces",
        "approximate": True,
        "sampling_deflection_mm": deflection,
        "available": available,
        "source_to_rebuild_max_mm": source_to_rebuild["max_mm"],
        "rebuild_to_source_max_mm": rebuild_to_source["max_mm"],
        "symmetric_max_mm": (
            max(
                source_to_rebuild["max_mm"],
                rebuild_to_source["max_mm"],
            )
            if available
            else None
        ),
        "source_sample_count": source_to_rebuild["sample_count"],
        "rebuild_sample_count": rebuild_to_source["sample_count"],
    }


def _boolean_shape(left, right, operation):
    """One native call; a null result is not evidence of a valid empty solid."""
    started = time.perf_counter()
    result = {"operation": operation, "method": "native_common" if operation == "common" else "native_cut",
              "calls": 1, "status": "failed", "available": False}
    try:
        shape = left.common(right) if operation == "common" else left.cut(right)
        if shape is None or shape.isNull():
            result.update(status="unavailable", reason="null_result_validity_unconfirmed")
            return result, None
        valid = bool(shape.isValid())
        volume = float(shape.Volume)
        result.update(is_valid=valid, volume_mm3=volume if math.isfinite(volume) else None)
        if not valid or not math.isfinite(volume) or volume < 0:
            result.update(reason="invalid_or_non_finite_boolean_result")
            return result, None
        empty = not shape.Faces and not shape.Edges and not shape.Solids
        if not empty and not shape.Solids:
            result.update(status="unavailable", reason="boolean_result_has_no_solid")
            return result, None
        result.update(status="available", available=True, valid_empty=bool(empty),
                      reason=None, proof_of_equivalence=False)
        return result, shape
    except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
        result.update(exception_type=type(exc).__name__, reason=str(exc)[:300])
        return result, None
    finally:
        result["elapsed_ms"] = (time.perf_counter() - started) * 1000.0


def _measurement(shape, name):
    if name == "volume":
        return float(shape.Volume), "mm3", "native_volume"
    if name == "area":
        return float(shape.Area), "mm2", "native_area"
    if name == "center_of_mass":
        center = _shape_center_of_mass(shape)
        if center is None:
            raise ValueError("mass_properties_unavailable")
        return _vector_dict(center), "mm", "native_mass_properties"
    if name == "bounding_box":
        return _bound_box_summary(_geometry_bound_box(shape)), "mm", "brep_optimal_without_triangulation_or_tolerance_expansion"
    raise ValueError("Unknown measurement")


def _compare_shapes(source, target, contract, options):
    """Run only selected read-only computations; never accept a task."""
    checks = {}
    for name in options.get("measurements", []):
        started = time.perf_counter()
        row = {"status": "failed", "available": False}
        try:
            left, unit, method = _measurement(source, name)
            right, _unit, _method = _measurement(target, name)
            if isinstance(left, dict):
                delta = {key: right[key] - left[key] for key in left}
                values = [*left.values(), *right.values(), *delta.values()]
            else:
                delta = right - left
                values = [left, right, delta]
            if not all(math.isfinite(value) for value in values):
                raise ValueError("non_finite_measurement")
            row.update(status="available", available=True, source=left, target=right,
                       delta=delta, units=unit, method=method)
            if name == "center_of_mass":
                row["distance_mm"] = _point_distance(left, right)
            if name == "bounding_box":
                row["tightness"] = "not_guaranteed_for_trimmed_geometry"
        except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
            row.update(reason=str(exc)[:300], exception_type=type(exc).__name__)
        row["elapsed_ms"] = (time.perf_counter() - started) * 1000.0
        checks[name] = row
    if options.get("surface_sampling", False):
        started = time.perf_counter()
        try:
            row = _bidirectional_surface_distance(source, target, contract)
            row["status"] = "available" if row["available"] else "unavailable"
            if not row["available"]:
                row["reason"] = "one_or_more_sampling_directions_unavailable"
        except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
            row = {"status": "failed", "available": False, "reason": str(exc)[:300],
                   "exception_type": type(exc).__name__, "approximate": True}
        row["elapsed_ms"] = (time.perf_counter() - started) * 1000.0
        checks["surface_sampling"] = row
    for operation in options.get("boolean_operations", []):
        left, right = (target, source) if operation == "target_minus_source" else (source, target)
        checks[operation], _shape = _boolean_shape(left, right, operation)
    return checks


def _residual_regions(source_shape, rebuild_shape, contract):
    """Explicit missing/excess cuts, with direction-local failures and filtering facts."""
    source_evidence = _geometry_evidence(source_shape, contract, include_repetition=False)
    source_regions = _semantic_regions(source_shape, source_evidence, contract)["regions"]
    residuals, operations = [], {}
    for kind, left, right, operation in (
        ("missing", source_shape, rebuild_shape, "source_minus_target"),
        ("excess", rebuild_shape, source_shape, "target_minus_source"),
    ):
        row, difference = _boolean_shape(left, right, operation)
        operations[kind] = row
        row.update(filtered_component_count=0, filtered_volume_mm3=0.0)
        if not row["available"] or difference is None:
            continue
        try:
            for index, component in enumerate(list(difference.Solids), 1):
                volume = float(component.Volume)
                if not math.isfinite(volume) or volume < 0 or not component.isValid():
                    raise ValueError("invalid_residual_component")
                if volume <= contract["numeric_volume_epsilon_mm3"]:
                    row["filtered_component_count"] += 1
                    row["filtered_volume_mm3"] += volume
                    continue
                boundary = _bound_box_summary(_geometry_bound_box(component))
                associated = [region["region_id"] for region in source_regions
                              if _boxes_overlap(boundary, region.get("boundary"), contract["linear_tolerance_mm"])]
                residuals.append({"residual_id": "%s-%03d" % (kind, index), "kind": kind,
                                  "volume_mm3": volume, "area_mm2": float(component.Area),
                                  "bounding_box_mm": boundary,
                                  "bounding_box_tightness": "not_guaranteed_for_trimmed_geometry",
                                  "associated_region_ids": sorted(set(associated))})
        except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
            row.update(status="failed", available=False, reason=str(exc)[:300],
                       exception_type=type(exc).__name__)
            residuals = [item for item in residuals if item["kind"] != kind]
    missing = [item for item in residuals if item["kind"] == "missing"]
    excess = [item for item in residuals if item["kind"] == "excess"]
    available = all(row["available"] for row in operations.values())
    return {"geometry_scope": "body_result", "operations": operations,
            "source_model_revision": source_evidence["stable_selection"]["model_revision"],
            "missing": missing, "excess": excess, "regions": residuals,
            "summary": {"missing_count": len(missing), "excess_count": len(excess),
                        "total_missing_volume_mm3": sum(item["volume_mm3"] for item in missing) if operations["missing"]["available"] else None,
                        "total_excess_volume_mm3": sum(item["volume_mm3"] for item in excess) if operations["excess"]["available"] else None,
                        "has_residual": bool(residuals) if available else None},
            "available": available, "method": "explicit_directional_native_cuts_connected_solids"}


def _boxes_overlap(left, right, tolerance):
    if not isinstance(left, dict) or not isinstance(right, dict):
        return False
    for axis in ("x", "y", "z"):
        left_min = float(left[axis + "_min"]) - tolerance
        left_max = left_min + float(left[axis + "_length"]) + tolerance
        right_min = float(right[axis + "_min"]) - tolerance
        right_max = right_min + float(right[axis + "_length"]) + tolerance
        if left_max < right_min or right_max < left_min:
            return False
    return True
