"""Model fields, endpoint identity and credential binding."""

import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit, urlunsplit
from .registry import PROVIDERS
from .limits import CONTEXT_RESERVE_TOKENS
from REAgent.values import encode_json

DEFAULT_OUTPUT_TOKENS = 128000

def endpoint(provider, base_url):
    if provider not in PROVIDERS:
        raise ValueError("Choose a registered model provider")
    if (not isinstance(base_url, str) or not base_url
            or re.search(r"[\s\x00-\x1f\x7f\\]", base_url)):
        raise ValueError("Base URL must be an HTTP/HTTPS API root without whitespace")
    try:
        url = urlsplit(base_url)
        port = url.port
    except ValueError:
        raise ValueError("Base URL has an invalid authority or port") from None
    if (url.scheme not in ("http", "https") or not url.hostname or port == 0
            or url.username is not None or url.password is not None or "@" in url.netloc
            or "?" in base_url or "#" in base_url):
        raise ValueError("Base URL requires a host and no credentials, query or fragment")
    path = url.path.rstrip("/")
    if any(path.endswith(adapter.path) for adapter in PROVIDERS.values()):
        raise ValueError("Enter the API root in Base URL, without messages, responses or chat/completions")
    return urlunsplit((url.scheme.lower(), url.netloc.lower(), path + PROVIDERS[provider].path, "", ""))

def input_limit_tokens(config):
    """Usable input room: context minus the output budget and working reserve."""
    return config.context_tokens - config.output_tokens - CONTEXT_RESERVE_TOKENS


@dataclass(frozen=True)
class ModelConfig:
    provider: str
    base_url: str
    model: str
    context_tokens: int
    # Application budgets, not claims about the service's limits or token usage.
    output_tokens: int = DEFAULT_OUTPUT_TOKENS
    reasoning_effort: str = "default"
    binding_controls: bool = field(init=False)

    def __post_init__(self):
        endpoint(self.provider, self.base_url)
        if not isinstance(self.model, str) or not self.model.strip():
            raise ValueError("An explicit model is required")
        provider = PROVIDERS[self.provider]
        if self.reasoning_effort not in provider.efforts(self.provider, provider.protocol, self.model):
            raise ValueError("Choose a native reasoning effort supported by this model")
        capabilities = provider.capabilities(self.provider, provider.protocol, self.model)
        object.__setattr__(self, "binding_controls", bool(capabilities and capabilities.binding_controls))
        for name in ("context_tokens", "output_tokens"):
            if type(getattr(self, name)) is not int or getattr(self, name) <= 0:
                raise ValueError(name + " must be a positive integer")
        if input_limit_tokens(self) <= 0:
            raise ValueError("Context size must exceed max response plus %s tokens of working space" % CONTEXT_RESERVE_TOKENS)

def model_identity(config):
    return (config.provider, PROVIDERS[config.provider].protocol, endpoint(config.provider, config.base_url), config.model)

def credential_binding(provider, base_url):
    return encode_json((provider, PROVIDERS[provider].protocol, endpoint(provider, base_url)))
