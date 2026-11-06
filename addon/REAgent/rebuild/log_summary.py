"""Explicit bounded journal facts; detailed interfaces are returned separately."""

import math

from REAgent.protocol.features import DRESSUP_SCOPE, PRIMITIVE_COMMANDS
from REAgent.protocol.observations import IMAGE_OBSERVATION_KINDS
from REAgent.rebuild.results import ToolEvent


def _pick(value: dict | None, fields):
    if value is None:
        return {}
    keys = value.keys()
    return {key: value[key] for key in fields if key in keys}


def _rows(values: list[dict] | None, fields, limit):
    values = [] if values is None else values
    return {"items": [_pick(value, fields) for value in values[:limit]],
            "count": len(values), "omitted": max(0, len(values) - limit)}


def reference_summary(reference):
    summary = _pick(reference, ("kind", "role", "object_name", "datum_name", "coordinate_system_name"))
    if reference is None:
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
        if key in reference:
            child = reference[key]
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
    if value is not None:
        for key, limit in (("restoration_errors", 8), ("residual_files", 8),
                           ("temporary_cleanup_errors", 8), ("presentation_restore_errors", 8)):
            items = value.get(key)
            if isinstance(items, list):
                summary[key] = items[:limit]
    return summary


def selection_summary(value):
    summary = _pick(value, ("all_resolved", "input_count", "selection_mode", "selection_kind", "face_role",
                            "predecessor", "body", "status", "failed_input", "match_count", "reason"))
    if value is None:
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
    kind, query = request["kind"], request["query"]
    fields = {
        "global": (), "entity": ("object", "edit_object", "fields", "offset", "limit", "include"),
        "feature": ("object",),
        "selection_resolution": ("body_name", "edge_scope", "edge_role", "face_role", "nearest_limit"),
        "regions": ("offset", "limit"), "points": (), "residual": (),
        "support_extension": ("radius_mm",),
        "initial_views": ("width", "height", "output_long_edge_px"),
        "view": ("width", "height", "output_long_edge_px", "views", "paired", "include_section", "section_axis",
                 "projection", "display_mode", "transparency", "focus_box_mm"),
    }
    summary = {"kind": kind}
    for key, value in _pick(query, fields[kind]).items():
        if isinstance(value, list):
            summary[key] = [item[:80] for item in value[:16]]
            summary[key + "_count"] = len(value)
            summary[key + "_omitted"] = max(0, len(value) - 16)
        else:
            summary[key] = value[:200] if isinstance(value, str) else value
    for key in ("edge_queries", "edge_selectors", "edge_refs", "face_selectors", "face_refs", "faces", "edges", "points", "selectors"):
        if key not in query:
            continue
        if query[key] == "all":
            summary[key + "_scope"] = "all"
        else:
            summary[key + "_count"] = len(query[key])
    return summary


def observation_summary(request, result):
    summary = {"observation": observation_request_summary(request), "evidence_id": result["evidence_id"]}
    summary.update(_pick(result, ("pagination", "residual_summary", "display_objects",
                                 "evidence_roles", "evidence_status", "geometry_scope", "target_geometry_scope",
                                 "image_delivery",
                                 "geometry_available", "geometry_unavailable_reason", "writable_references_available")))
    if "shape" in result:
        summary["shape"] = _pick(result["shape"], ("is_valid", "solid_count", "face_count", "edge_count",
                                                   "volume_mm3", "area_mm2", "bounding_box_mm", "bounding_box_method", "bounding_box_tightness"))
    if request.get("kind") == "feature":
        summary.update(_pick(result, ("object", "type_id", "body", "predecessor", "tip")))
        sketch = result.get("sketch")
        for key, page, fields in (
                ("sketch_page", sketch.get("page") if isinstance(sketch, dict) else None,
                 ("section", "offset", "total", "next_offset")),
                ("vertex_page", result.get("vertex_page"), ("offset", "total", "next_offset"))):
            if isinstance(page, dict):
                summary[key] = _pick(page, fields)
                items = page.get("items")
                if isinstance(items, list):
                    summary[key]["returned"] = len(items)
    if request.get("kind") == "residual":
        summary["operations"] = {direction: _pick(row, ("operation", "method", "status", "available", "reason",
            "exception_type", "calls", "elapsed_ms", "is_valid", "valid_empty", "volume_mm3",
            "filtered_component_count", "filtered_volume_mm3")) for direction, row in result.get("operations", {}).items()}
    if request.get("kind") == "selection_resolution":
        summary["selection_resolution"] = selection_summary(result.get("selection_resolution", result))
    if request.get("kind") in IMAGE_OBSERVATION_KINDS:
        delivery = result.get("image_delivery", {})
        summary["image_delivery"] = _pick(delivery, (
            "status", "image_count", "total_bytes", "scope", "inspection",
            "direction_count", "capture_id", "capture_number", "reason", "exception_type", "limit", "board_limit"))
        summary["image_delivery"]["images"] = [_pick(row, (
            "image_index", "artifact_key", "role", "size_bytes", "width_px", "height_px"))
            for row in delivery.get("images", [])[:8]]
        summary.update(_pick(result, ("requested_display_mode", "normalized_display_mode", "object_display_mode")))
        summary["scale"] = _pick(result.get("scale"), ("shared", "camera_height_mm", "millimeters_per_pixel", "width_px", "height_px", "focus_mm"))
        if "initial_views" in result:
            summary["initial_views"] = _pick(result["initial_views"], ("directions", "direction_count", "complete", "capture_id"))
        summary["outputs"] = {key: {"path": row["path"], "layout": _pick(row["layout"], (
            "rows", "columns", "width_px", "height_px", "sampling_width_px", "sampling_height_px", "output_long_edge_px"))}
                              for key, row in result["outputs"].items()}
        if result.get("section") is not None:
            summary["section"] = result["section"]
        if "image_cleanup" in result:
            summary["image_cleanup"] = {key: item for key, item in result["image_cleanup"].items() if key != "files"}
    if "recovery" in result:
        summary["recovery"] = recovery_summary(result["recovery"])
    return summary


