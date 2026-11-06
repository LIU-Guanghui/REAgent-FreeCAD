"""Chat Completions projection, native assistant replay and complete SSE parsing."""

from REAgent.model.messages import AssistantTurn, Instruction, TextBlock, ImageBlock, ToolCall, ToolReply

import base64

from REAgent.model.errors import ModelError
from REAgent.model.providers.common import StreamState, standard_usage
from REAgent.values import decode_json

PROTOCOL = "chat_completions"
PATH = "/chat/completions"


def encode_message(message, identity):
    if isinstance(message, Instruction):
        return {"role": "system", "content": message.text}
    if isinstance(message, TextBlock):
        return {"role": message.role, "content": message.text}
    if isinstance(message, AssistantTurn):
        result = {"role": "assistant", "content": message.content or None}
        if message.calls:
            result["tool_calls"] = [{"id": call.id, "type": "function", "function": {
                "name": call.name, "arguments": call.arguments}} for call in message.calls]
        if message.provider_state is not None:
            owner, state = message.provider_state
            if owner == identity:
                result["reasoning_content"] = state
        return result
    if isinstance(message, ToolReply):
        if not message.call_id:
            raise ValueError("A model result requires its original call identity")
        return {"role": "tool", "tool_call_id": message.call_id, "content": message.text}
    if isinstance(message, ImageBlock):
        content = [{"type": "text", "text": message.text}]
        for identity, prepared in message.images:
            content.append({"type": "text", "text": identity})
            content.append({"type": "image_url", "image_url": {
                "url": "data:image/png;base64," + base64.b64encode(prepared.data).decode("ascii"),
                "detail": "high"}})
        return {"role": "user", "content": content}
    raise TypeError("An internal message value is required")

def encode_native_message(message, identity):
    if (isinstance(message, AssistantTurn) and message.provider_state is not None
            and message.provider_state[0] == identity):
        return message.provider_state[1]
    return encode_message(message, identity)


def request(config, messages, tools, identity, *, cache_key=None, encoder=encode_message):
    functions = [{"type": "function", "function": {
        "name": name, "description": spec["description"], "parameters": spec["input_schema"]}}
        for name, spec in tools.items()]
    body = {"model": config.model, "messages": [encoder(message, identity) for message in messages],
            "max_tokens": config.output_tokens,
            "stream": True, "stream_options": {"include_usage": True}}
    if functions:
        body["tools"] = functions
        body["tool_choice"] = "auto"
    if config.reasoning_effort != "default":
        body["reasoning_effort"] = config.reasoning_effort
    return body


