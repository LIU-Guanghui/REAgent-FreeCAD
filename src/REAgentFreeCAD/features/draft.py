"""Public evidence guard for native Draft with an explicit neutral plane."""

from ..log_summary import execution_summary, reference_summary
from .execution import validate_domain_evidence


def run(name, args, record, started, ctx):
    if name != "PartDesign_Draft":
        raise ValueError("Unknown Draft command")
    validate_domain_evidence(args, record)
    value = ctx.call(record, name, **{k: v for k, v in args.items() if k != "session_id"})
    ctx.emit(record, "feature.committed", name,
             {**execution_summary(value), "body": value["readback"]["body"],
              "feature": value["readback"]["feature"],
              "predecessor": value["readback"]["predecessor"],
              "face_count": len(value["readback"]["faces"]),
              "actual_base_faces": value["readback"]["faces"], "requested_face_count": len(args["face_refs"]),
              "neutral_plane": reference_summary(args["neutral_plane"]),
              "neutral_kind": args["neutral_plane"]["kind"],
              "angle_deg": args["angle_deg"], "reversed": args["reversed"],
              "state": value["state"], "transaction": value["transaction"],
              "intent": args["intent"][:200]}, started)
    return value
