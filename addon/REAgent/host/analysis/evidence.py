"""Core B-Rep evidence, topology relations, repetition, and stable selectors."""

import json
import math

import FreeCAD as App
import Part

from REAgent.host.numeric import _vector_dict
from REAgent.host.analysis.math3d import _cross_vector, _dot, _normalized_vector, _point_distance, _vector_difference, _vector_length, _vector_midpoint
from REAgent.host.analysis.measurements import _shape_tolerance
from REAgent.host.analysis.sampling import _material_side_at_sample
from REAgent.host.analysis.topology import _dihedral_relations, _edge_detail, _edge_fingerprint, _edge_geometry_fingerprint, _edge_topology_fingerprint, _face_detail, _face_fingerprint, _face_geometry_fingerprint, _face_topology_fingerprint, _quantize, _same_shape_index, _stable_digest, _transient_name_index, _wire_details


def _geometry_evidence(shape, contract, *, include_relations=True, include_repetition=True):
    """Build revision-bound topology evidence without persisting OCCT indices."""

    faces = list(shape.Faces)
    edges = list(shape.Edges)
    edge_face_names = {"Edge%d" % (index + 1): [] for index in range(len(edges))}
    face_edge_names = {"Face%d" % (index + 1): [] for index in range(len(faces))}
    for face_index, face in enumerate(faces):
        face_name = "Face%d" % (face_index + 1)
        for face_edge in list(face.Edges):
            edge_index = _same_shape_index(face_edge, edges)
            if edge_index is None:
                continue
            edge_name = "Edge%d" % (edge_index + 1)
            if edge_name not in face_edge_names[face_name]:
                face_edge_names[face_name].append(edge_name)
            if face_name not in edge_face_names[edge_name]:
                edge_face_names[edge_name].append(face_name)

    face_adjacency = {name: [] for name in face_edge_names}
    for adjacent_faces in edge_face_names.values():
        for left_index, left_name in enumerate(adjacent_faces):
            for right_name in adjacent_faces[left_index + 1 :]:
                if right_name not in face_adjacency[left_name]:
                    face_adjacency[left_name].append(right_name)
                if left_name not in face_adjacency[right_name]:
                    face_adjacency[right_name].append(left_name)
    for names in face_adjacency.values():
        names.sort(key=_transient_name_index)

    edge_details = [
        _edge_detail(
            edge,
            index + 1,
            edge_face_names["Edge%d" % (index + 1)],
            contract,
        )
        for index, edge in enumerate(edges)
    ]
    edge_by_name = {edge["name"]: edge for edge in edge_details}
    for edge in edge_details:
        fingerprint = _edge_fingerprint(edge, contract, include_position=True)
        rigid_fingerprint = _edge_fingerprint(
            edge, contract, include_position=False
        )
        edge["signature"] = _stable_digest(fingerprint)
        edge["rigid_signature"] = _stable_digest(rigid_fingerprint)

    face_details = []
    wire_details = []
    for index, face in enumerate(faces):
        face_name = "Face%d" % (index + 1)
        detail = _face_detail(face, index + 1, face_adjacency, contract)
        detail["bounding_box_method"] = "brep_optimal_without_triangulation_or_tolerance_expansion"
        detail["bounding_box_tightness"] = "not_guaranteed_for_trimmed_geometry"
        detail["boundary_edges"] = list(face_edge_names[face_name])
        detail["local_tolerance_mm"] = _shape_tolerance(face)
        detail["wires"] = _wire_details(
            face,
            face_name,
            edges,
            edge_by_name,
            contract,
        )
        wire_details.extend(detail["wires"])
        face_details.append(detail)

    surface_by_name = {
        face["name"]: face["surface_type"] for face in face_details
    }
    for edge in edge_details:
        edge["geometry_signature"] = _stable_digest(
            _edge_geometry_fingerprint(edge, contract)
        )
        edge["topology_signature"] = _stable_digest(
            _edge_topology_fingerprint(edge, surface_by_name)
        )
    for face in face_details:
        fingerprint = _face_fingerprint(
            face, edge_by_name, surface_by_name, contract, include_position=True
        )
        rigid_fingerprint = _face_fingerprint(
            face, edge_by_name, surface_by_name, contract, include_position=False
        )
        face["signature"] = _stable_digest(fingerprint)
        face["rigid_signature"] = _stable_digest(rigid_fingerprint)
        face["geometry_signature"] = _stable_digest(
            _face_geometry_fingerprint(face, contract)
        )
        face["topology_signature"] = _stable_digest(
            _face_topology_fingerprint(face, edge_by_name, surface_by_name)
        )

    revision_payload = {
        "shape_type": str(shape.ShapeType),
        "faces": sorted(face["signature"] for face in face_details),
        "edges": sorted(edge["signature"] for edge in edge_details),
    }
    model_revision = "geom-" + _stable_digest(revision_payload, length=24)
    face_signature_index = {}
    edge_signature_index = {}
    wire_signature_index = {}
    for face in face_details:
        face_signature_index.setdefault(face["signature"], []).append(face["name"])
        face["stable_selector"] = {
            "kind": "face",
            "model_revision": model_revision,
            "signature": face["signature"],
        }
    for edge in edge_details:
        edge_signature_index.setdefault(edge["signature"], []).append(edge["name"])
        edge["stable_selector"] = {
            "kind": "edge",
            "model_revision": model_revision,
            "signature": edge["signature"],
        }
    for wire in wire_details:
        wire_signature_index.setdefault(wire["signature"], []).append(
            wire["name"]
        )
        wire["stable_selector"] = {
            "kind": "wire",
            "model_revision": model_revision,
            "signature": wire["signature"],
        }

    dihedral = _dihedral_relations(
        shape, faces, edges, face_details, edge_details, contract
    ) if include_relations else []
    repetition = _repetition_evidence(face_details, contract) if include_repetition else {}
    topology = {
        "global_tolerance_mm": _shape_tolerance(shape),
        "face_to_edges": face_edge_names,
        "edge_to_faces": edge_face_names,
    }
    stable_selection = {
        "model_revision": model_revision,
        "revision_basis": "quantized_geometry_adjacency",
        "transient_handles": "FaceN/EdgeN names are valid only for this response",
        "face_signatures": face_signature_index,
        "edge_signatures": edge_signature_index,
        "wire_signatures": wire_signature_index,
    }
    return {
        "faces": face_details,
        "edges": edge_details,
        "wires": wire_details,
        "face_adjacency": face_adjacency,
        "topology": topology,
        "topological_relations": {
            "dihedral_edges": dihedral,
            "tangent_face_pairs": [
                relation
                for relation in dihedral
                if relation["continuity"] == "tangent"
            ],
            "sharp_face_pairs": [
                relation
                for relation in dihedral
                if relation["continuity"] == "sharp"
            ],
        },
        "stable_selection": stable_selection,
        "repetition": repetition,
    }


