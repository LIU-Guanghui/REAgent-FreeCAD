"""Explicit, frozen built-in Provider contributions; registration starts no resources."""

from dataclasses import dataclass
from types import MappingProxyType

from .capabilities import effort_choices, find_capabilities
from .providers import anthropic, deepseek, openai


@dataclass(frozen=True)
class Provider:
    name: str
    protocol: str
    path: str
    base_url: str
    request: object
    headers: object
    parser: object
    capabilities: object
    efforts: object


def freeze_providers(contributions):
    entries = {}
    for item in contributions:
        if not isinstance(item, Provider) or not all((item.name, item.protocol, item.path, item.base_url)):
            raise ValueError("Incomplete Provider definition")
        if item.name in entries:
            raise ValueError("Duplicate Provider: " + item.name)
        if not all(callable(value) for value in (item.request, item.headers, item.parser, item.capabilities, item.efforts)):
            raise ValueError("Provider is missing a required capability")
        entries[item.name] = item
    if not entries:
        raise ValueError("At least one Provider is required")
    return MappingProxyType(entries)


PROVIDERS = freeze_providers((
    Provider("Anthropic", anthropic.PROTOCOL, anthropic.PATH, anthropic.BASE_URL,
             anthropic.request, anthropic.headers, anthropic.Parser, find_capabilities, effort_choices),
    Provider("OpenAI", openai.PROTOCOL, openai.PATH, openai.BASE_URL,
             openai.request, openai.headers, openai.Parser, find_capabilities, effort_choices),
    Provider("DeepSeek", deepseek.PROTOCOL, deepseek.PATH, deepseek.BASE_URL,
             deepseek.request, deepseek.headers, deepseek.Parser, find_capabilities, effort_choices),
))
