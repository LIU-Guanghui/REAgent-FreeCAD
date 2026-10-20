"""Explicit bounded journal facts; detailed interfaces are returned separately."""

import math

from .contract import DRESSUP_SCOPE, IMAGE_OBSERVATION_KINDS, PRIMITIVE_COMMANDS


def _pick(value, fields):
    return {key: value[key] for key in fields if key in value} if isinstance(value, dict) else {}


def _rows(values, fields, limit):
    values = values if isinstance(values, list) else []
    return {"items": [_pick(value, fields) for value in values[:limit]],
            "count": len(values), "omitted": max(0, len(values) - limit)}


def reference_summary(reference):
    summary = _pick(reference, ("kind", "role", "object_name", "datum_name", "coordinate_system_name"))
    if not isinstance(reference, dict):
        return summary
    obj = reference.get("object")
    if isinstance(obj, str):
        summary["object"] = obj
    elif isinstance(obj, dict):
        summary["object"] = _pick(obj, ("object", "object_name", "role"))
    if "subelements" in reference:
        summary["subelements"] = reference["subelements"][:64]
        summary["subelements_omitted"] = max(0, len(reference["subelements"]) - 64)
    owner = _pick(reference.get("owner"), ("role", "object_name"))
    if owner:
        summary["owner"] = owner
    for key in ("face_ref", "edge_ref"):
        child = reference.get(key)
        if isinstance(child, dict):
            summary[key] = {**_pick(child, ("kind", "role", "object_name")),
                            "owner": _pick(child.get("owner"), ("role", "object_name"))}
    return summary


def _result_geometry_summary(geometry):
    return None if geometry is None else _pick(geometry, (
        "shape_type", "is_valid", "solid_count", "volume_mm3", "area_mm2",
        "bounding_box_mm", "bounding_box_method", "bounding_box_tightness"))


def execution_summary(value):
    return {**_pick(value, ("state", "status", "result_body", "tip_before", "tip", "transaction")),
            "result_geometry": _result_geometry_summary(value["result_geometry"])}


def history_summary(value):
    return {**_pick(value, ("status", "state", "transaction", "result", "result_body", "tip")),
            "result_geometry": _result_geometry_summary(value["result_geometry"])}


_RECOVERY_FIELDS = ("checked_at", "execution_status", "document_restored", "model_unchanged",
                    "result_geometry_unchanged", "history_restored", "presentation_restored",
                    "restoration_confirmed", "restored", "target_writes_blocked", "blocked",
                    "temporary_cleanup_error_count", "presentation_restore_error_count",
                    "temporary_cleanup_errors_omitted", "presentation_restore_errors_omitted",
                    "residual_file_count", "residual_files_omitted", "restoration_error_count",
                    "restoration_errors_omitted")


def recovery_summary(value):
    summary = _pick(value, _RECOVERY_FIELDS)
    if isinstance(value, dict):
        for key, limit in (("restoration_errors", 8), ("residual_files", 8),
                           ("temporary_cleanup_errors", 8), ("presentation_restore_errors", 8)):
            if isinstance(value.get(key), list):
                summary[key] = value[key][:limit]
    return summary


def selection_summary(value):
    summary = _pick(value, ("all_resolved", "input_count", "selection_mode", "selection_kind", "face_role",
                            "predecessor", "body", "status", "failed_input", "match_count", "reason"))
    if not isinstance(value, dict):
        return summary
    names = value.get("matched_names", [])
    summary.update(matched_names=names[:DRESSUP_SCOPE["edge_limit"]], matched_count=len(names),
                   matched_names_omitted=max(0, len(names) - DRESSUP_SCOPE["edge_limit"]))
    rows = []
    for row in value.get("results", [])[:64]:
        detail = _pick(row, ("input", "input_index", "status", "match_count", "matched_by", "reason"))
        detail["match_names"] = row.get("match_names", [])[:64]
        detail["match_names_omitted"] = max(0, len(row.get("match_names", [])) - 64)
        rows.append(detail)
    summary.update(results=rows, results_omitted=max(0, len(value.get("results", [])) - 64))
    return summary


