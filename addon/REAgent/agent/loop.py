"""Fixed complete-batch progression through a frozen domain tool boundary."""

from REAgent.model.errors import ModelError


def _unexecuted_tail(session, tools, rows, start, reason):
    for index in range(start, len(rows)):
        session.record_result(index, tools.unexecuted("not_executed", reason))


def _notify(session, tools, call, arguments, result):
    notification = tools.notification(call, arguments, result)
    if notification is not None:
        payload, images = notification
        session.publish("tool_result", payload, images=images)


def _execute_batch(session, tools, reply, published, generation):
    active = session.active
    parsed, invalid, rejection = tools.preflight(reply, published, active.policy)
    rows = [(arguments, tools.unexecuted("invalid_tool_request", invalid[index])
             if invalid[index] is not None else tools.unexecuted(
                 "batch_rejected" if rejection else "not_executed",
                 rejection or "Call has not executed")) for index, arguments in enumerate(parsed)]
    session.begin_batch(reply, rows)
    stopped = None
    for index, (call, arguments) in enumerate(zip(reply.calls, parsed)):
        result = rows[index][1]
        if not rejection:
            if stopped or session.cancelled(generation):
                result = tools.unexecuted("not_executed", stopped or "Requirements changed or pause was requested")
            else:
                try:
                    tools.check(active.policy, call, arguments)
                    session.publish(status="preflighting_tool")
                except (ModelError, ValueError) as exc:
                    result = tools.unexecuted("tool_precondition", str(exc)[:300])
                else:
                    if session.cancelled(generation):
                        result = tools.unexecuted("not_executed", "Requirements changed or pause was requested")
                    else:
                        try:
                            description = tools.describe(call, arguments)
                            session.publish("tool_started", description)
                            if session.cancelled(generation):
                                result = tools.unexecuted("not_executed", "Pause arrived before tool dispatch")
                            else:
                                result = tools.execute(call, arguments, prepare_retention=session.prepare_retention,
                                    generation=generation, on_progress=lambda value:
                                        session.publish("tool_progress", {"tool_call_id": call.id, **description, **value}))
                        except BaseException:
                            result = tools.interrupted(call)
                            session.record_result(index, result)
                            _unexecuted_tail(session, tools, rows, index + 1, "An earlier call was interrupted")
                            _notify(session, tools, call, arguments, result)
                            raise
        session.record_result(index, result)
        try:
            _notify(session, tools, call, arguments, result)
        except BaseException:
            _unexecuted_tail(session, tools, rows, index + 1, "An earlier result could not be processed")
            raise
        is_error, effect = tools.result_state(result)
        if is_error or effect == "unknown":
            stopped = "An earlier call failed or has an unknown outcome"


def run(session, tools, *, resume):
    try:
        session.turn_control(resume=resume)
        if resume:
            session.requests.recover_model_outcome()
        session.commit_batch(recover=resume)
        while True:
            generation, paused = session.turn_control()
            if paused:
                return session.publish(status="paused", reason="pause_requested")
            record, published = session.prepare_request(generation)
            reply = session.request_model(record, generation)
            if not reply.calls:
                session.commit_batch(reply)
                if session.cancelled(generation):
                    return session.publish(status="paused", reason="requirements_changed_or_paused")
                session.publish("model_proposal", {"text": reply.content, "response_id": reply.id})
                if session.cancelled(generation):
                    return session.publish(status="paused", reason="requirements_changed_or_paused")
                return session.publish(status="model_stopped", reason="model_stopped_without_tools")
            _execute_batch(session, tools, reply, published, generation)
            session.commit_batch()
            if session.cancelled(generation):
                return session.publish(status="paused", reason="requirements_changed_or_paused")
            tools.check(session.active.policy)
    except ModelError as exc:
        if exc.code != "request_cancelled":
            session.publish("error", exc.detail())
        return session.publish(status="paused", reason=exc.code)
    except Exception as exc:
        session.publish("error", {"code": "agent_internal_error", "message": type(exc).__name__})
        return session.publish(status="paused", reason="agent_internal_error")
    except BaseException:
        session.publish(status="paused", reason="worker_interrupted")
        raise
