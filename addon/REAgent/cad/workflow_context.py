"""Concrete session state, guarded Bridge calls and journal coordination."""

import time
from contextvars import ContextVar

from REAgent.cad.client import BridgeClient
from REAgent.protocol.features import FEATURE_SPECS
from REAgent.protocol.observations import IMAGE_OBSERVATION_KINDS
from REAgent.protocol.operations import OPERATION_SPECS, request_side_effect
from REAgent.cad.errors import PublicError
from REAgent.cad.journal import invalidate_validation


TARGET_STATE_EVIDENCE = "target_state"


def evidence_issue(fact, role, document, revision):
    if fact is None:
        return "is not registered in this task"
    if fact["role"] != role or fact["document"] != document:
        return f"does not belong to the bound {role.title()} document"
    if fact["revision"] != revision:
        return f"belongs to a different {role.title()} revision"
    if (fact["kind"] in IMAGE_OBSERVATION_KINDS
            and fact["image_delivery"]["status"] != "prepared"):
        return "has no prepared image output"
    return None


def evidence_summary(evidence_id, fact):
    value = {"evidence_id": evidence_id, **{key: fact[key] for key in ("role", "kind", "revision")},
             "actor": fact.get("actor")}
    if "image_delivery" in fact:
        delivery = fact["image_delivery"]
        value["image_preparation"] = {"status": delivery["status"], "capture_id": delivery.get("capture_id")}
    registration = fact.get("image_registration")
    if registration is not None:
        value["image_keys"] = [row["artifact_key"] for row in registration["plan"]]
        value["paired_revisions"] = registration.get("paired_revisions")
    return value


def _invalidate_target_comparisons(record, reason):
    for key in ("recent_validation", "recent_manual_comparison"):
        record[key] = invalidate_validation(record[key], reason)


def mark_target_unknown(record):
    record.update(blocked=True, current_target_revision=None)
    _invalidate_target_comparisons(record, "target_write_outcome_unknown")


