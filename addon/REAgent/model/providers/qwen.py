"""Qwen Chat Completions with native thinking controls and assistant replay."""

from . import chat_completions
from .chat_completions import PATH, PROTOCOL, MessageParser as Parser, encode_native_message
from .common import bearer_headers as headers

BASE_URL = "https://ap-southeast-1.maas.qwencloudapi.com/compatible-mode/v1"


def request(config, messages, tools, identity, *, cache_key=None):
    body = chat_completions.request(config, messages, tools, identity, encoder=encode_native_message)
    body["max_completion_tokens"] = body.pop("max_tokens")
    if config.reasoning_effort == "none":
        del body["reasoning_effort"]
        body["enable_thinking"] = False
    return body
