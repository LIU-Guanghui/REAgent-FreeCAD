"""User actions, chat/rebuild composition and lifecycle coordination on one worker."""

from dataclasses import replace

from REAgent.agent.context import Conversation
from REAgent.agent.run import Run
from REAgent.agent.tools import EmptyTools
from REAgent.model.messages import Instruction
from REAgent.model.errors import ModelError
from REAgent.rebuild.discovery_policy import DiscoveryPolicy
from REAgent.rebuild.toolset import HISTORY_PREFIX, SUMMARY_INSTRUCTION
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
        self.run = Run(create_model(config, api_key=api_key), journal=self._journal,
                       on_state=lambda status, reason: snapshot_state(self, status, reason), on_event=on_event)

    def _journal(self, **values):
        if self.rebuild is None:
            raise ModelError("task_binding_changed", "There is no original task Journal")
        return self.rebuild.model_journal(**values)

    @property
    def last_batch(self):
        return self.run.batches.latest_results

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

    def chat(self, text):
        self.run.enter()
        try:
            if self.rebuild is not None and self.rebuild.controller.task_state() is not None:
                raise RuntimeError("Use the active CAD task instead of opening another conversation")
            if not isinstance(text, str) or not text.strip():
                raise ValueError("A chat message must be non-empty")
            if self.mode != "chat":
                conversation = Conversation(Instruction(_CHAT_INSTRUCTIONS), text,
                    summary_instruction=SUMMARY_INSTRUCTION, history_prefix=HISTORY_PREFIX)
                self._reset()
                if self.rebuild is not None:
                    self.rebuild.reset()
                self.mode = "chat"
                self.run.activate(conversation, None, None, EmptyTools())
            else:
                self.supplement(text)
            return self.run.advance(resume=True)
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
                self.run.turn_control(resume=True)
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
            self.run.proxy = self.run.model.proxy_status()
            self.run.activate(conversation, DiscoveryPolicy(), None, self.rebuild)
            self.run.state("starting")
            begun = True
            result = self.rebuild.controller.execute("agent_start_session", arguments, on_progress=self._progress)
            self.run.batches.latest_results = (result,)
            facts = self.rebuild.controller.task_state()
            self.rebuild.bind(facts)
            self.run.activate(conversation, DiscoveryPolicy(), self.rebuild.journal_binding, self.rebuild)
            with self.run.control.lock:
                self.run.control.active = facts is not None
            self.run.record_human(result, lambda value: self.rebuild.human_material("agent_start_session", value))
            self._publish_human("agent_start_session", result)
            if result.is_error or facts is None:
                return self.run.state("paused", "session_start_failed")
            return self.run.advance(resume=False)
        except BaseException:
            if begun:
                facts = self.rebuild.controller.task_state()
                self.rebuild.bind(facts)
                active = self.run.active
                self.run.activate(active.conversation, active.policy, self.rebuild.journal_binding, self.rebuild)
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
            return self.run.advance(resume=True)
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
            generation, _paused = self.run.turn_control(resume=True)
            self.run.requests.recover_model_outcome()
            self.run.commit_batch(recover=True)
            active, tools = self.run.active, self.run.tools
            tools.check(active.policy, binding_only=True)
            self.run.state("compacting_context")
            active.conversation.compact(self.run.context_model, tools.directory(), tools.facts(active.policy),
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
            result = self.rebuild.controller.execute(name, arguments, on_progress=self._progress)
            self.run.record_human(result, lambda value: self.rebuild.human_material(name, value))
            self._publish_human(name, result)
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
            result = self.rebuild.controller.execute("agent_close_session", {}, on_progress=self._progress)
            self.run.requests.finish_close(released=self.rebuild.controller.task_state() is None)
            self.run.batches.latest_results = (result,)
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
                        self.run.activate(conversation, policy, active.binding, self.run.tools)
                    if pending is not None:
                        pending.reply = replace(pending.reply, provider_state=None)
                self.run.use_model(model)
            return self.run.state(self.run.status, "settings_updated")
        finally:
            self.run.lock.release()
