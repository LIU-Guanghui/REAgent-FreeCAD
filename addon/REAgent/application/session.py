"""Coordinate chat and CAD tasks on one Source worker."""

from REAgent.agent.context import Conversation
from REAgent.agent.actions import Action
from REAgent.agent.errors import AgentError
from REAgent.agent.run import Run, action_errors
from REAgent.agent.tools import EmptyTools
from REAgent.cad.controller import Controller, has_retained_task
from REAgent.cad.errors import PublicError
from REAgent.cad.adopted_observations import AdoptedObservations
from REAgent.cad.toolset import CadTools
from REAgent.cad.results import ToolResult
from REAgent.cad.tasks.registry import WORKFLOWS
from .presentation import journal_update_payload, snapshot_state


def normalize_objective(kind, objective=None):
    """Return explicit non-empty objective or the file workflow's canonical default objective."""
    if kind not in WORKFLOWS:
        raise ValueError("Unknown CAD workflow: " + str(kind))
    if objective is not None and not isinstance(objective, str):
        raise ValueError("Objective must be a string")
    text = objective.strip() if isinstance(objective, str) else ""
    if text:
        return text
    workflow = WORKFLOWS[kind]
    if workflow.objective:
        return workflow.objective
    raise ValueError("Describe the part to create")


def prepare_open_arguments(workflow, arguments, prepare_inputs):
    """Prepare arguments for agent_open_session, converting image2cad paths once in Worker."""
    if not isinstance(arguments, dict):
        raise AgentError("invalid_request", "Session arguments must be an object")
    args = dict(arguments)
    if workflow.image_inputs:
        if "paths" not in args or not isinstance(args["paths"], list):
            raise AgentError("invalid_reference", "Image workflow requires a paths list")
        try:
            args["inputs"] = prepare_inputs(args.pop("paths"))
        except ValueError as exc:
            raise AgentError("invalid_reference", str(exc)) from exc
    elif "paths" in args:
        raise AgentError("invalid_request", "Only image2cad accepts paths")
    return args


_CHAT_INSTRUCTIONS = """
You are REAgent, an assistant in FreeCAD.
Answer the user's questions and help them explore design ideas.
CAD modeling starts only when the user selects a CAD workflow from the Source task panel.
"""


def action_error(exc):
    if isinstance(exc, AgentError):
        return exc.detail()
    return {"code": "worker_action_failed", "message": type(exc).__name__}


def assert_source_release():
    if has_retained_task():
        raise RuntimeError("Finish the retained task before releasing Source")


