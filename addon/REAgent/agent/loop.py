"""Fixed complete-batch progression through a frozen domain tool boundary."""

from REAgent.model.errors import ModelError


def _unexecuted_tail(session, tools, rows, start, reason, cause=None):
    for index in range(start, len(rows)):
        session.record_result(index, tools.unexecuted("not_executed", reason, cause=cause))


def _notify(session, tools, call, arguments, result, *, remaining=0, reason=None):
    notification = tools.notification(call, arguments, result)
    if notification is not None:
        payload, images = notification
        if remaining:
            payload = {**payload, "unexecuted": {"count": remaining, "reason": reason}}
        session.batches.describe(call.id, payload)
        session.publish("tool_result", {**session.batches.pending.identity, **payload}, images=images)


def _execute_batch(session, tools, reply, published, generation):
    active = session.active
    parsed, invalid, rejection = tools.preflight(reply, published, active.policy)
    rejection_index = next((i for i, error in enumerate(invalid) if error is not None), 0)
    rejection_id = reply.calls[rejection_index].id if rejection else None
    rows = [(arguments, tools.unexecuted(
        "invalid_tool_request" if invalid[index] is not None else "batch_rejected" if rejection else "not_executed",
        invalid[index] or rejection or "Call has not executed",
        cause=rejection_id if index != rejection_index else None))
        for index, arguments in enumerate(parsed)]
    session.begin_batch(reply, rows, rejection=rejection)
    if rejection:
        _notify(session, tools, reply.calls[rejection_index], *rows[rejection_index],
                remaining=len(rows) - 1, reason="The batch was rejected before dispatch.")
        return
    identity = session.batches.pending.identity
    if reply.content and not tools.suppress_reply(reply):
        session.public_reply(reply, generation, progress=True)
    for index, (call, arguments) in enumerate(zip(reply.calls, parsed)):
        remaining = len(rows) - index - 1
        if session.cancelled(generation):
            _unexecuted_tail(session, tools, rows, index, "Requirements changed or pause was requested")
            break
        try:
            session.check()
            session.publish(status="preflighting_tool")
        except ModelError as exc:
            result = tools.unexecuted(exc.code, str(exc)[:300])
            session.record_result(index, result)
            _unexecuted_tail(session, tools, rows, index + 1, "The current task no longer permits modeling", call.id)
            _notify(session, tools, call, arguments, result, remaining=remaining,
                    reason="The current task no longer permits modeling.")
            raise
        description = tools.describe(call, arguments)
        session.publish("tool_started", {**identity, "tool_call_id": call.id, **description})
        try:
            session.dispatch_claim(generation)
        except ModelError:
            _unexecuted_tail(session, tools, rows, index, "Pause or new requirements arrived before dispatch")
            break
        try:
            result = tools.execute(call, arguments, prepare_retention=session.prepare_retention,
                prepare_handoff=session.prepare_handoff, action=session.action,
                policy=session.active.policy, purpose=session.purpose,
                generation=generation, on_progress=lambda value:
                    session.publish("tool_progress", {"tool_call_id": call.id, **description, **value, **identity}))
        except BaseException:
            result = tools.interrupted(call)
            session.record_result(index, result)
            reason = "The reported operation was interrupted."
            _unexecuted_tail(session, tools, rows, index + 1, reason, call.id)
            _notify(session, tools, call, arguments, result, remaining=remaining, reason=reason)
            raise
        session.record_result(index, result)
        if tools.report_ready(call, result):
            session.finish_action()
        is_error, effect = tools.result_state(result)
        if is_error or effect == "unknown":
            reason = ("The reported operation has an unknown outcome." if effect == "unknown"
                      else "The reported operation failed.")
            _unexecuted_tail(session, tools, rows, index + 1, reason, call.id)
            _notify(session, tools, call, arguments, result, remaining=remaining, reason=reason)
            break
        try:
            _notify(session, tools, call, arguments, result)
        except BaseException:
            _unexecuted_tail(session, tools, rows, index + 1, "An earlier result could not be processed", call.id)
            raise

def run(session, tools, *, resume):
    try:
        session.turn_control(resume=resume)
        if resume:
            session.requests.recover_model_outcome()
        session.commit_batch(recover=resume)
        if resume and session.report_only:
            session.replay_report()
            return session.publish(status="paused", reason="report_run_ended")
        while True:
            generation, paused = session.turn_control()
            if paused:
                return session.publish(status="paused", reason="pause_requested")
            if session.report_only and session.report.requested:
                session.replay_report()
                return session.publish(status="model_stopped", reason="report_run_ended")
            session.request_identity = {}
            record, published = session.prepare_request(generation)
            reply = session.request_model(record, generation)
            if not reply.calls:
                session.commit_batch(reply)
                if session.report_only:
                    session.replay_report()
                    return session.publish(status="paused" if session.cancelled(generation) else "model_stopped",
                                           reason="report_run_ended")
                if session.cancelled(generation):
                    return session.publish(status="paused", reason="requirements_changed_or_paused")
                session.public_reply(reply, generation, progress=False)
                if session.cancelled(generation):
                    return session.publish(status="paused", reason="requirements_changed_or_paused")
                return session.publish(status="model_stopped", reason="model_stopped_without_tools")
            _execute_batch(session, tools, reply, published, generation)
            session.commit_batch()
            if session.cancelled(generation):
                return session.publish(status="paused", reason="requirements_changed_or_paused")
            session.check()
    except ModelError as exc:
        if exc.code != "request_cancelled":
            session.publish("error", {**session.request_identity, **exc.detail()})
        return session.publish(status="paused", reason=exc.code)
    except Exception as exc:
        session.publish("error", {"code": "agent_internal_error", "message": type(exc).__name__})
        return session.publish(status="paused", reason="agent_internal_error")
    except BaseException:
        session.publish(status="paused", reason="worker_interrupted")
        raise
