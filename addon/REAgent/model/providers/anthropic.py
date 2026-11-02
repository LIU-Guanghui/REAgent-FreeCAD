"""Anthropic Messages projection, signed thinking and native stream completion."""

from REAgent.model.messages import AssistantTurn, TextBlock, ImageBlock, ToolCall, ToolReply

import base64

from REAgent.model.errors import ModelError
from REAgent.model.providers.common import StreamState, checked_calls, material_text, token_count, usage_value
from REAgent.values import decode_json, encode_json
from REAgent.model.capabilities import cache_mode

PROTOCOL = "messages"
PATH = "/messages"
BASE_URL = "https://api.anthropic.com/v1"


def headers(config, key):
    result = {"x-api-key": key, "anthropic-version": "2023-06-01"}
    if config.binding_controls:
        result["anthropic-beta"] = "thinking-binding-controls-2026-08-01"
    return result


def request(config, messages, tools, identity, *, cache_key=None):
    system, rows, coverage = [], [], []

    def append(role, content):
        if rows and rows[-1]["role"] == role:
            rows[-1]["content"].extend(content)
        else:
            rows.append({"role": role, "content": content})

    for message in messages:
        if isinstance(message, TextBlock) and message.trailing:
            coverage.append({"type": "text", "text": message.text})
        elif isinstance(message, AssistantTurn):
            if message.provider_state is not None and message.provider_state[0] == identity:
                content = []
                for block in message.provider_state[1]:
                    if block["type"] == "tool_use":
                        content.append({"type": "tool_use", "id": block["id"], "name": block["name"],
                                        "input": decode_json(block["input_json"])})
                    else:
                        content.append(dict(block))
            else:
                content = [{"type": "text", "text": message.content}] if message.content else []
                content.extend({"type": "tool_use", "id": call.id, "name": call.name,
                                "input": decode_json(call.arguments)} for call in message.calls)
            if content:
                append("assistant", content)
        elif isinstance(message, ToolReply):
            if not message.call_id:
                raise ValueError("A model result requires its original call identity")
            append("user", [{"type": "tool_result", "tool_use_id": message.call_id,
                             "content": message.text}])
        elif isinstance(message, ImageBlock):
            content = [{"type": "text", "text": message.text}]
            for label, image in message.images:
                content.extend(({"type": "text", "text": label},
                                {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                                            "data": base64.b64encode(image.data).decode("ascii")}}))
            append("user", content)
        else:
            role, text = material_text(message)
            block = {"type": "text", "text": text}
            if role == "system":
                system.append(block)
            else:
                append(role, [block])
    # Summary coverage is request data; keep it after history, without assistant prefill.
    if coverage:
        append("user", coverage)
    body = {"model": config.model, "system": system, "messages": rows,
            "max_tokens": config.output_tokens, "stream": True}
    if tools:
        body["tools"] = [{"name": name, "description": spec["description"], "input_schema": spec["input_schema"]}
                         for name, spec in tools.items()]
        body["tool_choice"] = {"type": "auto"}
    if config.reasoning_effort != "default":
        body["output_config"] = {"effort": config.reasoning_effort}
    if config.binding_controls:
        body["thinking"] = {"type": "adaptive", "block_binding": {"prefix_mismatch_behavior": "drop_block"}}
    if cache_mode(config) == "anthropic":
        if system:
            system[-1]["cache_control"] = {"type": "ephemeral"}
        boundaries = [row["content"][-1] for row in rows if row["role"] == "user" and row["content"]]
        # Earlier turn ends remain explicit even when a new image turn exceeds the 20-block lookback.
        for block in boundaries[-3:]:
            block["cache_control"] = {"type": "ephemeral"}
    return body


def normalize_usage(value):
    base = token_count(value.get("input_tokens"))
    cache_read = token_count(value.get("cache_read_input_tokens"))
    cache_write = token_count(value.get("cache_creation_input_tokens"))
    source = "reported"
    if cache_read is not None or cache_write is not None:
        base = None if base is None or cache_read is None or cache_write is None else base + cache_read + cache_write
        source = "derived"
    return usage_value(base, value.get("output_tokens"), input_source=source,
                       details={"cache_read_tokens": cache_read, "cache_write_tokens": cache_write})


