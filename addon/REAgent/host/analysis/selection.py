"""Geometry queries and strict current subelement resolution."""

import math

from REAgent.host.numeric import _finite_number, _positive_number
from REAgent.host.analysis.references import _normalize_semantic_entity_ref


def _reject_unknown_parameters(parameters, allowed):
    unknown = sorted(set(parameters) - allowed)
    if unknown:
        raise ValueError("Unknown feature parameters: %s" % ", ".join(unknown))


def _require_nonzero_vector(x_value, y_value, z_value, field):
    if math.sqrt(x_value ** 2 + y_value ** 2 + z_value ** 2) <= 1e-12:
        raise ValueError("%s must be non-zero" % field)


def _dressup_edge_queries(value):
    if value is None:
        return []
    if not isinstance(value, list) or not value or len(value) > 64:
        raise ValueError("parameters.edge_queries must contain 1 to 64 queries")
    normalized = []
    for index, query in enumerate(value):
        prefix = "parameters.edge_queries[%d]" % index
        if not isinstance(query, dict):
            raise ValueError(prefix + " must be an object")
        _reject_unknown_parameters(
            query,
            {"curve_type", "center_mm", "direction", "length_mm", "tolerance_mm"},
        )
        curve_type = query.get("curve_type", "Line")
        if curve_type not in ("Line", "Circle"):
            raise ValueError(prefix + ".curve_type must be Line or Circle")
        center = query.get("center_mm")
        direction = query.get("direction")
        if not isinstance(center, list) or len(center) != 3:
            raise ValueError(prefix + ".center_mm must be [x, y, z]")
        if not isinstance(direction, list) or len(direction) != 3:
            raise ValueError(prefix + ".direction must be [x, y, z]")
        center = [
            _finite_number(component, prefix + ".center_mm")
            for component in center
        ]
        direction = [
            _finite_number(component, prefix + ".direction")
            for component in direction
        ]
        _require_nonzero_vector(
            direction[0], direction[1], direction[2], prefix + ".direction"
        )
        normalized.append(
            {
                "curve_type": curve_type,
                "center_mm": center,
                "direction": direction,
                "length_mm": _positive_number(
                    query.get("length_mm"), prefix + ".length_mm"
                ),
                "tolerance_mm": _positive_number(
                    query.get("tolerance_mm", 0.02), prefix + ".tolerance_mm"
                ),
            }
        )
    return normalized


def _dressup_edge_selectors(value):
    if value is None:
        return []
    if not isinstance(value, list) or not value or len(value) > 64:
        raise ValueError("parameters.edge_selectors must contain 1 to 64 selectors")
    normalized = []
    for index, selector in enumerate(value):
        if not isinstance(selector, dict):
            raise ValueError(
                "parameters.edge_selectors[%d] must be an object" % index
            )
        if set(selector) != {"kind", "model_revision", "signature"}:
            raise ValueError(
                "Each edge selector must contain kind, model_revision, and signature"
            )
        if selector.get("kind") != "edge":
            raise ValueError("Dress-up selectors must have kind=edge")
        model_revision = selector.get("model_revision")
        signature = selector.get("signature")
        if not isinstance(model_revision, str) or not model_revision:
            raise ValueError("Dress-up selector model_revision must be non-empty")
        if not isinstance(signature, str) or not signature:
            raise ValueError("Dress-up selector signature must be non-empty")
        normalized.append(
            {
                "kind": "edge",
                "model_revision": model_revision,
                "signature": signature,
            }
        )
    return normalized


def _dressup_face_selectors(value):
    if value is None:
        return []
    if not isinstance(value, list) or not value or len(value) > 64:
        raise ValueError("parameters.face_selectors must contain 1 to 64 selectors")
    normalized = []
    for index, selector in enumerate(value):
        prefix = "parameters.face_selectors[%d]" % index
        if not isinstance(selector, dict) or set(selector) != {
                "kind", "model_revision", "signature"}:
            raise ValueError(prefix + " must contain kind, model_revision and signature")
        if selector["kind"] != "face":
            raise ValueError(prefix + ".kind must be face")
        if not isinstance(selector["model_revision"], str) or not selector["model_revision"]:
            raise ValueError(prefix + ".model_revision must be non-empty")
        if not isinstance(selector["signature"], str) or not selector["signature"]:
            raise ValueError(prefix + ".signature must be non-empty")
        normalized.append(dict(selector))
    return normalized


