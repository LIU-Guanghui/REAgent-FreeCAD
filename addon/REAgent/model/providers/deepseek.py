"""DeepSeek Chat Completions with reasoning-content replay."""

from . import chat_completions
from .chat_completions import PATH, PROTOCOL, Parser
from .common import bearer_headers as headers

BASE_URL = "https://api.deepseek.com/v1"


def request(config, messages, tools, identity, *, cache_key=None):
    body = chat_completions.request(config, messages, tools, identity)
    body["n"] = 1
    if not tools:
        body["tool_choice"] = "none"
    return body
