"""Public evidence guard for native Skin Thickness."""

from ..log_summary import execution_summary
from .execution import validate_domain_evidence


def run(name, args, record, started, ctx):
    if name != "PartDesign_Thickness":
        raise ValueError("Unknown Thickness command")
    validate_domain_evidence(args, record)
    value = ctx.call(record, name, **{k: v for k, v in args.items() if k != "session_id"})
    ctx.emit(record, "feature.committed", name,
             {**execution_summary(value), "body": value["readback"]["body"],
              "feature": value["readback"]["feature"],
              "predecessor": value["readback"]["predecessor"],
              "opening_count": len(value["readback"]["faces"]),
              "actual_opening_faces": value["readback"]["faces"], "requested_face_count": len(args["face_refs"]),
              "thickness_mm": args["thickness_mm"],
              "direction": args["direction"], "join": args["join"],
              "state": value["state"], "transaction": value["transaction"],
              "intent": args["intent"][:200]}, started)
    return value