def validation_summary(value):
    summary = _pick(value, ("scope", "status", "requested"))
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


def save_summary(value, recent_validation, recent_manual_comparison):
    return {**_pick(value, ("saved", "status", "saved_to", "checkpoint", "result", "result_body", "tip")),
            "model_state": _pick(value.get("model_state"), ("state",)), "recent_validation": recent_validation,
            "recent_manual_comparison": recent_manual_comparison}


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
                                               "original_stage", "view_phase", "direction", "history_moved", "recomputed_objects",
                                               "link_index", "owner", "subelement", "changed_fields"))
    if isinstance(detail.get("section"), dict):
        summary["failure_detail"]["section"] = detail["section"]
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
    history_fields = ("reason", "expected", "top", "top_ownership", "pending_transaction",
                      "pending_documents", "redo_top", "redo_count", "session_owned_count",
                      "unowned_entries", "model_restored", "redo_changed")
    guard = _pick(detail, history_fields)
    if guard:
        summary["native_guard"] = guard
    if "selection_resolution" in error:
        summary["selection_resolution"] = selection_summary(error["selection_resolution"])
    if "recovery" in error:
        summary["recovery_error"] = _pick(error["recovery"], ("original_error", "recovery_error"))
    return summary


def request_summary(name, arguments: dict):
    """Bounded projection of a schema-validated call; invalid inputs stay in errors."""
    fields = ("knowledge_id", "transaction", "role", "body_name", "sketch_name", "datum_name", "feature_name",
              "predecessor_name", "object_name", "type_id", "intent", "mode", "depth_mode", "mouth_mode", "join", "reversed",
              "radius_mm", "size_mm", "size2_mm", "flip_direction", "length_mm", "diameter_mm", "depth_mm",
              "angle_deg", "thickness_mm", "validation_operation_id", "paired_evidence_id")
    summary = {key: value[:200] if isinstance(value, str) else value
               for key, value in _pick(arguments, fields).items()}
    for key in ("position_mm", "axis", "direction"):
        if key in arguments:
            value = arguments[key]
            summary[key] = reference_summary(value) if isinstance(value, dict) and "kind" in value else value
    if name in PRIMITIVE_COMMANDS:
        summary["dimensions"] = {field: arguments[field] for field, _native, _rule in PRIMITIVE_COMMANDS[name]["inputs"]
                                 if field in arguments}
    for key in ("geometry", "constraints", "edge_refs", "face_refs", "source_feature_names", "tool_body_names"):
        if key in arguments:
            summary[key + "_count"] = len(arguments[key])
    if name == "EditFeature":
        summary["changed_fields"] = sorted(arguments["changes"])
        summary["values"] = {key: item for key, item in arguments["changes"].items()
                             if isinstance(item, (int, float, bool, str))}
    if name == "agent_observe":
        summary["observation"] = observation_request_summary(arguments["request"])
    elif "request" in arguments:
        summary["discovery"] = _pick(arguments["request"], ("kind", "name", "type_id", "role"))
    if "reference" in arguments:
        summary["reference"] = reference_summary(arguments["reference"])
    if name == "agent_validate":
        summary["options"] = _pick(arguments["options"], (
            "measurements", "surface_sampling", "boolean_operations", "parameter_audit"))
    return summary


