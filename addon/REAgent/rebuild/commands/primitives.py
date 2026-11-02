"""Public evidence guard and bounded adapter for native primitive commands."""

from REAgent.protocol.features import PRIMITIVE_COMMANDS
from REAgent.rebuild.log_summary import execution_summary
from REAgent.rebuild.commands.execution import validate_domain_evidence


def run(name, args, record, started, ctx):
    spec = PRIMITIVE_COMMANDS.get(name)
    if spec is None:
        raise ValueError("Unknown native primitive command")
    validate_domain_evidence(args, record)
    payload = {key: value for key, value in args.items() if key != "session_id"}
    value = ctx.call(record, name, **payload)
    summary = {
        **execution_summary(value),
        "body": value["readback"]["body"],
        "feature": value["readback"]["feature"],
        "family": spec["family"],
        "operation": "additive" if spec["additive"] else "subtractive",
        "state": value["state"],
        "transaction": value["transaction"],
        "intent": args["intent"][:200],
        spec["position_field"]: args[spec["position_field"]],
        "coordinate_system": "body_local", "actual_placement": value["readback"]["placement"],
    }
    if spec["oriented"]:
        summary.update(axis=args["axis"], angle_deg=args["angle_deg"])
    if spec["family"] == "Wedge":
        summary["dimensions"] = {field: args[field] for field, _native, _rule in spec["inputs"]}
        summary["key_parameters"] = {
            "front_span_mm": [args["xmax_mm"] - args["xmin_mm"],
                              args["ymax_mm"] - args["ymin_mm"],
                              args["zmax_mm"] - args["zmin_mm"]],
            "back_edge_axis": "z" if args["x2min_mm"] == args["x2max_mm"] else "x",
        }
    else:
        summary["key_parameters"] = {
            field: args[field] for field, _native, _rule in spec["inputs"]
        }
    ctx.emit(record, "feature.committed", name, summary, started)
    return value


def contribute():
    return {name: run for name in PRIMITIVE_COMMANDS}
