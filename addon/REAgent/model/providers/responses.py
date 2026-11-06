"""Responses projection and complete typed-event parsing."""

from REAgent.model.messages import AssistantTurn, ImageBlock, ToolCall, ToolReply

import base64

from REAgent.model.errors import ModelError
from REAgent.model.providers.common import StreamState, checked_calls, material_text, standard_usage
from REAgent.values import decode_json

PROTOCOL = "responses"
PATH = "/responses"


def request(config, messages, tools, identity, *, cache_key=None):
    items = []
    for message in messages:
        if isinstance(message, AssistantTurn):
            if message.provider_state is not None and message.provider_state[0] == identity:
                items.extend(message.provider_state[1])
                continue
            if message.content:
                items.append({"role": "assistant", "content": message.content})
            items.extend({"type": "function_call", "call_id": call.id, "name": call.name, "arguments": call.arguments}
                         for call in message.calls)
        elif isinstance(message, ToolReply):
            if not message.call_id:
                raise ValueError("A model result requires its original call identity")
            items.append({"type": "function_call_output", "call_id": message.call_id, "output": message.text})
        elif isinstance(message, ImageBlock):
            content = [{"type": "input_text", "text": message.text}]
            for label, image in message.images:
                content.extend(({"type": "input_text", "text": label},
                                {"type": "input_image", "image_url": "data:image/png;base64," +
                                 base64.b64encode(image.data).decode("ascii"), "detail": "high"}))
            items.append({"role": "user", "content": content})
        else:
            role, text = material_text(message)
            items.append({"role": role, "content": text})
    body = {"model": config.model, "input": items, "max_output_tokens": config.output_tokens,
            "stream": True, "store": False, "truncation": "disabled", "include": ["reasoning.encrypted_content"]}
    if tools:
        body["tools"] = [{"type": "function", "name": name, "description": spec["description"],
                          "parameters": spec["input_schema"], "strict": False} for name, spec in tools.items()]
        body["tool_choice"] = "auto"
    if config.reasoning_effort != "default":
        body["reasoning"] = {"effort": config.reasoning_effort}
    return body


class Parser(StreamState):
    def parse(self, events):
        identity = None
        added, done, arguments, texts = {}, {}, {}, {}
        for event, data in events:
            value = decode_json(data)
            if not isinstance(value, dict) or not isinstance(value.get("type"), str):
                raise ModelError("invalid_stream", "Invalid Responses event")
            kind = value["type"]
            if event is not None and event != kind:
                raise ModelError("invalid_stream", "Conflicting SSE event type")
            response = value.get("response")
            if response is not None:
                if (not isinstance(response, dict) or not isinstance(response.get("id"), str)
                        or not response["id"] or identity is not None and response["id"] != identity):
                    raise ModelError("invalid_stream", "Responses identity changed")
                identity = response["id"]
                if kind in ("response.failed", "response.incomplete"):
                    details = response.get("incomplete_details") or response.get("error") or {}
                    reason = details.get("reason", details.get("code")) if isinstance(details, dict) else None
                    if kind == "response.failed":
                        self.service_failure(reason)
                    else:
                        self.ending(reason, ())
                    self.protocol_completed = True
                elif kind == "response.completed":
                    if response.get("status") != "completed" or response.get("error") or response.get("incomplete_details"):
                        raise ModelError("incomplete_response", "Response did not complete successfully")
                    self.ending("completed", ("completed",))
                    self.completed()
                if response.get("usage") is not None:
                    self.usage = standard_usage(response["usage"], responses=True)
            if kind in ("error", "response.failed", "response.incomplete"):
                if self.failure is None:
                    self.service_failure(value.get("code"))
                self.completed()
            if kind in ("response.output_item.added", "response.output_item.done"):
                index, item = value.get("output_index"), value.get("item")
                if type(index) is not int or not 0 <= index < 256 or not isinstance(item, dict) or not item.get("id"):
                    raise ModelError("invalid_stream", "Invalid output item")
                if kind.endswith(".added"):
                    if index in added:
                        raise ModelError("invalid_stream", "Repeated output item")
                    added[index] = item["id"]
                else:
                    if added.get(index) != item["id"] or index in done:
                        raise ModelError("invalid_stream", "Output item identity is inconsistent")
                    done[index] = item
            elif kind in ("response.output_text.delta", "response.function_call_arguments.delta"):
                index, delta = value.get("output_index"), value.get("delta")
                if type(index) is not int or added.get(index) != value.get("item_id") or not isinstance(delta, str):
                    raise ModelError("invalid_stream", "Unbound response delta")
                if kind == "response.output_text.delta":
                    part = value.get("content_index")
                    if type(part) is not int or part < 0:
                        raise ModelError("invalid_stream", "Invalid text part")
                    texts.setdefault((index, part), []).append(delta)
                    if delta:
                        yield delta
                else:
                    arguments.setdefault(index, []).append(delta)
            elif kind == "response.completed":
                if response is None:
                    raise ModelError("incomplete_response", "Response did not complete successfully")
                output = response.get("output")
                if not isinstance(output, list) or len(output) > 256 or set(added) != set(range(len(output))) or set(done) != set(added):
                    raise ModelError("invalid_stream", "Output item completion is incomplete")
                calls, content = [], []
                for index, item in enumerate(output):
                    if not isinstance(item, dict) or done[index] != item:
                        raise ModelError("invalid_stream", "Final output differs from completed items")
                    item_type = item.get("type")
                    if item_type == "function_call":
                        if item.get("status") != "completed":
                            raise ModelError("incomplete_response", "Function arguments did not complete")
                        call = ToolCall(item.get("call_id"), item.get("name"), item.get("arguments"))
                        if index in arguments and "".join(arguments[index]) != call.arguments:
                            raise ModelError("invalid_stream", "Final function arguments differ from deltas")
                        calls.append(call)
                    elif item_type == "message":
                        if item.get("role") != "assistant" or item.get("status") != "completed" or not isinstance(item.get("content"), list):
                            raise ModelError("invalid_stream", "Invalid assistant message")
                        for part_index, part in enumerate(item["content"]):
                            if part.get("type") == "refusal":
                                self.ending("refusal", ())
                                self.completed()
                            if part.get("type") != "output_text" or not isinstance(part.get("text"), str):
                                raise ModelError("unsupported_response", "Unsupported assistant content")
                            if (index, part_index) in texts and "".join(texts[index, part_index]) != part["text"]:
                                raise ModelError("invalid_stream", "Final text differs from deltas")
                            content.append(part["text"])
                    elif item_type == "reasoning":
                        if not isinstance(item.get("encrypted_content"), str) or not item["encrypted_content"]:
                            raise ModelError("unsupported_response", "Reasoning state was not retained for stateless continuation")
                    else:
                        raise ModelError("unsupported_response", "Unsupported Responses output item")
                yield AssistantTurn(identity, "".join(content), checked_calls(calls), self.usage,
                                    (self.identity, tuple(output)), self.detail())
                return
        raise ModelError("incomplete_stream", "Responses stream ended without response.completed")
