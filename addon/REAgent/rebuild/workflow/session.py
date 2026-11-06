"""Session workflow for the bound Source and Target windows."""

import os
import uuid

from REAgent.rebuild.artifacts import absolute_path, artifact_paths, same_path
from REAgent.rebuild.errors import PublicError, error_payload
from REAgent.rebuild.journal import JournalRecorder, saved_applicability
from REAgent.protocol.observations import SESSION_OBSERVATION_POLICY_SCHEMA
from REAgent.schema import validate_input_schema
from REAgent.rebuild.workflow.discovery import runtime as discover_runtime
from REAgent.rebuild.workflow_context import evidence_summary
from REAgent.protocol.definitions import validation_items


def _close_side_effect(results):
    if any(
        entry.get("status") in ("failed", "abandoned")
        and (entry.get("error", {}).get("side_effect") == "unknown"
             or entry.get("side_effect") == "unknown")
        for entry in results.values()
        if isinstance(entry, dict)
    ):
        return "unknown"
    if any(
        entry.get("status") == "released"
        or entry.get("side_effect") == "committed"
        or entry.get("error", {}).get("side_effect") == "committed"
        for entry in results.values()
        if isinstance(entry, dict)
    ):
        return "committed"
    return "none"


def _bounded_close_results(results):
    # There are at most two roles; preserve the per-role facts and rely on the
    # existing bounded error projection for nested diagnostics.
    return {
        role: dict(entry) if isinstance(entry, dict) else {"status": "failed"}
        for role, entry in results.items()
    }


def _startup_phase(ctx, record, phase):
    record["startup"]["phase"] = phase
    ctx.progress(session_id=record["session_id"], phase=phase,
                 source_document=record["source_document"], target_document=record["target_document"],
                 source_path=record["startup"]["source_path"], resume_path=record["startup"]["resume_path"],
                 artifacts=dict(record["artifacts"]))


def _session_metadata(record, *, addon_block=None):
    startup_block = record["startup"]["status"] != "ready"
    blocks = {"startup": startup_block, "closed": record["closed"],
              "journal": not record["journal"].complete, "controller": bool(record["blocked"]),
              "source": record["source_basis_unknown"], "addon": addon_block}
    return {"session_id": record["session_id"], "startup": record["startup"],
            "session_status": "closed" if record["closed"] else record["startup"]["status"],
            "source_document": record["source_document"], "target_document": record["target_document"],
            "close_results": record["close_results"], "artifacts": dict(record["artifacts"]),
            "observation_policy": record["observation_policy"], "source_basis_unknown": record["source_basis_unknown"],
            "last_saved_checkpoint": saved_applicability(record),
            "target_writes_blocked": any(value is True for value in blocks.values()),
            "write_block_sources": blocks, "journal": record["journal"].metadata()}


def _release_closed(record, ctx):
    target_abandoned = record["close_results"].get("target", {}).get("status") == "abandoned"
    unconfirmed = [role for role in record["startup"]["unconfirmed_roles"]
                   if role != "target" or not target_abandoned]
    if (record["closed"] and record["journal"].complete and not unconfirmed
            and not record["source_document"] and (not record["target_document"] or target_abandoned)):
        del ctx.sessions[record["session_id"]]
        return True
    return False


