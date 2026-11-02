"""Core read-only predecessor bounds for late dress-up evidence.

This module deliberately works on the JSON-like geometry evidence returned by
``evidence._geometry_evidence``.  It never asks OCCT to remove faces, heal a
shape, or construct a temporary document. The result is a bounded hypothesis
about the sharp predecessor envelope that a Fillet may have trimmed; it is not
a reconstructed Shape and it is never sufficient by itself to create a
feature."""

import math

_AXES = ("x", "y", "z")
_BLEND_SURFACES = {"Cylinder", "Sphere", "Toroid"}


def infer_virtual_predecessor_bounds(
    analysis,
    *,
    face_names=None,
    selectors=None,
    support_face_names=None,
    radius_mm=None,
    tolerance_mm=None,
):
    """Infer candidate predecessor envelopes without changing Source.

    ``face_names`` identify transition faces for an explicit local question.
    If omitted, Fillet transitions are discovered from tangent blend groups.
    All references are revision-scoped and unresolved selectors are reported
    as counter-evidence instead of silently falling back to FaceN.
    """

    if not isinstance(analysis, dict):
        raise ValueError("analysis must be an object")
    tolerance = _positive_or_default(tolerance_mm, 0.01)
    faces = {
        face.get("name"): face
        for face in analysis.get("faces", [])
        if isinstance(face, dict) and isinstance(face.get("name"), str)
    }
    revision = (analysis.get("stable_selection") or {}).get("model_revision")
    if not isinstance(revision, str) or not revision:
        raise ValueError("analysis has no stable model revision")

    requested, unresolved = _resolve_face_names(
        faces, face_names, selectors, revision
    )
    requested_supports, unresolved_supports = _resolve_face_names(
        faces, support_face_names, None, revision
    )
    unresolved.extend(unresolved_supports)
    adjacency = _adjacency(analysis)
    topological = analysis.get("topological_relations") or {}
    tangent_pairs = topological.get("tangent_face_pairs", [])
    tangent_graph = _relation_graph(tangent_pairs)

    if requested:
        groups = _connected_groups(requested, adjacency)
    else:
        groups = _discover_fillet_groups(faces, tangent_graph, tolerance)

    candidates = []
    for index, transition_group in enumerate(groups, start=1):
        transitions = tuple(sorted(transition_group))
        supports = _support_faces(
            faces,
            transitions,
            adjacency,
            tangent_graph,
            requested_supports,
        )
        candidate = _candidate_group(
            faces,
            transitions,
            supports,
            adjacency,
            tangent_graph,
            tolerance,
            radius_mm=radius_mm,
            group_id="fillet-predecessor-%03d" % index,
            unresolved=unresolved,
        )
        candidates.append(candidate)

    warnings = []
    if unresolved:
        warnings.append("one_or_more_revision_bound_selectors_unresolved")
    if not candidates:
        warnings.append("no_complete_fillet_transition_group_detected")
    return {
        "mode": "read_only_virtual_predecessor",
        "feature_family": "fillet",
        "model_revision": revision,
        "available": bool(candidates),
        "source_unchanged": True,
        "mutation_performed": False,
        "bounds_method": "observed_brep_bounds_with_analytic_support_extension",
        "bounds_tightness": "not_guaranteed_for_trimmed_geometry",
        "selector_resolution": {
            "requested_faces": sorted(requested),
            "requested_support_faces": sorted(requested_supports),
            "unresolved": sorted(set(unresolved)),
        },
        "candidate_groups": candidates,
        "warnings": warnings,
        "contract": {
            "predecessor_bounds_are_candidates": True,
            "trimmed_face_bounds_are_not_primitive_dimensions": True,
            "requires_forward_regeneration_before_write": True,
            "source_shape_was_not_defeatured": True,
        },
    }


