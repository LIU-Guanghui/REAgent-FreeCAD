"""Original model result slots, frozen send mappings and Journal completion."""

from dataclasses import dataclass
import time

from REAgent.model.messages import AssistantTurn
from REAgent.model.errors import ModelError
from REAgent.model.capabilities import cache_mode
from REAgent.values import to_json_value, encode_json, decode_json
from .messages import ImageMaterial


@dataclass(frozen=True)
class SendRecord:
    active: object
    revision: int
    generation: int
    binding: str | None
    published: str
    prepared: object
    unit_ids: tuple
    delivery_ids: tuple
    observations: str

    @property
    def directory(self):
        return decode_json(self.published)


class ModelRequests:
    def __init__(self, run, model, journal):
        self.run, self._model, self.journal = run, model, journal
        self._pending_context_log = None
        self.reset()

    def reset(self):
        if getattr(self, "_model_outcome", None) is not None or self._pending_context_log is not None:
            raise ModelError("pending_results", "Recover original model and context logs before resetting requests")
        self._usage, self._usage_keys = {}, {}
        self._request_number = 0
        self._last_usage = self._last_model_diagnostic = self._model_outcome = None

    def capture(self, prepared, generation, published):
        active = self.run.active
        messages = prepared.messages if prepared is not None else ()
        sent = {id(message) for message in messages}
        units = tuple(unit for unit in active.conversation.units if all(id(item) in sent for item in unit.messages))
        return SendRecord(active, active.conversation.revision, generation, active.binding,
                          encode_json(published), prepared, tuple(unit.id for unit in units),
                          tuple(item.delivery_id for item in messages if isinstance(item, ImageMaterial) and item.images),
                          encode_json(tuple(marker for unit in units for marker in unit.observations)))

    def snapshot(self):
        return to_json_value({"usage": self._usage, "last_usage": self._last_usage,
                              "last_model_request": self._last_model_diagnostic,
                              "pending_model_result": self._model_outcome is not None})

    def use_model(self, model):
        self._model = model

    @property
    def context_log_pending(self):
        return self._pending_context_log is not None

    @property
    def model_result_pending(self):
        return self._model_outcome is not None

    def begin_close(self):
        """Explicit ending abandons local context adoption, not the Recorder."""
        self._pending_context_log = None

    def finish_close(self, *, released):
        if released:
            self._model_outcome = None

    def receipt_candidate(self):
        """Retain this original slot identity while Run prepares outside its lock."""
        return self._model_outcome

    def adopt_receipt(self, slot, record):
        """Called under Run.control.lock with its ActiveContext exchange."""
        if (slot is None or self._model_outcome is not slot
                or slot["record"] is not record or slot["reply"] is None):
            return False
        slot["adopted"] = True
        self._model_outcome = None
        return True

    def reject_receipt(self, slot):
        if slot is not None:
            self._retain_unadopted(slot, "request_cancelled")

    def _tally(self, kind, usage, identity):
        self._last_usage = {"kind": kind, "provider": identity[0], "protocol": identity[1],
                            "model": identity[3], "usage": usage}
        key = (identity, kind)
        if key not in self._usage_keys:
            self._usage_keys[key] = str(len(self._usage_keys) + 1)
        row = to_json_value(self._usage.get(self._usage_keys[key], {
            "provider": identity[0], "protocol": identity[1], "model": identity[3], "kind": kind, "attempts": 0,
            "counts": {name: {"known_sum": 0, "reported_requests": 0, "derived_requests": 0, "unknown_requests": 0}
                       for name in ("input_tokens", "output_tokens", "total_tokens", "cache_read_tokens",
                                    "cache_write_tokens", "cache_miss_tokens", "reasoning_tokens")}}))
        row["attempts"] += 1
        for name, count in row["counts"].items():
            value = None if usage is None else usage.get(name, usage.get("details", {}).get(name))
            if value is None:
                count["unknown_requests"] += 1
            else:
                count["known_sum"] += value
                count[usage["sources"].get(name, "reported") + "_requests"] += 1
        self._usage[self._usage_keys[key]] = row
        return {**self._last_usage, "totals": row}

    def _local_log(self, event, summary, *, function):
        """Persist a prepared local change before its short in-memory adoption."""
        if self.run.active is None or self.run.active.binding is None:
            return
        binding = self.run.active.binding
        self._pending_context_log = binding
        meta = self.journal(binding=binding, event=event, summary=summary, function=function)
        if (not meta["complete"] or meta["appended_sequence"] is None
                or meta["appended_sequence"] > meta["persisted_sequence"]):
            raise ModelError("context_journal_pending", "Local context change was not adopted; original journal must be recovered")
        self._pending_context_log = None

    def recover_context_log(self):
        if self._pending_context_log is None:
            return
        meta = self.journal(binding=self._pending_context_log, flush=True)
        if not meta["complete"]:
            raise ModelError("context_journal_pending", "The original local context journal is still incomplete")
        self._pending_context_log = None

    def _request_log(self, slot, event=None, summary=None, *, persist=True, flush=False):
        if slot["binding"] is None:
            return {"complete": True, "appended_sequence": 0, "persisted_sequence": 0}
        return self.journal(binding=slot["binding"], event=event, summary=summary,
                                       duration_ms=(time.perf_counter() - slot["started"]) * 1000 if event else None,
                                       persist=persist, flush=flush)

    def _retain_unadopted(self, slot, reason="model_journal_pending"):
        if slot.get("disposition_attempted"):
            return
        slot["disposition_attempted"] = True
        try:
            meta = self._request_log(slot, "model.decision_not_adopted", {
                "request": slot["number"], "kind": slot["kind"], "reason": reason}, persist=False)
            slot["disposition_sequence"] = meta["appended_sequence"]
        except ModelError:
            slot["disposition_sequence"] = None

    def recover_model_outcome(self):
        """Explicit Continue flushes the original log once, never an old decision."""
        slot = self._model_outcome
        if slot is None:
            return
        self._retain_unadopted(slot)
        meta = self._request_log(slot, flush=True)
        sequences = (slot.get("terminal_sequence"), slot.get("disposition_sequence"))
        if (not meta["complete"] or any(sequence is None or sequence > meta["persisted_sequence"]
                                         for sequence in sequences)):
            raise ModelError("model_journal_pending", "Original model result is retained; the journal is still incomplete")
        if slot["network_started"] and not slot["accounted"]:
            try:
                usage_event = self._tally(slot["kind"], slot["usage"], slot["identity"])
                slot["accounted"] = True
                self.run.publish("usage", usage_event)
            except BaseException:
                raise ModelError("model_result_pending", "Original model result is retained; usage processing is still incomplete") from None
        self._model_outcome = None

    def request(self, kind, prepared, generation, *, prepare_error=None, validate_reply=None, record=None):
        if self._model_outcome is not None:
            raise ModelError("model_journal_pending", "Use Continue to recover the original journal before another model request")
        config, identity = self._model.config, self._model.identity
        self._request_number += 1
        record = record or self.capture(prepared, generation, self.run.tools.directory())
        slot = {"record": record, "identity": identity, "number": self._request_number, "kind": kind, "generation": generation,
                "binding": record.binding,
                "started": time.perf_counter(), "network_started": False, "reply": None, "error": None,
                "accounted": False, "terminal_sequence": None, "network_time": None,
                "first_event_time": None, "first_visible_time": None}
        self._model_outcome = slot
        parameters = {"request": slot["number"], "kind": kind, "provider": identity[0], "protocol": identity[1],
                      "model": identity[3], "reasoning_effort": config.reasoning_effort,
                      "sent_effort": None if config.reasoning_effort == "default" else config.reasoning_effort,
                      "output_tokens": config.output_tokens, "binding_controls": config.binding_controls,
                      "cache_mode": cache_mode(config) or "unknown_gateway_or_model",
                      "estimate": prepared.estimate if prepared else None,
                      "checkpoint": self.run.conversation.checkpoint,
                      "retained_image_groups": sorted(self.run.conversation.retained_groups)}
        request_info = {"kind": kind, "output_tokens": config.output_tokens}

        def network_started():
            slot["network_started"] = True
            slot["network_time"] = time.perf_counter()

        def first_event():
            slot["first_event_time"] = time.perf_counter()

        try:
            previous = self._request_log(slot)
            if not previous["complete"]:
                raise ModelError("model_journal_pending", "The task journal must be complete before a model request")
            meta = self._request_log(slot, "model.request_started", {**parameters, "phase": "prepared"})
            if (not meta["complete"] or meta["appended_sequence"] is None
                    or meta["appended_sequence"] > meta["persisted_sequence"]):
                raise ModelError("model_journal_pending", "Model request start could not be persisted; no HTTP request was sent")
            if prepare_error is not None:
                raise prepare_error
            for item in self._model.stream(prepared.request, cancelled=lambda: self.run.cancelled(generation),
                                           on_network_start=network_started, on_first_event=first_event):
                if isinstance(item, AssistantTurn):
                    slot["reply"] = item
                else:
                    if item and slot["first_visible_time"] is None:
                        slot["first_visible_time"] = time.perf_counter()
                    self.run.publish("summary_preview" if kind == "summary" else "text_preview", {
                        "text": item, "generation": generation, "request": slot["number"]})
            if slot["reply"] is None:
                raise ModelError("incomplete_response", "No final model response was retained")
            if kind == "model":
                if any(call.id in self.run.call_ids for call in slot["reply"].calls):
                    raise ModelError("duplicate_call_id", "Model reused an earlier tool call identity")
                if not slot["record"].directory and slot["reply"].calls:
                    raise ModelError("chat_tools_unavailable", "No tools were published for this conversation")
            if validate_reply is not None:
                validate_reply(slot["reply"])
        except ModelError as exc:
            slot["error"] = exc
        except BaseException as exc:
            slot["error"] = ModelError("request_interrupted", "Model request processing was interrupted")
            if not isinstance(exc, Exception):
                raise
        finally:
            reply, error = slot["reply"], slot["error"]
            usage = error.usage if error is not None and error.usage is not None else reply.usage if reply is not None else None
            # Preserve the original outcome before fallible accounting, notifications or disk I/O.
            slot["usage"] = usage
            if error is not None:
                if error.usage is None:
                    error.usage = usage
                if reply is not None and reply.diagnostics:
                    error.native_reason = error.native_reason or reply.diagnostics.get("native_reason")
                    error.protocol_completed = reply.diagnostics.get("protocol_completed", False)
                error.diagnostics = {**(error.diagnostics or {}), "request": request_info}
            diagnostic = {**parameters, "network_started": slot["network_started"],
                          "network_function": "ModelClient.stream" if slot["network_started"] else None,
                          "tool_call_count": len(reply.calls) if reply is not None else None,
                          "status": "not_sent" if not slot["network_started"] else
                          "cancelled" if error is not None and error.code == "request_cancelled" else
                          "failed" if error is not None else "completed",
                          "usage": usage, "duration_ms": (time.perf_counter() - slot["started"]) * 1000,
                          "input_estimate_error_tokens": (prepared.estimate["input_tokens_estimate"] - usage["input_tokens"]
                              if prepared is not None and usage is not None and usage["input_tokens"] is not None else None),
                          "time_to_first_sse_ms": ((slot["first_event_time"] - slot["network_time"]) * 1000
                              if slot["first_event_time"] is not None and slot["network_time"] is not None else None),
                          "time_to_first_visible_ms": ((slot["first_visible_time"] - slot["network_time"]) * 1000
                              if slot["first_visible_time"] is not None and slot["network_time"] is not None else None),
                          "error": {key: value for key, value in error.detail().items()
                                    if key not in ("usage", "diagnostics")} if error is not None else None,
                          "diagnostics": reply.diagnostics if reply is not None else error.diagnostics if error else None}
            self._last_model_diagnostic = diagnostic
            usage_event, processing_failed = None, False
            try:
                if slot["network_started"] and not slot["accounted"]:
                    usage_event = self._tally(kind, usage, slot["identity"])
                    slot["accounted"] = True
            except BaseException:
                processing_failed = True
            try:
                meta = self._request_log(slot, "model.request_finished", diagnostic)
                slot["terminal_sequence"] = meta["appended_sequence"]
                journal_complete = (meta["complete"] and meta["appended_sequence"] is not None
                                    and meta["appended_sequence"] <= meta["persisted_sequence"])
            except BaseException:
                journal_complete = False
            if not journal_complete or processing_failed:
                code = "model_journal_pending" if not journal_complete else "model_result_pending"
                self._retain_unadopted(slot, code)
                raise ModelError(code, "Model result is retained; no decision was dispatched. Continue retries the original journal once",
                                 diagnostics={"request": request_info}) from None
            if usage_event is not None:
                try:
                    self.run.publish("usage", usage_event)
                except BaseException:
                    self._retain_unadopted(slot, "model_result_pending")
                    raise ModelError("model_result_pending", "Model result is retained after a presentation interruption",
                                     diagnostics={"request": request_info}) from None
            if kind != "model" or slot["error"] is not None:
                self._model_outcome = None
        if slot["error"] is not None:
            raise slot["error"]
        return slot["reply"]