def _feature_semantic_refs(
    value, expected_kind, expected_intended_role, parameter_name, maximum
):
    if value is None:
        return []
    if not isinstance(value, list) or not value or len(value) > maximum:
        raise ValueError(
            "parameters.%s must contain 1 to %d references"
            % (parameter_name, maximum)
        )
    normalized = []
    for index, reference in enumerate(value):
        prefix = "parameters.%s[%d]" % (parameter_name, index)
        normalized_ref = _normalize_semantic_entity_ref(
            reference,
            expected_kind=expected_kind,
            expected_owner_role="rebuild",
            expected_intended_role=expected_intended_role,
            prefix=prefix,
        )
        normalized.append(normalized_ref)
    return normalized


def _edge_query_direction(edge):
    curve = edge.get("curve", {})
    axis = curve.get("axis")
    if isinstance(axis, dict):
        return (float(axis["x"]), float(axis["y"]), float(axis["z"]))
    direction = curve.get("direction")
    if isinstance(direction, dict):
        return (float(direction["x"]), float(direction["y"]), float(direction["z"]))
    endpoints = edge.get("endpoints_mm", [])
    if len(endpoints) == 2:
        return (
            float(endpoints[1]["x"]) - float(endpoints[0]["x"]),
            float(endpoints[1]["y"]) - float(endpoints[0]["y"]),
            float(endpoints[1]["z"]) - float(endpoints[0]["z"]),
        )
    return None


def _edge_query_measurements(query, edge, angular_tolerance_deg):
    """Measure one submitted query against one edge without selecting it."""

    curve_type_matches = edge.get("curve_type") == query["curve_type"]
    center = edge.get("center_mm", {})
    try:
        center_delta = math.sqrt(
            sum(
                (float(center[axis]) - query["center_mm"][index]) ** 2
                for index, axis in enumerate(("x", "y", "z"))
            )
        )
    except (KeyError, TypeError, ValueError):
        center_delta = None
    tolerance = max(float(query["tolerance_mm"]), 1e-7)
    try:
        length_delta = abs(
            float(edge.get("length_mm")) - query["length_mm"]
        )
    except (TypeError, ValueError):
        length_delta = None
    edge_direction = _edge_query_direction(edge)
    left_length = math.sqrt(
        sum(component * component for component in edge_direction)
    ) if edge_direction is not None else 0.0
    right_length = math.sqrt(sum(component * component for component in query["direction"]))
    if left_length <= 1e-12 or right_length <= 1e-12:
        direction_delta = None
    else:
        cosine = abs(
            sum(
                edge_direction[index] * query["direction"][index]
                for index in range(3)
            )
            / (left_length * right_length)
        )
        cosine = max(-1.0, min(1.0, cosine))
        direction_delta = math.degrees(math.acos(cosine))
    angular_tolerance = max(float(angular_tolerance_deg), 1e-6)
    within_tolerance = bool(
        curve_type_matches
        and center_delta is not None
        and center_delta <= tolerance
        and length_delta is not None
        and length_delta <= tolerance
        and direction_delta is not None
        and direction_delta <= angular_tolerance
    )
    score = (
        (0.0 if curve_type_matches else 1000.0)
        + (center_delta / tolerance if center_delta is not None else 1000000.0)
        + (length_delta / tolerance if length_delta is not None else 1000000.0)
        + (
            direction_delta / angular_tolerance
            if direction_delta is not None
            else 1000000.0
        )
    )
    return {
        "name": edge.get("name"),
        "score": float(score),
        "within_tolerance": within_tolerance,
        "deltas": {
            "curve_type_matches": curve_type_matches,
            "center_mm": center_delta,
            "length_mm": length_delta,
            "direction_deg": direction_delta,
        },
        "tolerances": {
            "center_mm": tolerance,
            "length_mm": tolerance,
            "direction_deg": angular_tolerance,
        },
    }


def _bounded_nearest(measurements, limit):
    return sorted(
        measurements, key=lambda item: (item["score"], item.get("name") or "")
    )[:limit]


