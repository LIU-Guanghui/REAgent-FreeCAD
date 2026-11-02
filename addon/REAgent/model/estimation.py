"""Local model accounting and same-prefix service calibration; no context semantics."""

import math
from .messages import AssistantTurn, Instruction, TextBlock, ImageBlock, ToolReply
from .capabilities import visual_tokens
from .config import model_identity
from REAgent.model.limits import (
    IMAGE_SOFT_TARGET_COUNT, IMAGE_SOFT_TARGET_TOKENS)
from REAgent.values import encode_json

def material_estimate(config, message):
    """Conservative local accounting; opaque bytes are never tokenized as text."""
    def size(value):
        return len(encode_json(value).encode("utf-8"))

    if isinstance(message, Instruction):
        return "rules", size(message.text), 0
    if isinstance(message, TextBlock):
        return message.category, size(message.text), 0
    if isinstance(message, ToolReply):
        return "results", len(message.text.encode("utf-8")), 0
    if isinstance(message, ImageBlock):
        return "images", len(message.text.encode("utf-8")) + sum(
            len(label.encode("utf-8")) + visual_tokens(config, item.width_px, item.height_px)[0]
            for label, item in message.images), 0
    if isinstance(message, AssistantTurn):
        visible = size(message.content) + size([{"id": call.id, "name": call.name,
                                                "arguments": call.arguments} for call in message.calls])
        if message.provider_state is None or message.provider_state[0] != model_identity(config):
            return "assistant_calls", visible, 0
        state = message.provider_state[1]
        encoded = size(state)
        if config.provider == "DeepSeek":
            return "provider_state", visible + encoded, encoded
        reported = (message.usage or {}).get("output_tokens")
        allowance = reported if reported is not None else config.output_tokens
        return "provider_state", visible + allowance, encoded
    raise TypeError("Unsupported message for estimation")

def image_estimates(config, messages):
    return [{"width_px": item.width_px, "height_px": item.height_px,
             "detail": "high",
             "tokens_estimate": visual_tokens(config, item.width_px, item.height_px)[0],
             "method": visual_tokens(config, item.width_px, item.height_px)[1]}
            for message in messages if isinstance(message, ImageBlock) for _label, item in message.images]

def estimate(config, baseline, last_layout, messages, tools, request_bytes):
    messages, tools_key = tuple(messages), encode_json(tools)
    parts = {"rules": 0, "tools": len(tools_key.encode("utf-8")), "user": 0,
             "assistant_calls": 0, "results": 0, "facts_summary": 0, "images": 0,
             "provider_state": 0, "protocol_reserve": 512 + 64 * len(messages)}
    opaque_bytes = 0
    for message in messages:
        category, tokens, encoded = material_estimate(config, message)
        if isinstance(message, AssistantTurn) and category == "provider_state":
            visible = len(encode_json(message.content).encode("utf-8")) + len(encode_json([
                {"id": call.id, "name": call.name, "arguments": call.arguments} for call in message.calls]).encode("utf-8"))
            parts["assistant_calls"] += visible
            tokens -= visible
        parts[category] += tokens
        opaque_bytes += encoded
    local = sum(parts.values())
    method, estimated = "local_utf8_bounds_visual_rules_and_retained_output_allowances", local
    if baseline is not None:
        previous, reply = baseline
        prefix = previous.messages
        usage = reply.usage or {}
        tail = messages[len(prefix):]
        if (tools_key == previous.tools_key and messages[:len(prefix)] == prefix
                and any(item is reply for item in tail)
                and usage.get("input_tokens") is not None and usage.get("output_tokens") is not None):
            estimated = usage["input_tokens"] + usage["output_tokens"] + sum(
                material_estimate(config, item)[1] + 64 for item in tail if item is not reply)
            estimated += max(512, math.ceil(estimated * 0.05))
            method = "service_input_plus_adopted_output_and_new_suffix_with_margin"
    change = None
    if last_layout is not None:
        old_messages, old_tools = last_layout
        if old_tools != tools_key:
            change = "tools"
        else:
            for previous, current in zip(old_messages, messages):
                if previous != current:
                    change = material_estimate(config, current)[0]
                    break
            if change is None and len(messages) < len(old_messages):
                change = "history_compaction"
    images = image_estimates(config, messages)
    return {"input_tokens_estimate": estimated, "local_tokens_estimate": local,
            "method": method, "components": parts, "opaque_encoded_bytes": opaque_bytes,
            "opaque_method": "plaintext_utf8_bound" if config.provider == "DeepSeek" else "retained_output_upper_allowance",
            "service_usage": "separate",
            "image_count": len(images), "image_bytes": sum(len(item.data) for message in messages
                if isinstance(message, ImageBlock) for _label, item in message.images),
            "image_soft_target_met": len(images) <= IMAGE_SOFT_TARGET_COUNT and sum(row["tokens_estimate"] for row in images) <= IMAGE_SOFT_TARGET_TOKENS,
            "images": images, "request_bytes": request_bytes, "first_changed_category": change}
