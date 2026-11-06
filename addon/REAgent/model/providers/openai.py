"""OpenAI Responses with native prompt-cache controls."""

from REAgent.model.capabilities import cache_mode
from . import responses
from .common import bearer_headers as headers
from .responses import PATH, PROTOCOL, Parser

BASE_URL = "https://api.openai.com/v1"


def request(config, messages, tools, identity, *, cache_key=None):
    body = responses.request(config, messages, tools, identity)
    if cache_mode(config) == "openai_explicit":
        body["prompt_cache_options"] = {"mode": "implicit", "ttl": "30m"}
        if cache_key is not None:
            body["prompt_cache_key"] = cache_key
    return body