def _edge_query_resolution(
    query, index, evidence, tolerance_contract, nearest_limit, used_names
):
    measurements = [
        _edge_query_measurements(
            query,
            edge,
            tolerance_contract["angular_tolerance_deg"],
        )
        for edge in evidence["edges"]
    ]
    matching = [item for item in measurements if item["within_tolerance"]]
    duplicate_matches = [
        item["name"]
        for item in matching
        if item["name"] in used_names
    ]
    if len(matching) == 1 and duplicate_matches:
        matching = []
    status = (
        "matched" if len(matching) == 1
        else "missing" if not matching
        else "ambiguous"
    )
    return {
        "input": "edge_queries[%d]" % index,
        "input_kind": "edge_query",
        "input_index": index,
        "submitted": query,
        "status": status,
        "match_count": len(matching),
        "match_names": [item["name"] for item in matching],
        "match_scores": [item["score"] for item in matching],
        "reason": "duplicate_match" if not matching and duplicate_matches else None,
        "already_matched_names": duplicate_matches,
        "tolerances": {
            "linear_mm": float(query["tolerance_mm"]),
            "angular_deg": float(
                tolerance_contract["angular_tolerance_deg"]
            ),
        },
        "nearest_candidates": _bounded_nearest(measurements, nearest_limit),
    }


def _edge_selector_resolution(
    selector, index, evidence, nearest_limit, used_names
):
    stable = evidence["stable_selection"]
    current_revision = stable["model_revision"]
    signature_names = list(
        stable["edge_signatures"].get(selector["signature"], [])
    )
    if selector["model_revision"] != current_revision:
        signature_names = []
    duplicate_matches = [name for name in signature_names if name in used_names]
    matches = signature_names
    if len(matches) == 1 and duplicate_matches:
        matches = []
    status = (
        "matched" if len(matches) == 1
        else "missing" if not matches
        else "ambiguous"
    )
    nearest = [
        {
            "name": name,
            "score": 0.0,
            "within_tolerance": True,
            "deltas": {
                "signature_matches": True,
            },
        }
        for name in signature_names[:nearest_limit]
    ]
    reason = "revision_mismatch" if selector["model_revision"] != current_revision else None
    if not matches and duplicate_matches:
        reason = "duplicate_match"
    return {
        "input": "edge_selectors[%d]" % index,
        "input_kind": "stable_selector",
        "input_index": index,
        "submitted": selector,
        "status": status,
        "match_count": len(matches),
        "match_names": matches,
        "match_scores": [0.0 for _name in matches],
        "reason": reason,
        "already_matched_names": duplicate_matches,
        "expected": {
            "model_revision": selector["model_revision"],
            "signature": selector["signature"],
        },
        "actual_model_revision": current_revision,
        "nearest_candidates": nearest,
    }


def _reference_candidate_measurement(reference, detail):
    expected = reference["signatures"]
    geometry_matches = detail.get("geometry_signature") == expected["geometry"]
    topology_matches = detail.get("topology_signature") == expected["topology"]
    return {
        "name": detail["name"],
        "score": float((not geometry_matches) + (not topology_matches)),
        "within_tolerance": bool(geometry_matches and topology_matches),
        "deltas": {
            "geometry_signature_matches": geometry_matches,
            "topology_signature_matches": topology_matches,
        },
    }


