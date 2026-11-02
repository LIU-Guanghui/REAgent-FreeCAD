"""Conversation semantics, references and local image group identities."""

from dataclasses import dataclass

@dataclass(frozen=True)
class UserText:
    text: str
    generation: int = 0

@dataclass(frozen=True)
class ToolOutcome:
    source: str
    value: dict
    call_id: str | None = None
    prefix: str = ""
    references: tuple = ()

@dataclass(frozen=True)
class ImageMaterial:
    value: dict
    prefix: str
    images: tuple
    group_id: str
    delivery_id: str

@dataclass(frozen=True)
class HistorySummary:
    value: dict
    prefix: str = ""
    purpose: str = "narrative"

@dataclass(frozen=True)
class CurrentFacts:
    value: dict
    prefix: str
    sequence: int = 0
    references: tuple = ()