def start(args, ctx):
    _clients = ctx.clients
    _sessions = ctx.sessions
    _call = ctx.call
    _emit = ctx.emit
    _runtime = lambda role: discover_runtime(role, _clients)
    if _sessions:
        previous = next(iter(_sessions.values()))
        ctx.progress(session_id=previous["session_id"], phase="blocked_by_current_task")
        ctx.guard_journal(previous)
        raise PublicError("task_active", "controller_preflight",
                          "Finish the retained task, including failed startup or closed journal state, before starting another",
                          side_effect="unknown" if previous["blocked"] or previous["startup"]["unconfirmed_roles"] else "none")
    for client in _clients.values():
        client.reset_binding()
    policy = args.get("observation_policy", SESSION_OBSERVATION_POLICY_SCHEMA["default"])
    validate_input_schema(policy, SESSION_OBSERVATION_POLICY_SCHEMA, path="agent_start_session.observation_policy")
    source_path = absolute_path(args["source_path"], "agent_start_session.source_path", (".step", ".stp"))
    output_dir = absolute_path(args["output_dir"], "agent_start_session.output_dir")
    resume_path = (absolute_path(args["resume_path"], "agent_start_session.resume_path", (".fcstd",))
                   if "resume_path" in args else None)
    for field, path in (("source_path", source_path), ("resume_path", resume_path)):
        if path and not os.path.isfile(path):
            failure = PublicError("invalid_request", "public_validation", field + " must name an existing file")
            failure.field_path = "agent_start_session." + field
            raise failure
    session_id = "session-" + uuid.uuid4().hex[:20]
    record = {"session_id": session_id, "source_document": None, "target_document": None,
              "evidence_order": [], "evidence_facts": {}, "blocked": False,
              "recent_validation": {"status": "not_requested"},
              "recent_manual_comparison": {"status": "not_requested"}, "recent_paired_views": {},
              "reconstruction": {"phase": "reconstructing", "run_id": None},
              "delivery_receipts": {}, "delivery_receipt": None,
              "artifacts": artifact_paths(source_path, output_dir), "close_results": {},
              "closed": False, "finished_recorded": False}
    record.update(observation_policy=policy, initial_source_views=None, source_basis_unknown=False)
    record["startup"] = {
        "status": "starting", "phase": "preflight", "unconfirmed_roles": [],
        "source_path": source_path, "resume_path": resume_path,
    }
    record["journal"] = JournalRecorder(session_id, record["artifacts"], on_update=ctx.journal_observer)
    _sessions[session_id] = record
    _startup_phase(ctx, record, "preflight")
    payload = {k: v for k, v in args.items() if k not in ("output_dir", "observation_policy")}
    payload["source_path"] = source_path
    if resume_path:
        payload["resume_path"] = resume_path
    payload.setdefault("document", "Model")
    payload["session_id"] = session_id
    created = []
    try:
        _startup_phase(ctx, record, "journal_start")
        record["journal"].record_start(function="agent_start_session", resume_path=resume_path, summary={
            "operation_id": (ctx.call_context.get() or {}).get("operation_id"),
            "source_path": source_path, "resume_path": resume_path, "observation_policy": policy})
        payload["artifacts"] = dict(record["artifacts"])
        _startup_phase(ctx, record, "discover_runtime")
        runtime = _runtime("both")
        if not all(v["available"] for v in runtime.values()):
            failure = PublicError("capability_unavailable", "capability_discovery", "Required bound GUI or native contract is unavailable")
            failure.runtime = runtime
            raise failure
        if runtime["source"]["instance"]["process_id"] == runtime["target"]["instance"]["process_id"]:
            raise ValueError("Source and Target must be separate GUI processes")
        if any(item["instance"].get("active_task") is not None for item in runtime.values()):
            raise PublicError("task_active", "capability_discovery",
                              "A GUI retains an active or unresolved task; do not replace its binding")
        _startup_phase(ctx, record, "target_start")
        ctx.guard_journal(record)
        target = _clients["target"].call("agent_start_session", **payload)
        record["startup"]["source_path"] = target["source_path"]
        record["target_document"] = target["document"]
        created.append("target")
        context = ctx.call_context.get()
        if context is not None:
            context["side_effect"] = "committed"
        _emit(record, "session.role_started", "agent_start_session", {"role": "target", "document": target["document"]}, role="target")
        source_args = {"document": payload["document"] + "_Source",
                       "source_path": target["source_path"], "session_id": session_id,
                       "artifacts": dict(record["artifacts"]),
                       **{k:v for k,v in target["tolerance_contract"].items()
                       if k in ("linear_tolerance_mm", "angular_tolerance_deg")}}
        _startup_phase(ctx, record, "source_start")
        ctx.guard_journal(record)
        source = _clients["source"].call("agent_start_session", **source_args)
        record["source_document"] = source["document"]
        created.append("source")
        _emit(record, "session.role_started", "agent_start_session", {"role": "source", "document": source["document"]}, role="source")
        _startup_phase(ctx, record, "verify_revision")
        revision = target.get("source_revision")
        if (not isinstance(revision, str) or not revision
                or target.get("source_revision_basis") != "step_sha256"
                or source.get("source_revision_basis") != "step_sha256"
                or source.get("source_revision") != revision):
            raise ValueError("Source and Target did not confirm one matching STEP revision")
        record["source_revision"] = revision
        if target.get("artifacts") != record["artifacts"] or source.get("artifacts") != record["artifacts"]:
            raise ValueError("Both roles must confirm the allocated artifact identity")
    except Exception as failure:
        failed_phase = record["startup"]["phase"]
        operation = getattr(failure, "operation", None)
        unconfirmed = operation is not None and operation.side_effect == "unknown"
        if unconfirmed:
            role = "source" if "target" in created else "target"
            record["startup"]["unconfirmed_roles"] = [role]
            record["blocked"] = True
            if role == "source":
                record["source_basis_unknown"] = True
        _startup_phase(ctx, record, "cleanup")
        cleanup_results = {}
        for role in reversed(created):
            try:
                cleanup_results[role] = _call(record, "agent_close_session", role)
            except Exception as cleanup_error:
                cleanup_results[role] = {"status": "failed", "error": error_payload(cleanup_error)}
        if unconfirmed:
            role = "source" if "target" in created else "target"
            cleanup_results.setdefault(role, {"status": "creation_unconfirmed", "side_effect": "unknown"})
        if cleanup_results:
            failure.close_results = _bounded_close_results(cleanup_results)
            failure.close_side_effect = "unknown" if unconfirmed else _close_side_effect(cleanup_results)
        for role, result in cleanup_results.items():
            if result.get("status") == "released" and result.get("ended") is True:
                record[role + "_document"] = None
        record["close_results"] = cleanup_results
        record["startup"].update(status="failed", failed_phase=failed_phase)
        record["startup"]["error"] = error_payload(failure)
        context = ctx.call_context.get()
        if context is not None and (unconfirmed or record["blocked"]):
            context["side_effect"] = "unknown"
        _startup_phase(ctx, record, "failed")
        raise
    _startup_phase(ctx, record, "record_started")
    _emit(record, "task.started", "agent_start_session", {"source_path": target.get("source_path"),
                                                  "target_document": target["document"], "observation_policy": policy})
    record["startup"]["status"] = "ready"
    _startup_phase(ctx, record, "ready")
    return {**target, **_session_metadata(record),
            "initial_source_views": {"status": "not_prepared" if policy == "visual" else "not_required"},
            "runtime": runtime}


