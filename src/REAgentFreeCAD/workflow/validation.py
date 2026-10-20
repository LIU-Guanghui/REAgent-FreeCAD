"""Requested validation facts, dependency revisions and separate persistence."""

from ..journal import validation_applicability, validation_reference
from ..log_summary import save_summary, validation_summary


def run(name, args, record, started, ctx):
    _call = ctx.call
    _emit = ctx.emit
    _call_context = ctx.call_context
    if name == "agent_validate":
        value = _call(record, name, options=args.get("options", {}))
        context = _call_context.get() or {}
        provenance = value.pop("validation_provenance", None)
        record["recent_validation"] = validation_reference({**value, "validation_provenance": provenance}, context.get("operation_id"))
        record["_recent_validation_provenance"] = provenance
        _emit(record, "validation.completed", name, validation_summary(value), started)
        return value
    if name == "agent_save":
        value = _call(record, name)
        record["recent_validation"] = validation_applicability(record["recent_validation"],
            record.get("_recent_validation_provenance"), value.pop("validation_provenance", None))
        _emit(
            record,
            "checkpoint.saved",
            name,
            save_summary(value, record["recent_validation"]),
            started,
        )
        return {**value, "recent_validation": record["recent_validation"], "journal": record["journal"].metadata()}
    raise ValueError("Unknown tool in workflow group")