def _edge_reference_resolution(
    reference,
    index,
    evidence,
    tolerance_contract,
    nearest_limit,
    used_names,
):
    details = list(evidence["edges"])
    current_revision = evidence["stable_selection"]["model_revision"]
    measurements = [
        _reference_candidate_measurement(reference, detail)
        for detail in details
    ]
    exact = [
        item
        for item in measurements
        if item["within_tolerance"]
    ]
    revision_matches = reference["owner"]["revision"] == current_revision
    selected = exact if revision_matches else []
    candidate_count = len(selected)
    matched_by = "geometry_and_topology_signatures" if candidate_count == 1 else None
    nearest = _bounded_nearest(measurements, nearest_limit)
    duplicate_matches = [
        item["name"] for item in selected if item["name"] in used_names
    ]
    if candidate_count == 1 and duplicate_matches:
        candidate_count = 0
        selected = []
        matched_by = None
    status = (
        "matched" if candidate_count == 1
        else "missing" if candidate_count == 0
        else "ambiguous"
    )
    match_names = [item["name"] for item in selected]
    updated_reference = None
    if status == "matched":
        detail = next(item for item in details if item["name"] == match_names[0])
        updated_reference = dict(reference)
        updated_reference["owner"] = {
            **reference["owner"], "role": "rebuild", "revision": current_revision,
        }
        updated_reference["signatures"] = {
            "geometry": detail["geometry_signature"],
            "topology": detail["topology_signature"],
        }
        updated_reference["native_hint"] = {
            "adapter": "freecad",
            "subelement": detail["name"],
        }
        updated_reference["last_resolution"] = {
            "status": "resolved",
            "matched_by": matched_by,
            "subelement": detail["name"],
            "revision": current_revision,
        }
    return {
        "input": "edge_refs[%d]" % index,
        "input_kind": "semantic_reference",
        "input_index": index,
        "submitted": reference,
        "status": status,
        "match_count": candidate_count,
        "match_names": match_names,
        "match_scores": [item["score"] for item in selected],
        "matched_by": matched_by,
        "reason": "revision_mismatch" if not revision_matches else "duplicate_match" if not selected and duplicate_matches else None,
        "already_matched_names": duplicate_matches,
        "expected": {
            "owner": reference["owner"],
            "signatures": reference["signatures"],
        },
        "actual_model_revision": current_revision,
        "nearest_candidates": nearest,
        "updated_reference": updated_reference,
    }


def _resolve_dressup_selection(
    evidence, parameters, tolerance_contract, nearest_limit=3
):
    """Resolve explicit edge inputs and return per-input read-only diagnostics."""

    if (
        isinstance(nearest_limit, bool)
        or not isinstance(nearest_limit, int)
        or nearest_limit < 1
        or nearest_limit > 5
    ):
        raise ValueError("nearest_limit must be an integer from 1 to 5")
    queries = list(parameters.get("edge_queries", []))
    selectors = list(parameters.get("edge_selectors", []))
    references = list(parameters.get("edge_refs", []))
    edge_scope = parameters.get("edge_scope")
    if references:
        mode = "semantic_refs"
        values = references
    elif edge_scope == "all":
        mode = "all"
        values = ["all"]
    elif selectors:
        mode = "stable_selectors"
        values = selectors
    else:
        mode = "edge_queries"
        values = queries

    used_names = set()
    results = []
    updated_references = []
    for index, value in enumerate(values):
        if mode == "edge_queries":
            result = _edge_query_resolution(
                value,
                index,
                evidence,
                tolerance_contract,
                nearest_limit,
                used_names,
            )
        elif mode == "stable_selectors":
            result = _edge_selector_resolution(
                value, index, evidence, nearest_limit, used_names
            )
        elif mode == "semantic_refs":
            result = _edge_reference_resolution(
                value,
                index,
                evidence,
                tolerance_contract,
                nearest_limit,
                used_names,
            )
            if result["updated_reference"] is not None:
                updated_references.append(result["updated_reference"])
        else:
            names = [edge["name"] for edge in evidence["edges"]]
            result = {
                "input": "edge_scope",
                "input_kind": "literal_scope",
                "input_index": 0,
                "submitted": "all",
                "status": "matched" if names else "missing",
                "match_count": len(names),
                "match_names": names,
                "match_scores": [0.0 for _name in names],
                "reason": None if names else "predecessor_has_no_edges",
                "nearest_candidates": [],
            }
        if result["status"] == "matched":
            used_names.update(result["match_names"])
        results.append(result)

    all_resolved = bool(results) and all(
        result["status"] == "matched" for result in results
    )
    matched_names = [
        name
        for result in results
        if result["status"] == "matched"
        for name in result["match_names"]
    ]
    return {
        "selection_mode": mode,
        "model_revision": evidence["stable_selection"]["model_revision"],
        "read_only": True,
        "all_resolved": all_resolved,
        "input_count": len(results),
        "matched_names": matched_names,
        "results": results,
        "updated_references": updated_references,
    }