def _candidate_group(
    faces,
    transitions,
    supports,
    adjacency,
    tangent_graph,
    tolerance,
    *,
    radius_mm,
    group_id,
    unresolved,
):
    transition_faces = [faces[name] for name in transitions if name in faces]
    support_faces = [faces[name] for name in supports if name in faces]
    observed = _bbox_union(
        [face.get("bounding_box_mm") for face in transition_faces]
    )
    support_bounds = _bbox_union(
        [face.get("bounding_box_mm") for face in support_faces]
    )
    counter = []
    if unresolved:
        counter.append("unresolved_revision_bound_selector")
    if len(support_faces) < 2:
        counter.append("insufficient_support_surfaces")
    if any(
        face.get("surface_type") not in _BLEND_SURFACES
        for face in transition_faces
    ):
        counter.append("transition_surface_not_compatible")
    touched_supports = {
        neighbour
        for name in transitions
        for neighbour in adjacency.get(name, ())
    }
    if any(name not in touched_supports for name in supports):
        counter.append("wrong_support_extrapolation")
    if any(face.get("surface_type") not in ("Plane", "Cylinder") for face in support_faces):
        counter.append("non_planar_support_surface")

    law, delta, law_counter = _parameter_law(
        transition_faces,
        tolerance,
        radius_mm=radius_mm,
    )
    counter.extend(law_counter)

    support_constraints = []
    planar_supports = []
    for support in support_faces:
        surface = support.get("surface") or {}
        axis = surface.get("axis")
        if support.get("surface_type") == "Plane" and _unit_vector(axis):
            planar_supports.append(support)
            support_constraints.append(
                {
                    "support_face": support["name"],
                    "surface_type": "Plane",
                    "normal": axis,
                    "plane_position": surface.get("position"),
                    "extension_axes": _extension_axes(axis),
                    "delta_mm": delta,
                    "method": "analytic_support_plane_extrapolation",
                }
            )
        elif support.get("surface_type") == "Cylinder":
            support_constraints.append(
                {
                    "support_face": support["name"],
                    "surface_type": "Cylinder",
                    "axis": axis,
                    "radius_mm": (surface or {}).get("radius"),
                    "extension_axes": [],
                    "delta_mm": delta,
                    "method": "cylindrical_support_observation_only",
                }
            )

    predecessor = None
    method = "insufficient_support_for_extrapolation"
    if observed and planar_supports and delta is not None:
        predecessor = _expand_for_supports(
            _bbox_union([observed, support_bounds]), planar_supports, delta
        )
        method = "support_surface_extension"
    else:
        counter.append("ambiguous_predecessor_extent")

    transition_radius_values = _transition_values(transition_faces)
    confidence = "low"
    if (
        predecessor
        and len(planar_supports) >= 2
        and not any(
            item in counter
            for item in (
                "unresolved_revision_bound_selector",
                "insufficient_support_surfaces",
                "wrong_support_extrapolation",
                "transition_surface_not_compatible",
                "variable_radius_or_distance",
                "missing_radius_or_distance",
            )
        )
    ):
        confidence = "high" if len(set(_support_touch_count(transitions, supports, adjacency, tangent_graph))) == 1 else "medium"
    elif supports:
        confidence = "medium"

    return {
        "group_id": group_id,
        "transition_faces": list(transitions),
        "support_faces": list(supports),
        "observed_bounds": observed,
        "support_bounds": support_bounds,
        "inferred_predecessor_bounds": predecessor,
        "inference_method": method,
        "parameter_law": law,
        "transition_values_mm": transition_radius_values,
        "support_constraints": support_constraints,
        "confidence": confidence,
        "counter_evidence": sorted(set(counter)),
        "evidence": {
            "transition_adjacency": {
                name: sorted(set(adjacency.get(name, ())) & set(supports))
                for name in transitions
            },
            "requires_sharp_predecessor_intersection": True,
            "observed_transition_faces_are_trimmed": True,
        },
    }


def _parameter_law(
    faces,
    tolerance,
    *,
    radius_mm,
):
    values = _transition_values(faces)
    counter = []
    supplied = _positive(radius_mm)
    values = [supplied] * len(faces) if supplied is not None else values
    if not values:
        counter.append("missing_radius_or_distance")
        return {"kind": "unknown"}, None, counter
    reference = sum(values) / len(values)
    if max(abs(value - reference) for value in values) <= tolerance:
        return {
            "kind": "constant",
            "radius_mm": _round(reference),
        }, reference, counter
    counter.append("variable_radius_or_distance")
    return {
        "kind": "multi_radius",
        "stations": [
            {
                "position": _round(index / max(1, len(values) - 1)),
                "radius_mm": _round(value),
            }
            for index, value in enumerate(values)
        ],
    }, reference, counter


