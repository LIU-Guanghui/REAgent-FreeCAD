"""Public evidence guard for the two native whole-Sketch Pipe commands."""

from REAgent.rebuild.log_summary import execution_summary


def run(name, args, record, started, ctx):
    if name not in ("PartDesign_AdditivePipe", "PartDesign_SubtractivePipe"):
        raise ValueError("Unknown Pipe command")
    value = ctx.call(record, name, **{k: v for k, v in args.items() if k != "session_id"})
    ctx.emit(record, "feature.committed", name,
             {**execution_summary(value), "body": value["readback"]["body"], "feature": value["readback"]["feature"],
              "profile": args["profile_sketch_name"],
              "path": args["path_sketch_name"],
              "state": value["state"], "transaction": value["transaction"],
              "intent": args["intent"][:200]}, started)
    return value


def contribute():
    return {name: run for name in ('PartDesign_AdditivePipe', 'PartDesign_SubtractivePipe')}
