"""User-action identity and the single retained report state."""

from dataclasses import dataclass
import uuid


@dataclass(frozen=True)
class Action:
    id: str
    scope: str
    source_generation: int
    source_text: str

    @classmethod
    def create(cls, scope, generation, text):
        return cls("run-" + uuid.uuid4().hex[:20], scope, generation, text)

    def snapshot(self):
        return {"run_id": self.id, "scope": self.scope,
                "source_generation": self.source_generation, "source_preview": self.source_text[:160]}


@dataclass(frozen=True)
class ReportState:
    run_id: str
    requested: bool = False
    reply: object = None
    identity: object = None

    def snapshot(self):
        return {"run_id": self.run_id, "requested": self.requested, "reply": self.event()}

    def event(self):
        if self.reply is None:
            return None
        return {**self.identity, "run_id": self.run_id, "text": self.reply.content,
                "response_id": self.reply.id, "progress": False, "adopted": True,
                "retained_report": True}