def observation_request_summary(request):
    kind = request.get("kind")
    query = request.get("query", {})
    kind = kind if isinstance(kind, str) else None
    query = query if isinstance(query, dict) else {}
    fields = {
        "global": (), "entity": ("object", "fields", "offset", "limit", "include"),
        "selection_resolution": ("body_name", "edge_scope", "edge_role", "face_role", "nearest_limit"),
        "regions": ("offset", "limit"), "points": (), "residual": (),
        "support_extension": ("radius_mm",),
        "initial_views": ("width", "height"),
        "view": ("width", "height", "views", "paired", "include_section", "section_axis",
                 "projection", "display_mode", "transparency", "focus_box_mm"),
    }
    summary = {"kind": kind}
    for key, value in _pick(query, fields.get(kind, ())).items():
        if isinstance(value, (str, bool, int)):
            summary[key] = value[:200] if isinstance(value, str) else value
        elif isinstance(value, float) and math.isfinite(value):
            summary[key] = value
        elif isinstance(value, list):
            summary[key] = [item[:80] for item in value[:16] if isinstance(item, str)]
            summary[key + "_count"] = len(value)
            summary[key + "_omitted"] = max(0, len(value) - 16)
        elif key == "focus_box_mm" and isinstance(value, dict):
            summary[key] = {axis: item for axis, item in _pick(value, (
                "x_min", "x_max", "y_min", "y_max", "z_min", "z_max")).items()
                if isinstance(item, (int, float)) and math.isfinite(item)}
    for key in ("edge_queries", "edge_selectors", "edge_refs", "face_selectors", "face_refs", "faces", "edges", "points", "selectors"):
        if isinstance(query.get(key), list):
            summary[key + "_count"] = len(query[key])
    return summary


def observation_summary(request, result):
    summary = {"observation": observation_request_summary(request), "evidence_id": result["evidence_id"]}
    summary.update(_pick(result, ("pagination", "residual_summary", "views", "board", "display_objects",
                                 "evidence_roles", "evidence_status", "geometry_scope", "target_geometry_scope",
                                 "image_delivery",
                                 "geometry_available", "geometry_unavailable_reason", "writable_references_available")))
    if "shape" in result:
        summary["shape"] = _pick(result["shape"], ("is_valid", "solid_count", "face_count", "edge_count",
                                                   "volume_mm3", "area_mm2", "bounding_box_mm", "bounding_box_method", "bounding_box_tightness"))
    if request.get("kind") == "residual":
        summary["operations"] = {direction: _pick(row, ("operation", "method", "status", "available", "reason",
            "exception_type", "calls", "elapsed_ms", "is_valid", "valid_empty", "volume_mm3",
            "filtered_component_count", "filtered_volume_mm3")) for direction, row in result.get("operations", {}).items()}
    if request.get("kind") == "selection_resolution":
        summary["selection_resolution"] = selection_summary(result.get("selection_resolution", result))
    if request.get("kind") in IMAGE_OBSERVATION_KINDS:
        delivery = result.get("image_delivery", {})
        summary["image_delivery"] = _pick(delivery, (
            "status", "image_count", "total_bytes", "scope", "inspection", "representation",
            "direction_count", "capture_id", "capture_number", "reason", "exception_type", "limit", "board_limit"))
        summary["image_delivery"]["images"] = [_pick(row, (
            "image_id", "content_index", "artifact_key", "role", "direction", "size_bytes", "width_px", "height_px"))
            for row in delivery.get("images", [])[:8]]
        summary.update(_pick(result, ("requested_display_mode", "normalized_display_mode", "object_display_mode")))
        summary["scale"] = _pick(result.get("scale"), ("shared", "camera_height_mm", "millimeters_per_pixel", "width_px", "height_px", "focus_mm"))
        if "initial_views" in result:
            summary["initial_views"] = _pick(result["initial_views"], ("directions", "direction_count", "complete", "capture_id"))
            summary["boards"] = {key: {"path": row["path"], "layout": _pick(row["layout"], (
                "rows", "columns", "cell_order", "width_px", "height_px"))}
                                 for key, row in result["boards"].items()}
    if "recovery" in result:
        summary["recovery"] = recovery_summary(result["recovery"])
    return summary


def validation_summary(value):
    summary = _pick(value, ("scope", "status", "requested"))
    summary["native"] = _pick(value.get("native"), ("state", "reason", "exception_type"))
    summary["native"]["result_geometry"] = _result_geometry_summary(value["native"]["result_geometry"])
    summary["read_errors"] = {stage: _pick(row, ("reason", "exception_type"))
                              for stage, row in value.get("read_errors", {}).items()}
    summary["checks"] = {name: _pick(row, (
        "status", "available", "reason", "exception_type", "method", "units", "source", "target", "delta",
        "applicability", "applicability_reason",
        "distance_mm", "tightness", "approximate", "sampling_deflection_mm", "symmetric_max_mm",
        "source_to_rebuild_max_mm", "rebuild_to_source_max_mm", "source_sample_count", "rebuild_sample_count",
        "operation", "calls", "elapsed_ms", "is_valid", "valid_empty", "volume_mm3", "proof_of_equivalence"))
        for name, row in value.get("checks", {}).items()}
    summary["conclusions"] = {name: _pick(row, ("available", "valid", "verified", "basis", "unavailable_reason",
        "requested", "attempted_property_count", "successful_property_count", "scope", "in_progress", "all_parameters_verified"))
        for name, row in value.get("conclusions", {}).items()}
    structure = value.get("structure") or {}
    summary["structure"] = {**_pick(structure, ("status", "passed", "in_progress")),
        "failures": _rows(structure.get("failures"), ("object", "reason", "type_id", "kind", "message", "state"), 16)}
    audit = value.get("parameter_audit", {})
    summary["parameter_audit"] = {**_pick(audit, ("status", "reason", "property_count", "scope")),
        "probes": _rows(audit.get("features"), ("object", "property", "status", "error", "restored"), 16),
        "restoration": recovery_summary(audit.get("restoration"))}
    for source, projected in zip(audit.get("features", []), summary["parameter_audit"]["probes"]["items"]):
        detail = source.get("detail") or {}
        projected["detail"] = _pick(detail, ("step", "reason", "property_type", "exception_type"))
        for key in ("requested", "actual"):
            scalar = detail.get(key)
            if isinstance(scalar, (int, float)) and math.isfinite(scalar):
                projected["detail"][key] = scalar
            elif isinstance(scalar, dict):
                projected["detail"][key] = _pick(scalar, ("value", "unit"))
    summary["recovery"] = recovery_summary(value.get("document_state", {}))
    return summary