def finish_reconstruction(args, record, ctx):
    context = ctx.call_context.get()
    trusted = context.get("execution") or {}
    if trusted.get("actor") != "model" or trusted.get("scope") != "reconstruct" or not trusted.get("run_id"):
        raise ValueError("Delivery requires an adopted model reconstruction action")
    receipt_id = "delivery-" + trusted["run_id"]
    existing = record["delivery_receipts"].get(receipt_id)
    if existing is not None:
        return {"status": "delivered", "receipt": existing, "repeated": True}
    ctx.guard_journal(record)
    if record["blocked"] or record["source_basis_unknown"]:
        raise ValueError("Unknown Target or Source restoration prevents delivery")
    state = ctx.call(record, "agent_get_state")
    basis = state.get("source_basis", {})
    if basis.get("matches_bound") is not True or state.get("audit_restoration_block"):
        raise ValueError("Current Source basis and Target restoration must be confirmed")
    revision = record.get("current_target_revision")
    comparison = record["recent_validation"]
    pair = record["recent_paired_views"]
    if (comparison.get("actor") != "model" or comparison.get("operation_id") != args["validation_operation_id"]
            or pair.get("evidence_id") != args["paired_evidence_id"]):
        raise ValueError("Reference the current model comparison and complete paired board: "
                         + str({"validation_operation_id": comparison.get("operation_id"),
                                "paired_evidence_id": pair.get("evidence_id")}))
    observations = trusted.get("observations", {})
    geometry_receipt = observations.get("geometry_comparison", {})
    image_receipt = observations.get("paired_views", {})
    if (geometry_receipt.get("operation_id") != comparison["operation_id"]
            or image_receipt.get("evidence_id") != pair["evidence_id"]
            or geometry_receipt.get("scope") != "model_request_completed"
            or image_receipt.get("scope") != "model_request_completed"):
        raise ValueError("The current geometry result and complete PNG board must enter an adopted action request")
    registered = record["evidence_facts"].get(pair["evidence_id"], {})
    registration = registered.get("image_registration") or {}
    revisions = {"source_revision": record["source_revision"], "target_revision": revision}
    if not revision:
        raise ValueError("Current Target revision is unavailable; delivery cannot be confirmed")
    if not registration:
        raise ValueError("Paired image evidence is not registered to the current task")
    if registration.get("actor") != "model":
        raise ValueError("Paired image evidence is not registered as a model capture")
    if registered.get("image_delivery", {}).get("status") != "prepared":
        raise ValueError("Paired image evidence is not prepared for delivery")
    if image_receipt.get("document") != record["target_document"]:
        raise ValueError("Adopted paired image receipt does not name the current Target document")
    identity = registration.get("paired_revisions") or {}
    if not identity:
        raise ValueError("Paired image evidence has no revision identity")
    if identity.get("source_revision") != revisions["source_revision"]:
        raise ValueError("Paired image Source revision is missing or differs from the bound Source")
    if identity.get("target_revision") != revisions["target_revision"]:
        raise ValueError("Paired image Target revision is missing or differs from the current Target")
    items = validation_items(comparison.get("requested", {}))
    if not items:
        raise ValueError("Delivery needs at least one requested geometric comparison")
    facts = comparison.get("facts", {})
    provenance = record.get("_recent_validation_provenance") or {}
    limitations = []
    available = False
    expected = {"document": record["target_document"], **revisions}
    if (geometry_receipt.get("document") != expected["document"]
            or geometry_receipt.get("revision") != revision):
        raise ValueError("Geometry receipt differs from the current Target document or revision")
    selected = [*items, *(["parameter_audit"] if comparison["requested"].get("parameter_audit") else [])]
    for item in selected:
        row = facts.get(item, {})
        if row.get("status") in (None, "pending", "running", "not_requested"):
            raise ValueError("Every selected delivery check must have an actual terminal result")
        dependency = provenance.get(item)
        required = expected if item != "parameter_audit" else {"document": expected["document"], "target_revision": revision}
        if not dependency or any(dependency.get(key) != value for key, value in required.items()):
            raise ValueError("A selected check has stale or unknown dependency revisions: " + item)
        usable = row.get("available") is True if item != "parameter_audit" else row.get("restored") is True
        if item in items and usable:
            available = True
        if not usable or row.get("status") in ("failed", "unavailable", "error"):
            limitations.append({"check": item, "status": row["status"], "reason": row.get("reason")})
    if not available:
        raise ValueError("All selected geometric comparisons are unavailable; no delivery receipt was issued")
    receipt = {"receipt_id": receipt_id, "run_id": trusted["run_id"], "actor": "model",
               "document": record["target_document"], **revisions,
               "validation_operation_id": comparison["operation_id"], "paired_evidence_id": pair["evidence_id"],
               "checks": facts, "limitations": limitations, "notes": args.get("notes")}
    record["pending_delivery"] = {"receipt": receipt, "arguments": dict(args), "execution": dict(trusted)}
    ctx.emit(record, "reconstruction.delivery_prepared", "agent_finish_reconstruction", {
                 key: receipt[key] for key in ("receipt_id", "run_id", "validation_operation_id",
                                               "paired_evidence_id", "checks", "limitations")},
             context["started"], role="agent")
    ctx.guard_journal(record)
    context["pending_delivery"] = (record, receipt)
    return {"status": "delivered", "receipt": receipt, "repeated": False}


