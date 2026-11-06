"""Requested validation facts, dependency revisions and separate persistence."""

from REAgent.rebuild.journal import invalidate_validation, saved_applicability, validation_applicability, validation_reference
from REAgent.rebuild.log_summary import save_summary, validation_summary
from REAgent.protocol.definitions import validation_items


def run(name, args, record, started, ctx):
    _call = ctx.call
    _emit = ctx.emit
    _call_context = ctx.call_context
    if name == "agent_validate":
        options = args["options"]
        requests = validation_items(options)
        if record["blocked"] or record["source_basis_unknown"]:
            reason = "write_state_unknown" if record["blocked"] else "source_restoration_unknown"
            value = {"status": "completed", "scope": "target", "requested": options,
                     "checks": {item: {"status": "unavailable", "available": False, "reason": reason} for item in requests},
                     "parameter_audit": {"status": "unavailable", "reason": reason} if options.get("parameter_audit") else {"status": "not_requested"},
                     "document_state": {"target_writes_blocked": True}}
        else:
            value = _call(record, name, options=options)
        context = _call_context.get() or {}
        trusted = context.get("execution") or {}
        actor = trusted.get("actor", "external")
        provenance = value.pop("validation_provenance", None)
        reference = {**validation_reference({**value, "validation_provenance": provenance}, context.get("operation_id")),
                     "actor": actor, "document": record["target_document"]}
        if actor == "human":
            value["validation_provenance"] = provenance
        else:
            record["recent_validation"] = reference
            record["_recent_validation_provenance"] = provenance
        value.update(actor=actor, document=record["target_document"], validation_reference=reference)
        _emit(record, "validation.completed", name, validation_summary(value), started)
        return value
    if name == "agent_save":
        value = _call(record, name)
        provenance = value.pop("validation_provenance", None)
        record["recent_validation"] = validation_applicability(record["recent_validation"],
            record.get("_recent_validation_provenance"), provenance)
        record["recent_manual_comparison"] = validation_applicability(record.get("recent_manual_comparison", {}),
            record.get("_manual_comparison_provenance"), provenance)
        summary = save_summary(value, record["recent_validation"], record["recent_manual_comparison"])
        if value.get("saved") is True:
            record["last_saved_checkpoint"] = {**summary,
                "operation_id": (_call_context.get() or {}).get("operation_id"),
                "revisions": provenance, "document": record["target_document"],
                "saved_revision": value.get("saved_revision")}
            record["current_target_revision"] = value.get("revision_after")
        _emit(
            record,
            "checkpoint.saved",
            name,
            summary,
            started,
        )
        return {**value, "recent_validation": record["recent_validation"],
                "recent_manual_comparison": record["recent_manual_comparison"],
                "last_saved_checkpoint": saved_applicability(record), "journal": record["journal"].metadata()}
    raise ValueError("Unknown tool in workflow group")


def record_manual_comparison(record, comparison_id, rows):
    """Publish the complete human reference once, from its original step results."""
    facts, provenance = {}, {}
    for identity, result in rows:
        value, step = result.value, identity["comparison_step"]
        if step == "geometry" and "validation_reference" in value:
            facts.update(value["validation_reference"]["facts"])
            provenance.update(value["validation_provenance"] or {})
        elif step == "views" and value.get("image_delivery", {}).get("status") == "prepared":
            facts["paired_views"] = {"status": "prepared", "available": True, "applicability": "current",
                                     "evidence_id": value["evidence_id"]}
            provenance["paired_views"] = {"document": record["target_document"], **value["paired_revisions"]}
        else:
            facts[step] = {"status": value.get("status") or "failed", "available": False,
                           "reason": value.get("reason") or value.get("message"), "applicability": "unknown"}
    current = {"document": record["target_document"], "source_revision": record["source_revision"],
               "target_revision": record.get("current_target_revision")}
    versions = {tuple(item.get(key) for key in current) for item in provenance.values()
                if all(item.get(key) is not None for key in current)}
    cross_revision = len(versions) > 1
    reference = {"comparison_id": comparison_id, "actor": "human", "status": "recorded",
                 "facts": facts, "applicability": "current", "cross_revision": cross_revision,
                 "steps": [{**identity, "is_error": result.is_error, "side_effect": result.side_effect}
                           for identity, result in rows]}
    reference = validation_applicability(reference, provenance, current)
    if cross_revision:
        reference = invalidate_validation(reference, "comparison_steps_cross_revision")
    record.update(recent_manual_comparison=reference, _manual_comparison_provenance=provenance)
    return reference


def contribute():
    return {name: run for name in ('agent_validate', 'agent_save')}
