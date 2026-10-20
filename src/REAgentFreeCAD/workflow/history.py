"""Session-owned native undo and redo adaptation."""

from ..log_summary import history_summary


def run(name, args, record, started, ctx):
    _call = ctx.call
    _emit = ctx.emit
    if name in ("agent_undo", "agent_redo"):
        value = _call(record, name, transaction=args["transaction"])
        event = {"agent_undo": "feature.undone", "agent_redo": "feature.redone"}[name]
        _emit(record, event, name, history_summary(value), started)
        return value
    raise ValueError("Unknown tool in workflow group")
