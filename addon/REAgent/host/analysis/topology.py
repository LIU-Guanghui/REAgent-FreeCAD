"""Core B-Rep details, stable fingerprints, selectors, and relations."""

import hashlib
import json
import math
import re

import FreeCAD as App

from REAgent.host.numeric import _vector_dict
from REAgent.protocol.observations import entity_record
from REAgent.host.analysis.math3d import _normalized_vector, _parallel_line_distance, _plane_distance, _point_distance, _vector_length, _vectors_parallel
from REAgent.host.analysis.measurements import _bound_box_summary, _geometry_bound_box, _mass_center, _shape_tolerance
from REAgent.host.analysis.sampling import _face_surface_sample


def _edge_detail(edge, index, adjacent_faces, contract):
    name = "Edge%d" % index
    # OCCT can expose internal or degenerate edges whose Curve accessor raises
    # instead of returning a curve.  Keep those edges in the evidence set as
    # Unknown so one malformed edge cannot abort semantic selection entirely.
    try:
        curve = getattr(edge, "Curve", None)
    except (AttributeError, RuntimeError, TypeError, ValueError):
        curve = None
    curve_type = type(curve).__name__ if curve is not None else "Unknown"
    parameter_range = [
        float(value) for value in getattr(edge, "ParameterRange", (0.0, 0.0))
    ]
    vertices = []
    for vertex in list(getattr(edge, "Vertexes", [])):
        point = getattr(vertex, "Point", None)
        if point is not None:
            vertices.append(_vector_dict(point))
    center = _mass_center(edge)
    try:
        closed = bool(edge.isClosed())
    except (AttributeError, RuntimeError, TypeError):
        closed = len(vertices) == 1
    try:
        periodic = bool(curve.isPeriodic())
    except (AttributeError, RuntimeError, TypeError):
        periodic = False
    length = float(getattr(edge, "Length", 0.0))
    midpoint = None
    if (
        len(parameter_range) == 2
        and abs(parameter_range[1] - parameter_range[0]) > 1e-12
    ):
        try:
            midpoint = _vector_dict(
                edge.valueAt((parameter_range[0] + parameter_range[1]) / 2.0)
            )
        except (AttributeError, RuntimeError, TypeError, ValueError):
            midpoint = None
    local_tolerance = _shape_tolerance(edge)
    return entity_record("edge", {
        "name": name,
        "index": index,
        "curve_type": curve_type,
        "orientation": str(getattr(edge, "Orientation", "Unknown")),
        "length_mm": length,
        "center_mm": _vector_dict(center) if center is not None else None,
        "center_unavailable_reason": "kernel_mass_center_unavailable" if center is None else None,
        "endpoints_mm": vertices,
        "midpoint_mm": midpoint,
        "parameter_range": parameter_range,
        "closed": closed,
        "periodic": periodic,
        "curve": _curve_parameters(curve, curve_type),
        "adjacent_faces": list(adjacent_faces),
        "local_tolerance_mm": local_tolerance,
    })


def _curve_parameters(curve, curve_type):
    if curve is None:
        return {}
    result = {}
    vector_properties = (
        "Axis",
        "Center",
        "Direction",
        "Location",
        "Focus1",
        "Focus2",
    )
    scalar_properties = (
        "Radius",
        "MajorRadius",
        "MinorRadius",
        "Degree",
        "NbPoles",
    )
    for property_name in vector_properties:
        try:
            result[_snake_case(property_name)] = _vector_dict(
                getattr(curve, property_name)
            )
        except (AttributeError, RuntimeError, TypeError, ValueError):
            pass
    for property_name in scalar_properties:
        try:
            result[_snake_case(property_name)] = float(
                getattr(curve, property_name)
            )
        except (AttributeError, RuntimeError, TypeError, ValueError):
            pass
    return result


