"""Public evidence guard and bounded log for Linear and Polar Patterns."""

from ..log_summary import execution_summary
from .execution import validate_domain_evidence

_COMMANDS = ("PartDesign_LinearPattern", "PartDesign_PolarPattern")


def run(name, args, record, started, ctx):
    if name not in _COMMANDS:
        raise ValueError("Unknown native Pattern command")
    validate_domain_evidence(args, record)
    value = ctx.call(record, name, **{key: item for key, item in args.items()
                                      if key != "session_id"})
    readback = value["readback"]
    ctx.emit(record, "feature.committed", name,
             {**execution_summary(value), "body": readback["body"], "feature": readback["feature"],
              "predecessor": readback["predecessor"],
              "source_count": readback["source_count"],
              "source_features": readback["source_features"],
              "axis_kind": args["axis"]["kind"],
              "axis_object": readback["axis_object"],
              "axis_parent": readback["axis_parent"],
              "extent_mode": args["extent_mode"],
              "extent": args.get("distance_mm", args.get("angle_deg")),
              "occurrences": args["occurrences"], "reversed": args["reversed"],
              "state": value["state"], "transaction": value["transaction"],
              "intent": args["intent"][:200]}, started)
    return value
