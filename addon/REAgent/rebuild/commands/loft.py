"""Public evidence guard for the two ordered native Loft commands."""

from REAgent.rebuild.log_summary import execution_summary


def run(name, args, record, started, ctx):
    if name not in ("PartDesign_AdditiveLoft", "PartDesign_SubtractiveLoft"):
        raise ValueError("Unknown Loft command")
    value = ctx.call(record, name, **{k: v for k, v in args.items() if k != "session_id"})
    ctx.emit(record, "feature.committed", name,
             {**execution_summary(value), "body": value["readback"]["body"], "feature": value["readback"]["feature"],
              "profile": args["profile_sketch_name"],
              "section": args["section_sketch_name"],
              "state": value["state"], "transaction": value["transaction"],
              "intent": args["intent"][:200]}, started)
    return value


def contribute():
    return {name: run for name in ('PartDesign_AdditiveLoft', 'PartDesign_SubtractiveLoft')}