class Session:
    """Only pause, supplement and snapshot are GUI-safe; all actions use the worker."""

    @property
    def last_batch(self):
        return self.run.batches.latest_results

    @property
    def last_result_identities(self):
        return self.run.batches.latest_identities

    @action_errors()
    def __init__(self, config, *, api_key, on_event=None):
        self.mode = None
        self.cad_tools = None
        self.comparison = None
        self.comparison_rows = ()
        try:
            self.run = Run(config, api_key=api_key, journal=self._journal,
                           on_state=lambda status, reason: snapshot_state(self, status, reason), on_event=on_event)
        except ValueError as exc:
            raise AgentError("invalid_credentials", str(exc)) from None

    @action_errors()
    def configure(self, config, *, api_key):
        self.run.enter()
        try:
            if self.cad_tools is not None and self.cad_tools.controller.task_state() is not None:
                if (self.run.status not in ("paused", "ready")
                        or not self.run.update_model_key(config, api_key=api_key)):
                    raise RuntimeError("An active CAD task keeps its original model settings; pause to update its key")
            else:
                self.run.configure_model(config, api_key=api_key)
            return self.run.state(self.run.status, "settings_updated")
        except ValueError as exc:
            raise AgentError("invalid_credentials", str(exc)) from None
        finally:
            self.run.lock.release()

    @action_errors()
    def chat(self, text):
        self.run.enter()
        try:
            if self.cad_tools is not None and self.cad_tools.controller.task_state() is not None:
                raise RuntimeError("Use the active CAD task instead of opening another conversation")
            if not isinstance(text, str) or not text.strip():
                raise ValueError("A chat message must be non-empty")
            if self.mode != "chat":
                self.run.control.start_task()
                conversation = Conversation(_CHAT_INSTRUCTIONS, text)
                self._reset()
                if self.cad_tools is not None:
                    self.cad_tools.reset()
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

    @action_errors()
    def start_task(self, kind, arguments, objective=None, *, prepare_inputs, view_input, expected_target=None):
        self.run.enter()
        begun = False
        try:
            if self.cad_tools is not None and self.cad_tools.controller.task_state() is not None:
                raise RuntimeError("Finish the retained task before starting another")
            workflow = WORKFLOWS[kind]
            objective = normalize_objective(kind, objective)
            if workflow.requires_images:
                self.run.require_visual_model()
            arguments = prepare_open_arguments(workflow, arguments, prepare_inputs)
            controller = Controller(workflow, view_input=view_input)
            try:
                controller.pin_expected_target(expected_target)
            except PublicError as exc:
                raise AgentError(exc.code, str(exc)) from exc
            controller.observe_journal(self._journal_update)
            self.cad_tools = CadTools(controller)
            continuing = self.mode == "chat" and self.run.conversation is not None
            if continuing:
                self.run.control.capture(resume=True)
                self.run.requests.recover_model_outcome()
                self.run.commit_batch(recover=True)
                conversation = self.run.conversation.fork()
                conversation.set_instructions(self.cad_tools.instructions())
                generation = self.run.control.advance_generation()
                conversation.add_requirement(generation, objective)
            else:
                conversation = Conversation(self.cad_tools.instructions(), objective)
                self.run.control.start_task()
            self._reset()
            self.cad_tools.reset()
            self.mode = "cad"
            self.run.stream_chat = False
            self.run.refresh_proxy_status()
            initial = Action.create("reconstruct", conversation.requirements[-1].generation, objective)
            self.run.activate(conversation, AdoptedObservations(), None, self.cad_tools, action=initial)
            self.run.state("starting")
            begun = True
            result = self.cad_tools.controller.execute("agent_open_session", arguments, on_progress=self._progress,
                execution={"actor": "human", "scope": "manual", "purpose": "action", "binding": None})
            self.run.batches.latest_results = (result,)
            self.run.batches.latest_identities = ({"function": "agent_open_session"},)
            facts = self.cad_tools.controller.task_state()
            self.cad_tools.bind(facts)
            self.run.activate(conversation, AdoptedObservations(), self.cad_tools.journal_binding, self.cad_tools, action=initial)
            if facts is not None:
                self.cad_tools.action_started(initial, self.cad_tools.journal_binding)
            with self.run.control.lock:
                self.run.control.active = facts is not None
            self._publish_human("agent_open_session", result)
            self.run.record_human(result, lambda value: self.cad_tools.human_material("agent_open_session", value),
                                  function="agent_open_session")
            if result.is_error or facts is None:
                return self.run.state("paused", "session_start_failed")
            return self._drive(resume=False)
        except BaseException:
            if begun:
                facts = self.cad_tools.controller.task_state()
                self.cad_tools.bind(facts)
                active = self.run.active
                self.run.activate(active.conversation, active.policy, self.cad_tools.journal_binding, self.cad_tools, action=active.action)
                with self.run.control.lock:
                    self.run.control.active = facts is not None
                self.run.state("paused", "start_interrupted")
            raise
        finally:
            self.run.lock.release()

    @action_errors()
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

    def pause(self):
        self.run.control.pause()

    def supplement(self, text):
        self.run.control.supplement(text)

    @action_errors()
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
            if self.cad_tools is None or self.cad_tools.controller.task_state() is None:
                self.run.requests.finish_close(released=True)
                return self.run.state("closed", "task_ended")
            result = self.cad_tools.controller.execute("agent_close_session", {}, on_progress=self._progress,
                execution={"actor": "human", "binding": self.cad_tools.journal_binding, "scope": "manual", "purpose": "action"})
            self.run.requests.finish_close(released=self.cad_tools.controller.task_state() is None)
            self.run.batches.latest_results = (result,)
            self.run.batches.latest_identities = ({"function": "agent_close_session"},)
            self._publish_human("agent_close_session", result)
            self.run.state("closed", "cleanup_pending" if self.cad_tools.controller.task_state() else "task_ended")
            return result
        finally:
            self.run.lock.release()

    def snapshot(self):
        return self.run.snapshot()

    def compact(self):
        return self.run.compact()

    @action_errors()
    def compare(self, options, view_request):
        self.run.enter()
        try:
            previous_status = self.run.status
            if self.cad_tools is None or not self.cad_tools.workflow.paired_views or self.run.conversation is None:
                raise AgentError("task_required", "Start an STP or STL task before comparing")
            try:
                options, view_request = self.cad_tools.prepare_comparison(options, view_request)
            except ValueError as exc:
                raise AgentError("invalid_comparison", str(exc)) from exc
            if options is not None and not self.cad_tools.workflow.compares_stp:
                raise AgentError("invalid_comparison", "STL comparison uses paired views")
            self._recover()
            facts = self.run.check(binding_only=True)
            if facts["session_status"] != "ready" or facts["target_writes_blocked"] or facts["source_basis_unknown"]:
                raise AgentError("task_blocked", "Current binding, journal and restoration must be known before comparison")
            binding = self.cad_tools.journal_binding
            documents = {"source": facts["source_document"], "target": facts["target_document"]}
            with self.run.control.lock:
                generation = self.run.control.generation
            self.comparison_rows = ()

            def cancelled():
                with self.run.control.lock:
                    return self.run.control.generation != generation

            def on_step(identity, result):
                self.comparison_rows += ((identity, result),)
                self.run.batches.latest_results = tuple(item[1] for item in self.comparison_rows)
                self.run.batches.latest_identities = tuple(item[0] for item in self.comparison_rows)
                self.run.publish("tool_result", {**identity, "result": result.value,
                    "is_error": result.is_error, "side_effect": result.side_effect}, images=result.images)

            comparison, stop_reason = self.cad_tools.compare(options, view_request, binding=binding,
                documents=documents, cancelled=cancelled, on_progress=self._progress, on_step=on_step)
            self.comparison = comparison
            aggregate = ToolResult(comparison)
            if self.run.batches.human is not None:
                raise AgentError("pending_results", "The original human result is still awaiting recording")
            self.run.batches.human = (aggregate, lambda item: self.cad_tools.human_material("Session.compare", item))
            self.run.requests.local_log("comparison.completed", {"comparison_id": comparison["comparison_id"], "actor": "human",
                "steps": [{key: child[key] for key in ("function", "comparison_step", "operation_id", "is_error", "side_effect")}
                          for child in comparison["steps"]]}, function="Session.compare")
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

    def save(self):
        return self._human("agent_save", {})

    def _reset(self):
        self.run.reset_task()
        self.comparison = None
        self.comparison_rows = ()

    def _recover(self):
        self.run.requests.recover_model_outcome()
        self.run.commit_batch(recover=True)
        self.run.replay_report()
        if self.cad_tools is not None and self.cad_tools.controller.task_state() is not None:
            facts = self.cad_tools.controller.task_state()
            if not facts["journal"]["complete"]:
                meta = self.cad_tools.model_journal(binding=self.cad_tools.journal_binding, flush=True)
                if not meta["complete"]:
                    raise AgentError("journal_pending", "Original task journal is still incomplete")
            result = self.cad_tools.controller.recover_delivery(self.cad_tools.journal_binding)
            if result is not None:
                self.run.publish("tool_result", {"function": "agent_finish_reconstruction",
                    "result": result.value, "is_error": result.is_error, "side_effect": result.side_effect,
                    "recovered": True})
            current = self.cad_tools.controller.task_state() or {}
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

    def _human(self, name, arguments):
        self.run.enter()
        try:
            with action_errors():
                if self.run.conversation is None or self.cad_tools is None:
                    raise RuntimeError("Start a task first")
                self.run.requests.recover_model_outcome()
                self.run.commit_batch(recover=True)
                result = self.cad_tools.controller.execute(name, arguments, on_progress=self._progress,
                    execution={"actor": "human", "binding": self.cad_tools.journal_binding, "scope": "manual", "purpose": "action"})
                self.run.batches.latest_results = (result,)
                self.run.batches.latest_identities = ({"function": name},)
                self._publish_human(name, result)
                self.run.record_human(result, lambda value: self.cad_tools.human_material(name, value), function=name)
                closed = self.cad_tools.controller.task_state() is None
                if closed:
                    self.run.activate(None, None, None, EmptyTools())
                    self.mode = None
                self.run.state("closed" if closed else "paused", "human_control")
                return result
        except BaseException as exc:
            self.run.state("paused", exc.code if isinstance(exc, AgentError) else "human_control_interrupted")
            raise
        finally:
            self.run.lock.release()

    def _progress(self, value):
        self.run.publish("tool_progress", value)

    def _publish_human(self, name, result):
        self.run.publish("tool_result", {"function": name, "result": result.value,
                         "is_error": result.is_error, "side_effect": result.side_effect}, images=result.images)

    def _journal(self, **values):
        if self.cad_tools is None:
            raise AgentError("task_binding_changed", "There is no original task Journal")
        return self.cad_tools.model_journal(**values)

    def _journal_update(self, recorder):
        self.run.publish("journal_update", journal_update_payload(recorder))
