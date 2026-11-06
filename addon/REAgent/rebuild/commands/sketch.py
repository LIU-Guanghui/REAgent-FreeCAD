"""Public adapter for the supported native Sketch command."""

from REAgent.rebuild.log_summary import execution_summary, reference_summary


def run(name, args, record, started, ctx):
    if name not in ("PartDesign_NewSketch", "Sketcher_MapSketch",
                    "Sketcher_EditSketch",
                    "Sketcher_ValidateSketch"):
        raise ValueError("Unknown Sketch command")
    if name == "Sketcher_ValidateSketch":
        value = ctx.call(record, name, body_name=args["body_name"],
                         sketch_name=args["sketch_name"],
                         tolerance_mm=args["tolerance_mm"])
        ctx.emit(record, "sketch.diagnosed", name,
                 {"body": args["body_name"], "sketch": args["sketch_name"],
                  "tolerance_mm": args["tolerance_mm"],
                  "missing_coincidences": value["missing_point_coincidence_count"],
                  "degenerate_geometries": value["degenerated_geometry_count"],
                  "geometry_count": value["geometry_count"], "constraint_count": value["constraint_count"],
                  "fully_constrained": value["fully_constrained"],
                  "constraint_references_valid": value["constraint_references_valid"],
                  "unavailable_categories": value["unavailable_categories"], "repair_performed": value["repair_performed"]},
                 started)
        return value
    payload = {key: args[key] for key in ("body_name", "sketch_name", "intent",
                                        "evidence", "expected_revision")}
    if name == "Sketcher_MapSketch":
        payload["reference"] = args["reference"]
    if name == "Sketcher_EditSketch":
        payload.update(geometry=args["geometry"], constraints=args["constraints"])
    value = ctx.call(record, name, **payload)
    summary = {**execution_summary(value), "body": value["readback"]["body"], "sketch": value["readback"]["sketch"],
               "state": value["state"], "transaction": value["transaction"],
               "intent": args["intent"][:200]}
    if name == "Sketcher_MapSketch":
        summary["reference"] = reference_summary(args["reference"])
        summary["actual_reference"] = reference_summary(value["readback"]["reference"])
        summary["map_mode"] = value["readback"]["map_mode"]
    if name == "Sketcher_EditSketch":
        summary.update(input_geometry_count=len(args["geometry"]), input_constraint_count=len(args["constraints"]))
    for key in ("geometry_count", "constraint_count", "fully_constrained", "sketch_state"):
        if key in value["readback"]:
            summary[key] = value["readback"][key]
    ctx.emit(record, "feature.committed", name, summary, started)
    return value


def contribute():
    return {name: run for name in ('PartDesign_NewSketch', 'Sketcher_MapSketch', 'Sketcher_EditSketch', 'Sketcher_ValidateSketch')}