def _transition_values(faces):
    values = []
    for face in faces:
        surface = face.get("surface") or {}
        value = (
            surface.get("minor_radius")
            if face.get("surface_type") == "Toroid"
            else surface.get("radius")
        )
        try:
            value = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(value) and value > 0:
            values.append(value)
    return values


def _discover_fillet_groups(faces, tangent_graph, tolerance):
    # Toroids/Spheres are direct blend carriers.  Cylinders are admitted only
    # when their radius follows an adjacent carrier or when a cylinder-only
    # edge blend has two explicit non-blend tangent supports.  A distinct
    # radius cylinder touching two carriers is a boss/support wall and must
    # remain a predecessor support rather than a transition face.
    seed_names = {
        name
        for name, face in faces.items()
        if face.get("surface_type") in {"Toroid", "Sphere"}
        and _blend_radius(face) is not None
    }
    blend_names = set(seed_names)
    for name, face in faces.items():
        if face.get("surface_type") != "Cylinder":
            continue
        radius = _blend_radius(face)
        if radius is None:
            continue
        neighbours = set(tangent_graph.get(name, ()))
        seed_neighbours = neighbours & seed_names
        if seed_neighbours:
            non_blend_supports = {
                neighbour
                for neighbour in neighbours
                if neighbour not in seed_names
                and faces.get(neighbour, {}).get("surface_type") not in _BLEND_SURFACES
            }
            if any(
                abs(radius - float(_blend_radius(faces[seed]) or 0.0)) <= tolerance
                for seed in seed_neighbours
            ) or (len(seed_neighbours) == 1 and non_blend_supports):
                blend_names.add(name)
            continue
        non_blend_supports = {
            neighbour
            for neighbour in neighbours
            if neighbour not in seed_names
            and faces.get(neighbour, {}).get("surface_type") not in _BLEND_SURFACES
        }
        if len(non_blend_supports) >= 2:
            blend_names.add(name)
    groups = []
    remaining = set(blend_names)
    blend_graph = {
        name: set(tangent_graph.get(name, ())) & blend_names
        for name in blend_names
    }
    while remaining:
        start = min(remaining)
        # Do not traverse through a non-transition support wall.  A shared
        # boss cylinder intentionally separates its lower and upper Fillets.
        component = _component(start, blend_graph) & blend_names
        remaining.difference_update(component)
        has_support = any(
            neighbour not in blend_names
            for name in component
            for neighbour in tangent_graph.get(name, ())
        )
        if component and has_support:
            groups.append(component)
    return groups


def _support_faces(faces, transitions, adjacency, relation_graph, requested):
    if requested:
        return tuple(sorted(name for name in requested if name in faces and name not in transitions))
    supports = set()
    for name in transitions:
        neighbours = set(relation_graph.get(name, ())) | set(adjacency.get(name, ()))
        for neighbour in neighbours:
            if neighbour in transitions or neighbour not in faces:
                continue
            if (
                neighbour in relation_graph.get(name, ())
                or faces[neighbour].get("surface_type") in {"Plane", "Cylinder"}
            ):
                supports.add(neighbour)
    return tuple(sorted(supports))


def _resolve_face_names(faces, face_names, selectors, revision):
    resolved = set()
    unresolved = []
    if face_names is not None:
        if not isinstance(face_names, (list, tuple)):
            raise ValueError("face_names must be a list")
        for name in face_names:
            if not isinstance(name, str) or name not in faces:
                unresolved.append(str(name))
            else:
                resolved.add(name)
    if selectors is not None:
        if not isinstance(selectors, (list, tuple)):
            raise ValueError("selectors must be a list")
        for selector in selectors:
            if not isinstance(selector, dict) or selector.get("kind") != "face":
                unresolved.append("invalid_selector")
                continue
            matches = [
                name
                for name, face in faces.items()
                if face.get("signature") == selector.get("signature")
            ]
            if len(matches) != 1:
                unresolved.append(str(selector.get("signature", "missing_signature")))
            else:
                resolved.add(matches[0])
    return resolved, unresolved