def _semantic_regions(shape, evidence, contract):
    """Build compact, revision-bound evidence regions without mutating Shape."""

    faces = list(evidence.get("faces", []))
    wires_by_face = {}
    for wire in evidence.get("wires", []):
        wires_by_face.setdefault(wire.get("face"), []).append(wire)
    revision = evidence.get("stable_selection", {}).get("model_revision")
    groups = {}
    for face in faces:
        group_type, carrier = _semantic_carrier(face, contract)
        key = (group_type, json.dumps(_carrier_group_key(carrier), sort_keys=True))
        groups.setdefault(key, []).append(face)

    face_to_group = {}
    regions = []
    for (group_type, _carrier_json), members in sorted(
        groups.items(), key=lambda item: (item[0][0], item[0][1])
    ):
        member_names = sorted(face["name"] for face in members)
        signatures = sorted(face.get("signature", "") for face in members)
        region_id = "region-" + _stable_digest(
            {"revision": revision, "group_type": group_type, "faces": signatures},
            length=24,
        )
        for name in member_names:
            face_to_group[name] = region_id
        member_wires = [
            wire
            for name in member_names
            for wire in wires_by_face.get(name, [])
        ]
        side_records = [
            _face_material_side(face, shape, contract)
            for face in members
        ]
        regions.append(
            {
                "region_id": region_id,
                "model_revision": revision,
                "group_type": group_type,
                "faces": member_names,
                "face_signatures": signatures,
                "surface_types": sorted({face["surface_type"] for face in members}),
                "wires": [wire["name"] for wire in member_wires],
                "outer_wires": [
                    wire["name"] for wire in member_wires if wire.get("role") == "outer"
                ],
                "inner_wires": [
                    wire["name"] for wire in member_wires if wire.get("role") == "inner"
                ],
                "adjacent_regions": [],
                "normals_at_samples": _unique_vectors(
                    [face.get("surface_sample", {}).get("normal") for face in members], contract
                ),
                "material_side": _aggregate_material_side(side_records),
                "material_side_evidence": side_records,
                "boundary": _region_boundary(members),
                "boundary_tightness": "not_guaranteed_for_trimmed_geometry",
                "carrier": _semantic_carrier(members[0], contract)[1],
            }
        )

    region_by_id = {region["region_id"]: region for region in regions}
    for face_name, adjacent_names in evidence.get("face_adjacency", {}).items():
        owner = face_to_group.get(face_name)
        if owner is None:
            continue
        for adjacent_name in adjacent_names:
            adjacent = face_to_group.get(adjacent_name)
            if adjacent is not None and adjacent != owner:
                if adjacent not in region_by_id[owner]["adjacent_regions"]:
                    region_by_id[owner]["adjacent_regions"].append(adjacent)
    for region in regions:
        region["adjacent_regions"].sort()

    tangent_groups = _tangent_region_groups(evidence, face_to_group, revision)
    return {
        "model_revision": revision,
        "regions": regions,
        "tangent_groups": tangent_groups,
        "region_count": len(regions),
        "tangent_group_count": len(tangent_groups),
        "evidence_policy": "carrier_adjacency_material_side",
    }


