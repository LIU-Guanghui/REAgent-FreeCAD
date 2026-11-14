"""Original Controller results and verified capture bytes."""

from dataclasses import dataclass
from typing import Any, TypedDict
from REAgent.json_values import require_text, to_json_value
from REAgent.protocol.results import SIDE_EFFECTS


class ToolEvent(TypedDict, total=False):
    """Presentation of a prepared call or a completed ToolResult."""

    function: str
    request: dict
    result: dict
    is_error: bool
    side_effect: str

@dataclass(frozen=True)
class PreparedImage:
    """Verified capture bytes, separate from JSON and model transport."""

    data: bytes
    artifact_key: str
    image_id: str
    width_px: int
    height_px: int
    mime_type: str = "image/png"

    def __post_init__(self):
        if not isinstance(self.data, bytes) or not self.data:
            raise ValueError("prepared image must contain PNG bytes")
        require_text(self.artifact_key, "artifact_key")
        require_text(self.image_id, "image_id")
        if any(type(value) is not int or value <= 0 for value in (self.width_px, self.height_px)):
            raise ValueError("prepared image requires positive pixel dimensions")

@dataclass(frozen=True)
class ToolResult:
    """One final Controller outcome; no deferred lookup or SDK envelope."""

    value: dict[str, Any]
    images: tuple[PreparedImage, ...] = ()
    is_error: bool = False
    side_effect: str = "none"

    def __post_init__(self):
        if not isinstance(self.value, dict):
            raise ValueError("tool result must be an object")
        object.__setattr__(self, "value", to_json_value(self.value))
        if self.side_effect not in SIDE_EFFECTS:
            raise ValueError("unsupported tool side effect")
        if any(not isinstance(item, PreparedImage) for item in self.images):
            raise ValueError("tool images must contain verified PNG values")
