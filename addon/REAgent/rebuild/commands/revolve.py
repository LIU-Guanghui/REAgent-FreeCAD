"""Public evidence guard for Revolution and Groove."""

from REAgent.rebuild.log_summary import execution_summary, reference_summary
from REAgent.rebuild.commands.execution import validate_domain_evidence


def run(name, args, record, started, ctx):
    if name not in ("PartDesign_Revolution", "PartDesign_Groove"):
        raise ValueError("Unknown revolve command")
    validate_domain_evidence(args, record)
    value = ctx.call(record, name, **{k: v for k, v in args.items() if k != "session_id"})
    ctx.emit(record, "feature.committed", name,
             {**execution_summary(value), "body": value["readback"]["body"], "feature": value["readback"]["feature"],
              "profile": args["sketch_name"], "axis": reference_summary(args["axis"]),
              "angle_deg": args["angle_deg"], "reversed": args["reversed"],
              "state": value["state"], "transaction": value["transaction"],
              "intent": args["intent"][:200]}, started)
    return value


def contribute():
    return {name: run for name in ('PartDesign_Revolution', 'PartDesign_Groove')}
