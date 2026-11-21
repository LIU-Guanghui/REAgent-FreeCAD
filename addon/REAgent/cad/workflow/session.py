"""Session workflow for the bound Source and Target windows."""

import os
import uuid
from pathlib import Path

from REAgent.cad.artifacts import absolute_path, artifact_paths, same_path
from REAgent.cad.errors import PublicError, error_payload
from REAgent.cad.journal import JournalRecorder, saved_applicability
from REAgent.protocol.definitions import NATIVE_SOURCE_EXTENSIONS
from REAgent.protocol.observations import INITIAL_SOURCE_DISPLAY_MODES, SESSION_OBSERVATION_POLICY_SCHEMA
from REAgent.json_schema import validate_input_schema
from REAgent.cad.workflow.discovery import runtime as discover_runtime
from REAgent.cad.workflow_context import evidence_issue, evidence_summary, mark_target_unknown


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
                 source_path=record["startup"]["source_path"],
                 artifacts=dict(record["artifacts"]))


def _input_labels(inputs):
    return [{"index": item["index"], "filename": item["filename"]} for item in inputs]


def session_metadata(record, *, addon_block=None):
    startup_block = record["startup"]["status"] != "ready"
    blocks = {"startup": startup_block, "closed": record["closed"],
              "journal": not record["journal"].complete, "controller": bool(record["blocked"]),
              "source": record["source_basis_unknown"], "addon": addon_block}
    return {"session_id": record["session_id"], "startup": record["startup"],
            "session_status": "closed" if record["closed"] else record["startup"]["status"],
            "workflow": record["workflow"].name, "source_document": record["source_document"],
            "target_document": record["target_document"],
            "inputs": _input_labels(record["inputs"]),
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
        ctx.clients["target"].reset_binding(clear_pin=True)
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
    validate_input_schema(policy, SESSION_OBSERVATION_POLICY_SCHEMA, path="agent_open_session.observation_policy")
    workflow = ctx.spec
    source_path = (absolute_path(args["source_path"], "agent_open_session.source_path",
                                NATIVE_SOURCE_EXTENSIONS[workflow.native_source]) if workflow.native_source else None)
    inputs = args.get("inputs", [])
    if bool(inputs) != workflow.image_inputs or (not source_path and "source_path" in args):
        raise ValueError("Input files must match the selected CAD workflow")
    stem = Path(source_path or inputs[0]["path"]).stem if source_path or inputs else "Model"
    output_dir = absolute_path(args["output_dir"], "agent_open_session.output_dir")
    if source_path and not os.path.isfile(source_path):
        failure = PublicError("invalid_request", "public_validation", "source_path must name an existing file")
        failure.field_path = "agent_open_session.source_path"
        raise failure
    session_id = "session-" + uuid.uuid4().hex[:20]
    record = {"session_id": session_id, "workflow": workflow, "inputs": inputs,
              "source_document": None, "target_document": None, "source_revision": None,
              "evidence_order": [], "evidence_facts": {}, "blocked": False,
              "recent_validation": {"status": "not_requested"},
              "recent_manual_comparison": {"status": "not_requested"}, "recent_paired_views": {},
              "reconstruction": {"phase": "reconstructing", "run_id": None},
              "delivery_receipts": {}, "delivery_receipt": None,
              "artifacts": artifact_paths(stem, output_dir), "close_results": {},
              "closed": False, "finished_recorded": False}
    record.update(observation_policy=policy, initial_source_views=None, source_basis_unknown=False)
    record["startup"] = {
        "status": "starting", "phase": "preflight", "unconfirmed_roles": [],
        "source_path": source_path,
    }
    record["journal"] = JournalRecorder(session_id, record["artifacts"], on_update=ctx.journal_observer)
    _sessions[session_id] = record
    _startup_phase(ctx, record, "preflight")
    payload = {k: v for k, v in args.items() if k not in ("output_dir", "observation_policy", "inputs")}
    if source_path:
        payload["source_path"] = source_path
    payload.setdefault("document", "Model")
    source_revision_basis = "step_sha256" if workflow.compares_stp else "file_sha256"
    payload["session_id"] = session_id
    created = []
    try:
        _startup_phase(ctx, record, "journal_start")
        record["journal"].record_start(function="agent_open_session", summary={
            "operation_id": (ctx.call_context.get() or {}).get("operation_id"),
            "workflow": workflow.name, "source_path": source_path,
            "input_count": len(inputs), "inputs": _input_labels(inputs),
            "observation_policy": policy})
        payload["artifacts"] = dict(record["artifacts"])
        _startup_phase(ctx, record, "discover_runtime")
        runtime = _runtime("both" if source_path else "target")
        if not all(v["available"] for v in runtime.values()):
            failure = PublicError("capability_unavailable", "capability_discovery", "Required bound GUI or native contract is unavailable")
            failure.runtime = runtime
            raise failure
        if source_path and runtime["source"]["instance"]["process_id"] == runtime["target"]["instance"]["process_id"]:
            raise ValueError("Source and Target must be separate GUI processes")
        if any(item["instance"].get("active_task") is not None for item in runtime.values()):
            raise PublicError("task_active", "capability_discovery",
                              "A GUI retains an active or unresolved task; do not replace its binding")
        _startup_phase(ctx, record, "target_start")
        ctx.guard_journal(record)
        target = _clients["target"].call("agent_open_session", **payload)
        record["startup"]["source_path"] = target["source_path"]
        record["target_document"] = target["document"]
        created.append("target")
        context = ctx.call_context.get()
        if context is not None:
            context["side_effect"] = "committed"
        _emit(record, "session.role_started", "agent_open_session", {"role": "target", "document": target["document"]}, role="target")
        if target.get("artifacts") != record["artifacts"]:
            raise ValueError("Target must confirm the allocated artifact identity")
        if source_path:
            source_args = {"document": payload["document"] + "_Source",
                           "source_path": target["source_path"], "session_id": session_id,
                           "artifacts": dict(record["artifacts"]),
                           **{k: v for k, v in target["tolerance_contract"].items()
                              if k in ("linear_tolerance_mm", "angular_tolerance_deg")}}
            _startup_phase(ctx, record, "source_start")
            ctx.guard_journal(record)
            source = _clients["source"].call("agent_open_session", **source_args)
            record["source_document"] = source["document"]
            created.append("source")
            _emit(record, "session.role_started", "agent_open_session",
                  {"role": "source", "document": source["document"]}, role="source")
            _startup_phase(ctx, record, "verify_revision")
            revision = target.get("source_revision")
            if (not isinstance(revision, str) or not revision
                    or target.get("source_revision_basis") != source_revision_basis
                    or source.get("source_revision_basis") != source_revision_basis
                    or source.get("source_revision") != revision):
                raise ValueError("Source and Target did not confirm one matching reference revision")
            record["source_revision"] = revision
            if source.get("artifacts") != record["artifacts"]:
                raise ValueError("Source must confirm the allocated artifact identity")
    except Exception as failure:
        failed_phase = record["startup"]["phase"]
        operation = getattr(failure, "operation", None)
        role_starting = {"target_start": "target", "source_start": "source"}.get(failed_phase)
        unconfirmed = (role_starting is not None and operation is not None and operation.side_effect == "unknown")
        if unconfirmed:
            record["startup"]["unconfirmed_roles"] = [role_starting]
            mark_target_unknown(record)
            if role_starting == "source":
                record["source_basis_unknown"] = True
        _startup_phase(ctx, record, "cleanup")
        cleanup_results = {}
        for role in reversed(created):
            try:
                cleanup_results[role] = _call(record, "agent_close_session", role)
            except Exception as cleanup_error:
                cleanup_results[role] = {"status": "failed", "error": error_payload(cleanup_error)}
        if unconfirmed:
            cleanup_results[role_starting] = {"status": "creation_unconfirmed", "side_effect": "unknown"}
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
    _emit(record, "task.started", "agent_open_session", {"source_path": target.get("source_path"),
                                                  "target_document": target["document"], "observation_policy": policy})
    record["startup"]["status"] = "ready"
    _startup_phase(ctx, record, "ready")
    initial_views = workflow.native_source in INITIAL_SOURCE_DISPLAY_MODES and policy == "visual"
    return {**target, **session_metadata(record),
            "initial_source_views": {"status": "not_prepared" if initial_views else "not_required"},
            "runtime": runtime}


def _dimension_check(check, fact):
    measurement = check["measurement"]
    path = measurement["readback_path"]
    value = next((row for row in fact.get("dimension_readbacks", ()) if row["readback_path"] == path), None)
    if value is None:
        raise ValueError(f"Replace {path} with a path from dimension_readbacks in the cited Target observation for {check['requirement']}.")
    if measurement["unit"] != value["unit"]:
        raise ValueError("Express the drawing nominal and tolerance in " + value["unit"] + " for " + check["requirement"])
    tolerance = measurement.get("tolerance")
    if tolerance is not None and tolerance["lower"] > tolerance["upper"]:
        raise ValueError("List the drawing tolerances in ascending order for " + check["requirement"])
    readback = {**measurement, "measured": value["value"], "unit": value["unit"]}
    if "object" in fact:
        readback["target_object"] = fact["object"]
    return {**check, "measurement": readback}


def _delivery_checks(checks, record, observations, revision):
    adopted = set(observations.get("target_evidence_ids", ()))
    used_queries = set()
    confirmed = []
    errors = []
    for index, check in enumerate(checks):
        observation_id = check["observation_id"]
        fact = record["evidence_facts"].get(observation_id)
        issue = evidence_issue(fact, "target", record["target_document"], revision)
        if issue is None and observation_id not in adopted:
            issue = "has not been adopted in a model request"
        if issue is not None:
            errors.append(f"checks[{index}].observation_id {observation_id} {issue}")
            continue
        registration = fact.get("image_registration") or {}
        used_queries.add("paired_views" if registration.get("paired_revisions") else fact["kind"])
        try:
            confirmed.append(_dimension_check(check, fact) if "measurement" in check else check)
        except ValueError as exc:
            errors.append(str(exc))
    if errors:
        raise ValueError(". ".join(errors[:4]))
    workflow = record["workflow"]
    for kinds in workflow.delivery_queries:
        if used_queries.isdisjoint(kinds):
            raise ValueError("Delivery checks must cite current "
                             + " or ".join(kind.replace("_", " ") for kind in kinds) + " evidence")
    if workflow.require_dimension_readback and not any("measurement" in check for check in confirmed):
        raise ValueError("Drawing delivery requires a dimension with a numeric Target readback")
    return confirmed


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
    ctx.require_source_reference(record)
    state = ctx.call(record, "agent_get_state")
    basis = state.get("source_basis", {})
    if ((record["source_document"] and basis.get("matches_bound") is not True)
            or state.get("audit_restoration_block")):
        raise ValueError("Current reference basis and Target restoration must be confirmed")
    revision = record.get("current_target_revision")
    workflow = record["workflow"]
    observations = trusted["observations"]
    if workflow.delivery is not None:
        details = workflow.delivery(record, args, observations, revision)
    else:
        if workflow.require_reference is not None:
            workflow.require_reference(record, observations)
        model_state = state.get("model_state", {})
        if not revision or model_state.get("state") != "solid_ready":
            raise ValueError("Delivery requires one valid current native solid")
        geometry = model_state["result_geometry"]
        details = {
            "source_revision": record["source_revision"],
            "target_revision": revision,
            "model_state": {
                "state": model_state["state"],
                "result_geometry": {"is_valid": geometry["is_valid"], "solid_count": geometry["solid_count"]},
            },
            "checks": _delivery_checks(args["checks"], record, observations, revision),
        }
    receipt = {"receipt_id": receipt_id, "run_id": trusted["run_id"], "actor": "model",
               "document": record["target_document"], **details, "notes": args.get("notes")}
    record["pending_delivery"] = {"receipt": receipt, "arguments": dict(args), "execution": dict(trusted)}
    summary = {key: receipt[key] for key in
               ("receipt_id", "run_id", "document", "target_revision", "model_state",
                "validation_operation_id", "paired_evidence_id")
               if key in receipt}
    if workflow.compares_stp:
        summary["limitation_count"] = len(receipt["limitations"])
    else:
        summary["checks"] = receipt["checks"]
    ctx.emit(record, "reconstruction.delivery_prepared", "agent_finish_reconstruction", summary,
             context["started"], role="agent")
    ctx.guard_journal(record)
    context["pending_delivery"] = (record, receipt)
    return {"status": "delivered", "receipt": receipt, "repeated": False}


def run(name, args, record, started, ctx):
    _runtime = lambda role: discover_runtime(role, ctx.clients)
    _call = ctx.call
    _emit = ctx.emit
    _KNOWN_EVIDENCE_ID_LIMIT = ctx.known_evidence_limit
    if name == "agent_ping": return _runtime("both" if ctx.spec.native_source else "target")
    if name == "agent_open_session": return start(args, ctx)
    if record is None: raise ValueError("session_id is required")
    if name == "agent_finish_reconstruction":
        return finish_reconstruction(args, record, ctx)
    if name == "agent_manage_journal":
        if args["action"] == "read":
            return record["journal"].read(args.get("after_sequence", 0), args.get("limit", 100))
        output_dir = absolute_path(args["output_dir"], "agent_manage_journal.output_dir")
        if not same_path(output_dir, record["artifacts"]["output_dir"]):
            raise ValueError("flush output_dir must match the session's bound directory")
        journal = record["journal"].flush()
        released = _release_closed(record, ctx)
        return {"session_id": record["session_id"], "action": "flush", "journal": journal,
                "session_retained": not released, "native_state": "not_queried"}
    if name == "agent_get_state":
        if record["startup"]["status"] != "ready" or record["closed"]:
            return {
                **session_metadata(record), "native_state": "not_queried",
                "initial_source_views": {"status": "unavailable",
                                         "reason": "session_closed" if record["closed"] else "session_start_incomplete"},
            }
        value = _call(record, "agent_get_state")
        ctx.stage_target_state_evidence(record, value, revision=value["revision"])
        offset = args.get("evidence_offset", max(0, len(record["evidence_order"]) - _KNOWN_EVIDENCE_ID_LIMIT))
        limit = args.get("evidence_limit", _KNOWN_EVIDENCE_ID_LIMIT)
        known_evidence_ids = record["evidence_order"][offset:offset + limit]
        basis = value.get("source_basis", {})
        preparation = record["initial_source_views"]
        has_initial_views = ctx.spec.native_source in INITIAL_SOURCE_DISPLAY_MODES
        preparation_state = dict(preparation) if preparation else {
            "status": "not_prepared" if has_initial_views and record["observation_policy"] == "visual" else "not_required"}
        if has_initial_views:
            if record["source_basis_unknown"]:
                preparation_state.update(status="unavailable", reason="source_restoration_unknown")
            elif not basis.get("available") or basis.get("matches_bound") is not True:
                preparation_state.update(status="unavailable", reason="source_file_unavailable_or_changed")
        return {**value, **session_metadata(record, addon_block=bool(value.get("audit_restoration_block"))),
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
    return {name: run for name in ('agent_ping', 'agent_open_session', 'agent_get_state', 'agent_close_session',
                                  'agent_manage_journal', 'agent_finish_reconstruction')}