def _wire_details(face, face_name, global_edges, edge_by_name, contract):
    result = []
    outer_wire = getattr(face, "OuterWire", None)
    for index, wire in enumerate(list(getattr(face, "Wires", []))):
        edge_names = []
        for wire_edge in list(getattr(wire, "Edges", [])):
            edge_index = _same_shape_index(wire_edge, global_edges)
            if edge_index is not None:
                edge_names.append("Edge%d" % (edge_index + 1))
        if outer_wire is None:
            is_outer = index == 0
        else:
            try:
                is_outer = bool(wire.isSame(outer_wire))
            except (AttributeError, RuntimeError, TypeError):
                is_outer = index == 0
        edge_signatures = [
            edge_by_name[name]["signature"]
            for name in edge_names
            if name in edge_by_name
        ]
        signature = _stable_digest(
            {
                "role": "outer" if is_outer else "inner",
                "ordered_edges": _canonical_cycle(edge_signatures),
                "perimeter": _quantize(
                    sum(
                        edge_by_name[name]["length_mm"]
                        for name in edge_names
                        if name in edge_by_name
                    ),
                    contract["linear_tolerance_mm"],
                ),
            }
        )
        result.append(
            {
                "name": "%s/Wire%d" % (face_name, index + 1),
                "face": face_name,
                "index": index + 1,
                "role": "outer" if is_outer else "inner",
                "orientation": str(getattr(wire, "Orientation", "Unknown")),
                "closed": bool(getattr(wire, "isClosed", lambda: True)()),
                "edges": edge_names,
                "signature": signature,
            }
        )
    return result


def _canonical_cycle(values):
    if not values:
        return []
    sequences = []
    for sequence in (list(values), list(reversed(values))):
        for index in range(len(sequence)):
            sequences.append(sequence[index:] + sequence[:index])
    return min(sequences)


def _quantize(value, tolerance):
    tolerance = max(float(tolerance), 1e-12)
    return int(round(float(value) / tolerance))


def _quantized_vector(vector, tolerance, include_position=True):
    if vector is None or not include_position:
        return None
    return [
        _quantize(vector["x"], tolerance),
        _quantize(vector["y"], tolerance),
        _quantize(vector["z"], tolerance),
    ]


def _quantized_parameters(parameters, contract, include_position=True):
    result = {}
    position_names = {
        "center",
        "position",
        "location",
        "focus1",
        "focus2",
        "apex",
    }
    direction_names = {"axis", "direction"}
    for name, value in sorted(parameters.items()):
        if name in position_names:
            if include_position:
                result[name] = _quantized_vector(
                    value, contract["linear_tolerance_mm"]
                )
        elif name in direction_names:
            result[name] = _quantized_vector(
                value,
                math.sin(
                    math.radians(contract["angular_tolerance_deg"])
                ),
            )
        elif isinstance(value, (int, float)):
            tolerance = (
                math.radians(contract["angular_tolerance_deg"])
                if "angle" in name
                else contract["linear_tolerance_mm"]
            )
            result[name] = _quantize(value, tolerance)
    return result


def _edge_fingerprint(edge, contract, include_position):
    endpoints = [
        _quantized_vector(
            endpoint,
            contract["linear_tolerance_mm"],
            include_position,
        )
        for endpoint in edge["endpoints_mm"]
    ]
    endpoints = sorted(endpoint for endpoint in endpoints if endpoint is not None)
    return {
        "curve_type": edge["curve_type"],
        "length": _quantize(
            edge["length_mm"], contract["linear_tolerance_mm"]
        ),
        "center": _quantized_vector(
            edge["center_mm"],
            contract["linear_tolerance_mm"],
            include_position,
        ),
        "endpoints": endpoints,
        "closed": edge["closed"],
        "periodic": edge["periodic"],
        "adjacent_face_count": len(edge["adjacent_faces"]),
        "curve": _quantized_parameters(
            edge["curve"], contract, include_position
        ),
    }


def _edge_geometry_fingerprint(edge, contract):
    fingerprint = _edge_fingerprint(edge, contract, include_position=True)
    fingerprint.pop("adjacent_face_count", None)
    return fingerprint


def _edge_topology_fingerprint(edge, surface_by_name):
    return {
        "orientation": edge["orientation"],
        "vertex_count": len(edge["endpoints_mm"]),
        "closed": edge["closed"],
        "adjacent_face_count": len(edge["adjacent_faces"]),
        "adjacent_surface_types": sorted(
            surface_by_name.get(name, "Unknown")
            for name in edge["adjacent_faces"]
        ),
    }


