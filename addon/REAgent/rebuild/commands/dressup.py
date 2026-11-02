"""Evidence guards for native Fillet, Chamfer, Draft and Thickness commands."""

from REAgent.rebuild.log_summary import execution_summary, reference_summary
from REAgent.rebuild.commands.execution import validate_domain_evidence


def fillet(name, args, record, started, ctx):
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


def chamfer(name, args, record, started, ctx):
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


def draft(name, args, record, started, ctx):
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


def thickness(name, args, record, started, ctx):
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


def contribute():
    return {"PartDesign_Fillet": fillet, "PartDesign_Chamfer": chamfer,
            "PartDesign_Draft": draft, "PartDesign_Thickness": thickness}
