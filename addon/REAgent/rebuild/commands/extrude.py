"""Public evidence guard for Pad and Pocket."""

from REAgent.rebuild.log_summary import execution_summary
from REAgent.rebuild.commands.execution import validate_domain_evidence


def run(name, args, record, started, ctx):
    if name not in ("PartDesign_Pad", "PartDesign_Pocket"):
        raise ValueError("Unknown extrude command")
    validate_domain_evidence(args, record)
    value = ctx.call(record, name, **{k: v for k, v in args.items() if k != "session_id"})
    ctx.emit(record, "feature.committed", name,
             {**execution_summary(value), "body": value["readback"]["body"], "feature": value["readback"]["feature"],
              "profile": args["sketch_name"], "length_mm": args["length_mm"],
              "reversed": args["reversed"], "state": value["state"],
              "transaction": value["transaction"], "intent": args["intent"][:200]}, started)
    return value


def contribute():
    return {name: run for name in ('PartDesign_Pad', 'PartDesign_Pocket')}