def run(name, args, record, started, ctx):
    _runtime = lambda role: discover_runtime(role, ctx.clients)
    _call = ctx.call
    _emit = ctx.emit
    _KNOWN_EVIDENCE_ID_LIMIT = ctx.known_evidence_limit
    if name == "agent_ping": return _runtime("both")
    if name == "agent_start_session": return start(args, ctx)
    if record is None: raise ValueError("session_id is required")
    if name == "agent_finish_reconstruction":
        return finish_reconstruction(args, record, ctx)
    if name == "agent_journal":
        if args["action"] == "read":
            return record["journal"].read(args.get("after_sequence", 0), args.get("limit", 100))
        output_dir = absolute_path(args["output_dir"], "agent_journal.output_dir")
        if not same_path(output_dir, record["artifacts"]["output_dir"]):
            raise ValueError("flush output_dir must match the session's bound directory")
        journal = record["journal"].flush()
        released = _release_closed(record, ctx)
        return {"session_id": record["session_id"], "action": "flush", "journal": journal,
                "session_retained": not released, "native_state": "not_queried"}
    if name == "agent_get_state":
        if record["startup"]["status"] != "ready" or record["closed"]:
            return {
                **_session_metadata(record), "native_state": "not_queried",
                "initial_source_views": {"status": "unavailable",
                                         "reason": "session_closed" if record["closed"] else "session_start_incomplete"},
            }
        value = _call(record, "agent_get_state")
        offset = args.get("evidence_offset", max(0, len(record["evidence_order"]) - _KNOWN_EVIDENCE_ID_LIMIT))
        limit = args.get("evidence_limit", _KNOWN_EVIDENCE_ID_LIMIT)
        known_evidence_ids = record["evidence_order"][offset:offset + limit]
        basis = value.get("source_basis", {})
        preparation = record["initial_source_views"]
        preparation_state = dict(preparation) if preparation else {
            "status": "not_prepared" if record["observation_policy"] == "visual" else "not_required"}
        if record["source_basis_unknown"]:
            preparation_state.update(status="unavailable", reason="source_restoration_unknown")
        elif not basis.get("available") or basis.get("matches_bound") is not True:
            preparation_state.update(status="unavailable", reason="source_file_unavailable_or_changed")
        return {**value, **_session_metadata(record, addon_block=bool(value.get("audit_restoration_block"))),
                "initial_source_views": preparation_state,
                "known_evidence": {
                    "ids": known_evidence_ids,
                    "facts": [evidence_summary(item, record["evidence_facts"][item])
                              for item in known_evidence_ids],
                    "total": len(record["evidence_order"]),
                    "returned": len(known_evidence_ids),
                    "offset": offset,
                    "next_offset": offset + len(known_evidence_ids) if offset + len(known_evidence_ids) < len(record["evidence_order"]) else None,
                    "omitted": max(
                        0,
                        len(record["evidence_order"])
                        - len(known_evidence_ids),
                    ),
                    "order": "oldest_to_newest",
                    "scope": "current_session",
                },
                "recent_validation": record["recent_validation"],
                "recent_manual_comparison": record["recent_manual_comparison"],
                "recent_paired_views": record["recent_paired_views"],
                "reconstruction": record["reconstruction"], "delivery_receipt": record["delivery_receipt"]}
    if name == "agent_close_session":
        record["closed"] = True
        results = record.setdefault("close_results", {})
        for role in ("target", "source"):
            if role == "target" and results.get(role, {}).get("status") == "abandoned":
                continue
            if not record[role + "_document"]:
                if role == "target" and role in record["startup"]["unconfirmed_roles"]:
                    results.setdefault(role, {"status": "creation_unconfirmed", "side_effect": "unknown"})
                continue
            try:
                value = _call(record, name, role)
            except Exception as exc:
                results[role] = {"status": "failed", "error": error_payload(exc)}
                continue
            if not isinstance(value, dict):
                results[role] = {"status": "failed", "reason": "Session release returned an invalid result"}
                continue
            results[role] = value
            if value.get("status") == "released" and value.get("ended") is True:
                record[role + "_document"] = None
            else:
                results[role] = {"status": "failed", "reason": "Session release was not confirmed"}
        target = results.get("target")
        if target is not None and target.get("status") not in ("released", "abandoned"):
            # Human End task/Stop abandons only local Target ownership. Remote
            # cleanup and earlier unknown model outcomes remain unconfirmed.
            results["target"] = {**target, "status": "abandoned", "abandon_reason": "human_end_task",
                                 "document": record["target_document"], "native_cleanup": "unconfirmed"}
        abandoned_roles = [role for role, value in results.items() if value.get("status") == "abandoned"]
        if not record["finished_recorded"]:
            record["finished_recorded"] = True
            _emit(record, "task.finished", name, {"roles": _bounded_close_results(results),
                  "unconfirmed_creation": list(record["startup"]["unconfirmed_roles"]),
                  "target_write_state_unknown": record["blocked"],
                  "source_restoration_unknown": record["source_basis_unknown"],
                  "abandoned_roles": abandoned_roles, "document_actions": "none"}, started)
        else:
            _emit(record, "task.end_retried", name, {"roles": _bounded_close_results(results),
                  "abandoned_roles": abandoned_roles}, started)
        journal = record["journal"]
        if not journal.complete and journal.retryable_io:
            try:
                journal.flush()
            except PublicError:
                pass  # Keep the original recorder; another explicit End can retry once.
        released = _release_closed(record, ctx)
        context = ctx.call_context.get()
        if context is not None:
            context["side_effect"] = "unknown" if record["startup"]["unconfirmed_roles"] else _close_side_effect(results)
        return {"ended": True, "roles": _bounded_close_results(results), "journal": journal.metadata(),
                "abandoned_roles": abandoned_roles,
                "target_write_state_unknown": record["blocked"],
                "source_restoration_unknown": record["source_basis_unknown"],
                "unconfirmed_creation": list(record["startup"]["unconfirmed_roles"]),
                "session_retained": not released, "session_status": "closed"}
    raise ValueError("Unknown tool in workflow group")


def contribute():
    return {name: run for name in ('agent_ping', 'agent_start_session', 'agent_get_state', 'agent_close_session',
                                  'agent_journal', 'agent_finish_reconstruction')}
