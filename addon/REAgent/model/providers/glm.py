"""GLM Chat Completions with streamed tools and preserved thinking."""

from . import chat_completions
from .chat_completions import PATH, PROTOCOL, MessageParser as Parser, encode_native_message
from .common import bearer_headers as headers

BASE_URL = "https://api.z.ai/api/paas/v4"


def request(config, messages, tools, identity, *, cache_key=None):
    body = chat_completions.request(config, messages, tools, identity, encoder=encode_native_message)
    del body["stream_options"]
    body["thinking"] = {"type": "enabled", "clear_thinking": False}
    if tools:
        body["tool_stream"] = True
    return body
