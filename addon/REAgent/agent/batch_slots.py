"""Original assistant and result slots survive projection/presentation failures."""

from dataclasses import dataclass


@dataclass
class PendingBatch:
    reply: object
    rows: list


class BatchSlots:
    def __init__(self):
        self.pending = None
        self.retention = None
        self.human = None
        self.latest_results = ()

    def begin(self, reply, rows):
        if self.pending is not None:
            raise ValueError("An original batch still needs recording")
        self.pending = PendingBatch(reply, rows)
        self.latest_results = tuple(row[1] for row in rows)

    def record(self, index, result):
        arguments, previous = self.pending.rows[index]
        if result is not previous:
            self.pending.rows[index] = (arguments, result)
            self.latest_results = tuple(row[1] for row in self.pending.rows)

    def clear(self):
        self.pending = self.retention = self.human = None
        self.latest_results = ()
