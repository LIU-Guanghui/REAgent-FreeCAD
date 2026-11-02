"""Neutral model messages and immutable pixel inputs."""

from dataclasses import dataclass


@dataclass(frozen=True)
class TextBlock:
    role: str
    text: str
    category: str
    trailing: bool = False


@dataclass(frozen=True)
class ToolReply:
    call_id: str
    text: str


@dataclass(frozen=True)
class Image:
    data: bytes
    width_px: int
    height_px: int
    mime_type: str


@dataclass(frozen=True)
class ImageBlock:
    text: str
    images: tuple

@dataclass(frozen=True)
class Instruction:
    text: str

@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: str

@dataclass(frozen=True)
class AssistantTurn:
    id: str
    content: str
    calls: tuple
    usage: dict | None
    provider_state: object | None = None
    diagnostics: dict | None = None
