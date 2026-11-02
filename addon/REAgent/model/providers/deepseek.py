"""DeepSeek Chat Completions projection and complete-response parsing."""

from REAgent.model.messages import AssistantTurn, Instruction, TextBlock, ImageBlock, ToolCall, ToolReply

import base64

from REAgent.model.errors import ModelError
from REAgent.model.providers.common import StreamState, standard_usage
from REAgent.values import decode_json

PROTOCOL = "chat_completions"
PATH = "/chat/completions"
BASE_URL = "https://api.deepseek.com/v1"


def headers(config, key):
    return {"Authorization": "Bearer " + key}


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

def request(config, messages, tools, identity, *, cache_key=None):
    functions = [{"type": "function", "function": {
        "name": name, "description": spec["description"], "parameters": spec["input_schema"]}}
        for name, spec in tools.items()]
    body = {"model": config.model, "messages": [encode_message(message, identity) for message in messages],
            "max_tokens": config.output_tokens, "n": 1,
            "stream": True, "stream_options": {"include_usage": True},
            "tool_choice": "auto" if functions else "none"}
    if functions:
        body["tools"] = functions
    if config.reasoning_effort != "default":
        body["reasoning_effort"] = config.reasoning_effort
    return body


class Parser(StreamState):
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
                final_calls = tuple(ToolCall(calls[index]["id"], calls[index]["name"], calls[index]["arguments"])
                                    for index in range(len(calls)))
                if (bool(final_calls) != (finish == "tool_calls")
                        or len({item.id for item in final_calls}) != len(final_calls)
                        or any(not item.id or not item.name or not item.arguments for item in final_calls)):
                    raise ModelError("invalid_stream", "Tool call identity or finish reason is inconsistent", usage=usage)
                yield AssistantTurn(identity, "".join(text), final_calls, usage,
                                    (self.identity, "".join(reasoning)) if reasoning else None, self.detail())
                return
            chunk = decode_json(data)
            if isinstance(chunk, dict) and "error" in chunk:
                detail = chunk["error"]
                self.service_failure(detail.get("code", detail.get("type")) if isinstance(detail, dict) else None)
                if chunk.get("usage") is not None:
                    self.usage = standard_usage(chunk["usage"])
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
                incoming = standard_usage(chunk["usage"])
                if usage is not None and incoming != usage:
                    raise ModelError("invalid_stream", "Conflicting usage chunks", usage=usage)
                usage = self.usage = incoming
            if choices == [] and chunk.get("usage") is not None and finish is not None:
                continue
            if (not isinstance(choices, list) or len(choices) != 1 or finish is not None
                    or not isinstance(choices[0], dict) or type(choices[0].get("index")) is not int
                    or choices[0]["index"] != 0):
                raise ModelError("invalid_stream", "Expected exactly one unfinished choice", usage=usage)
            delta = choices[0].get("delta")
            if not isinstance(delta, dict):
                raise ModelError("invalid_stream", "Response delta must be an object", usage=usage)
            if set(delta) - {"role", "content", "tool_calls", "refusal", "reasoning_content"}:
                raise ModelError("unsupported_delta_fields", "Response delta contains unsupported fields", usage=usage)
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
                if (not isinstance(fragment, dict) or set(fragment) - {"index", "id", "type", "function"}
                        or type(fragment.get("index")) is not int or not 0 <= fragment["index"] < 64):
                    raise ModelError("invalid_stream", "Invalid tool call index or structure", usage=usage)
                call = calls.setdefault(fragment["index"], {"id": "", "name": "", "arguments": "", "type": None})
                if "type" in fragment:
                    if fragment["type"] != "function":
                        raise ModelError("unsupported_response", "Only function tools are supported", usage=usage)
                    call["type"] = fragment["type"]
                if "id" in fragment:
                    if not isinstance(fragment["id"], str) or not fragment["id"] or (call["id"] and call["id"] != fragment["id"]):
                        raise ModelError("invalid_stream", "Conflicting tool call identity", usage=usage)
                    call["id"] = fragment["id"]
                function = fragment.get("function", {})
                if not isinstance(function, dict) or set(function) - {"name", "arguments"}:
                    raise ModelError("invalid_stream", "Invalid function fragment", usage=usage)
                for key in ("name", "arguments"):
                    if key in function:
                        if not isinstance(function[key], str):
                            raise ModelError("invalid_stream", "Function fragments must be strings", usage=usage)
                        call[key] += function[key]
            if reason is not None:
                finish = reason
        raise ModelError("incomplete_stream", "Stream ended without a complete end marker", usage=self.usage)
