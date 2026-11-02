"""One run owns the active context; domain preparation always happens outside locks."""

from threading import Lock

from REAgent.model.errors import CapacityError, ModelError
from REAgent.values import to_json_value, decode_json
from .batch_slots import BatchSlots
from .context import ActiveContext
from .control import Control
from .loop import run
from .projection import ContextModel
from .model_requests import ModelRequests
from .tools import EmptyTools, check_tools


class Run:
    def __init__(self, model, *, journal, on_state, on_event=None):
        self.lock = Lock()
        self.control = Control()
        self.active = None
        self.tools = EmptyTools()
        self.model = model
        self.context_model = ContextModel(model)
        self.batches = BatchSlots()
        self.requests = ModelRequests(self, model, journal)
        self.call_ids = set()
        self.on_state, self.on_event = on_state, on_event
        self.status = "idle"
        self.cached_snapshot = {"status": "idle", "mode": None, "model_configured": True}
        self.callback_error = self.estimate = self.context_info = self.proxy = self.last_compaction = None

    @property
    def conversation(self):
        return self.active.conversation if self.active is not None else None

    def enter(self):
        if not self.lock.acquire(blocking=False):
            raise RuntimeError("The original worker is still active; do not start another action")

    def activate(self, conversation, policy, binding, tools):
        check_tools(tools)
        candidate = ActiveContext(conversation, policy, binding)
        with self.control.lock:
            self.active = candidate
            self.control.active = conversation is not None
        self.tools = tools

    def snapshot(self):
        with self.control.lock:
            snapshot = {**self.cached_snapshot, "pause_requested": self.control.paused,
                        "generation": self.control.generation,
                        "pending_supplements": len(self.control.supplements)}
        return to_json_value(snapshot)

    def state(self, status, reason=None):
        self.status = "ready" if status == "model_stopped" else status
        snapshot = self.on_state(self.status, reason)
        detached = to_json_value(snapshot)
        with self.control.lock:
            self.cached_snapshot = detached
        self.publish("state", snapshot)
        return self.snapshot()

    def publish(self, event=None, value=None, *, images=(), status=None, reason=None):
        if status is not None:
            return self.state(status, reason)
        if self.on_event is None:
            return
        try:
            binding = self.active.binding if self.active is not None else None
            accepted = self.on_event({"event": event, "session_id": binding,
                                      **to_json_value(value), "images": tuple(images)})
            if accepted is False:
                self.control.pause()
        except Exception as exc:
            self.callback_error = "Event consumer failed: " + type(exc).__name__
            self.control.pause()

    def turn_control(self, *, resume=False):
        return self.control.capture(resume=resume)

    def cancelled(self, generation):
        return self.control.cancelled(generation)

    def begin_batch(self, reply, rows):
        self.batches.begin(reply, rows)

    def record_result(self, index, result):
        self.batches.record(index, result)

    def prepare_retention(self, group, policy, generation, details):
        if not self.conversation.loaded_group(group):
            raise ValueError("Read this complete registered image group into context first")
        self.batches.retention = {"group": group, "policy": policy, "generation": generation, "details": details}

    def commit_batch(self, reply=None, *, recover=False):
        if recover:
            self.requests.recover_context_log()
        elif self.requests.context_log_pending:
            raise ModelError("context_journal_pending", "Use Continue to recover the original context journal")
        if reply is not None:
            self.begin_batch(reply, [])
        active, pending, retention = self.active, self.batches.pending, self.batches.retention
        human = self.batches.human
        source = active.conversation
        revision = source.revision
        with self.control.lock:
            supplements = tuple(self.control.supplements)
            generation, paused = self.control.generation, self.control.paused
        if pending is None and human is None and not supplements:
            return
        candidate = source.fork()
        notifications = ()
        if pending is not None:
            rows = list(pending.rows)
            if retention is not None:
                adopted = retention["generation"] == generation and not paused
                rows, notifications, adopted = self.tools.stage_retention(pending.reply, rows, retention, adopted)
                if adopted:
                    candidate.retain(retention["group"], retention["policy"])
                self.requests._local_log("context.retention_prepared", {
                    **retention["details"], "generation": retention["generation"],
                    "adoption": "ready" if adopted else "rejected", "checkpoint": candidate.checkpoint},
                    function="Run.prepare_retention")
            materials, observations = self.tools.reply_materials(pending.reply, rows)
            candidate.record_reply(pending.reply, materials, observations=observations)
        if human is not None:
            result, project = human
            candidate.record_human(project(result))
        for number, text in supplements:
            candidate.add_requirement(number, text)
        policy = self.tools.prepare_context(active.policy, candidate)
        replacement = ActiveContext(candidate, policy, active.binding)
        last = tuple(row[1] for row in rows) if pending is not None else self.batches.latest_results
        # All domain work, logging and callbacks precede this short compare/exchange.
        with self.control.lock:
            if (self.active is not active or source.revision != revision or self.batches.pending is not pending
                    or self.batches.retention is not retention
                    or self.batches.human is not human
                    or self.control.generation != generation or self.control.paused != paused
                    or tuple(self.control.supplements[:len(supplements)]) != supplements):
                raise ModelError("pending_results", "Staged context changed; original batch and supplements retained")
            self.active = replacement
            self.batches.latest_results = last
            self.batches.pending = self.batches.retention = self.batches.human = None
            del self.control.supplements[:len(supplements)]
        for notification in notifications:
            self.publish("tool_result", notification)

    def context_callbacks(self, generation):
        if (self.batches.pending is not None or self.batches.human is not None or self.requests.model_result_pending
                or self.requests.context_log_pending):
            raise ModelError("pending_results", "Recover the original results and journal before preparing context")
        if self.cancelled(generation):
            raise ModelError("request_cancelled", "Requirements changed before context preparation")
        return {"cancelled": lambda: self.cancelled(generation),
                "request_summary": lambda prepared, validate: self.requests.request(
                    "summary", prepared, generation, validate_reply=validate),
                "commit": lambda candidate, source: self._adopt_context(candidate, source, generation),
                "attempt_key": (generation, self.model.identity, self.model.config)}

    def _adopt_context(self, candidate, source, generation):
        active = self.active
        revision = source.revision
        if (self.batches.pending is not None or self.batches.human is not None or self.requests.model_result_pending
                or self.requests.context_log_pending):
            raise ModelError("pending_results", "Original results must be resolved before context adoption")
        changed = candidate.last_compaction is not source.last_compaction
        policy = self.tools.prepare_context(active.policy, candidate)
        replacement = ActiveContext(candidate, policy, active.binding)
        if changed:
            self.requests._local_log("context.replacement_prepared", candidate.last_compaction, function="Run._adopt_context")
        with self.control.lock:
            if (self.control.paused or self.control.generation != generation or self.active is not active
                    or active.conversation is not source or source.revision != revision):
                raise ModelError("request_cancelled", "Context replacement cancelled; original history retained")
            self.active = replacement
            if changed:
                self.last_compaction = candidate.last_compaction

    def record_human(self, result, project):
        if self.batches.human is not None:
            raise ModelError("pending_results", "An original human result still needs recording")
        self.batches.latest_results = (result,)
        self.batches.human = (result, project)
        self.commit_batch()

    def prepare_request(self, generation):
        active = self.active
        self.tools.check(active.policy)
        facts, published = self.tools.facts(active.policy), self.tools.directory()
        self.state("preparing_model")
        try:
            prepared = active.conversation.prepare(self.context_model, published, facts,
                                                  **self.context_callbacks(generation))
        except CapacityError as exc:
            self.requests.request("model", None, generation, prepare_error=exc)
            raise
        record = self.requests.capture(prepared, generation, published)
        self.estimate = dict(prepared.estimate)
        self.proxy = self.model.proxy_status()
        self.state("requesting_model")
        return record, published

    def request_model(self, record, generation):
        try:
            reply = self.requests.request("model", record.prepared, generation, record=record)
        except ModelError as exc:
            if exc.code != "model_context_limit" or self.cancelled(generation):
                raise
            active = self.active
            self.tools.check(active.policy)
            published = self.tools.directory()
            prepared = active.conversation.compact(self.context_model, published, self.tools.facts(active.policy),
                **self.context_callbacks(generation), reason="service_context_limit")
            record = self.requests.capture(prepared, generation, published)
            self.estimate = dict(prepared.estimate)
            reply = self.requests.request("model", prepared, generation, record=record)
        self._adopt_receipt(record)
        self.model.observe_usage(record.prepared.request, reply)
        self.call_ids.update(call.id for call in reply.calls)
        return reply

    def _adopt_receipt(self, record):
        slot = self.requests.receipt_candidate()
        active = record.active
        candidate = active.conversation.fork()
        candidate.receipt(record.unit_ids, record.delivery_ids)
        policy = self.tools.prepare_receipt(active.policy, decode_json(record.observations))
        replacement = ActiveContext(candidate, policy, record.binding)
        with self.control.lock:
            accepted = (self.active is active and active.conversation.revision == record.revision
                        and active.binding == record.binding and self.control.generation == record.generation
                        and not self.control.paused and self.requests.adopt_receipt(slot, record))
            if accepted:
                self.active = replacement
        if not accepted:
            self.requests.reject_receipt(slot)
            raise ModelError("request_cancelled", "Original main request was not adopted; no delivery receipt issued")

    def advance(self, *, resume):
        return run(self, self.tools, resume=resume)

    def use_model(self, model):
        self.model = self.context_model.client = model
        self.requests.use_model(model)
