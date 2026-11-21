"""Detached application snapshots and read-only CAD presentation services."""

from REAgent.application.profiles import idle_context
from REAgent.cad import images, log_summary
from REAgent.cad.tools import manual_comparison_contract, prepare_comparison
from REAgent.cad.tasks.registry import WORKFLOWS
from .control import project_images_metadata


def preview_entries(prepared, value, artifacts):
    return images.preview_entries(prepared, value, artifacts)


def read_preview_image(entry):
    return images.read_preview_image(entry)


def tool_message(event, *, started=False):
    return log_summary.tool_message(event, started=started)


def comparison_contract():
    return manual_comparison_contract()


def _context_info(session, facts, active, request_state, *, prepared=None):
    run = session.run
    info = {**run.model_budget(), "status": "not_started", "can_compact": False,
            "last_service_report": request_state["last_usage"], "last_compaction": run.last_compaction}
    if active is not None and active.conversation is not None:
        conversation = active.conversation
        try:
            estimate = dict(prepared) if prepared is not None else conversation.estimate(
                run.context_model, run.directory(), run.facts())
            used = estimate["input_tokens_estimate"]
            bound = session.mode == "chat" or session.cad_tools.bound(facts)
            info.update(status="available", estimate=estimate,
                basis="prepared_request" if prepared is not None else "completed_units",
                unit_count=len(conversation.units), summary_present=conversation.summary is not None,
                can_compact=bound and not request_state["pending_model_result"] and bool(conversation.compactable_units(run.context_model)),
                checkpoint=conversation.checkpoint, retained_image_groups=sorted(conversation.retained_groups),
                pending_results=run.batches.pending is not None or run.batches.human is not None or request_state["pending_model_result"],
                occupancy_percent_estimate=100 * used / info["capacity_tokens"],
                remaining_tokens_estimate=info["input_limit_tokens"] - used)
        except Exception as exc:
            info.update(status="unavailable", error=type(exc).__name__)
    return info


def snapshot_state(session, status, reason):
    run = session.run
    facts = session.cad_tools.controller.task_state() if session.cad_tools is not None else None
    active = run.active
    request_state = run.requests.snapshot()
    if status in ("ready", "paused", "closed", "requesting_model"):
        run.context_info = _context_info(session, facts, active, request_state,
                                        prepared=run.estimate if status == "requesting_model" else None)
    policy_state = session.cad_tools.snapshot_state(active.policy) if active is not None and active.policy is not None else {
        "request_completed_observations": {}}
    return {"status": status, "reason": reason, "controller": facts,
            "action": run.action.snapshot() if run.action else None,
            "report": run.report.snapshot() if run.report is not None else None,
            "requirements": [{"generation": item.generation,
                "status": "adopted" if (active.binding, item.generation) in run.requirement_receipts else "pending"}
                for item in active.conversation.requirements] if active and active.conversation else [],
            "mode": session.mode, "model_configured": True, **policy_state,
            "last_request_estimate": run.estimate, "usage": request_state["usage"],
            "context": run.context_info, "proxy_discovery": run.proxy,
            "callback_error": run.callback_error,
            "last_model_request": request_state["last_model_request"],
            "pending_model_result": request_state["pending_model_result"],
            "pending_batch": None if run.batches.pending is None else {
                "call_count": run.batches.pending.call_count, "recording": "pending"},
            "last_batch": [{"is_error": result.is_error, "side_effect": result.side_effect,
                            "operation_id": result.value.get("operation_id")} for result in run.batches.latest_results]}


def journal_update_payload(recorder):
    """Bound recent journal events for live GUI and cached status viewers."""
    rows, characters = [], 0
    for row in reversed(recorder.events[-200:]):
        cost = len(str(row))
        if characters + cost > 196608:
            break
        rows.append(row)
        characters += cost
    return {"session_id": recorder.task, "events": list(reversed(rows)), "journal": recorder.metadata()}


def assemble_details(snapshot, *, task_open, results, progress, snapshot_received_at, fallback_config=None):
    """Assemble detached status details shared by the GUI viewer and CLI status."""
    context = snapshot.get("context")
    if context is None or not (snapshot.get("controller") or snapshot.get("mode") == "chat"):
        context = idle_context(fallback_config if snapshot.get("mode") != "fixed" else None)
    return {
        "snapshot": snapshot,
        "task_open": task_open,
        "last_completed_batch": [
            {
                **{
                    key: row[key]
                    for key in (
                        "function", "action_number", "request_number", "response_id",
                        "generation", "tool_call_id", "is_error", "side_effect",
                    )
                    if key in row
                },
                "result": row["value"],
            }
            for row in results
        ],
        "last_progress": progress,
        "snapshot_received_at": snapshot_received_at,
        "context": context,
    }


