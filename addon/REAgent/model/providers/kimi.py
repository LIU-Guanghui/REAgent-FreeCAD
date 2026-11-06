"""Kimi Chat Completions with complete assistant replay."""

from . import chat_completions
from .chat_completions import PATH, PROTOCOL, MessageParser as Parser, encode_native_message
from .common import bearer_headers as headers

BASE_URL = "https://api.moonshot.ai/v1"


def request(config, messages, tools, identity, *, cache_key=None):
    body = chat_completions.request(config, messages, tools, identity, encoder=encode_native_message)
    body["max_completion_tokens"] = body.pop("max_tokens")
    return body