class WorkflowContext:
    """Process-local dependencies for CAD tools and feature adapters."""

    known_evidence_limit = 128

    def __init__(self, workflow, view_input=None) -> None:
        self.spec = workflow
        self.view_input = view_input
        self.clients = {"target": BridgeClient(role="target")}
        if workflow.native_source:
            self.clients["source"] = BridgeClient(role="source")
        self.sessions = {}
        self.call_context = ContextVar("freecad_call", default=None)
        self.journal_observer = None

    def record(self, session_id: str):
        if session_id not in self.sessions:
            error = PublicError("unknown_session", "public_preflight", "Unknown session_id")
            error.field_path = "session_id"
            raise error
        return self.sessions[session_id]


    def associated(self, arguments):
        session_id = arguments.get("session_id") if isinstance(arguments, dict) else None
        return self.sessions.get(session_id) if isinstance(session_id, str) else None


    def progress(self, **values):
        context = self.call_context.get()
        if context is not None:
            context["progress"] = values
            context["publish_progress"](values)


    def emit(self, record, event, function, summary, started=None, level="info", role="target", persist=True):
        if record:
            try:
                context = self.call_context.get() or {}
                if event == "feature.committed" and "feature_evidence" in context:
                    evidence_used = []
                    for item in context["feature_evidence"]:
                        fact = record["evidence_facts"][item]
                        row = {"evidence_id": item, "role": fact["role"], "kind": fact["kind"]}
                        if fact["kind"] in IMAGE_OBSERVATION_KINDS:
                            row["image_preparation_status"] = fact["image_delivery"]["status"]
                        evidence_used.append(row)
                    summary = {**summary, "evidence_used": evidence_used}
                    summary.setdefault("intent", context.get("feature_intent"))
                    summary["intent_origin"] = "agent_statement"
                    if record["initial_source_views"] is not None:
                        summary["initial_views_preparation"] = {
                            "evidence_id": record["initial_source_views"]["evidence_id"],
                            "scope": "session_precondition", "inspection": "not_attested"}
                trusted = context.get("execution") or {}
                record["journal"].emit(event, function=function, persist=persist,
                    summary={"operation_id": context.get("operation_id"),
                             "actor": trusted.get("actor", "external"), "run_id": trusted.get("run_id"),
                             **{key: trusted[key] for key in ("comparison_id", "comparison_step") if key in trusted},
                             **summary}, role=role, level=level,
                    duration_ms=None if started is None else (time.perf_counter()-started)*1000)
            except Exception as exc:
                record["journal"].note_failure(exc)


    def guard_journal(self, record):
        if not record["journal"].complete:
            failure = PublicError("journal_pending", "public_preflight",
                                  "Journal is incomplete; inspect the task log. End task retries a recoverable I/O failure in the original output directory; lost events and cleanup residue remain unresolved")
            failure.error_class = "persistence"
            raise failure

    def require_source_reference(self, record):
        if record["source_document"]:
            task = self.clients["source"].call("agent_ping").get("active_task") or {}
            if (task.get("session_id") != record["session_id"]
                    or task.get("reference_current") is not True):
                raise ValueError("The native Source changed or closed. Start a new task from the reference file.")

    def stage_target_state_evidence(self, record, value, *, revision):
        if record["blocked"]:
            return
        context = self.call_context.get()
        evidence_id = "target-state-" + context["operation_id"].removeprefix("op-")
        fact = {"role": "target", "document": record["target_document"], "kind": TARGET_STATE_EVIDENCE,
                "revision": revision, "object": value["tip"],
                "actor": (context.get("execution") or {}).get("actor", "external")}
        context["pending_evidence"] = (record, evidence_id, fact)
        value["evidence_id"] = evidence_id

    def call(self, record, method, role="target", **params):
        document = record[role + "_document"]
        if not document: raise PublicError("capability_unavailable", "public_preflight", "Session has no " + role + " document")
        payload = {"document": document, **params}
        if "session_id" in OPERATION_SPECS[method]["input_schema"]["properties"]:
            payload["session_id"] = record["session_id"]
        effect = request_side_effect(method, payload)
        target_was_blocked = bool(role == "target" and record.get("blocked"))
        if (target_was_blocked
                and effect != "none"
                and method != "agent_close_session"):
            raise PublicError(
                "write_blocked",
                "public_preflight",
                "Target write state is unknown; only read operations or explicit session close are allowed",
                side_effect="unknown",
            )
        if effect != "none" and method not in ("agent_save", "agent_close_session"):
            self.guard_journal(record)
            if (role == "target" and effect == "committed"
                    and (method != "agent_observe" or payload["query"].get("paired"))):
                self.require_source_reference(record)
        try:
            operation = self.clients[role].call_result(method, **payload)
            context = self.call_context.get()
            if context is not None: context["side_effect"] = operation.side_effect
            value = operation.value
            if role == "target" and isinstance(value, dict):
                previous_revision = record.get("current_target_revision")
                current_revision = value.get("revision_after") or value.get("revision") or value.get("model_revision")
                changed = operation.side_effect == "committed" and (
                    method in ("agent_undo", "agent_redo") or method in FEATURE_SPECS)
                revision_changed = (current_revision is not None and previous_revision is not None
                                    and current_revision != previous_revision)
                native_block = value.get("audit_restoration_block") or value.get("document_state", {}).get("write_block")
                if native_block:
                    mark_target_unknown(record)
                elif not target_was_blocked:
                    if changed or revision_changed:
                        _invalidate_target_comparisons(record, "target_revision_changed")
                    if current_revision is not None or changed:
                        record["current_target_revision"] = current_revision
                if "document_state" in value:
                    value["document_state"]["target_writes_blocked"] = bool(record.get("blocked"))
                if method in FEATURE_SPECS and value.get("status") == "committed":
                    self.stage_target_state_evidence(record, value, revision=value["revision_after"])
            return value
        except Exception as exc:
            if method == "agent_close_session":
                raise  # Binding release uncertainty does not change model facts.
            operation = getattr(exc, "operation", None)
            if role == "source" and (getattr(exc, "instance_changed", False)
                                     or operation is not None and operation.side_effect == "unknown"):
                record["source_basis_unknown"] = True
                record["initial_source_views"] = None
            if role == "target" and (getattr(exc, "instance_changed", False)
                                     or operation is not None and operation.side_effect == "unknown"):
                mark_target_unknown(record)
            raise