def compaction_text(change):
    before, after = change["before"], change["after"]
    label = "Context image payload reduced" if change["reason"] == "image_pressure" else "Context compacted"
    text = (
        label
        + ": Local estimate {:,} → {:,} tokens; request bytes {:,} → {:,}.".format(
            before["local_tokens_estimate"],
            after["local_tokens_estimate"],
            before["request_bytes"],
            after["request_bytes"],
        )
    )
    if change["estimate_method_changed"]:
        text += " Capacity estimate method changed; the two capacity estimates are not directly comparable."
    return text


def status_text(details):
    """Format human-readable status summary shared by GUI Status viewer and CLI status."""
    snapshot, context = details["snapshot"], details["context"]
    facts = snapshot.get("controller") or {}
    artifacts = facts.get("artifacts") or {}
    workflow = WORKFLOWS.get(facts.get("workflow"))
    mode = snapshot.get("mode")
    state = str(snapshot.get("status", "idle")).replace("_", " ")
    type_label = (
        workflow.title
        if workflow
        else "Fixed CAD session"
        if mode == "fixed"
        else "Conversation"
        if mode == "chat"
        else "Ready for the first message"
        if details["task_open"]
        else "No active task"
    )
    model_label = (
        "Fixed command mode (no model)"
        if mode == "fixed"
        else str(context.get("model") or "Not configured")
    )
    rows = [
        "Task",
        "Last reported state: " + state[:1].upper() + state[1:],
        "Snapshot received: " + str(details["snapshot_received_at"] or "Not received"),
        "Type: " + type_label,
        "",
        "Model: " + model_label,
        "",
        "Context",
    ]
    capacity = context.get("capacity_tokens")
    if capacity is not None:
        rows.extend((
            "Capacity: {:,} tokens".format(capacity),
            "Output budget: {:,} tokens".format(context["output_tokens"]),
            "Working reserve: {:,} tokens".format(context["reserve_tokens"]),
        ))
    if context.get("status") == "available":
        estimate = context["estimate"]
        rows.append(
            "Estimated input: ~{:,} tokens ({:.1f}%)".format(
                estimate["input_tokens_estimate"],
                context["occupancy_percent_estimate"],
            )
        )
        rows.append(
            "Estimate basis: "
            + (
                "Service usage plus new suffix"
                if estimate["method"].startswith("service_")
                else "Conservative local bound"
            )
        )
        rows.append(
            "Estimated room after output/reserve: ~{:,} tokens".format(
                context["remaining_tokens_estimate"]
            )
        )
        if not estimate["image_soft_target_met"]:
            rows.append("Protected image groups exceed the soft target; hard limits still apply.")
        if context.get("pending_results") or snapshot.get("pause", {}).get("queued"):
            rows.append("In-flight results or queued corrections are not fully included.")
    else:
        rows.append(
            "Input estimate: "
            + (
                "Not used in fixed command mode"
                if mode == "fixed"
                else "Conversation not started"
                if context.get("status") == "not_started"
                else "Unavailable"
            )
        )
    report = context.get("last_service_report")
    if report is not None:
        count = (report.get("usage") or {}).get("input_tokens")
        rows.extend((
            "",
            "Last service report (historical): " + ("Summary" if report["kind"] == "summary" else "Main request"),
            "Input: " + ("Unknown" if count is None else "{:,} tokens".format(count)),
        ))
    change = context.get("last_compaction")
    if change is not None:
        rows.extend(("", compaction_text(change)))
        usage = change.get("summary_usage")
        if usage:
            rows.append(
                "Summary cost (reported): Input %s · Output %s tokens"
                % (usage.get("input_tokens"), usage.get("output_tokens"))
            )
    for total in snapshot.get("usage", {}).values():
        counts = total["counts"]
        rows.extend((
            "",
            "Cumulative request usage · " + ("Summary" if total["kind"] == "summary" else "Main"),
            "Known input: {:,} · Output: {:,} tokens; input unknown for {} requests".format(
                counts["input_tokens"]["known_sum"],
                counts["output_tokens"]["known_sum"],
                counts["input_tokens"]["unknown_requests"],
            ),
        ))
    request = snapshot.get("last_model_request") or {}
    if request.get("error"):
        error = request["error"]
        rows.extend(("", "Request failed: " + str(error.get("code")) + " · " + str(error.get("message"))))
    if snapshot.get("pending_model_result"):
        rows.append("Result retained; journal recovery is required before a new decision.")
    if snapshot.get("callback_error"):
        rows.append("Presentation callback failed: " + str(snapshot["callback_error"]))
    if facts:
        rows.extend(("", "CAD documents", "Target: " + str(facts.get("target_document") or "Not created")))
        if workflow and workflow.native_source:
            rows.append("Source: " + str(facts.get("source_document") or "Not created"))
        if facts.get("inputs"):
            rows.extend(("", "Reference files"))
            rows.extend(item["filename"] for item in facts["inputs"])
        rows.extend((
            "",
            "Output",
            "Folder: " + str(artifacts.get("output_dir") or "Not selected"),
            "Model file (planned): " + str(artifacts.get("model_path") or "Not allocated"),
        ))
        action = snapshot.get("action") or {}
        rows.append("Current action: " + str(action.get("scope") or "None"))
        if action.get("scope") == "target_edit" and facts.get("reconstruction", {}).get("phase") == "reconstructing":
            rows.append("Reconstruction is paused; Target conversation does not resume it.")
        if mode != "fixed":
            requirements = snapshot.get("requirements") or []
            rows.append("Messages awaiting model processing: " + str(sum(item["status"] == "pending" for item in requirements)))
        receipt = facts.get("delivery_receipt")
        if receipt:
            rows.extend(("", "Last reconstruction delivery"))
            if workflow and workflow.compares_stp:
                for name, check in receipt["checks"].items():
                    rows.append(name + ": " + str(check.get("status") or "unknown"))
                limits = receipt["limitations"]
                rows.append("Limitations: " + (str(len(limits)) if limits else "None recorded"))
                rows.extend("  " + item["check"] + ": " + str(item.get("reason") or item["status"]) for item in limits)
            else:
                solid_count = receipt["model_state"]["result_geometry"]["solid_count"]
                rows.extend((f"Native result · {solid_count} valid solid", "Evidence cited in the model's checks"))
                for check in receipt["checks"]:
                    rows.extend((
                        "",
                        check["requirement"] + " · " + check["verdict"],
                        check["observed"],
                        check["source"] + " · " + check["observation_id"],
                    ))
                    measurement = check.get("measurement")
                    if measurement is not None:
                        unit = measurement["unit"]
                        field = " / ".join(str(key) for key in measurement["readback_path"])
                        if measurement.get("target_object"):
                            field = measurement["target_object"] + " · " + field
                        rows.append("Target field · " + field)
                        rows.append(f'Nominal {measurement["nominal"]} {unit} · Measured {measurement["measured"]} {unit}')
                        tolerance = measurement.get("tolerance")
                        if tolerance is not None:
                            rows.append(f'Drawing tolerance {tolerance["lower"]:+} to {tolerance["upper"]:+} {unit}')
                if receipt["notes"]:
                    rows.append(receipt["notes"])
        manual = facts.get("recent_manual_comparison") or {}
        if manual.get("status") == "recorded":
            rows.append("Last manual comparison: " + str(manual.get("applicability", "unknown")))
        saved = facts.get("last_saved_checkpoint")
        if saved:
            rows.append("Last confirmed save: " + str(saved.get("saved_to") or "Path unavailable"))
            rows.append(
                {
                    "changed": "Target changed since the last confirmed save.",
                    "unchanged": "Last observed Target matches the last confirmed save.",
                }.get(saved.get("applicability"), "Current Target and last confirmed save could not be compared.")
            )
        if facts.get("target_writes_blocked"):
            sources = [key[:1].upper() + key[1:] for key, value in facts.get("write_block_sources", {}).items() if value is True]
            rows.append("Modeling blocked: " + (", ".join(sources) or "Inspect task and recovery details"))
        journal = facts.get("journal") or {}
        if journal and not journal.get("complete"):
            rows.append("Journal incomplete; retained facts have not all been persisted.")
    return "\n".join(rows)
