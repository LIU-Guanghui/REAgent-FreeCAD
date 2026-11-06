"""Single synchronous CAD tool executor; no transport or thread startup."""

import time
import uuid
from threading import Lock

from REAgent.protocol.features import FEATURE_SPECS
from REAgent.protocol.definitions import resolve_retry_action
from REAgent.schema import validate_input_schema
from REAgent.rebuild.errors import PublicError, error_payload
from REAgent.rebuild.results import ToolResult
from REAgent.values import to_json_value
from REAgent.rebuild.tools import _binding_keys
from REAgent.rebuild.commands.families import FAMILIES
from REAgent.rebuild.commands.execution import validate_domain_evidence
from REAgent.rebuild.log_summary import failure_summary, request_summary
from REAgent.rebuild.tools import TOOLS
from REAgent.rebuild.images import cleanup_unpublished_outputs
from REAgent.rebuild.action_policy import guard as guard_action
from REAgent.rebuild.workflow import diagnostics, discovery, history, observation, session, validation
from REAgent.rebuild.workflow_context import WorkflowContext, evidence_summary

def _execution_directory():
    from types import MappingProxyType
    entries = {}
    for family in (session, discovery, observation, history, validation, diagnostics, *FAMILIES):
        for name, handler in family.contribute().items():
            if name in entries or name not in TOOLS or not callable(handler):
                raise ValueError("Duplicate, unknown or incomplete Controller tool: " + name)
            entries[name] = handler
    if set(entries) != set(TOOLS):
        raise ValueError("Controller handlers differ from the internal tool catalog")
    return MappingProxyType(entries)


_execution_lock = Lock()
_current_controller = None


def has_retained_task():
    """Application release includes terminal cleanup and Journal retention."""
    if not _execution_lock.acquire(blocking=False):
        return True
    try:
        return (_current_controller is not None
                and bool(_current_controller.workflow.sessions))
    finally:
        _execution_lock.release()


def _success_response(value, context):
    try:
        return ToolResult(value, tuple(context.get("image_content", ())),
                          side_effect=context["side_effect"])
    except (TypeError, ValueError) as exc:
        raise PublicError("response_encoding_failed", "controller_response", str(exc),
                          side_effect=context["side_effect"]) from exc


