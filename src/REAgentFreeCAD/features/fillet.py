"""Public evidence guard for native constant-radius Fillet."""

from ..log_summary import execution_summary
from .execution import validate_domain_evidence


def run(name, args, record, started, ctx):
    if name != "PartDesign_Fillet":
        raise ValueError("Unknown Fillet command")
    validate_domain_evidence(args, record)
    value = ctx.call(record, name, **{k: v for k, v in args.items() if k != "session_id"})
    ctx.emit(record, "feature.committed", name,
             {**execution_summary(value), "body": value["readback"]["body"],
              "feature": value["readback"]["feature"],
              "predecessor": value["readback"]["predecessor"],
              "edge_count": len(value["readback"]["edges"]),
              "requested_edge_count": len(args["edge_refs"]), "actual_base_edges": value["readback"]["edges"],
              "radius_mm": args["radius_mm"],
              "actual_radius_mm": value["readback"]["radius_mm"],
              "state": value["state"], "transaction": value["transaction"],
              "intent": args["intent"][:200]}, started)
    return value
