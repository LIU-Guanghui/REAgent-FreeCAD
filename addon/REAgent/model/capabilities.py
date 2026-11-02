"""Exact protocol capabilities; never derive or limit an output budget here."""

import math
from dataclasses import dataclass
from urllib.parse import urlsplit
from REAgent.model.limits import UNKNOWN_IMAGE_TOKENS


EFFORTS = ("default", "none", "low", "medium", "high", "xhigh", "max")
_THINKING = ("low", "medium", "high", "xhigh", "max")


@dataclass(frozen=True)
class ModelCapabilities:
    provider: str
    protocol: str
    model: str
    efforts: tuple
    image_input: bool = True
    tools: bool = True
    binding_controls: bool = False
    vision: str = "unknown"
    cache: str | None = None


CATALOG = (
    ModelCapabilities("OpenAI", "responses", "gpt-6-astra", _THINKING,
                      vision="patch32_high", cache="openai_explicit"),
    ModelCapabilities("OpenAI", "responses", "gpt-6.1-sol", _THINKING, cache="openai_explicit"),
    ModelCapabilities("OpenAI", "responses", "gpt-6.1-terra", ("none", *_THINKING), cache="openai_explicit"),
    ModelCapabilities("OpenAI", "responses", "gpt-6-luna", ("none", *_THINKING), cache="openai_explicit"),
    *(ModelCapabilities("Anthropic", "messages", name, _THINKING, binding_controls=True,
                        vision="patch28_high", cache="anthropic")
      for name in ("claude-fable-5-1", "claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5")),
    ModelCapabilities("DeepSeek", "chat_completions", "deepseek-flash", ("none", "low", "high", "max"),
                      vision="deepseek_1024", cache="automatic"),
    ModelCapabilities("DeepSeek", "chat_completions", "deepseek-v4-pro", ("none", "low", "high", "max"),
                      image_input=False, cache="automatic"),
)


def find_capabilities(provider, protocol, model):
    return next((row for row in CATALOG if (row.provider, row.protocol, row.model)
                 == (provider, protocol, model)), None)


def effort_choices(provider, protocol, model):
    row = find_capabilities(provider, protocol, model)
    return ("default", *row.efforts) if row else EFFORTS


def cache_mode(config):
    """Explicit vendor controls require the exact native endpoint and model."""
    roots = {"OpenAI": ("api.openai.com", "/v1"), "Anthropic": ("api.anthropic.com", "/v1"),
             "DeepSeek": ("api.deepseek.com", "/v1")}
    url = urlsplit(config.base_url)
    if url.scheme != "https" or url.port not in (None, 443) or (url.hostname, url.path.rstrip("/")) != roots[config.provider]:
        return None
    row = find_capabilities(config.provider, {"OpenAI": "responses", "Anthropic": "messages",
                            "DeepSeek": "chat_completions"}[config.provider], config.model)
    return row.cache if row else None


def visual_tokens(config, width, height):
    """A stated upper estimate for the actual high-detail representation, not usage."""
    protocol = {"OpenAI": "responses", "Anthropic": "messages", "DeepSeek": "chat_completions"}[config.provider]
    row = find_capabilities(config.provider, protocol, config.model)
    rule = row.vision if row and cache_mode(config) is not None else "unknown"
    if width <= 0 or height <= 0:
        return UNKNOWN_IMAGE_TOKENS, "unknown_dimensions"
    if rule == "deepseek_1024":
        return 1024, "native_visual_upper_bound"
    if rule == "patch28_high":
        scale = min(1, 2576 / max(width, height))
        return min(4784, math.ceil(width * scale / 28) * math.ceil(height * scale / 28)), rule
    if rule == "patch32_high":
        scale = min(1, 65535 / max(width, height))
        width, height = max(1, int(width * scale)), max(1, int(height * scale))
        return math.ceil(min(2500, math.ceil(width / 32) * math.ceil(height / 32)) * 1.2), rule
    return UNKNOWN_IMAGE_TOKENS, "unknown_model_or_gateway_visual_allowance"
