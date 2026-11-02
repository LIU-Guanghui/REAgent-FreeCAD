"""Original Controller results and verified capture bytes."""

from dataclasses import dataclass
from typing import Any
from REAgent.values import NeutralContractError, require_json_value, _required_text
from REAgent.protocol.results import SIDE_EFFECTS

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
            raise NeutralContractError("prepared image must contain PNG bytes")
        _required_text(self.artifact_key, "artifact_key")
        _required_text(self.image_id, "image_id")
        if any(type(value) is not int or value <= 0 for value in (self.width_px, self.height_px)):
            raise NeutralContractError("prepared image requires positive pixel dimensions")

@dataclass(frozen=True)
class ToolResult:
    """One final Controller outcome; no deferred lookup or SDK envelope."""

    value: dict[str, Any]
    images: tuple[PreparedImage, ...] = ()
    is_error: bool = False
    side_effect: str = "none"

    def __post_init__(self):
        require_json_value(self.value)
        if self.side_effect not in SIDE_EFFECTS:
            raise NeutralContractError("unsupported tool side effect")
        if any(not isinstance(item, PreparedImage) for item in self.images):
            raise NeutralContractError("tool images must contain verified PNG values")