class Controller:
    """Own one retained task and execute each complete tool call on the caller's worker."""

    def __init__(self):
        self._handlers = _execution_directory()
        self.workflow = WorkflowContext()
        self.operation = None

    def observe_journal(self, observer):
        self.workflow.journal_observer = observer

    def execute(self, name, arguments=None, *, on_progress=None, execution=None):
        global _current_controller
        if not _execution_lock.acquire(blocking=False):
            return ToolResult({"code": "execution_busy", "stage": "controller_preflight",
                               "message": "The current tool has not finished; do not repeat a write.",
                               "side_effect": "none", "retry_action": "do_not_retry"},
                              is_error=True)
        try:
            if _current_controller not in (None, self):
                return ToolResult({"code": "task_active", "stage": "controller_preflight",
                                   "message": "Another Controller still retains the current task.",
                                   "side_effect": "none", "retry_action": "do_not_retry"},
                                  is_error=True)
            _current_controller = self
            context = {"operation_id": "op-" + uuid.uuid4().hex[:20],
                       "started": time.perf_counter(), "side_effect": "none",
                       "execution": to_json_value(execution) if execution is not None else None}
            self.operation = {"operation_id": context["operation_id"], "function": name,
                              "status": "running"}

            def publish(progress):
                snapshot = to_json_value(progress)
                self.operation = {**self.operation, "progress": snapshot}
                if on_progress is not None:
                    try:
                        on_progress(to_json_value(self.operation))
                    except Exception:
                        # Presentation failure must not change the CAD outcome.
                        pass

            context["publish_progress"] = publish
            result = self._execute_tool(name, arguments, context)
            # Startup tracks creation uncertainty; end reports binding effects.
            if (result.side_effect == "unknown"
                    and name not in ("agent_start_session", "agent_close_session")):
                for record in self.workflow.sessions.values():
                    record["blocked"] = True
            self.operation = {**self.operation, "status": "completed",
                              "side_effect": result.side_effect}
            return result
        except BaseException:
            metadata_only = name == "agent_finish_reconstruction"
            if self.operation is not None:
                self.operation = {**self.operation, "status": "interrupted",
                                  "side_effect": "none" if metadata_only else "unknown"}
            if metadata_only:
                # Delivery only reads native state and commits local metadata.
                # Keep a prepared receipt/Journal failure without inventing a CAD write.
                raise
            for record in self.workflow.sessions.values():
                phase = record["startup"]["phase"]
                if (name == "agent_close_session"
                        or name == "agent_start_session" and phase in ("cleanup", "failed")):
                    continue
                record["blocked"] = True
                record["source_basis_unknown"] = True
                role = {"target_start": "target", "source_start": "source"}.get(phase)
                if role and role not in record["startup"]["unconfirmed_roles"]:
                    record["startup"]["unconfirmed_roles"].append(role)
            raise
        finally:
            if _current_controller is self and not self.workflow.sessions:
                _current_controller = None
            _execution_lock.release()

    def task_state(self):
        if not _execution_lock.acquire(blocking=False):
            raise RuntimeError("Read the last event snapshot while a tool is running")
        try:
            record = next(iter(self.workflow.sessions.values()), None)
            if record is None:
                return None
            evidence_ids = record["evidence_order"][-self.workflow.known_evidence_limit:]
            return to_json_value({**session._session_metadata(record),
                                  "source_revision": record.get("source_revision"),
                                  "initial_source_views": record["initial_source_views"],
                                  "recent_validation": record["recent_validation"],
                                  "recent_manual_comparison": record.get("recent_manual_comparison", {}),
                                  "recent_paired_views": record.get("recent_paired_views", {}),
                                  "current_target_revision": record.get("current_target_revision"),
                                  "reconstruction": record.get("reconstruction", {}),
                                  "delivery_receipt": record.get("delivery_receipt"),
                                  "known_evidence": [evidence_summary(item, record["evidence_facts"][item])
                                                     for item in evidence_ids],
                                  "evidence_total": len(record["evidence_order"])})
        finally:
            _execution_lock.release()

    def action_started(self, binding, action):
        """Publish an already adopted local action; no CAD or journal I/O here."""
        if not _execution_lock.acquire(blocking=False):
            raise RuntimeError("The original Controller is still busy")
        try:
            record = self.workflow.record(binding)
            if action.scope == "reconstruct" and record.get("reconstruction", {}).get("run_id") != action.id:
                record["reconstruction"] = {"phase": "reconstructing", "run_id": action.id}
        finally:
            _execution_lock.release()

    def recover_delivery(self, binding):
        """Confirm only the original pending declaration after explicit journal recovery."""
        if not _execution_lock.acquire(blocking=False):
            raise RuntimeError("The original Controller is still busy")
        try:
            record = self.workflow.record(binding)
            pending = record.get("pending_delivery")
            if pending is None:
                return None
            arguments = {key: value for key, value in pending["arguments"].items() if key != "session_id"}
            trusted = pending["execution"]
        finally:
            _execution_lock.release()
        result = self.execute("agent_finish_reconstruction", arguments, execution=trusted)
        if result.is_error and result.value.get("code") != "journal_pending":
            if _execution_lock.acquire(blocking=False):
                try:
                    self.workflow.record(binding).pop("pending_delivery", None)
                finally:
                    _execution_lock.release()
        return result

    def record_manual_comparison(self, binding, comparison_id, rows):
        if not _execution_lock.acquire(blocking=False):
            raise RuntimeError("The original Controller is still busy")
        try:
            return to_json_value(validation.record_manual_comparison(self.workflow.record(binding), comparison_id, rows))
        finally:
            _execution_lock.release()

    def evidence_references(self):
        """Read on the worker outside dispatch, just like task_state."""
        if not _execution_lock.acquire(blocking=False):
            raise RuntimeError("Read the last event snapshot while a tool is running")
        try:
            record = next(iter(self.workflow.sessions.values()), None)
            return tuple(record["evidence_order"]) if record is not None else ()
        finally:
            _execution_lock.release()

    def image_group(self, evidence_id):
        """Resolve retention on the worker before entering CAD dispatch."""
        if not _execution_lock.acquire(blocking=False):
            raise RuntimeError("Read the last event snapshot while a tool is running")
        try:
            record = next(iter(self.workflow.sessions.values()), None)
            if record is None or record["evidence_facts"].get(evidence_id, {}).get("image_registration") is None:
                raise ValueError("No complete registered final PNG group exists for this task")
            return evidence_id
        finally:
            _execution_lock.release()

    def model_journal(self, session_id, *, event=None, summary=None, duration_ms=None, persist=True, flush=False,
                      function="ModelRequests.request"):
        """Internal, binding-checked persistence; never enters CAD execution."""
        if not _execution_lock.acquire(blocking=False):
            raise RuntimeError("The original Controller is still busy")
        try:
            record = self.workflow.sessions.get(session_id)
            if record is None or _current_controller not in (None, self):
                raise ValueError("The original journal binding is no longer available")
            recorder = record["journal"]
            sequence = None
            if event is not None:
                row = recorder.emit(event, function=function, role="agent", summary=summary,
                                    duration_ms=duration_ms, persist=persist)
                sequence = row["sequence"] if row is not None else None
            if flush:
                recorder.flush()
            return {"appended_sequence": sequence, **recorder.metadata()}
        finally:
            _execution_lock.release()

    def _bind_arguments(self, name, arguments):
        if name not in TOOLS:
            raise PublicError("capability_unavailable", "controller_preflight", "Unknown CAD tool")
        arguments = {} if arguments is None else arguments
        if not isinstance(arguments, dict):
            raise PublicError("invalid_request", "controller_preflight", "Tool arguments must be an object")
        if "session_id" in arguments:
            raise PublicError("invalid_request", "controller_preflight",
                              "The Controller supplies the current task binding")
        arguments = to_json_value(arguments)
        record = next(iter(self.workflow.sessions.values()), None)
        if (name != "agent_start_session" and record is not None
                and "session_id" in _binding_keys(TOOLS[name]["input_schema"])):
            arguments["session_id"] = record["session_id"]
        return arguments

    def dispatch(self, name, args):
        workflow = self.workflow
        if name not in TOOLS: raise PublicError("capability_unavailable", "public_discovery", "Unknown public tool")
        validate_input_schema(args, TOOLS[name]["input_schema"], path=name)
        context = workflow.call_context.get()
        context["request_summary"], context["request_role"] = _request_summary(name, args)
        record = workflow.record(args["session_id"]) if "session_id" in args else None
        if (record is not None and (record["startup"]["status"] != "ready" or record["closed"])
                and name not in ("agent_get_state", "agent_close_session", "agent_journal")):
            raise PublicError("session_start_incomplete", "public_preflight",
                              "Session is not active; inspect startup, close and journal facts before any modeling",
                              side_effect="unknown" if record["blocked"] else "none")
        trusted = context.get("execution")
        guard_record = record or (workflow.sessions.get(trusted.get("binding")) if trusted else None)
        if guard_record is not None:
            guard_action(guard_record, name, args, trusted)
        if name in FEATURE_SPECS and FEATURE_SPECS[name]["side_effect"] != "none":
            validate_domain_evidence(args, record, scope=trusted["scope"] if trusted else "reconstruct")
        if "evidence" in args and "intent" in args:
            context["feature_evidence"] = args["evidence"]
            context["feature_intent"] = args["intent"][:400]
        return self._handlers[name](name, args, record, context["started"], workflow)


    def _execute_tool(self, name, arguments, context):
        workflow = self.workflow
        token = workflow.call_context.set(context)
        record = None
        value = None
        try:
            arguments = self._bind_arguments(name, arguments)
            record = workflow.associated(arguments)
            if record is not None:
                workflow.progress(session_id=record["session_id"])
            value = self.dispatch(name, arguments)
            value = {**value, "operation_id": context["operation_id"]}
            if record is not None: value["journal"] = record["journal"].metadata()
            response = _success_response(value, context)
            # Publish only after successful local response construction. This does
            # not certify network receipt or inspection; failures never attach images.
            pending = context.get("pending_evidence")
            if pending is not None:
                evidence_record, evidence_id, fact = pending
                cleanup_started = time.perf_counter()
                if "image_delivery" in value:
                    if value["image_delivery"]["status"] != "prepared":
                        value["image_cleanup"] = cleanup_unpublished_outputs(value, evidence_record["artifacts"])
                    cleanup = value.get("image_cleanup")
                    if cleanup is not None:
                        fact["image_cleanup"] = {key: item for key, item in cleanup.items() if key != "files"}
                        workflow.emit(evidence_record, "observation.images_cleanup", name,
                            {"evidence_id": evidence_id, **cleanup}, cleanup_started,
                            level="info" if cleanup["status"] == "completed" else "warning", role=fact["role"],
                            persist=evidence_record["journal"].complete)
                    # The cleanup event also needs durable confirmation. Do not
                    # publish images if its write failed after observation.completed.
                    if value["image_delivery"]["status"] == "prepared" and not evidence_record["journal"].complete:
                        value["image_delivery"] = {"status": "unavailable", "image_count": 0,
                            "scope": "observation_capture", "reason": "journal_pending", "inspection": "not_attested"}
                        value["image_previews"] = []
                        context["image_content"] = ()
                        fact.pop("image_registration", None)
                        fact["image_delivery"] = value["image_delivery"]
                        cleanup = value["image_cleanup"] = cleanup_unpublished_outputs(value, evidence_record["artifacts"])
                        fact["image_cleanup"] = {key: item for key, item in cleanup.items() if key != "files"}
                        workflow.emit(evidence_record, "observation.delivery_unavailable", name,
                            {"evidence_id": evidence_id, "reason": "journal_pending", "image_cleanup": cleanup},
                            role=fact["role"], persist=False)
                    value["journal"] = evidence_record["journal"].metadata()
                    response = _success_response(value, context)
                previous_preparation = evidence_record["initial_source_views"]
                evidence_record["evidence_facts"][evidence_id] = fact
                try:
                    evidence_record["evidence_order"].append(evidence_id)
                    if fact["kind"] == "initial_views" and fact["image_delivery"]["status"] == "prepared":
                        evidence_record["initial_source_views"] = {
                            "status": "prepared", "evidence_id": evidence_id,
                            "direction_count": fact["initial_views"]["direction_count"],
                            "capture_id": fact["initial_views"]["capture_id"],
                            "scope": "local_response_preparation", "inspection": "not_attested"}
                    if (fact.get("actor") == "model" and fact.get("image_registration", {}).get("paired_revisions")
                            and fact.get("image_delivery", {}).get("status") == "prepared"):
                        evidence_record["recent_paired_views"] = {
                            "evidence_id": evidence_id, "actor": "model",
                            "paired_revisions": fact["image_registration"]["paired_revisions"]}
                except Exception:
                    evidence_record["evidence_facts"].pop(evidence_id, None)
                    if evidence_record["evidence_order"] and evidence_record["evidence_order"][-1] == evidence_id:
                        evidence_record["evidence_order"].pop()
                    evidence_record["initial_source_views"] = previous_preparation
                    raise
            completion = context.get("pending_delivery")
            if completion is not None:
                delivery_record, receipt = completion
                self.workflow.guard_journal(delivery_record)
                delivery_record.update(
                    delivery_receipts={**delivery_record["delivery_receipts"], receipt["receipt_id"]: receipt},
                    delivery_receipt=receipt,
                    reconstruction={"phase": "delivered", "run_id": receipt["run_id"]})
                delivery_record.pop("pending_delivery", None)
            return response
        except Exception as exc:
            if record is None:
                record = workflow.sessions.get(context.get("progress", {}).get("session_id"))
            error = {"operation_id": context["operation_id"], **error_payload(exc)}
            pending = context.get("pending_evidence")
            if (pending is not None and isinstance(value, dict) and "outputs" in value
                    and pending[1] not in pending[0]["evidence_facts"]):
                try:
                    cleanup = cleanup_unpublished_outputs(value, pending[0]["artifacts"])
                    error["image_cleanup"] = cleanup
                    workflow.emit(pending[0], "observation.delivery_unavailable", name,
                        {"evidence_id": pending[1], "reason": "response_or_registration_failed", "image_cleanup": cleanup},
                        role=pending[2]["role"], persist=pending[0]["journal"].complete)
                except Exception as cleanup_error:
                    error["image_cleanup"] = {"status": "unconfirmed", "exception_type": type(cleanup_error).__name__}
            if record is not None:
                error["session_id"] = record["session_id"]
            # Per-role cleanup facts override the last successful Bridge call.
            if (not isinstance(exc, PublicError) and getattr(exc, "operation", None) is None
                    and not hasattr(exc, "close_results")):
                error["side_effect"] = context["side_effect"]
                error["retry_action"] = resolve_retry_action(
                    error["error_class"], error["side_effect"]
                )
            if hasattr(exc, "runtime"): error["runtime"] = exc.runtime
            query, role = context.get("request_summary", {}), context.get("request_role", "agent")
            if name != "agent_journal":
                workflow.emit(record, "operation.failed", name, failure_summary(error, query), context["started"],
                              level="error", role=role, persist=not (record and record["journal"].start_failed))
            if record is not None: error["journal"] = record["journal"].metadata()
            return ToolResult(error, is_error=True, side_effect=error["side_effect"])
        finally:
            workflow.call_context.reset(token)


def _request_summary(name, arguments):
    summary = request_summary(name, arguments)
    role = arguments.get("role", arguments.get("request", {}).get("role"))
    if role not in ("source", "target", "both"):
        role = "target" if name in FEATURE_SPECS or name in (
            "agent_undo", "agent_redo", "agent_save", "agent_validate") else "agent"
    return summary, role