def _resolve_face_selection(evidence, parameters, role, nearest_limit=3):
    """Resolve explicit current faces without choosing among similar candidates."""
    if role not in ("draft_target", "draft_neutral", "thickness_opening",
                    "mirror_plane"):
        raise ValueError("Unknown face selection role")
    if (isinstance(nearest_limit, bool) or not isinstance(nearest_limit, int)
            or nearest_limit < 1 or nearest_limit > 5):
        raise ValueError("nearest_limit must be an integer from 1 to 5")
    selectors = list(parameters.get("face_selectors", []))
    references = list(parameters.get("face_refs", []))
    if bool(selectors) == bool(references):
        raise ValueError("Face selection requires exactly one of selectors or refs")
    mode = "semantic_refs" if references else "stable_selectors"
    values = references if references else selectors
    details = list(evidence["faces"])
    current_revision = evidence["stable_selection"]["model_revision"]
    used_names = set()
    results = []
    for index, value in enumerate(values):
        if mode == "stable_selectors":
            candidates = list(evidence["stable_selection"]["face_signatures"].get(
                value["signature"], []))
            revision_matches = value["model_revision"] == current_revision
            matches = candidates if revision_matches else []
            matched_by = "current_face_signature" if len(matches) == 1 else None
            input_name = "face_selectors[%d]" % index
            expected = {"model_revision": value["model_revision"],
                        "signature": value["signature"]}
            nearest = [{"name": name, "score": 0.0} for name in candidates[:nearest_limit]]
        else:
            revision_matches = value["owner"]["revision"] == current_revision
            measurements = [_reference_candidate_measurement(value, detail)
                            for detail in details]
            candidates = [item["name"] for item in measurements
                          if item["within_tolerance"]]
            matches = candidates if revision_matches else []
            matched_by = ("geometry_and_topology_signatures"
                          if len(matches) == 1 else None)
            input_name = "face_refs[%d]" % index
            expected = {"owner": value["owner"], "signatures": value["signatures"]}
            nearest = _bounded_nearest(measurements, nearest_limit)
        duplicates = [name for name in matches if name in used_names]
        if len(matches) == 1 and duplicates:
            matches = []
            matched_by = None
        status = "matched" if len(matches) == 1 else "missing" if not matches else "ambiguous"
        if status == "matched":
            used_names.add(matches[0])
        results.append({
            "input": input_name, "input_kind": mode, "input_index": index,
            "submitted": value, "status": status, "match_count": len(matches),
            "match_names": matches, "matched_by": matched_by,
            "reason": ("revision_mismatch" if not revision_matches else
                       "duplicate_match" if duplicates else None),
            "expected": expected, "actual_model_revision": current_revision,
            "nearest_candidates": nearest,
        })
    all_resolved = all(row["status"] == "matched" for row in results)
    return {
        "selection_mode": mode, "selection_kind": "face", "face_role": role,
        "model_revision": current_revision, "read_only": True,
        "all_resolved": all_resolved, "input_count": len(results),
        "matched_names": [name for row in results for name in row["match_names"]],
        "results": results,
    }


class SelectionResolutionError(ValueError):
    def __init__(self, report):
        failed = next(
            result
            for result in report["results"]
            if result["status"] != "matched"
        )
        self.diagnostics = {
            "kind": "edge_selection_resolution",
            "selection_mode": report["selection_mode"],
            "model_revision": report["model_revision"],
            "failed_input": failed["input"],
            "submitted": failed["submitted"],
            "status": failed["status"],
            "match_count": failed["match_count"],
            "expected": failed.get("expected"),
            "tolerances": failed.get("tolerances"),
            "nearest_candidates": failed.get("nearest_candidates", []),
        }
        super().__init__(
            "%s matched %d predecessor edges"
            % (failed["input"], failed["match_count"])
        )


class FaceSelectionResolutionError(ValueError):
    def __init__(self, report):
        failed = next(row for row in report["results"] if row["status"] != "matched")
        self.diagnostics = {
            "kind": "face_selection_resolution",
            "face_role": report["face_role"],
            "model_revision": report["model_revision"],
            "failed_input": failed["input"],
            "status": failed["status"],
            "reason": failed["reason"],
            "match_count": failed["match_count"],
            "expected": failed["expected"],
            "nearest_candidates": failed["nearest_candidates"],
        }
        super().__init__("%s matched %d predecessor faces" %
                         (failed["input"], failed["match_count"]))
