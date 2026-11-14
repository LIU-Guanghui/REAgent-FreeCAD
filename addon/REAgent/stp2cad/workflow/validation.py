"""Requested validation facts, dependency revisions and separate persistence."""

import uuid

from REAgent.stp2cad.journal import invalidate_validation, saved_applicability, validation_applicability, validation_reference
from REAgent.stp2cad.log_summary import save_summary, validation_summary
from REAgent.stp2cad.results import ToolResult
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


def _comparison_step(identity, result):
    value = result.value
    if identity["comparison_step"] == "geometry" and "checks" in value:
        summary = validation_summary(value)
    else:
        summary = {key: value[key] for key in (
            "status", "code", "message", "reason", "executed", "evidence_id", "image_delivery") if key in value}
    delivery = summary.get("image_delivery")
    if isinstance(delivery, dict):
        summary["image_delivery"] = {key: delivery[key] for key in (
            "status", "image_count", "direction_count", "reason") if key in delivery}
    revisions = value.get("paired_revisions") or {name: check.get("revisions")
        for name, check in value.get("checks", {}).items() if check.get("revisions")}
    return {**identity, "is_error": result.is_error, "side_effect": result.side_effect,
            "revisions": revisions, "summary": summary}


def compare_manually(controller, options, view_request, *, binding, documents, cancelled, on_progress, on_step):
    comparison_id = "comparison-" + uuid.uuid4().hex[:20]
    requests = []
    if options is not None:
        requests.append(("geometry", "agent_validate", {"options": options}))
    if view_request is not None:
        requests.append(("views", "agent_observe", {"role": "target", "request": view_request}))
    steps, rows = [], []
    stop_reason = None
    for step, function, arguments in requests:
        generation_changed = cancelled()
        if stop_reason or generation_changed:
            result = ToolResult({"status": "not_executed", "reason": stop_reason or "pause_or_new_message",
                                 "executed": False, "side_effect": "none"})
        else:
            try:
                result = controller.execute(function, arguments, on_progress=on_progress,
                    execution={"actor": "human", "binding": binding, "scope": "manual", "purpose": "action",
                               "comparison_id": comparison_id, "comparison_step": step})
            except BaseException:
                result = ToolResult({"code": "comparison_interrupted", "status": "unknown",
                    "message": "The original comparison did not return a confirmed result", "side_effect": "unknown"},
                    is_error=True, side_effect="unknown")
        identity = {"function": function, "comparison_id": comparison_id, "comparison_step": step,
                    "operation_id": result.value.get("operation_id")}
        rows.append((identity, result))
        # Keep the original result available if the summary cannot be built.
        on_step(identity, result)
        steps.append(_comparison_step(identity, result))
        current = controller.task_state()
        if result.side_effect == "unknown" or not current or current["source_basis_unknown"] or current["target_writes_blocked"]:
            stop_reason = "restoration_or_binding_unconfirmed"
    reference = controller.record_manual_comparison(binding, comparison_id, rows)
    comparison = {"comparison_id": comparison_id, "actor": "human", "steps": steps,
                  "documents": documents, "reference": reference}
    return comparison, stop_reason


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