def _face_fingerprint(
    face, edge_by_name, surface_by_name, contract, include_position
):
    edge_signatures = sorted(
        edge_by_name[name][
            "signature" if include_position else "rigid_signature"
        ]
        for name in face["boundary_edges"]
        if name in edge_by_name
    )
    bound_box = face["bounding_box_mm"]
    box_dimensions = sorted(
        _quantize(bound_box[name], contract["linear_tolerance_mm"])
        for name in ("x_length", "y_length", "z_length")
    )
    return {
        "surface_type": face["surface_type"],
        "orientation": face["orientation"],
        "area": _quantize(
            face["area_mm2"], contract["area_tolerance_mm2"]
        ),
        "center": _quantized_vector(
            face["center_mm"],
            contract["linear_tolerance_mm"],
            include_position,
        ),
        "box_dimensions": box_dimensions,
        "surface": _quantized_parameters(
            face["surface"], contract, include_position
        ),
        "wire_roles": sorted(wire["role"] for wire in face["wires"]),
        "boundary_edges": edge_signatures,
        "adjacent_surface_types": sorted(
            surface_by_name.get(name, "Unknown")
            for name in face["adjacent_faces"]
        ),
    }


def _face_geometry_fingerprint(face, contract):
    bound_box = face["bounding_box_mm"]
    return {
        "surface_type": face["surface_type"],
        "area": _quantize(face["area_mm2"], contract["area_tolerance_mm2"]),
        "center": _quantized_vector(
            face["center_mm"], contract["linear_tolerance_mm"], True
        ),
        "box_dimensions": sorted(
            _quantize(bound_box[name], contract["linear_tolerance_mm"])
            for name in ("x_length", "y_length", "z_length")
        ),
        "surface": _quantized_parameters(face["surface"], contract, True),
    }


def _face_topology_fingerprint(face, edge_by_name, surface_by_name):
    return {
        "orientation": face["orientation"],
        "wire_roles": sorted(wire["role"] for wire in face["wires"]),
        "boundary_curve_types": sorted(
            edge_by_name[name]["curve_type"]
            for name in face["boundary_edges"]
            if name in edge_by_name
        ),
        "adjacent_surface_types": sorted(
            surface_by_name.get(name, "Unknown")
            for name in face["adjacent_faces"]
        ),
    }


def _stable_digest(value, length=20):
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    return hashlib.sha256(payload.encode("ascii")).hexdigest()[:length]


def _resolve_geometry_selectors(value, stable_selection):
    if value is None:
        return {
            "face_names": [],
            "edge_names": [],
            "wire_names": [],
            "results": [],
        }
    if not isinstance(value, list) or not value:
        raise ValueError("selectors must be a non-empty list")
    face_names = []
    edge_names = []
    wire_names = []
    results = []
    revision = stable_selection["model_revision"]
    indexes = {
        "face": stable_selection["face_signatures"],
        "edge": stable_selection["edge_signatures"],
        "wire": stable_selection["wire_signatures"],
    }
    for selector in value:
        if not isinstance(selector, dict):
            raise ValueError("Each selector must be an object")
        kind = selector.get("kind")
        signature = selector.get("signature")
        if kind not in indexes or not isinstance(signature, str):
            raise ValueError(
                "selector kind/signature must identify a face, edge, or wire"
            )
        matches = list(indexes[kind].get(signature, []))
        if not matches:
            raise KeyError("Stable selector did not match current geometry")
        if kind == "face":
            face_names.extend(matches)
        elif kind == "edge":
            edge_names.extend(matches)
        else:
            wire_names.extend(matches)
        results.append(
            {
                "selector": selector,
                "matches": matches,
                "ambiguous": len(matches) > 1,
            }
        )
    return {
        "face_names": list(dict.fromkeys(face_names)),
        "edge_names": list(dict.fromkeys(edge_names)),
        "wire_names": list(dict.fromkeys(wire_names)),
        "results": results,
    }


