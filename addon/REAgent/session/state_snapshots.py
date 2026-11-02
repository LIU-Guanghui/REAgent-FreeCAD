"""Detached application snapshots and read-only rebuild presentation services."""

from REAgent.rebuild import images, log_summary
from REAgent.model.config import input_limit_tokens
from REAgent.model.limits import CONTEXT_RESERVE_TOKENS


def preview_entries(prepared, value, artifacts):
    return images.preview_entries(prepared, value, artifacts)


def read_preview_image(entry):
    return images.read_preview_image(entry)


def tool_message(event, *, started=False):
    return log_summary.tool_message(event, started=started)


def _context_info(session, facts, active, request_state, *, prepared=None):
    run = session.run
    config = run.model.config
    info = {"model": config.model, "capacity_tokens": config.context_tokens,
            "output_tokens": config.output_tokens, "reserve_tokens": CONTEXT_RESERVE_TOKENS,
            "reasoning_effort": config.reasoning_effort,
            "input_limit_tokens": input_limit_tokens(config),
            "status": "not_started", "can_compact": False,
            "last_service_report": request_state["last_usage"], "last_compaction": run.last_compaction}
    if active is not None and active.conversation is not None:
        conversation, tools = active.conversation, run.tools
        try:
            estimate = dict(prepared) if prepared is not None else conversation.estimate(
                run.context_model, tools.directory(), tools.facts(active.policy))
            used = estimate["input_tokens_estimate"]
            bound = session.mode == "chat" or session.rebuild.bound(facts)
            info.update(status="available", estimate=estimate,
                basis="prepared_request" if prepared is not None else "completed_units",
                unit_count=len(conversation.units), summary_present=conversation.summary is not None,
                can_compact=bound and not request_state["pending_model_result"] and bool(conversation.compactable_units(run.context_model)),
                checkpoint=conversation.checkpoint, retained_image_groups=sorted(conversation.retained_groups),
                pending_results=run.batches.pending is not None or run.batches.human is not None or request_state["pending_model_result"],
                occupancy_percent_estimate=100 * used / config.context_tokens,
                remaining_tokens_estimate=info["input_limit_tokens"] - used)
        except Exception as exc:
            info.update(status="unavailable", error=type(exc).__name__)
    return info


def snapshot_state(session, status, reason):
    run = session.run
    facts = session.rebuild.controller.task_state() if session.rebuild is not None else None
    active = run.active
    request_state = run.requests.snapshot()
    if status in ("ready", "paused", "closed", "requesting_model"):
        run.context_info = _context_info(session, facts, active, request_state,
                                        prepared=run.estimate if status == "requesting_model" else None)
    policy_state = session.rebuild.snapshot_state(active.policy) if active is not None and active.policy is not None else {
        "current_command": None, "request_completed_observations": {}}
    return {"status": status, "reason": reason, "controller": facts,
            "mode": session.mode, "model_configured": True, **policy_state,
            "last_request_estimate": run.estimate, "usage": request_state["usage"],
            "context": run.context_info, "proxy_discovery": run.proxy,
            "callback_error": run.callback_error,
            "last_model_request": request_state["last_model_request"],
            "pending_model_result": request_state["pending_model_result"],
            "pending_batch": None if run.batches.pending is None else {
                "call_count": len(run.batches.pending.reply.calls), "recording": "pending"},
            "last_batch": [{"is_error": result.is_error, "side_effect": result.side_effect,
                            "operation_id": result.value.get("operation_id")} for result in run.batches.latest_results]}
