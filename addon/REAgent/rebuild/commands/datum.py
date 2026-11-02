"""Public evidence and bounded logging adapter for four native Datum commands."""

from REAgent.rebuild.log_summary import execution_summary, reference_summary
from REAgent.rebuild.commands.execution import validate_domain_evidence

DATUM_COMMANDS = ("Part_DatumPlane", "Part_DatumLine", "Part_DatumPoint",
                  "Part_CoordinateSystem")


def run(name, args, record, started, ctx):
    if name not in DATUM_COMMANDS:
        raise ValueError("Unknown Datum command")
    validate_domain_evidence(args, record)
    payload = {key: args[key] for key in ("body_name", "datum_name", "mode",
                                        "position_mm", "intent", "evidence",
                                        "expected_revision")}
    if "reference" in args:
        payload["reference"] = args["reference"]
    if name != "Part_DatumPoint":
        payload.update(axis=args["axis"], angle_deg=args["angle_deg"])
    value = ctx.call(record, name, **payload)
    summary = {**execution_summary(value), "body": value["readback"]["body"], "datum": value["readback"]["datum"],
               "type_id": value["readback"]["type_id"], "mode": args["mode"],
               "state": value["state"], "transaction": value["transaction"],
               "intent": args["intent"][:200], "position_mm": args["position_mm"],
               "coordinate_system": "body_local", "actual_placement": value["readback"]["placement"]}
    for key in ("axis", "angle_deg"):
        if key in args:
            summary[key] = args[key]
    if "reference" in args:
        summary["reference"] = reference_summary(args["reference"])
        summary["actual_reference"] = reference_summary(value["readback"].get("reference", {}))
        summary["map_mode"] = value["readback"].get("map_mode")
        summary["actual_attachment_offset"] = value["readback"].get("attachment_offset")
    ctx.emit(record, "feature.committed", name, summary, started)
    return value


def contribute():
    return {name: run for name in ('Part_DatumPlane', 'Part_DatumLine', 'Part_DatumPoint', 'Part_CoordinateSystem')}
