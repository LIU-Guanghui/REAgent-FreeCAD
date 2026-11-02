"""Public evidence guard for native Hole."""

from REAgent.rebuild.log_summary import execution_summary
from REAgent.rebuild.commands.execution import validate_domain_evidence


def run(name, args, record, started, ctx):
    if name != "PartDesign_Hole":
        raise ValueError("Unknown Hole command")
    validate_domain_evidence(args, record)
    value = ctx.call(record, name, **{k: v for k, v in args.items() if k != "session_id"})
    summary = {**execution_summary(value), "body": value["readback"]["body"],
               "feature": value["readback"]["feature"],
               "profile": args["sketch_name"], "diameter_mm": args["diameter_mm"],
               "depth_mode": args["depth_mode"], "mouth_mode": args["mouth_mode"],
               "reversed": args["reversed"],
               "profile_circle_count": value["readback"]["profile_circle_count"],
               "state": value["state"], "transaction": value["transaction"],
               "intent": args["intent"][:200]}
    for key in ("depth_mm", "drill_point", "drill_point_angle_deg",
                "mouth_diameter_mm", "mouth_depth_mm", "mouth_angle_deg"):
        if key in args:
            summary[key] = args[key]
    ctx.emit(record, "feature.committed", name, summary, started)
    return value


def contribute():
    return {name: run for name in ('PartDesign_Hole',)}
