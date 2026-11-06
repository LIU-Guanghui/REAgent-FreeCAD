"""Gemini's OpenAI-compatible chat with thought signatures in their native positions."""

from REAgent.model.errors import ModelError
from . import chat_completions
from .chat_completions import PATH, PROTOCOL, MessageParser, encode_native_message
from .common import bearer_headers as headers, standard_usage, token_count

BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai"


def request(config, messages, tools, identity, *, cache_key=None):
    return chat_completions.request(config, messages, tools, identity, encoder=encode_native_message)


def retain_signature(previous, incoming):
    if (not isinstance(incoming, dict) or set(incoming) != {"google"}
            or not isinstance(incoming["google"], dict) or set(incoming["google"]) != {"thought_signature"}
            or not isinstance(incoming["google"]["thought_signature"], str)
            or not incoming["google"]["thought_signature"]):
        raise ModelError("unsupported_response", "Unsupported Gemini assistant extension")
    if previous is not None and previous != incoming:
        raise ModelError("invalid_stream", "Conflicting Gemini thought signatures")
    return incoming


class Parser(MessageParser):
    def __init__(self, identity, *, binding_controls=False):
        super().__init__(identity, binding_controls=binding_controls)
        self.extra_content = None
        self._indexed_calls = None

    def read_usage(self, value, previous=None):
        if not isinstance(value, dict):
            raise ModelError("invalid_stream", "Invalid Gemini usage object")
        incoming = token_count(value.get("prompt_tokens"))
        visible = token_count(value.get("completion_tokens"))
        total = token_count(value.get("total_tokens"))
        # Gemini can exclude thinking from completion_tokens while including it in total_tokens.
        derived = (incoming is not None and visible is not None and total is not None
                   and total > incoming + visible)
        result = standard_usage({**value, "completion_tokens": total - incoming} if derived else value)
        if derived:
            result["sources"]["output_tokens"] = "derived"
            result["details"]["visible_output_tokens"] = visible
        if previous is not None:
            for key in ("input_tokens", "output_tokens", "total_tokens"):
                before, after = previous[key], result[key]
                if before is not None and after is not None and (after < before or key == "input_tokens" and after != before):
                    raise ModelError("invalid_stream", "Conflicting cumulative Gemini usage", usage=previous)
        return result

    def delta(self, value):
        value = dict(value)
        extra = value.pop("extra_content", None)
        if extra is not None:
            self.extra_content = retain_signature(self.extra_content, extra)
        return super().delta(value)

    def call_index(self, fragment, calls):
        indexed = "index" in fragment
        if self._indexed_calls is not None and indexed != self._indexed_calls:
            raise ModelError("invalid_stream", "Gemini tool calls cannot mix indexed fragments and unindexed complete calls")
        self._indexed_calls = indexed
        if indexed:
            return super().call_index(fragment, calls)
        # The compatible endpoint also emits complete, id-bound calls without an index.
        function = fragment.get("function")
        if (not isinstance(fragment.get("id"), str) or not fragment["id"] or fragment.get("type") != "function"
                or not isinstance(function, dict)
                or any(not isinstance(function.get(key), str) or not function[key] for key in ("name", "arguments"))
                or any(call["id"] == fragment["id"] for call in calls.values())):
            raise ModelError("invalid_stream", "Unindexed Gemini calls must be complete and uniquely identified")
        return super().call_index({"index": len(calls)}, calls)

    def merge_call(self, fragment, call):
        fragment = dict(fragment)
        extra = fragment.pop("extra_content", None)
        if extra is not None:
            call["extra_content"] = retain_signature(call.get("extra_content"), extra)
        super().merge_call(fragment, call)

    def finish_matches_calls(self, finish, has_calls):
        return finish == "stop" or super().finish_matches_calls(finish, has_calls)

    def assistant_state(self, text, reasoning, calls):
        owner, message = super().assistant_state(text, reasoning, calls)
        if self.extra_content is not None:
            message["extra_content"] = self.extra_content
        return owner, message