def save_summary(value, recent_validation):
    return {**_pick(value, ("saved", "status", "saved_to", "checkpoint", "result", "result_body", "tip")),
            "model_state": _pick(value.get("model_state"), ("state",)), "recent_validation": recent_validation}


def failure_summary(error, request):
    summary = _pick(error, ("operation_id", "code", "stage", "message", "message_characters_omitted", "side_effect",
                            "retry_action", "error_class", "origin_method", "restored", "path", "issue_count", "issues_omitted"))
    summary["request"] = request
    if "file_error" in error:
        summary["file_error"] = _pick(error["file_error"], ("stage", "path", "exception_type", "message", "errno", "winerror"))
    if "issues" in error:
        summary["issues"] = _rows(error["issues"], ("path", "message"), 16)
    detail = error.get("detail", {})
    summary["recovery"] = recovery_summary(detail)
    summary["failure_detail"] = _pick(detail, ("step", "reason", "object", "property", "property_type", "exception_type",
                                               "original_error", "recovery_error", "committed", "transaction",
                                               "original_stage", "direction", "history_moved", "recomputed_objects"))
    if isinstance(detail.get("camera"), dict):
        camera = detail["camera"]
        summary["failure_detail"]["camera"] = _pick(camera, ("direction", "phase", "focal_distance_mm", "tolerance_mm"))
        for key in ("expected_focus_mm", "actual_focus_mm", "position_mm", "focus_error_mm"):
            summary["failure_detail"]["camera"][key] = (
                _pick(camera[key], ("x", "y", "z")) if isinstance(camera.get(key), dict) else None)
    if isinstance(detail.get("native_objects"), list):
        summary["native_objects"] = _rows(detail["native_objects"], ("object", "type_id", "state", "native_status",
            "status_unavailable", "Profile", "BaseFeature", "Tip", "Originals", "originals_omitted",
            "geometry_count", "constraint_count", "fully_constrained", "diagnostic_error"), 16)
        summary["objects_omitted"] = detail.get("objects_omitted", 0)
    if "selection_resolution" in error:
        summary["selection_resolution"] = selection_summary(error["selection_resolution"])
    if "recovery" in error:
        summary["recovery_error"] = _pick(error["recovery"], ("original_error", "recovery_error"))
    return summary


def request_summary(name, arguments):
    if not isinstance(arguments, dict):
        return {}
    fields = ("knowledge_id", "transaction", "role", "body_name", "sketch_name", "datum_name", "feature_name",
              "predecessor_name", "intent", "mode", "depth_mode", "mouth_mode", "join", "direction", "reversed",
              "radius_mm", "size_mm", "size2_mm", "flip_direction", "length_mm", "diameter_mm", "depth_mm",
              "angle_deg", "thickness_mm", "position_mm", "axis")
    summary = {key: value[:200] if isinstance(value, str) else value
               for key, value in _pick(arguments, fields).items()
               if isinstance(value, (str, int, float, bool)) and (not isinstance(value, float) or math.isfinite(value))}
    for key in ("position_mm", "axis"):
        value = arguments.get(key)
        if isinstance(value, list) and len(value) == 3 and all(isinstance(item, (int, float)) and math.isfinite(item) for item in value):
            summary[key] = value
        elif isinstance(value, dict):
            summary[key] = reference_summary(value)
    if name in PRIMITIVE_COMMANDS:
        summary["dimensions"] = {field: arguments[field] for field, _native, _rule in PRIMITIVE_COMMANDS[name]["inputs"]
                                 if isinstance(arguments.get(field), (int, float)) and math.isfinite(arguments[field])}
    for key in ("geometry", "constraints", "edge_refs", "face_refs", "source_feature_names", "tool_body_names"):
        if isinstance(arguments.get(key), list):
            summary[key + "_count"] = len(arguments[key])
    if isinstance(arguments.get("request"), dict):
        summary["observation"] = observation_request_summary(arguments["request"])
    if isinstance(arguments.get("reference"), dict):
        summary["reference"] = reference_summary(arguments["reference"])
    return summary
