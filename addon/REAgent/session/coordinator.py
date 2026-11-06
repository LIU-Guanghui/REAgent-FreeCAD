"""User actions, chat/rebuild composition and lifecycle coordination on one worker."""

from dataclasses import replace
import uuid

from REAgent.agent.context import Conversation
from REAgent.agent.actions import Action
from REAgent.agent.run import Run
from REAgent.agent.tools import EmptyTools
from REAgent.model.messages import Instruction
from REAgent.model.errors import ModelError
from REAgent.rebuild.discovery_policy import DiscoveryPolicy
from REAgent.rebuild.toolset import HISTORY_PREFIX, SUMMARY_INSTRUCTION
from REAgent.rebuild.results import ToolResult
from REAgent.rebuild.log_summary import validation_summary
from REAgent.rebuild.tools import prepare_comparison
from REAgent.values import to_json_value
from .bootstrap import create_model, create_rebuild
from .state_snapshots import snapshot_state


_CHAT_INSTRUCTIONS = """You are REAgent, an assistant inside FreeCAD. Answer the user's questions and
discuss designs normally. No CAD task or tool access exists in this conversation. Do not claim to
inspect, edit or save a model. The user explicitly starts a STEP reconstruction from the UI when needed.
"""


class Session:
    """Only pause, supplement and snapshot are GUI-safe; all actions use the worker."""

    def __init__(self, config, *, api_key, on_event=None):
        self.mode = None
        self.rebuild = None
        self.comparison = None
        self.comparison_rows = ()
        self.run = Run(create_model(config, api_key=api_key), journal=self._journal,
                       on_state=lambda status, reason: snapshot_state(self, status, reason), on_event=on_event)

    def _journal(self, **values):
        if self.rebuild is None:
            raise ModelError("task_binding_changed", "There is no original task Journal")
        return self.rebuild.model_journal(**values)

    @property
    def last_batch(self):
        return self.run.batches.latest_results

    @property
    def last_result_identities(self):
        return self.run.batches.latest_identities

    def pause(self):
        self.run.control.pause()

    def supplement(self, text):
        self.run.control.supplement(text)

    def snapshot(self):
        return self.run.snapshot()

    def _journal_update(self, recorder):
        rows, characters = [], 0
        for row in reversed(recorder.events[-200:]):
            cost = len(str(row))
            if characters + cost > 196608:
                break
            rows.append(row)
            characters += cost
        self.run.publish("journal_update", {"session_id": recorder.task,
                         "events": list(reversed(rows)), "journal": recorder.metadata()})

    def _reset(self):
        if self.run.batches.pending is not None or self.run.batches.human is not None:
            raise ModelError("pending_results", "Record the original results or explicitly end the task before starting another")
        self.run.requests.reset()
        self.run.call_ids = set()
        self.run.batches.clear()
        self.run.estimate = self.run.context_info = self.run.last_compaction = None
        self.run.callback_error = None
        self.run.requirement_receipts.clear()
        self.run.used_handoffs.clear()
        self.run.last_adoption = self.run.report = None
        self.comparison = None
        self.comparison_rows = ()

    def _recover(self):
        self.run.requests.recover_model_outcome()
        self.run.commit_batch(recover=True)
        self.run.replay_report()
        if self.rebuild is not None and self.rebuild.controller.task_state() is not None:
            facts = self.rebuild.controller.task_state()
            if not facts["journal"]["complete"]:
                meta = self.rebuild.model_journal(binding=self.rebuild.journal_binding, flush=True)
                if not meta["complete"]:
                    raise ModelError("journal_pending", "Original task journal is still incomplete")
            result = self.rebuild.controller.recover_delivery(self.rebuild.journal_binding)
            if result is not None:
                self.run.publish("tool_result", {"function": "agent_finish_reconstruction",
                    "result": result.value, "is_error": result.is_error, "side_effect": result.side_effect,
                    "recovered": True})
            current = self.rebuild.controller.task_state() or {}
            receipt = current.get("delivery_receipt") or {}
            if self.run.action and receipt.get("run_id") == self.run.action.id:
                self.run.finish_action()

    def _drive(self, *, resume):
        while True:
            result = self.run.advance(resume=resume)
            action = self.run.action
            with self.run.control.lock:
                latest = self.run.control.last_supplement
                manual = self.run.control.last_manual_pause
            if action is None or latest <= action.source_generation or manual > latest:
                return result
            self._recover()
            self.run.begin_action("target_edit" if self.mode == "cad" else "chat", latest)
            resume = True

    def chat(self, text):
        self.run.enter()
        try:
            if self.rebuild is not None and self.rebuild.controller.task_state() is not None:
                raise RuntimeError("Use the active CAD task instead of opening another conversation")
            if not isinstance(text, str) or not text.strip():
                raise ValueError("A chat message must be non-empty")
            if self.mode != "chat":
                self.run.control.start_task()
                conversation = Conversation(Instruction(_CHAT_INSTRUCTIONS), text,
                    summary_instruction=SUMMARY_INSTRUCTION, history_prefix=HISTORY_PREFIX)
                self._reset()
                if self.rebuild is not None:
                    self.rebuild.reset()
                self.mode = "chat"
                self.run.activate(conversation, None, None, EmptyTools())
            else:
                self.supplement(text)
            self.run.stream_chat = True
            self.run.commit_batch(recover=True)
            self.run.begin_action("chat", max(0, self.run.control.last_supplement))
            return self._drive(resume=True)
        finally:
            self.run.lock.release()

    def start(self, arguments, objective):
        self.run.enter()
        begun = False
        try:
            if self.rebuild is None:
                self.rebuild = create_rebuild(on_journal=self._journal_update)
            if self.rebuild.controller.task_state() is not None:
                raise RuntimeError("Finish the retained task before starting another")
            self.rebuild.validate_start(arguments)
            self.run.model.require_visual()
            continuing = self.mode == "chat" and self.run.conversation is not None
            if continuing:
                self.run.control.capture(resume=True)
                self.run.requests.recover_model_outcome()
                self.run.commit_batch(recover=True)
                conversation = self.run.conversation.fork()
                conversation.instructions = self.rebuild.instructions()
                generation = self.run.control.advance_generation()
                conversation.add_requirement(generation, objective)
            else:
                conversation = Conversation(self.rebuild.instructions(), objective,
                    summary_instruction=SUMMARY_INSTRUCTION, history_prefix=HISTORY_PREFIX)
                self.run.control.start_task()
            self._reset()
            self.rebuild.reset()
            self.mode = "cad"
            self.run.stream_chat = False
            self.run.proxy = self.run.model.proxy_status()
            initial = Action.create("reconstruct", conversation.requirements[-1].generation, objective)
            self.run.activate(conversation, DiscoveryPolicy(), None, self.rebuild, action=initial)
            self.run.state("starting")
            begun = True
            result = self.rebuild.controller.execute("agent_start_session", arguments, on_progress=self._progress,
                execution={"actor": "human", "scope": "manual", "purpose": "action", "binding": None})
            self.run.batches.latest_results = (result,)
            self.run.batches.latest_identities = ({"function": "agent_start_session"},)
            facts = self.rebuild.controller.task_state()
            self.rebuild.bind(facts)
            self.run.activate(conversation, DiscoveryPolicy(), self.rebuild.journal_binding, self.rebuild, action=initial)
            if facts is not None:
                self.rebuild.action_started(initial, self.rebuild.journal_binding)
            with self.run.control.lock:
                self.run.control.active = facts is not None
            self._publish_human("agent_start_session", result)
            self.run.record_human(result, lambda value: self.rebuild.human_material("agent_start_session", value),
                                  function="agent_start_session")
            if result.is_error or facts is None:
                return self.run.state("paused", "session_start_failed")
            return self._drive(resume=False)
        except BaseException:
            if begun:
                facts = self.rebuild.controller.task_state()
                self.rebuild.bind(facts)
                active = self.run.active
                self.run.activate(active.conversation, active.policy, self.rebuild.journal_binding, self.rebuild, action=active.action)
                with self.run.control.lock:
                    self.run.control.active = facts is not None
                self.run.state("paused", "start_interrupted")
            raise
        finally:
            self.run.lock.release()

    def advance(self):
        self.run.enter()
        try:
            if self.run.conversation is None:
                raise RuntimeError("Start a task first")
            self.run.callback_error = None
            self._recover()
            with self.run.control.lock:
                generation = self.run.control.last_supplement
            if generation < 0:
                raise ValueError("Send a new message to continue")
            self.run.begin_action("target_edit" if self.mode == "cad" else "chat", generation)
            return self._drive(resume=True)
        finally:
            self.run.lock.release()

    def compact(self):
        self.run.enter()
        with self.run.control.lock:
            was_paused = self.run.control.paused
        previous_status = self.run.status
        try:
            if self.run.conversation is None:
                raise ModelError("nothing_to_compact", "Start a conversation before compacting context")
            self.run.callback_error = None
            generation, _paused = self.run.control.capture(resume=True)
            self.run.requests.recover_model_outcome()
            self.run.commit_batch(recover=True)
            active, tools = self.run.active, self.run.tools
            self.run.check(binding_only=True)
            self.run.state("compacting_context")
            active.conversation.compact(self.run.context_model, self.run.directory(), self.run.facts(),
                                        **self.run.context_callbacks(generation))
            cancelled = self.run.cancelled(generation)
            return self.run.state("paused" if cancelled or was_paused else previous_status, "context_compacted")
        except ModelError as exc:
            state = self.run.state("paused", exc.code)
            if exc.code == "request_cancelled":
                return state
            raise
        except BaseException:
            self.run.state("paused", "compaction_interrupted")
            raise
        finally:
            with self.run.control.lock:
                self.run.control.paused = self.run.control.paused or was_paused
            self.run.lock.release()

    def _progress(self, value):
        self.run.publish("tool_progress", value)

    def _publish_human(self, name, result):
        self.run.publish("tool_result", {"function": name, "result": result.value,
                         "is_error": result.is_error, "side_effect": result.side_effect}, images=result.images)

    def _human(self, name, arguments):
        self.run.enter()
        try:
            if self.run.conversation is None or self.rebuild is None:
                raise RuntimeError("Start a task first")
            self.run.requests.recover_model_outcome()
            self.run.commit_batch(recover=True)
            result = self.rebuild.controller.execute(name, arguments, on_progress=self._progress,
                execution={"actor": "human", "binding": self.rebuild.journal_binding, "scope": "manual", "purpose": "action"})
            self.run.batches.latest_results = (result,)
            self.run.batches.latest_identities = ({"function": name},)
            self._publish_human(name, result)
            self.run.record_human(result, lambda value: self.rebuild.human_material(name, value), function=name)
            closed = self.rebuild.controller.task_state() is None
            if closed:
                self.run.activate(None, None, None, EmptyTools())
                self.mode = None
            self.run.state("closed" if closed else "paused", "human_control")
            return result
        except BaseException as exc:
            self.run.state("paused", exc.code if isinstance(exc, ModelError) else "human_control_interrupted")
            raise
        finally:
            self.run.lock.release()

    def save(self):
        return self._human("agent_save", {})

    def compare(self, options, view_request):
        self.run.enter()
        try:
            previous_status = self.run.status
            if self.rebuild is None or self.run.conversation is None:
                raise ValueError("Start a CAD task before comparing")
            try:
                options, view_request = prepare_comparison(to_json_value(options), to_json_value(view_request))
            except ValueError as exc:
                raise ModelError("invalid_comparison", str(exc)) from exc
            self._recover()
            facts = self.run.check(binding_only=True)
            if facts["session_status"] != "ready" or facts["target_writes_blocked"] or facts["source_basis_unknown"]:
                raise ValueError("Current binding, journal and restoration must be known before comparison")
            binding = self.rebuild.journal_binding
            comparison_id = "comparison-" + uuid.uuid4().hex[:20]
            with self.run.control.lock:
                generation = self.run.control.generation
            requests = []
            if options is not None:
                requests.append(("geometry", "agent_validate", {"options": options}))
            if view_request is not None:
                requests.append(("views", "agent_observe", {"role": "target", "request": view_request}))
            children, rows = [], []
            stop_reason = None
            self.comparison_rows = ()
            for step, function, arguments in requests:
                with self.run.control.lock:
                    cancelled = self.run.control.generation != generation
                if stop_reason or cancelled:
                    result = ToolResult({"status": "not_executed", "reason": stop_reason or "pause_or_new_message",
                                         "executed": False, "side_effect": "none"})
                else:
                    try:
                        result = self.rebuild.controller.execute(function, arguments, on_progress=self._progress,
                            execution={"actor": "human", "binding": binding, "scope": "manual", "purpose": "action",
                                       "comparison_id": comparison_id, "comparison_step": step})
                    except BaseException:
                        result = ToolResult({"code": "comparison_interrupted", "status": "unknown",
                            "message": "The original comparison did not return a confirmed result", "side_effect": "unknown"},
                            is_error=True, side_effect="unknown")
                identity = {"function": function, "comparison_id": comparison_id, "comparison_step": step,
                            "operation_id": result.value.get("operation_id")}
                rows.append((identity, result))
                self.comparison_rows = tuple(rows)
                self.run.batches.latest_results = tuple(item[1] for item in rows)
                self.run.batches.latest_identities = tuple(item[0] for item in rows)
                value = result.value
                summary = validation_summary(value) if step == "geometry" and "checks" in value else {
                    key: value[key] for key in ("status", "code", "message", "reason", "executed", "evidence_id", "image_delivery") if key in value}
                if isinstance(summary.get("image_delivery"), dict):
                    summary["image_delivery"] = {key: summary["image_delivery"][key] for key in
                        ("status", "image_count", "direction_count", "reason") if key in summary["image_delivery"]}
                revisions = value.get("paired_revisions") or {name: check.get("revisions")
                    for name, check in value.get("checks", {}).items() if check.get("revisions")}
                children.append({**identity, "is_error": result.is_error, "side_effect": result.side_effect,
                                 "revisions": revisions, "summary": summary})
                self.run.publish("tool_result", {**identity, "result": value,
                    "is_error": result.is_error, "side_effect": result.side_effect}, images=result.images)
                current = self.rebuild.controller.task_state()
                if result.side_effect == "unknown" or not current or current["source_basis_unknown"] or current["target_writes_blocked"]:
                    stop_reason = "restoration_or_binding_unconfirmed"
            reference = self.rebuild.controller.record_manual_comparison(binding, comparison_id, rows)
            self.comparison = {"comparison_id": comparison_id, "actor": "human", "steps": children,
                               "documents": {"source": facts["source_document"], "target": facts["target_document"]},
                               "reference": reference}
            aggregate = ToolResult(self.comparison)
            if self.run.batches.human is not None:
                raise ModelError("pending_results", "The original human result is still awaiting recording")
            self.run.batches.human = (aggregate, lambda item: self.rebuild.human_material("Session.compare", item))
            self.run.requests._local_log("comparison.completed", {"comparison_id": comparison_id, "actor": "human",
                "steps": [{key: child[key] for key in ("function", "comparison_step", "operation_id", "is_error", "side_effect")}
                          for child in children]}, function="Session.compare")
            self.run.commit_batch()
            with self.run.control.lock:
                latest = self.run.control.last_supplement
                manual = self.run.control.last_manual_pause
                paused = self.run.control.paused
            self.run.state("paused" if paused or stop_reason else previous_status, "manual_comparison_finished")
            if latest > generation and manual <= latest and stop_reason is None:
                self.run.begin_action("target_edit", latest)
                self._drive(resume=True)
            return aggregate
        finally:
            self.run.lock.release()

    def close(self):
        self.run.enter()
        try:
            self.pause()
            self.run.activate(None, None, None, EmptyTools())
            self.mode = None
            self.run.batches.clear()
            self.run.requests.begin_close()
            with self.run.control.lock:
                self.run.control.supplements = []
            if self.rebuild is None or self.rebuild.controller.task_state() is None:
                self.run.requests.finish_close(released=True)
                return self.run.state("closed", "task_ended")
            result = self.rebuild.controller.execute("agent_close_session", {}, on_progress=self._progress,
                execution={"actor": "human", "binding": self.rebuild.journal_binding, "scope": "manual", "purpose": "action"})
            self.run.requests.finish_close(released=self.rebuild.controller.task_state() is None)
            self.run.batches.latest_results = (result,)
            self.run.batches.latest_identities = ({"function": "agent_close_session"},)
            self._publish_human("agent_close_session", result)
            self.run.state("closed", "cleanup_pending" if self.rebuild.controller.task_state() else "task_ended")
            return result
        finally:
            self.run.lock.release()

    def configure(self, config, *, api_key):
        self.run.enter()
        try:
            if self.rebuild is not None and self.rebuild.controller.task_state() is not None:
                if self.run.status not in ("paused", "ready") or config != self.run.model.config:
                    raise RuntimeError("An active CAD task keeps its original model settings; pause to update its key")
                self.run.model.set_credentials(api_key=api_key)
            else:
                model = create_model(config, api_key=api_key)
                if model.identity != self.run.model.identity:
                    pending = self.run.batches.pending
                    if self.run.conversation is not None:
                        conversation = self.run.conversation.fork()
                        conversation.discard_provider_state()
                        active = self.run.active
                        policy = self.run.tools.prepare_context(active.policy, conversation)
                        self.run.activate(conversation, policy, active.binding, self.run.tools, action=active.action)
                    if pending is not None:
                        pending.reply = replace(pending.reply, provider_state=None)
                self.run.use_model(model)
            return self.run.state(self.run.status, "settings_updated")
        finally:
            self.run.lock.release()