def _semantic_carrier(face, contract):
    surface_type = face.get("surface_type", "Unknown")
    surface = face.get("surface", {})
    axis = _canonical_vector(surface.get("axis"), contract)
    axis_key = _canonical_vector_key(surface.get("axis"), contract)
    if surface_type == "Plane" and axis is not None:
        position = surface.get("position")
        projection = None
        if position is not None:
            projection = _quantize(_dot(position, axis), contract["linear_tolerance_mm"])
        return "coplanar", {
            "surface_type": surface_type,
            "axis": axis,
            "axis_key": axis_key,
            "normal_offset": projection,
        }
    if surface_type == "Cylinder" and axis is not None:
        center = surface.get("center")
        radial = None
        if center is not None:
            projection = _dot(center, axis)
            radial = {
                name: _quantize(
                    center[name] - projection * axis[name],
                    contract["linear_tolerance_mm"],
                )
                for name in ("x", "y", "z")
            }
        return "coaxial", {
            "surface_type": surface_type,
            "axis": axis,
            "axis_key": axis_key,
            "axis_line": radial,
        }
    return "surface", {
        "surface_type": surface_type,
        "geometry_signature": face.get("geometry_signature"),
    }


def _carrier_group_key(carrier):
    """Use tolerance-quantized carrier data for grouping, not display values."""

    result = dict(carrier)
    if "axis" in result:
        result.pop("axis", None)
    return result


def _canonical_vector(value, contract):
    if not isinstance(value, dict):
        return None
    length = _vector_length(value)
    if length <= 1e-12:
        return None
    result = {name: float(value[name]) / length for name in ("x", "y", "z")}
    for name in ("x", "y", "z"):
        if abs(result[name]) > 1e-9:
            if result[name] < 0:
                result = {axis: -result[axis] for axis in result}
            break
    return {name: round(result[name], 9) for name in ("x", "y", "z")}


def _canonical_vector_key(value, contract):
    normalized = _canonical_vector(value, contract)
    if normalized is None:
        return None
    angular = math.sin(math.radians(contract["angular_tolerance_deg"]))
    return {
        name: _quantize(normalized[name], max(angular, 1e-9))
        for name in ("x", "y", "z")
    }


def _face_material_side(face, shape, contract):
    if face is None:
        return {"classification": "unknown", "method": "missing_face"}
    index = face.get("index")
    if not isinstance(index, int) or not 1 <= index <= len(shape.Faces):
        return {"classification": "unknown", "unavailable_reason": "current_face_unavailable"}
    return _material_side_at_sample(shape.Faces[index - 1], shape, face.get("surface_sample", {}))


def _aggregate_material_side(records):
    values = [record.get("classification") for record in records if record.get("classification")]
    if not values:
        return "unknown"
    return values[0] if len(set(values)) == 1 else "mixed"