def tool_message(event: ToolEvent, *, started=False):
    """A short presentation of actual request/result facts, without inferred reasoning."""
    name = str(event.get("function") or "Tool")
    request = event.get("request", {})
    value = {} if started else event["result"]
    label = name.removeprefix("agent_").replace("_", " ")
    if name == "agent_observe":
        observation = request.get("observation", {})
        scope = observation.get("kind") or "observation"
        role = request.get("role") or value.get("role")
        label = "Observe " + (str(role)[:1].upper() + str(role)[1:] + " · " if role else "") + scope[:1].upper() + scope[1:].replace("_", " ")
        if observation.get("object"):
            label += " · " + str(observation["object"])
        counts = []
        if started:
            for category in ("faces", "edges"):
                if observation.get(category + "_scope") == "all":
                    counts.append("all " + category + " requested")
                elif observation.get(category + "_count", 0) > 0:
                    counts.append("%s %s requested" % (observation[category + "_count"], category))
        elif not event.get("is_error"):
            pagination = value.get("pagination")
            for category in ("faces", "edges"):
                page = pagination.get(category) if isinstance(pagination, dict) else None
                if isinstance(page, dict):
                    if page.get("requested") is True and type(page.get("returned")) is int:
                        count = str(page["returned"])
                        if type(page.get("selected_total")) is int and page["selected_total"] > page["returned"]:
                            count += "/" + str(page["selected_total"])
                        counts.append(count + " " + category + " returned")
                elif (isinstance(value.get(category), list) and (value[category]
                      or observation.get(category + "_scope") == "all"
                      or observation.get(category + "_count", 0) > 0)):
                    counts.append("%s %s returned" % (len(value[category]), category))
        if counts:
            label += " · " + ", ".join(counts)
    elif not name.startswith("agent_"):
        feature = request.get("feature_name") or request.get("sketch_name") or request.get("datum_name") or request.get("object_name") or request.get("body_name")
        if feature:
            label += " · " + str(feature)
    elif name == "agent_validate":
        options = request.get("options") or value.get("requested") or {}
        selected = [*options.get("measurements", []), *options.get("boolean_operations", [])]
        selected.extend(key for key in ("surface_sampling", "parameter_audit") if options.get(key))
        label = "Compare / audit" + (" · " + ", ".join(selected) if selected else "")
    label = label[:1].upper() + label[1:]
    if started:
        return label[:240] + ": Started"
    outcome = ("not executed" if value.get("executed") is False else
               "ended locally; cleanup pending" if (name == "agent_close_session"
                   and value.get("ended") is True and value.get("session_retained")) else
               "ended locally; Target cleanup unconfirmed" if (name == "agent_close_session"
                   and value.get("ended") is True and value.get("abandoned_roles")) else
               "outcome unknown" if event.get("side_effect") == "unknown" else
               "failed" if event.get("is_error") else
               "committed" if value.get("status") == "committed" else "completed")
    details = []
    if event.get("is_error"):
        details.append(str(value.get("message") or value.get("code") or "Inspect actual state before another operation"))
    elif name == "agent_close_session" and value.get("ended") is True:
        details.extend(role.title() + " release unconfirmed: " + str(row.get("error", {}).get("message") or row.get("reason") or row.get("status"))
                       for role, row in value.get("roles", {}).items() if row.get("status") == "failed")
        details.extend(role.title() + " local binding abandoned; remote cleanup unconfirmed: "
                       + str(row.get("error", {}).get("message") or row.get("reason") or row.get("abandon_reason"))
                       for role, row in value.get("roles", {}).items() if row.get("status") == "abandoned")
        if value.get("unconfirmed_creation"):
            details.append("creation unconfirmed: " + ", ".join(value["unconfirmed_creation"]))
        journal = value.get("journal") or {}
        if journal and not journal.get("complete"):
            details.append("journal incomplete: " + str((journal.get("error") or {}).get("message") or "not fully persisted"))
    elif event.get("side_effect") == "unknown":
        details.append(str(value.get("message") or value.get("code") or "Inspect actual state before another operation"))
    elif name == "agent_observe":
        delivery = value.get("image_delivery") or {}
        if delivery.get("status") == "prepared":
            details.append("%s prepared images" % delivery["image_count"])
        elif delivery.get("status") == "unavailable":
            details.append("images unavailable: " + str(delivery.get("reason")))
        if value.get("geometry_available") is False:
            details.append("geometry unavailable: " + str(value.get("geometry_unavailable_reason")))
    elif name == "agent_validate":
        audit = value.get("parameter_audit") or {}
        if audit.get("status") not in (None, "not_requested"):
            state = str(audit["status"]).replace("_", " ")
            outcome += "; Parameter audit " + state[:1].upper() + state[1:]
            reason = audit.get("reason")
            if audit["status"] == "failed":
                probe = next((row for row in audit.get("features", []) if row.get("status") == "failed"), None)
                if probe is not None:
                    reason = "%s.%s: %s" % (probe["object"], probe["property"], probe.get("error") or "probe failed")
            if reason:
                details.append(str(reason))
        details.extend(key + ": " + str(row.get("reason") or row.get("applicability_reason") or row.get("status"))
                       for key, row in value.get("checks", {}).items()
                       if row.get("available") is False or row.get("applicability", "current") != "current")
    return (label[:1].upper() + label[1:240] + ": " + outcome[:1].upper() + outcome[1:]
            + (". " + "; ".join(details)[:420] if details else ""))
