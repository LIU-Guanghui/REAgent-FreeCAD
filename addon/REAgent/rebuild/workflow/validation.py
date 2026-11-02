"""Requested validation facts, dependency revisions and separate persistence."""

from REAgent.rebuild.journal import validation_applicability, validation_reference
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
        provenance = value.pop("validation_provenance", None)
        record["recent_validation"] = validation_reference({**value, "validation_provenance": provenance}, context.get("operation_id"))
        record["_recent_validation_provenance"] = provenance
        _emit(record, "validation.completed", name, validation_summary(value), started)
        return value
    if name == "agent_save":
        value = _call(record, name)
        provenance = value.pop("validation_provenance", None)
        record["recent_validation"] = validation_applicability(record["recent_validation"],
            record.get("_recent_validation_provenance"), provenance)
        if value.get("saved") is True:
            record["last_saved_checkpoint"] = {**save_summary(value, record["recent_validation"]),
                "operation_id": (_call_context.get() or {}).get("operation_id"),
                "revisions": provenance,
                "scope": "last_confirmed_save; subsequent_edits_may_be_unsaved"}
        _emit(
            record,
            "checkpoint.saved",
            name,
            save_summary(value, record["recent_validation"]),
            started,
        )
        return {**value, "recent_validation": record["recent_validation"], "journal": record["journal"].metadata()}
    raise ValueError("Unknown tool in workflow group")


def contribute():
    return {name: run for name in ('agent_validate', 'agent_save')}