def _unique_vectors(vectors, contract):
    result = []
    seen = set()
    for vector in vectors:
        if not isinstance(vector, dict):
            continue
        key = tuple(
            _quantize(vector[name], max(contract["angular_tolerance_deg"], 1e-9))
            for name in ("x", "y", "z")
        )
        if key not in seen:
            seen.add(key)
            result.append(vector)
    return result


def _region_boundary(members):
    boxes = [face.get("bounding_box_mm") for face in members if face.get("bounding_box_mm")]
    if not boxes:
        return None
    result = {}
    for axis in ("x", "y", "z"):
        minimum = min(float(box[axis + "_min"]) for box in boxes)
        maximum = max(
            float(box[axis + "_min"]) + float(box[axis + "_length"])
            for box in boxes
        )
        result[axis + "_min"] = minimum
        result[axis + "_max"] = maximum
        result[axis + "_length"] = maximum - minimum
    return result


def _tangent_region_groups(evidence, face_to_group, revision):
    adjacency = {}
    for relation in evidence.get("topological_relations", {}).get("tangent_face_pairs", []):
        names = relation.get("faces", [])
        if len(names) != 2:
            continue
        left, right = names
        adjacency.setdefault(left, set()).add(right)
        adjacency.setdefault(right, set()).add(left)
    groups = []
    visited = set()
    for face_name in sorted(adjacency):
        if face_name in visited:
            continue
        stack = [face_name]
        component = []
        while stack:
            current = stack.pop()
            if current in visited:
                continue
            visited.add(current)
            component.append(current)
            stack.extend(sorted(adjacency.get(current, ()), reverse=True))
        faces = sorted(component)
        groups.append(
            {
                "region_id": "tangent-" + _stable_digest(
                    {"revision": revision, "faces": faces}, length=24
                ),
                "model_revision": revision,
                "faces": faces,
                "carrier_regions": sorted(
                    {face_to_group[name] for name in faces if name in face_to_group}
                ),
                "relation": "tangent_face_adjacency",
            }
        )
    return groups


def _classify_points(shape, points, tolerance):
    """Classify points against a solid without mutating the host document."""

    if not isinstance(points, list) or not points:
        raise ValueError("points must be a non-empty list")
    if len(points) > 256:
        raise ValueError("points must contain at most 256 items")
    results = []
    boundary_target = shape
    try:
        if getattr(shape, "Faces", None) and hasattr(Part, "makeCompound"):
            boundary_target = Part.makeCompound(list(shape.Faces))
    except (AttributeError, RuntimeError, TypeError, ValueError):
        boundary_target = shape
    for index, raw_point in enumerate(points):
        point = _point_vector(raw_point, "points[%d]" % index)
        boundary_distance = None
        try:
            boundary_distance = float(Part.Vertex(point).distToShape(boundary_target)[0])
        except (AttributeError, RuntimeError, TypeError, ValueError):
            pass
        if boundary_distance is not None and boundary_distance <= tolerance:
            classification = "boundary"
        else:
            try:
                inside = bool(shape.isInside(point, tolerance, False))
            except (AttributeError, RuntimeError, TypeError, ValueError):
                inside = None
            classification = "inside" if inside is True else "outside" if inside is False else "unknown"
        results.append(
            {
                "index": index,
                "point_mm": _vector_dict(point),
                "classification": classification,
                "boundary_distance_mm": boundary_distance,
                "tolerance_mm": tolerance,
            }
        )
    return results


def _point_vector(value, field):
    if isinstance(value, dict):
        raw = [value.get("x"), value.get("y"), value.get("z")]
    elif isinstance(value, (list, tuple)) and len(value) == 3:
        raw = list(value)
    else:
        raise ValueError("%s must be [x,y,z] or {x,y,z}" % field)
    try:
        numbers = [float(item) for item in raw]
    except (TypeError, ValueError):
        raise ValueError("%s must contain numeric coordinates" % field)
    if any(not math.isfinite(item) or abs(item) > 1000000 for item in numbers):
        raise ValueError("%s coordinates must be finite and bounded" % field)
    return App.Vector(*numbers)


