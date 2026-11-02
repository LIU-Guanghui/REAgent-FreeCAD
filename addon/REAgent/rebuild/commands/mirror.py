"""Public evidence guard and bounded log for native Mirrored."""

from REAgent.rebuild.log_summary import execution_summary
from REAgent.rebuild.commands.execution import validate_domain_evidence


def run(name, args, record, started, ctx):
    if name != "PartDesign_Mirrored":
        raise ValueError("Unknown Mirrored command")
    validate_domain_evidence(args, record)
    value = ctx.call(record, name, **{key: item for key, item in args.items()
                                      if key != "session_id"})
    readback = value["readback"]
    ctx.emit(record, "feature.committed", name,
             {**execution_summary(value), "body": readback["body"], "feature": readback["feature"],
              "predecessor": readback["predecessor"],
              "source_count": readback["source_count"],
              "source_features": readback["source_features"],
              "plane_kind": args["mirror_plane"]["kind"],
              "plane_object": readback["plane_object"],
              "state": value["state"], "transaction": value["transaction"],
              "intent": args["intent"][:200]}, started)
    return value


def contribute():
    return {name: run for name in ('PartDesign_Mirrored',)}