class Parser(StreamState):
    def read_usage(self, value, previous=None):
        incoming = standard_usage(value)
        if previous is not None and incoming != previous:
            raise ModelError("invalid_stream", "Conflicting usage chunks", usage=previous)
        return incoming

    def delta(self, value):
        if set(value) - {"role", "content", "tool_calls", "refusal", "reasoning_content"}:
            raise ModelError("unsupported_delta_fields", "Response delta contains unsupported fields", usage=self.usage)
        return value

    def call_index(self, fragment, calls):
        index = fragment.get("index")
        if type(index) is not int or not 0 <= index < 64:
            raise ModelError("invalid_stream", "Invalid tool call index or structure", usage=self.usage)
        return index

    def merge_call(self, fragment, call):
        if set(fragment) - {"index", "id", "type", "function"}:
            raise ModelError("invalid_stream", "Invalid tool call index or structure", usage=self.usage)
        if "type" in fragment:
            if fragment["type"] != "function":
                raise ModelError("unsupported_response", "Only function tools are supported", usage=self.usage)
            call["type"] = fragment["type"]
        if "id" in fragment:
            if not isinstance(fragment["id"], str) or not fragment["id"] or (call["id"] and call["id"] != fragment["id"]):
                raise ModelError("invalid_stream", "Conflicting tool call identity", usage=self.usage)
            call["id"] = fragment["id"]
        function = fragment.get("function", {})
        if not isinstance(function, dict) or set(function) - {"name", "arguments"}:
            raise ModelError("invalid_stream", "Invalid function fragment", usage=self.usage)
        for key in ("name", "arguments"):
            if key in function:
                if not isinstance(function[key], str):
                    raise ModelError("invalid_stream", "Function fragments must be strings", usage=self.usage)
                call["function"][key] += function[key]

    def assistant_state(self, text, reasoning, calls):
        return (self.identity, "".join(reasoning)) if reasoning else None

    def finish_matches_calls(self, finish, has_calls):
        return has_calls == (finish == "tool_calls")

    def parse(self, events):
        identity = None
        text, reasoning, calls, finish = [], [], {}, None
        usage = None
        for _event, data in events:
            if data == "[DONE]":
                self.completed()
                if finish not in ("stop", "tool_calls") or identity is None:
                    raise ModelError("incomplete_stream", "End marker arrived without a valid completion", usage=usage)
                if set(calls) != set(range(len(calls))):
                    raise ModelError("invalid_stream", "Tool call indexes are incomplete", usage=usage)
                if any(call["type"] != "function" for call in calls.values()):
                    raise ModelError("invalid_stream", "Tool call type was not confirmed", usage=usage)
                final_calls = tuple(ToolCall(calls[index]["id"], calls[index]["function"]["name"],
                                            calls[index]["function"]["arguments"])
                                    for index in range(len(calls)))
                if (not self.finish_matches_calls(finish, bool(final_calls))
                        or len({item.id for item in final_calls}) != len(final_calls)
                        or any(not item.id or not item.name or not item.arguments for item in final_calls)):
                    raise ModelError("invalid_stream", "Tool call identity or finish reason is inconsistent", usage=usage)
                yield AssistantTurn(identity, "".join(text), final_calls, usage,
                                    self.assistant_state(text, reasoning, calls), self.detail())
                return
            chunk = decode_json(data)
            if isinstance(chunk, dict) and "error" in chunk:
                detail = chunk["error"]
                self.service_failure(detail.get("code", detail.get("type")) if isinstance(detail, dict) else None)
                if chunk.get("usage") is not None:
                    self.usage = self.read_usage(chunk["usage"])
                raise self.failure
            if (not isinstance(chunk, dict) or chunk.get("object") != "chat.completion.chunk"
                    or not isinstance(chunk.get("id"), str) or not chunk["id"] or "error" in chunk):
                raise ModelError("invalid_stream", "Invalid Chat Completions chunk", usage=usage)
            if identity is not None and chunk["id"] != identity:
                raise ModelError("invalid_stream", "Completion identity changed within the stream", usage=usage)
            identity = chunk["id"]
            choices = chunk.get("choices")
            # Capture the native reason even when usage in this same chunk is bad.
            reason = (choices[0].get("finish_reason") if isinstance(choices, list) and len(choices) == 1
                      and isinstance(choices[0], dict) else None)
            if reason is not None:
                self.ending(reason, ("stop", "tool_calls"))
            if chunk.get("usage") is not None:
                usage = self.usage = self.read_usage(chunk["usage"], usage)
            if choices == [] and chunk.get("usage") is not None and finish is not None:
                continue
            if (not isinstance(choices, list) or len(choices) != 1 or finish is not None
                    or not isinstance(choices[0], dict) or type(choices[0].get("index")) is not int
                    or choices[0]["index"] != 0):
                raise ModelError("invalid_stream", "Expected exactly one unfinished choice", usage=usage)
            delta = choices[0].get("delta")
            if not isinstance(delta, dict):
                raise ModelError("invalid_stream", "Response delta must be an object", usage=usage)
            delta = self.delta(delta)
            if delta.get("role") not in (None, "assistant"):
                raise ModelError("unexpected_response_role", "Response role is not assistant", usage=usage)
            refusal = delta.get("refusal")
            if refusal is not None and not isinstance(refusal, str):
                raise ModelError("invalid_stream", "Refusal delta must be a string or null", usage=usage)
            if refusal:
                self.ending("refusal", ())
            if self.failure is not None:
                if reason is not None:
                    finish = reason
                continue
            thought = delta.get("reasoning_content")
            if thought is not None:
                if not isinstance(thought, str):
                    raise ModelError("invalid_stream", "Reasoning delta must be a string or null", usage=usage)
                reasoning.append(thought)
            content = delta.get("content")
            if content is not None:
                if not isinstance(content, str):
                    raise ModelError("invalid_stream", "Text delta must be a string", usage=usage)
                text.append(content)
                if content:
                    yield content
            fragments = delta.get("tool_calls", [])
            if not isinstance(fragments, list):
                raise ModelError("invalid_stream", "Invalid tool call fragments", usage=usage)
            for fragment in fragments:
                if not isinstance(fragment, dict):
                    raise ModelError("invalid_stream", "Invalid tool call index or structure", usage=usage)
                index = self.call_index(fragment, calls)
                call = calls.setdefault(index, {"id": "", "type": None,
                                                "function": {"name": "", "arguments": ""}})
                self.merge_call(fragment, call)
            if reason is not None:
                finish = reason
        raise ModelError("incomplete_stream", "Stream ended without a complete end marker", usage=self.usage)


class MessageParser(Parser):
    """Retain the assembled native assistant, including reasoning and call extensions."""

    def assistant_state(self, text, reasoning, calls):
        message = {"role": "assistant", "content": "".join(text) if text else None}
        if reasoning:
            message["reasoning_content"] = "".join(reasoning)
        if calls:
            message["tool_calls"] = [calls[index] for index in range(len(calls))]
        return self.identity, message