def _adjacency(analysis):
    value = analysis.get("adjacency") or analysis.get("face_adjacency") or {}
    return {
        name: set(neighbours)
        for name, neighbours in value.items()
        if isinstance(name, str) and isinstance(neighbours, (list, tuple, set))
    }


def _relation_graph(relations):
    graph = {}
    for relation in relations or ():
        names = relation.get("faces", []) if isinstance(relation, dict) else []
        if len(names) != 2 or not all(isinstance(name, str) for name in names):
            continue
        left, right = names
        graph.setdefault(left, set()).add(right)
        graph.setdefault(right, set()).add(left)
    return graph


def _connected_groups(names, graph):
    remaining = set(names)
    groups = []
    while remaining:
        start = min(remaining)
        group = _component(start, graph) & remaining
        remaining.difference_update(group)
        groups.append(group)
    return groups


def _component(start, graph):
    found = {start}
    pending = [start]
    while pending:
        current = pending.pop()
        for neighbour in graph.get(current, ()):
            if neighbour not in found:
                found.add(neighbour)
                pending.append(neighbour)
    return found


def _blend_radius(face):
    surface = face.get("surface") or {}
    value = (
        surface.get("minor_radius")
        if face.get("surface_type") == "Toroid"
        else surface.get("radius")
    )
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) and value > 0 else None


def _unit_vector(value):
    if not isinstance(value, dict):
        return None
    try:
        vector = [float(value[axis]) for axis in _AXES]
    except (KeyError, TypeError, ValueError):
        return None
    length = math.sqrt(sum(component * component for component in vector))
    if not math.isfinite(length) or length <= 1e-12:
        return None
    return tuple(component / length for component in vector)


def _extension_axes(axis):
    vector = _unit_vector(axis)
    if vector is None:
        return []
    return [name for name, component in zip(_AXES, vector) if abs(component) >= 0.5]


def _support_touch_count(transitions, supports, adjacency, relation_graph):
    support_set = set(supports)
    return [
        len((set(adjacency.get(name, ())) | set(relation_graph.get(name, ()))) & support_set)
        for name in transitions
    ]


def _bbox_union(boxes):
    boxes = [box for box in boxes if isinstance(box, dict)]
    if not boxes:
        return None
    result = {}
    for axis in _AXES:
        lows = [float(box[axis + "_min"]) for box in boxes if axis + "_min" in box]
        lengths = [float(box[axis + "_length"]) for box in boxes if axis + "_length" in box]
        if not lows or not lengths:
            return None
        low = min(lows)
        high = max(low_value + length for low_value, length in zip(lows, lengths))
        result[axis + "_min"] = low
        result[axis + "_length"] = high - low
    return result


def _expand_for_supports(box, supports, delta):
    if not box or delta is None:
        return None
    result = dict(box)
    axes = set()
    for support in supports:
        surface = support.get("surface") or {}
        axis_vector = _unit_vector(surface.get("axis"))
        for axis in _extension_axes(surface.get("axis")):
            axes.add(axis)
            position = surface.get("position")
            coordinate = None
            if isinstance(position, dict):
                try:
                    coordinate = float(position[axis])
                except (KeyError, TypeError, ValueError):
                    coordinate = None
            low_key = axis + "_min"
            length_key = axis + "_length"
            low = float(result[low_key])
            high = low + float(result[length_key])
            if coordinate is not None and axis_vector is not None:
                if coordinate <= (low + high) / 2.0:
                    result[low_key] = min(low, coordinate)
                    result[length_key] = max(high, high + delta) - result[low_key]
                elif coordinate >= (low + high) / 2.0:
                    result[low_key] = min(low - delta, low)
                    result[length_key] = max(high, coordinate) - result[low_key]
                continue
            result[low_key] = low - delta
            result[length_key] = float(result[length_key]) + 2.0 * delta
    return result


def _positive(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if isinstance(value, bool) or not math.isfinite(number) or number <= 0:
        return None
    return number


def _positive_or_default(value, default):
    number = _positive(value)
    return default if number is None else number


def _round(value):
    return float(round(float(value), 6))