class Parser(StreamState):
    def _transformations(self, value, *, required=False):
        if "input_transformations" not in value and not required:
            return
        rows = value.get("input_transformations")
        if not isinstance(rows, list):
            raise ModelError("provider_capability_unconfirmed", "Service did not confirm its thinking binding contract")
        counts = {}
        for row in rows:
            if not isinstance(row, dict):
                raise ModelError("invalid_stream", "Invalid thinking transformation report")
            if row.get("type") == "thinking_mismatch_allowed" and self.binding_controls:
                raise ModelError("provider_capability_unconfirmed", "Service did not apply the requested thinking binding policy")
            if row.get("type") == "thinking_dropped" and row.get("reason") in (
                    "prefix_binding_mismatch", "model_binding_mismatch", "organization_binding_mismatch"):
                reason = row["reason"]
                counts[reason] = counts.get(reason, 0) + 1
        self.diagnostics["thinking_dropped"] = counts

    def parse(self, events):
        identity, opened, finish = None, None, None
        blocks, calls, raw_usage = [], [], {}
        for event, data in events:
            value = decode_json(data)
            if not isinstance(value, dict) or not isinstance(value.get("type"), str):
                raise ModelError("invalid_stream", "Invalid Messages event")
            kind = value["type"]
            if event is not None and event != kind:
                raise ModelError("invalid_stream", "Conflicting SSE event type")
            if kind == "error":
                detail = value.get("error")
                self.service_failure(detail.get("type") if isinstance(detail, dict) else None)
                raise self.failure
            if kind == "ping":
                continue
            if kind == "message_start":
                message = value.get("message")
                if (identity is not None or not isinstance(message, dict) or message.get("role") != "assistant"
                        or message.get("type") != "message" or not isinstance(message.get("id"), str)
                        or not message["id"] or message.get("content") != []):
                    raise ModelError("invalid_stream", "Invalid Messages start")
                identity = message["id"]
                if not isinstance(message.get("usage"), dict):
                    raise ModelError("invalid_stream", "Invalid Messages usage")
                raw_usage.update(message["usage"])
                self.usage = normalize_usage(raw_usage)
                self._transformations(message, required=self.binding_controls)
                continue
            if identity is None:
                raise ModelError("invalid_stream", "Messages event arrived before message_start")
            if self.failure is not None and kind not in ("message_delta", "message_stop"):
                raise ModelError("invalid_stream", "Content arrived after a failed completion")
            if kind == "content_block_start":
                index, block = value.get("index"), value.get("content_block")
                if opened is not None or type(index) is not int or index != len(blocks) or index >= 256 or not isinstance(block, dict):
                    raise ModelError("invalid_stream", "Invalid content block start")
                block = dict(block)
                block_type = block.get("type")
                if block_type == "tool_use":
                    if not isinstance(block.get("input"), dict):
                        raise ModelError("invalid_stream", "Tool input must be an object")
                    block["input_json"] = ""
                elif block_type == "text":
                    if not isinstance(block.get("text"), str):
                        raise ModelError("invalid_stream", "Invalid text block")
                    if block["text"]:
                        yield block["text"]
                elif block_type == "thinking":
                    if not isinstance(block.get("thinking"), str) or not isinstance(block.get("signature"), str):
                        raise ModelError("invalid_stream", "Invalid thinking block")
                elif block_type == "redacted_thinking":
                    if not isinstance(block.get("data"), str):
                        raise ModelError("invalid_stream", "Invalid redacted thinking")
                else:
                    raise ModelError("unsupported_response", "Unsupported Messages content block")
                blocks.append(block)
                opened = index
            elif kind == "content_block_delta":
                if type(value.get("index")) is not int or value["index"] != opened:
                    raise ModelError("invalid_stream", "Unbound Messages delta")
                block, delta = blocks[opened], value.get("delta")
                if not isinstance(delta, dict):
                    raise ModelError("invalid_stream", "Invalid content delta")
                key = {"text_delta": "text", "thinking_delta": "thinking", "signature_delta": "signature",
                       "input_json_delta": "partial_json"}.get(delta.get("type"))
                expected = {"text_delta": "text", "thinking_delta": "thinking", "signature_delta": "thinking",
                            "input_json_delta": "tool_use"}.get(delta.get("type"))
                if key is None or expected != block["type"] or not isinstance(delta.get(key), str):
                    raise ModelError("unsupported_response", "Unsupported content delta")
                block["input_json" if key == "partial_json" else key] += delta[key]
                if key == "text" and delta[key]:
                    yield delta[key]
            elif kind == "content_block_stop":
                if type(value.get("index")) is not int or value["index"] != opened:
                    raise ModelError("invalid_stream", "Unbound content stop")
                block = blocks[opened]
                if block["type"] == "tool_use":
                    block["input_json"] = block["input_json"] or encode_json(block["input"])
                    del block["input"]
                    calls.append(ToolCall(block.get("id"), block.get("name"), block["input_json"]))
                if block["type"] == "thinking" and not block["signature"]:
                    raise ModelError("invalid_stream", "Thinking signature was not completed")
                opened = None
            elif kind == "message_delta":
                delta = value.get("delta")
                reason = delta.get("stop_reason") if isinstance(delta, dict) else None
                if reason is not None:
                    self.ending(reason, ("end_turn", "tool_use"))
                if opened is not None or not isinstance(delta, dict):
                    raise ModelError("invalid_stream", "Invalid message completion delta")
                if reason is not None:
                    if finish is not None and finish != reason:
                        raise ModelError("invalid_stream", "Messages stop reason changed")
                    finish = reason
                if value.get("usage") is not None:
                    if not isinstance(value["usage"], dict):
                        raise ModelError("invalid_stream", "Invalid usage delta")
                    raw_usage.update(value["usage"])
                    self.usage = normalize_usage(raw_usage)
                self._transformations(value)
            elif kind == "message_stop":
                self.completed()
                if opened is not None or finish not in ("end_turn", "tool_use") or bool(calls) != (finish == "tool_use"):
                    raise ModelError("incomplete_response", "Messages response did not finish consistently")
                content = "".join(block["text"] for block in blocks if block["type"] == "text")
                yield AssistantTurn(identity, content, checked_calls(calls), self.usage,
                                    (self.identity, tuple(blocks)), self.detail())
                return
        raise ModelError("incomplete_stream", "Messages stream ended without message_stop")
