"""Public evidence guard for native Body Boolean."""

from ..log_summary import execution_summary
from .execution import validate_domain_evidence


def run(name, args, record, started, ctx):
    if name != "PartDesign_Boolean":
        raise ValueError("Unknown Boolean command")
    validate_domain_evidence(args, record)
    value = ctx.call(record, name, **{k: v for k, v in args.items() if k != "session_id"})
    ctx.emit(record, "feature.committed", name,
             {**execution_summary(value), "body": value["readback"]["body"], "feature": value["readback"]["feature"],
              "mode": args["mode"], "tools": args["tool_body_names"],
              "tool_count": len(args["tool_body_names"]),
              "state": value["state"], "transaction": value["transaction"],
              "intent": args["intent"][:200]}, started)
    return value