def _dihedral_relations(
    shape, faces, edges, face_details, edge_details, contract
):
    face_by_name = {face["name"]: face for face in face_details}
    face_shape_by_name = {
        "Face%d" % (index + 1): face for index, face in enumerate(faces)
    }
    edge_shape_by_name = {
        "Edge%d" % (index + 1): edge for index, edge in enumerate(edges)
    }
    relations = []
    for edge in edge_details:
        adjacent = edge["adjacent_faces"]
        if len(adjacent) != 2:
            continue
        edge_shape = edge_shape_by_name[edge["name"]]
        left_shape = face_shape_by_name[adjacent[0]]
        right_shape = face_shape_by_name[adjacent[1]]
        normals = _edge_face_normals(edge_shape, left_shape, right_shape)
        angle = _normal_angle_deg(normals[0], normals[1])
        continuity = (
            "tangent"
            if angle is not None
            and min(angle, abs(180.0 - angle))
            <= contract["angular_tolerance_deg"]
            else "sharp"
        )
        material = _material_side_probe(
            shape, edge_shape, normals, contract
        )
        convexity = material["convexity"]
        material_side = material["material_side"]
        if continuity == "tangent":
            convexity = "smooth"
            material_side = "continuous"
        if angle is None or continuity == "tangent":
            interior_angle = 180.0 if continuity == "tangent" else None
        elif convexity == "convex":
            interior_angle = 180.0 - angle
        elif convexity == "concave":
            interior_angle = 180.0 + angle
        else:
            interior_angle = None
        relations.append(
            {
                "edge": edge["name"],
                "faces": list(adjacent),
                "face_signatures": [
                    face_by_name[name]["signature"] for name in adjacent
                ],
                "normal_angle_deg": angle,
                "interior_dihedral_deg": interior_angle,
                "continuity": continuity,
                "convexity": convexity,
                "material_side": material_side,
                "inside_probe_count": material["inside_probe_count"],
            }
        )
    return relations


def _edge_face_normals(edge, left_face, right_face):
    try:
        start, end = edge.ParameterRange
        point = edge.valueAt((float(start) + float(end)) / 2.0)
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return (None, None)
    result = []
    for face in (left_face, right_face):
        try:
            u_value, v_value = face.Surface.parameter(point)
            result.append(_vector_dict(face.normalAt(u_value, v_value)))
        except (AttributeError, RuntimeError, TypeError, ValueError):
            result.append(None)
    return tuple(result)


def _normal_angle_deg(left, right):
    if left is None or right is None:
        return None
    left_length = _vector_length(left)
    right_length = _vector_length(right)
    if left_length == 0 or right_length == 0:
        return None
    cosine = (
        left["x"] * right["x"]
        + left["y"] * right["y"]
        + left["z"] * right["z"]
    ) / (left_length * right_length)
    return math.degrees(math.acos(max(-1.0, min(1.0, cosine))))


def _material_side_probe(shape, edge, normals, contract):
    if normals[0] is None or normals[1] is None:
        return {
            "convexity": "unknown",
            "material_side": "unknown",
            "inside_probe_count": None,
        }
    try:
        start, end = edge.ParameterRange
        point = edge.valueAt((float(start) + float(end)) / 2.0)
        scale = max(contract["linear_tolerance_mm"] * 10.0, 1e-4)
        normalized = [_normalized_vector(normal) for normal in normals]
        inside_count = 0
        for left_sign, right_sign in (
            (-1.0, -1.0),
            (-1.0, 1.0),
            (1.0, -1.0),
            (1.0, 1.0),
        ):
            probe = App.Vector(
                float(point.x)
                + scale
                * (
                    left_sign * normalized[0]["x"]
                    + right_sign * normalized[1]["x"]
                ),
                float(point.y)
                + scale
                * (
                    left_sign * normalized[0]["y"]
                    + right_sign * normalized[1]["y"]
                ),
                float(point.z)
                + scale
                * (
                    left_sign * normalized[0]["z"]
                    + right_sign * normalized[1]["z"]
                ),
            )
            if shape.isInside(
                probe, contract["linear_tolerance_mm"], False
            ):
                inside_count += 1
    except (AttributeError, RuntimeError, TypeError, ValueError):
        inside_count = None
    if inside_count == 1:
        convexity = "convex"
        material_side = "single_quadrant"
    elif inside_count == 3:
        convexity = "concave"
        material_side = "three_quadrants"
    else:
        convexity = "unknown"
        material_side = "ambiguous"
    return {
        "convexity": convexity,
        "material_side": material_side,
        "inside_probe_count": inside_count,
    }


