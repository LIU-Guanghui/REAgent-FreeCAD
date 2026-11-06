"""Original assistant and result slots survive projection/presentation failures."""

from dataclasses import dataclass


@dataclass
class PendingBatch:
    reply: object
    rows: list
    identity: dict


class BatchSlots:
    def __init__(self):
        self.pending = None
        self.retention = None
        self.human = None
        self.handoff = None
        self.latest_results = ()
        self.latest_identities = ()

    def begin(self, reply, rows, identity):
        if self.pending is not None:
            raise ValueError("An original batch still needs recording")
        self.pending = PendingBatch(reply, rows, identity)
        if rows:
            self.latest_identities = tuple({**identity, "tool_call_id": call.id, "function": call.name}
                                           for call in reply.calls)
            self.latest_results = tuple(row[1] for row in rows)

    def record(self, index, result):
        arguments, previous = self.pending.rows[index]
        if result is not previous:
            self.pending.rows[index] = (arguments, result)
            self.latest_results = tuple(row[1] for row in self.pending.rows)

    def describe(self, call_id, payload):
        for row in self.latest_identities:
            if row["tool_call_id"] == call_id:
                row.update({key: payload[key] for key in ("function", "request", "unexecuted") if key in payload})
                return

    def clear(self):
        self.pending = self.retention = self.human = self.handoff = None
        self.latest_results = ()
        self.latest_identities = ()
