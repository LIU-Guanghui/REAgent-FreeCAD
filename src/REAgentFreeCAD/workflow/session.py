"""Session workflow for the bound Source and Target windows."""

import os
import uuid

from ..artifacts import absolute_path, artifact_paths, same_path
from ..errors import PublicError, error_payload
from ..journal import JournalRecorder
from ..contract import SESSION_OBSERVATION_POLICY_SCHEMA, validate_input_schema
from .discovery import runtime as discover_runtime


def _close_side_effect(results):
    if any(
        entry.get("status") == "failed"
        and (entry.get("error", {}).get("side_effect") == "unknown"
             or entry.get("side_effect") == "unknown")
        for entry in results.values()
        if isinstance(entry, dict)
    ):
        return "unknown"
    if any(
        entry.get("status") == "closed"
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
              "journal": not record["journal"].complete, "mcp": bool(record["blocked"]),
              "source": record["source_basis_unknown"], "addon": addon_block}
    return {"session_id": record["session_id"], "startup": record["startup"],
            "session_status": "closed" if record["closed"] else record["startup"]["status"],
            "source_document": record["source_document"], "target_document": record["target_document"],
            "close_results": record["close_results"], "artifacts": dict(record["artifacts"]),
            "observation_policy": record["observation_policy"], "source_basis_unknown": record["source_basis_unknown"],
            "target_writes_blocked": any(value is True for value in blocks.values()),
            "write_block_sources": blocks, "journal": record["journal"].metadata()}


def _release_closed(record, ctx):
    if (record["closed"] and record["journal"].complete and not record["startup"]["unconfirmed_roles"]
            and not record["source_document"] and not record["target_document"]):
        del ctx.sessions[record["session_id"]]
        return True
    return False


def start(args, ctx):
    _clients = ctx.clients
    _sessions = ctx.sessions
    _call = ctx.call
    _emit = ctx.emit
    _runtime = lambda role: discover_runtime(role, _clients)
    for previous in _sessions.values():
        if (previous["startup"]["status"] == "failed"
                and (previous["blocked"] or previous["startup"]["unconfirmed_roles"]
                     or previous["source_document"] or previous["target_document"])):
            ctx.progress(session_id=previous["session_id"], phase="blocked_by_previous_startup")
            raise PublicError("startup_unconfirmed", "public_preflight",
                              "A previous startup still has an unknown write outcome; inspect its session and preserve GUI state before another startup",
                              side_effect="unknown")
        if not previous["journal"].complete:
            ctx.progress(session_id=previous["session_id"], phase="blocked_by_journal")
            ctx.guard_journal(previous)
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
              "artifacts": artifact_paths(source_path, output_dir), "close_results": {},
              "closed": False, "finished_recorded": False}
    record.update(observation_policy=policy, initial_source_views=None, source_basis_unknown=False)
    record["startup"] = {
        "status": "starting", "phase": "preflight", "unconfirmed_roles": [],
        "source_path": source_path, "resume_path": resume_path,
    }
    record["journal"] = JournalRecorder(session_id, record["artifacts"])
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
        _startup_phase(ctx, record, "target_start")
        ctx.guard_journal(record)
        target = _clients["target"].call("agent_start_session", **payload)
        record["startup"]["source_path"] = target["source_path"]
        record["target_document"] = target["document"]
        created.append("target")
        context = ctx.call_context.get()
        if context is not None:
            context["side_effect"] = "committed"
        _emit(record, "session.role_started", "agent_start_session", {"role": "target", "document": target["document"]})
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
        _emit(record, "session.role_started", "agent_start_session", {"role": "source", "document": source["document"]})
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
            if result.get("status") in ("closed", "already_closed"):
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


def run(name, args, record, started, ctx):
    _runtime = lambda role: discover_runtime(role, ctx.clients)
    _call = ctx.call
    _emit = ctx.emit
    _KNOWN_EVIDENCE_ID_LIMIT = ctx.known_evidence_limit
    if name == "agent_ping": return _runtime("both")
    if name == "agent_start_session": return start(args, ctx)
    if record is None: raise ValueError("session_id is required")
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
        known_evidence_ids = record["evidence_order"][-_KNOWN_EVIDENCE_ID_LIMIT:]
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
                    "facts": [{"evidence_id": item, **record["evidence_facts"][item]}
                              for item in known_evidence_ids],
                    "total": len(record["evidence_order"]),
                    "returned": len(known_evidence_ids),
                    "omitted": max(
                        0,
                        len(record["evidence_order"])
                        - len(known_evidence_ids),
                    ),
                    "order": "oldest_to_newest",
                    "scope": "current_session",
                },
                "recent_validation": record["recent_validation"]}
    if name == "agent_close_session":
        if record["startup"]["unconfirmed_roles"]:
            raise PublicError("startup_unconfirmed", "public_preflight",
                              "A role's creation is unconfirmed. Preserve GUI state; the user must handle uncertain documents before restarting MCP. Restarting MCP alone does not clean up FreeCAD. Closing known roles does not resolve the unknown role.",
                              side_effect="unknown")
        if not args.get("discard_unsaved", False):
            raise ValueError("Native GUI unsaved state is unavailable. Save any changes, then explicitly use discard_unsaved=true, or close in the GUI")
        results = record.setdefault("close_results", {})
        failure = None
        for role in ("target", "source"):
            if not record[role + "_document"]:
                continue
            try:
                value = _call(record, name, role)
            except Exception as exc:
                results[role] = {"status": "failed", "error": error_payload(exc)}
                failure = exc
                for remaining in ("target", "source"):
                    if remaining == role or not record[remaining + "_document"]:
                        continue
                    results.setdefault(remaining, {"status": "not_attempted"})
                break
            results[role] = value
            if value.get("status") not in ("closed", "already_closed"):
                failure = ValueError("close_session returned an unknown status")
                results[role] = {"status": "failed", "error": error_payload(failure)}
                break
            record[role + "_document"] = None
        if failure is not None:
            failure.close_results = _bounded_close_results(results)
            failure.close_side_effect = _close_side_effect(results)
            raise failure
        if record["target_document"] or record["source_document"]:
            failure = ValueError("close_session did not confirm every session document")
            failure.close_results = _bounded_close_results(results)
            failure.close_side_effect = _close_side_effect(results)
            raise failure
        record["closed"] = True
        if not record["finished_recorded"]:
            record["finished_recorded"] = True
            _emit(record, "task.finished", name, {"closed": _bounded_close_results(results)}, started)
        released = _release_closed(record, ctx)
        return {"closed": _bounded_close_results(results), "journal": record["journal"].metadata(),
                "session_retained": not released, "session_status": "closed"}
    raise ValueError("Unknown tool in workflow group")
