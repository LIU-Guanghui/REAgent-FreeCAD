"""Public adapter for the native Body command."""

from REAgent.rebuild.log_summary import execution_summary
from REAgent.rebuild.commands.execution import validate_domain_evidence


def run(name, args, record, started, ctx):
    if name != "PartDesign_Body":
        raise ValueError("Unknown Body command")
    validate_domain_evidence(args, record)
    value = ctx.call(record, name, body_name=args["body_name"], intent=args["intent"],
                     evidence=args["evidence"], expected_revision=args["expected_revision"])
    ctx.emit(record, "feature.committed", name,
             {**execution_summary(value), "body": value["readback"]["body"],
              "intent": args["intent"][:200]}, started)
    return value


def contribute():
    return {name: run for name in ('PartDesign_Body',)}
