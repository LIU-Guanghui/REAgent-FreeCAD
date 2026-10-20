"""Native structure and actual parameter-probe facts; no task acceptance."""


def _native_validation_conclusions(structure, audit, *, requested, structure_unavailable_reason):
    attempted = int(audit.get("property_count", 0))
    rows = audit.get("features", [])
    successful = sum(row.get("status") == "passed" and row.get("restored") is True for row in rows)
    available = bool(requested and attempted > 0 and len(rows) == attempted)
    verified = bool(available and audit.get("status") == "passed" and successful == attempted
                    and audit.get("restoration", {}).get("restored") is True)
    return {
        "native_structure_state_valid": {
            "available": structure is not None, "valid": structure.get("passed") if structure else None,
            "in_progress": structure.get("in_progress") if structure else None,
            "basis": "native_structure_audit", "unavailable_reason": structure_unavailable_reason},
        "parameter_editing_verified": {
            "available": available, "verified": verified if available else None,
            "requested": bool(requested), "attempted_property_count": attempted,
            "successful_property_count": successful, "scope": audit.get("scope"),
            "all_parameters_verified": False,
            "unavailable_reason": None if available else "parameter_audit_not_requested" if not requested else audit.get("status", "no_actual_parameter_probes")},
    }
