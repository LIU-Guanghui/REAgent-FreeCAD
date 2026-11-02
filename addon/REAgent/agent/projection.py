"""Project context semantics before model encoding, keeping local identities here."""

from dataclasses import dataclass

from REAgent.model.messages import AssistantTurn, Image, ImageBlock, Instruction, TextBlock, ToolReply
from REAgent.values import encode_json
from .messages import CurrentFacts, HistorySummary, ImageMaterial, ToolOutcome, UserText


def project(message):
    if isinstance(message, (Instruction, AssistantTurn)):
        return message
    if isinstance(message, UserText):
        return TextBlock("user", message.text, "user")
    if isinstance(message, CurrentFacts):
        return TextBlock("user", message.prefix + encode_json({
            "fact_sequence": message.sequence, "facts": message.value}), "facts_summary")
    if isinstance(message, HistorySummary):
        if message.purpose not in ("narrative", "coverage"):
            raise ValueError("Unknown history material purpose")
        return TextBlock("user", (message.prefix if message.purpose == "narrative" else "") +
                         encode_json(message.value), "facts_summary", trailing=message.purpose == "coverage")
    if isinstance(message, ToolOutcome):
        if message.source == "model":
            if not message.call_id:
                raise ValueError("A model result requires its original call identity")
            return ToolReply(message.call_id, encode_json(message.value))
        if message.source == "human":
            return TextBlock("user", message.prefix + encode_json(message.value), "results")
        raise ValueError("Unknown tool outcome source")
    if isinstance(message, ImageMaterial):
        return ImageBlock(message.prefix + encode_json(message.value), tuple(
            (encode_json(label), Image(item.data, item.width_px, item.height_px,
                                      item.mime_type)) for label, item in message.images))
    raise TypeError("Unsupported conversation material")


@dataclass(frozen=True)
class ContextRequest:
    request: object
    messages: tuple

    @property
    def estimate(self):
        return self.request.estimate


class ContextModel:
    """Context accounting uses the same projection as the eventual request."""

    def __init__(self, client):
        self.client = client

    @property
    def config(self):
        return self.client.config

    def estimate(self, messages, tools):
        return self.client.estimate(tuple(map(project, messages)), tools)

    def material_estimate(self, message):
        return self.client.material_estimate(project(message))

    def image_estimates(self, messages):
        return self.client.image_estimates(tuple(map(project, messages)))

    def prepare(self, messages, tools):
        messages = tuple(messages)
        return ContextRequest(self.client.prepare(tuple(map(project, messages)), tools), messages)