def _repetition_evidence(faces, contract):
    groups = {}
    for face in faces:
        groups.setdefault(face["rigid_signature"], []).append(face)
    congruent = []
    linear = []
    circular = []
    mirror = []
    for signature, members in sorted(groups.items()):
        if len(members) < 2:
            continue
        names = [member["name"] for member in members]
        centers = [member["center_mm"] for member in members]
        congruent.append(
            {
                "rigid_signature": signature,
                "faces": names,
                "count": len(names),
            }
        )
        if any(center is None for center in centers):
            congruent[-1]["position_evidence_unavailable_reason"] = "face_mass_center_unavailable"
            continue
        for left_index, left in enumerate(members):
            for right in members[left_index + 1 :]:
                difference = _vector_difference(
                    right["center_mm"], left["center_mm"]
                )
                midpoint = _vector_midpoint(
                    left["center_mm"], right["center_mm"]
                )
                mirror.append(
                    {
                        "faces": [left["name"], right["name"]],
                        "candidate_plane_origin_mm": midpoint,
                        "candidate_plane_normal": _normalized_vector(difference),
                    }
                )
        linear_candidate = _linear_repeat_candidate(centers, contract)
        if linear_candidate is not None:
            linear_candidate["faces"] = names
            linear.append(linear_candidate)
        circular_candidate = _circular_repeat_candidate(centers, contract)
        if circular_candidate is not None:
            circular_candidate["faces"] = names
            circular.append(circular_candidate)
    return {
        "congruent_face_sets": congruent,
        "linear_repeat_sets": linear,
        "circular_repeat_sets": circular,
        "mirror_pair_candidates": mirror,
    }


def _linear_repeat_candidate(centers, contract):
    if len(centers) < 3:
        return None
    farthest = None
    for left_index, left in enumerate(centers):
        for right in centers[left_index + 1 :]:
            distance = _point_distance(left, right)
            if farthest is None or distance > farthest[0]:
                farthest = (distance, left, right)
    if farthest is None or farthest[0] <= contract["linear_tolerance_mm"]:
        return None
    direction = _normalized_vector(
        _vector_difference(farthest[2], farthest[1])
    )
    projections = []
    for center in centers:
        offset = _vector_difference(center, farthest[1])
        projection = _dot(offset, direction)
        residual = _vector_difference(
            offset,
            {
                "x": projection * direction["x"],
                "y": projection * direction["y"],
                "z": projection * direction["z"],
            },
        )
        if _vector_length(residual) > contract["linear_tolerance_mm"]:
            return None
        projections.append(projection)
    projections.sort()
    spacings = [
        projections[index + 1] - projections[index]
        for index in range(len(projections) - 1)
    ]
    spacing = sum(spacings) / len(spacings)
    if any(
        abs(value - spacing) > contract["linear_tolerance_mm"]
        for value in spacings
    ):
        return None
    return {
        "count": len(centers),
        "direction": direction,
        "spacing_mm": spacing,
    }


def _circular_repeat_candidate(centers, contract):
    if len(centers) < 3:
        return None
    centroid = {
        axis: sum(center[axis] for center in centers) / len(centers)
        for axis in ("x", "y", "z")
    }
    offsets = [_vector_difference(center, centroid) for center in centers]
    radii = [_vector_length(offset) for offset in offsets]
    radius = sum(radii) / len(radii)
    if radius <= contract["linear_tolerance_mm"]:
        return None
    if any(
        abs(value - radius) > contract["linear_tolerance_mm"]
        for value in radii
    ):
        return None
    normal = None
    for index in range(1, len(offsets) - 1):
        candidate = _cross_vector(offsets[0], offsets[index])
        if _vector_length(candidate) > contract["linear_tolerance_mm"]:
            normal = _normalized_vector(candidate)
            break
    if normal is None:
        return None
    if any(
        abs(_dot(offset, normal)) > contract["linear_tolerance_mm"]
        for offset in offsets
    ):
        return None
    basis_x = _normalized_vector(offsets[0])
    basis_y = _cross_vector(normal, basis_x)
    angles = sorted(
        math.atan2(_dot(offset, basis_y), _dot(offset, basis_x))
        % (2.0 * math.pi)
        for offset in offsets
    )
    differences = [
        (
            angles[(index + 1) % len(angles)] - angles[index]
        )
        % (2.0 * math.pi)
        for index in range(len(angles))
    ]
    expected = 2.0 * math.pi / len(angles)
    angular_tolerance = math.radians(contract["angular_tolerance_deg"])
    if any(abs(value - expected) > angular_tolerance for value in differences):
        return None
    return {
        "count": len(centers),
        "center_mm": centroid,
        "axis": normal,
        "radius_mm": radius,
        "angle_deg": 360.0,
    }
