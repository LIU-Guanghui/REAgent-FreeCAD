"""Public evidence guard for native equal/two-distance Chamfer."""

from ..log_summary import execution_summary
from .execution import validate_domain_evidence


def run(name, args, record, started, ctx):
    if name != "PartDesign_Chamfer":
        raise ValueError("Unknown Chamfer command")
    validate_domain_evidence(args, record)
    value = ctx.call(record, name, **{k: v for k, v in args.items() if k != "session_id"})
    summary = {**execution_summary(value), "body": value["readback"]["body"],
               "feature": value["readback"]["feature"],
               "predecessor": value["readback"]["predecessor"],
               "edge_count": len(value["readback"]["edges"]),
               "requested_edge_count": len(args["edge_refs"]), "actual_base_edges": value["readback"]["edges"],
               "mode": args["mode"], "size_mm": args["size_mm"],
               "state": value["state"], "transaction": value["transaction"],
               "intent": args["intent"][:200]}
    summary["actual_sizes"] = {key: value["readback"][key] for key in ("size_mm", "size2_mm", "flip_direction")}
    for key in ("size2_mm", "flip_direction"):
        if key in args:
            summary[key] = args[key]
    ctx.emit(record, "feature.committed", name, summary, started)
    return value
