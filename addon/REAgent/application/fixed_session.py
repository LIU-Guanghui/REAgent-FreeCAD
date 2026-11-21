"""Fixed-command CAD session sharing Worker lifecycle and Source panel presentation without a model."""

from threading import Lock

from REAgent.agent.errors import AgentError
from REAgent.cad.controller import Controller
from REAgent.cad.errors import PublicError
from REAgent.cad.log_summary import request_summary
from REAgent.cad.results import ToolResult
from REAgent.cad.command_names import FIXED_TOOLS
from REAgent.json_values import to_json_value
from REAgent.cad.tasks.registry import WORKFLOWS
from .presentation import journal_update_payload
from .session import prepare_open_arguments


class FixedSession:
    """Drive Controller directly with actor='human' while caching snapshots for lock-free Status reads."""

    mode = "fixed"
    comparison = None
    comparison_rows = ()

    @classmethod
    def probe_ping(cls, workflow_name="stp2cad"):
        """Probe runtime health via Worker without allocating a panel session or occupying Worker._session."""
        if workflow_name not in WORKFLOWS:
            raise AgentError("invalid_request", "Unknown CAD workflow: " + str(workflow_name))
        controller = Controller(WORKFLOWS[workflow_name])
        return controller.execute(
            "agent_ping",
            {},
            execution={"actor": "human", "scope": "manual", "purpose": "action", "binding": None},
        )

    def __init__(self, workflow_name="stp2cad", *, on_event=None, prepare_inputs=None, view_input=None):
        if workflow_name not in WORKFLOWS:
            raise AgentError("invalid_request", "Unknown CAD workflow: " + str(workflow_name))
        self._workflow_name = workflow_name
        self._workflow = WORKFLOWS[workflow_name]
        self._on_event = on_event
        self._prepare_inputs = prepare_inputs
        self._controller = Controller(self._workflow, view_input=view_input)
        self._controller.observe_journal(self._journal_update)
        self._lock = Lock()
        self._cached_facts = None
        self._status = "idle"
        self._reason = "initialized"
        self._last_batch = ()
        self._last_identities = ()

    @property
    def active(self):
        with self._lock:
            return self._cached_facts is not None

    @property
    def last_batch(self):
        with self._lock:
            return self._last_batch

    @property
    def last_result_identities(self):
        with self._lock:
            return self._last_identities

    def pause(self):
        """Record pause request compatibly with Worker lifecycle; single CAD calls run synchronously."""

    def snapshot(self):
        with self._lock:
            has_mode = self._cached_facts is not None or self._status == "starting"
            return to_json_value({
                "status": self._status,
                "reason": self._reason,
                "controller": self._cached_facts,
                "mode": "fixed" if has_mode else None,
                "model_configured": False,
                "action": {"scope": "manual", "actor": "human"} if self._cached_facts is not None else None,
                "pause": {"paused": False, "manual": False, "generation": 0, "queued": False},
                "last_batch": [
                    {
                        "is_error": row.is_error,
                        "side_effect": row.side_effect,
                        "operation_id": row.value.get("operation_id"),
                    }
                    for row in self._last_batch
                ],
            })

    def execute(self, command, arguments=None, *, workflow=None, expected_target=None):
        """Run one FIXED_TOOLS command in serial Controller ownership and emit Source panel events."""
        if command not in FIXED_TOOLS:
            raise AgentError("capability_unavailable", "Unknown or unsupported fixed command: " + str(command))
        if expected_target is not None and command != "agent_open_session":
            raise AgentError("invalid_request", "expected_target is only supported for agent_open_session")
        if workflow is not None and workflow != self._workflow_name and command != "agent_open_session":
            raise AgentError(
                "invalid_request",
                f"Active fixed session uses workflow {self._workflow_name}, not {workflow}",
            )
        if arguments is None:
            args = {}
        elif isinstance(arguments, dict):
            args = to_json_value(arguments)
        else:
            raise AgentError("invalid_request", "Tool arguments must be a JSON object")

        with self._lock:
            facts = self._cached_facts
        binding = facts.get("session_id") if facts is not None else None

        if command == "agent_open_session":
            if facts is not None:
                raise AgentError("task_active", "Finish the retained task before starting another")
            if self._prepare_inputs is None and self._workflow.image_inputs:
                raise AgentError("invalid_reference", "Image input preparation is unavailable")
            args = prepare_open_arguments(self._workflow, args, self._prepare_inputs)
            try:
                self._controller.pin_expected_target(expected_target)
            except PublicError as exc:
                raise AgentError(exc.code, str(exc)) from exc
            with self._lock:
                self._status = "starting"
                self._reason = "starting_session"
            self._emit("state", self.snapshot(), session_id=binding)

        try:
            summary = request_summary(command, args)
        except (KeyError, TypeError, AttributeError, ValueError):
            summary = {}
        try:
            self._emit(
                "tool_started",
                {"mode": "fixed", "function": command, "request": summary},
                session_id=binding,
            )
            result = self._controller.execute(
                command,
                args,
                on_progress=self._progress,
                execution={"actor": "human", "scope": "manual", "purpose": "action", "binding": binding},
            )
        except BaseException as exc:
            try:
                interrupted_facts = self._controller.task_state()
            except Exception:
                interrupted_facts = facts
            with self._lock:
                self._cached_facts = interrupted_facts
                self._status = "paused" if interrupted_facts is not None else "idle"
                self._reason = type(exc).__name__
            raise
        new_facts = self._controller.task_state()
        new_binding = new_facts["session_id"] if new_facts is not None else binding
        if new_facts is None:
            if command in ("agent_close_session", "agent_manage_journal") or facts is not None:
                status, reason = "closed", "task_ended"
            elif command == "agent_open_session" and result.is_error:
                status, reason = "closed", "session_start_failed"
            else:
                status, reason = "idle", "fixed_command_completed"
        elif new_facts.get("closed"):
            status, reason = "closed", "cleanup_pending"
        elif (new_facts.get("startup") or {}).get("status") != "ready":
            status, reason = "paused", "session_start_failed"
        else:
            status, reason = "ready", "fixed_command_completed"

        with self._lock:
            self._cached_facts = new_facts
            self._status = status
            self._reason = reason
            self._last_batch = (result,)
            self._last_identities = ({"function": command},)

        self._emit(
            "tool_result",
            {
                "mode": "fixed",
                "function": command,
                "request": summary,
                "result": to_json_value(result.value),
                "is_error": result.is_error,
                "side_effect": result.side_effect,
            },
            session_id=new_binding,
            images=tuple(result.images),
        )
        self._emit("state", self.snapshot(), session_id=new_binding)
        return result

    def close(self):
        """End the retained fixed task using the exact same controller close path as agent_close_session."""
        facts = self._controller.task_state()
        if facts is None:
            result = ToolResult({"ended": True, "session_retained": False, "session_status": "closed"})
            with self._lock:
                self._cached_facts = None
                self._status = "closed"
                self._reason = "task_ended"
                self._last_batch = (result,)
                self._last_identities = ({"function": "agent_close_session"},)
            self._emit(
                "tool_result",
                {
                    "mode": "fixed",
                    "function": "agent_close_session",
                    "request": {},
                    "result": to_json_value(result.value),
                    "is_error": False,
                    "side_effect": "none",
                },
            )
            self._emit("state", self.snapshot())
            return result
        return self.execute("agent_close_session", {})

    def _emit(self, event, value, *, session_id=None, images=()):
        if self._on_event is None:
            return
        with self._lock:
            fallback_binding = self._cached_facts.get("session_id") if self._cached_facts is not None else None
        self._on_event({
            "event": event,
            "session_id": session_id if session_id is not None else fallback_binding,
            **to_json_value(value),
            "images": tuple(images),
        })

    def _progress(self, value):
        self._emit("tool_progress", value)

    def _journal_update(self, recorder):
        self._emit("journal_update", journal_update_payload(recorder), session_id=recorder.task)
