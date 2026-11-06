"""One run owns the active context; domain preparation always happens outside locks."""

from threading import Lock
from dataclasses import replace

from REAgent.model.errors import CapacityError, ModelError
from REAgent.values import to_json_value, decode_json
from .batch_slots import BatchSlots
from .actions import Action, ReportState
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
        self.stream_chat = False
        self.request_identity = {}
        self.requirement_receipts = {}
        self.last_adoption = None
        self.report = None
        self.used_handoffs = {}
        self.cached_snapshot = {"status": "idle", "mode": None, "model_configured": True}
        self.callback_error = self.estimate = self.context_info = self.proxy = self.last_compaction = None

    @property
    def conversation(self):
        return self.active.conversation if self.active is not None else None

    @property
    def action(self):
        return self.active.action if self.active is not None else None

    @property
    def report_only(self):
        return self.report is not None and self.action is not None and self.report.run_id == self.action.id

    @property
    def purpose(self):
        return "report" if self.report_only else "action"

    def directory(self):
        if self.report_only:
            return {}
        return self.tools.directory(self.action) if self.active is not None else {}

    def facts(self):
        return self.tools.facts(self.active.policy, self.action) if self.active is not None else None

    def check(self, *, binding_only=False):
        return self.tools.check(binding_only=binding_only)

    def enter(self):
        if not self.lock.acquire(blocking=False):
            raise RuntimeError("The original worker is still active; do not start another action")

    def activate(self, conversation, policy, binding, tools, *, action=None):
        check_tools(tools)
        candidate = ActiveContext(conversation, policy, binding, action)
        with self.control.lock:
            self.active = candidate
            self.control.active = conversation is not None
        self.tools = tools

    def begin_action(self, scope, generation):
        text = next((item.text for item in reversed(self.conversation.requirements)
                     if item.generation == generation), None)
        if text is None:
            raise ModelError("missing_user_message", "The original user message is not retained")
        action = Action.create(scope, generation, text)
        self.replay_report()
        self.requests._local_log("run.action_started", {
            "run_id": action.id, "scope": scope, "source_generation": generation}, function="Run.begin_action")
        with self.control.lock:
            active = self.active
            self.active = ActiveContext(active.conversation, active.policy, active.binding, action)
        self.tools.action_started(action, self.active.binding)
        return action

    def dispatch_claim(self, generation):
        with self.control.lock:
            if self.control.paused or self.control.generation != generation:
                raise ModelError("request_cancelled", "Pause or new requirements arrived before dispatch")

    def finish_action(self):
        with self.control.lock:
            if not self.report_only:
                self.report = ReportState(self.action.id)

    def replay_report(self):
        event = self.report.event() if self.report is not None else None
        if event is not None:
            self.publish("model_reply", event)

    def snapshot(self):
        return to_json_value({**self.cached_snapshot, "pause": self.control.snapshot()})

    def state(self, status, reason=None):
        self.status = "ready" if status == "model_stopped" else status
        snapshot = self.on_state(self.status, reason)
        detached = to_json_value(snapshot)
        with self.control.lock:
            self.cached_snapshot = detached
        self.publish("state", self.snapshot())
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
        return self.control.capture(resume=resume,
            expected_generation=self.action.source_generation if self.action else None)

    def cancelled(self, generation):
        return self.control.cancelled(generation)

    def begin_batch(self, reply, rows, *, rejection=None):
        self.batches.begin(reply, rows, {**self.request_identity, "response_id": reply.id})
        if self.last_adoption is not None:
            summary = dict(self.last_adoption)
            if rejection:
                summary["batch_rejection"] = rejection
            self.requests._local_log("model.request_adopted", summary,
                                     function="Run.begin_batch", adopted=True)

    def record_result(self, index, result):
        self.batches.record(index, result)

    def prepare_retention(self, group, policy, generation, details):
        if not self.conversation.loaded_group(group):
            raise ValueError("Read this complete registered image group into context first")
        self.batches.retention = {"group": group, "policy": policy, "generation": generation, "details": details}

    def prepare_handoff(self, scope, source_generation, generation):
        action, receipt = self.action, self.last_adoption or {}
        with self.control.lock:
            latest = self.control.last_supplement
        if (action is None or self.report_only or action.source_generation != source_generation
                or source_generation != latest
                or receipt.get("run_id") != action.id or receipt.get("purpose") != "action"
                or source_generation not in receipt.get("user_generations", ())):
            raise ValueError("Continue requires this action's latest real UserText in its adopted model request")
        key = (self.active.binding, source_generation)
        if key in self.used_handoffs:
            raise ValueError("This user message has already started a reconstruction run")
        replacement = Action.create(scope, source_generation, action.source_text)
        self.batches.handoff = {"action": replacement, "generation": generation,
                                "source_action": action.id, "key": key}
        return replacement.id

    def commit_batch(self, reply=None, *, recover=False):
        if recover:
            self.requests.recover_context_log()
        elif self.requests.context_log_pending:
            raise ModelError("context_journal_pending", "Use Continue to recover the original context journal")
        if reply is not None:
            self.begin_batch(reply, [])
        active, pending, retention = self.active, self.batches.pending, self.batches.retention
        handoff = self.batches.handoff
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
        next_action = active.action
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
            if handoff is not None:
                ready = (not recover and handoff["generation"] == generation and not paused
                         and active.action.id == handoff["source_action"])
                rows, notifications, adopted = self.tools.stage_handoff(pending.reply, rows, handoff, ready)
                if adopted:
                    next_action = handoff["action"]
                self.requests._local_log("run.handoff_prepared", {
                    "source_run_id": handoff["source_action"], "run_id": handoff["action"].id,
                    "user_generation": handoff["key"][1], "adoption": "ready" if adopted else "rejected"},
                    function="Run.prepare_handoff")
            materials, observations = self.tools.reply_materials(pending.reply, rows)
            candidate.record_reply(pending.reply, materials, observations=observations)
        if human is not None:
            result, project = human
            candidate.record_human(project(result))
        for number, text in supplements:
            candidate.add_requirement(number, text)
        policy = self.tools.prepare_context(active.policy, candidate)
        replacement = ActiveContext(candidate, policy, active.binding, next_action)
        last = tuple(row[1] for row in rows) if pending is not None and rows else self.batches.latest_results
        # All domain work, logging and callbacks precede this short compare/exchange.
        with self.control.lock:
            if (self.active is not active or source.revision != revision or self.batches.pending is not pending
                    or self.batches.retention is not retention
                    or self.batches.handoff is not handoff
                    or self.batches.human is not human
                    or self.control.generation != generation or self.control.paused != paused
                    or tuple(self.control.supplements[:len(supplements)]) != supplements):
                raise ModelError("pending_results", "Staged context changed; original batch and supplements retained")
            self.active = replacement
            self.batches.latest_results = last
            self.batches.pending = self.batches.retention = self.batches.human = self.batches.handoff = None
            if next_action is not active.action and handoff is not None:
                self.used_handoffs[handoff["key"]] = next_action.id
            del self.control.supplements[:len(supplements)]
        if next_action is not active.action:
            self.tools.action_started(next_action, active.binding)
        if handoff is not None:
            self.requests._local_log("run.handoff_committed", {
                "source_run_id": handoff["source_action"], "run_id": handoff["action"].id,
                "user_generation": handoff["key"][1],
                "adoption": "adopted" if next_action is not active.action else "not_adopted"},
                function="Run.commit_batch", adopted=True)
        for notification in notifications:
            self.publish("tool_result", {**pending.identity, **notification})

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
        replacement = ActiveContext(candidate, policy, active.binding, active.action)
        if changed:
            self.requests._local_log("context.replacement_prepared", candidate.last_compaction, function="Run._adopt_context")
        with self.control.lock:
            if (self.control.paused or self.control.generation != generation or self.active is not active
                    or active.conversation is not source or source.revision != revision):
                raise ModelError("request_cancelled", "Context replacement cancelled; original history retained")
            self.active = replacement
            if changed:
                self.last_compaction = candidate.last_compaction

    def record_human(self, result, project, *, function):
        if self.batches.human is not None:
            raise ModelError("pending_results", "An original human result still needs recording")
        self.batches.latest_results = (result,)
        self.batches.latest_identities = ({"function": function},)
        self.batches.human = (result, project)
        self.commit_batch()

    def prepare_request(self, generation):
        active = self.active
        self.check()
        facts, published = self.facts(), self.directory()
        self.state("preparing_model")
        try:
            prepared = active.conversation.prepare(self.context_model, published, facts,
                                                  **self.context_callbacks(generation))
        except CapacityError as exc:
            self.requests.request("model", None, generation, prepare_error=exc)
            raise
        if self.report_only:
            with self.control.lock:
                if (self.control.paused or self.control.generation != generation
                        or self.report.requested):
                    raise ModelError("request_cancelled", "The report was already requested or this run stopped")
                self.report = replace(self.report, requested=True)
        record = self.requests.capture(prepared, generation, published)
        self.estimate = dict(prepared.estimate)
        self.proxy = self.model.proxy_status()
        self.state("requesting_model")
        return record, published

    def request_model(self, record, generation):
        try:
            reply = self.requests.request("model", record.prepared, generation, record=record)
        except ModelError as exc:
            if exc.code != "model_context_limit" or self.cancelled(generation) or record.purpose == "report":
                raise
            active = self.active
            self.check()
            published = self.directory()
            prepared = active.conversation.compact(self.context_model, published, self.facts(),
                **self.context_callbacks(generation), reason="service_context_limit")
            record = self.requests.capture(prepared, generation, published)
            self.estimate = dict(prepared.estimate)
            reply = self.requests.request("model", prepared, generation, record=record)
        self._adopt_receipt(record)
        self.model.observe_usage(record.prepared.request, reply)
        self.call_ids.update(call.id for call in reply.calls)
        return reply

    def public_reply(self, reply, generation, *, progress):
        if not self.cancelled(generation):
            self.publish("model_reply", {**self.request_identity, "run_id": self.action.id if self.action else None,
                         "response_id": reply.id,
                         "text": reply.content or "", "progress": progress, "adopted": True})

    def _adopt_receipt(self, record):
        slot = self.requests.receipt_candidate()
        active = record.active
        candidate = active.conversation.fork()
        candidate.receipt(record.unit_ids, record.delivery_ids)
        policy = self.tools.prepare_receipt(active.policy, decode_json(record.observations),
                                           purpose=record.purpose)
        replacement = ActiveContext(candidate, policy, record.binding, active.action)
        adopted = {"request_number": self.request_identity.get("request_number"),
                   "generation": record.generation, "run_id": record.action_id, "purpose": record.purpose,
                   "user_generations": list(record.user_generations), "requirements": []}
        with self.control.lock:
            accepted = (self.active is active and active.conversation.revision == record.revision
                        and active.binding == record.binding and self.control.generation == record.generation
                        and not self.control.paused and self.requests.adopt_receipt(slot, record))
            if accepted:
                self.active = replacement
                if record.purpose == "action":
                    for number in record.requirements:
                        key = (record.binding, number)
                        if key not in self.requirement_receipts:
                            adopted["requirements"].append(number)
                            self.requirement_receipts[key] = dict(self.request_identity)
                self.last_adoption = adopted
                if record.purpose == "report":
                    self.report = replace(self.report, reply=slot["reply"], identity=dict(self.request_identity))
        if not accepted:
            self.requests.reject_receipt(slot)
            self.publish("request_discarded", self.request_identity)
            raise ModelError("request_cancelled", "Original main request was not adopted; no delivery receipt issued")

    def advance(self, *, resume):
        return run(self, self.tools, resume=resume)

    def use_model(self, model):
        self.model = self.context_model.client = model
        self.requests.use_model(model)