def _face_detail(face, index, adjacency, contract):
    name = "Face%d" % index
    surface = face.Surface
    surface_type = type(surface).__name__
    parameter_range = [float(value) for value in face.ParameterRange]
    center = _mass_center(face)
    return entity_record("face", {
        "name": name,
        "index": index,
        "surface_type": surface_type,
        "orientation": str(face.Orientation),
        "area_mm2": float(face.Area),
        "center_mm": _vector_dict(center) if center is not None else None,
        "center_unavailable_reason": "kernel_mass_center_unavailable" if center is None else None,
        "surface_sample": _face_surface_sample(face, contract),
        "parameter_range": parameter_range,
        "wire_count": len(face.Wires),
        "edge_count": len(face.Edges),
        "bounding_box_mm": _bound_box_summary(_geometry_bound_box(face)),
        "surface": _surface_parameters(surface, surface_type),
        "adjacent_faces": list(adjacency[name]),
    })


def _surface_parameters(surface, surface_type):
    result = {}
    vector_properties = {
        "Plane": ("Axis", "Position"),
        "Cylinder": ("Axis", "Center"),
        "Cone": ("Axis", "Center", "Apex"),
        "Sphere": ("Axis", "Center"),
        "Toroid": ("Axis", "Center"),
    }
    scalar_properties = {
        "Cylinder": ("Radius",),
        "Cone": ("Radius", "SemiAngle"),
        "Sphere": ("Radius",),
        "Toroid": ("MajorRadius", "MinorRadius"),
    }
    for property_name in vector_properties.get(surface_type, ()):
        try:
            result[_snake_case(property_name)] = _vector_dict(
                getattr(surface, property_name)
            )
        except (AttributeError, RuntimeError):
            pass
    for property_name in scalar_properties.get(surface_type, ()):
        try:
            result[_snake_case(property_name)] = float(
                getattr(surface, property_name)
            )
        except (AttributeError, RuntimeError, TypeError, ValueError):
            pass
    return result


def _geometric_relations(faces, tolerance):
    relations = {
        "parallel_plane_pairs": [],
        "coplanar_plane_pairs": [],
        "coaxial_cylinder_pairs": [],
        "concentric_surface_pairs": [],
        "equal_radius_pairs": [],
    }
    for left_index, left in enumerate(faces):
        for right in faces[left_index + 1 :]:
            pair = {"faces": [left["name"], right["name"]]}
            left_surface = left["surface"]
            right_surface = right["surface"]
            left_type = left["surface_type"]
            right_type = right["surface_type"]

            if left_type == right_type == "Plane":
                left_axis = left_surface.get("axis")
                right_axis = right_surface.get("axis")
                if _vectors_parallel(left_axis, right_axis):
                    relations["parallel_plane_pairs"].append(pair)
                    left_position = left_surface.get("position")
                    right_position = right_surface.get("position")
                    if (
                        left_position is not None
                        and right_position is not None
                        and _plane_distance(
                            left_position, right_position, left_axis
                        )
                        <= tolerance
                    ):
                        relations["coplanar_plane_pairs"].append(pair)

            if left_type == right_type == "Cylinder":
                left_axis = left_surface.get("axis")
                right_axis = right_surface.get("axis")
                left_center = left_surface.get("center")
                right_center = right_surface.get("center")
                if (
                    _vectors_parallel(left_axis, right_axis)
                    and left_center is not None
                    and right_center is not None
                    and _parallel_line_distance(
                        left_center, right_center, left_axis
                    )
                    <= tolerance
                ):
                    relations["coaxial_cylinder_pairs"].append(pair)

            if left_type == right_type and left_type in ("Sphere", "Toroid"):
                left_center = left_surface.get("center")
                right_center = right_surface.get("center")
                if (
                    left_center is not None
                    and right_center is not None
                    and _point_distance(left_center, right_center) <= tolerance
                ):
                    relations["concentric_surface_pairs"].append(pair)

            left_radius = left_surface.get("radius")
            right_radius = right_surface.get("radius")
            if (
                left_type == right_type
                and left_radius is not None
                and right_radius is not None
                and abs(left_radius - right_radius) <= tolerance
            ):
                relations["equal_radius_pairs"].append(pair)
    return relations


def _same_shape_index(candidate, shapes):
    for index, shape in enumerate(shapes):
        try:
            if candidate.isSame(shape):
                return index
        except (AttributeError, RuntimeError, TypeError):
            if candidate is shape:
                return index
    return None


def _transient_name_index(name):
    match = re.search(r"([0-9]+)$", name)
    return int(match.group(1)) if match else 0


def _snake_case(name):
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()
