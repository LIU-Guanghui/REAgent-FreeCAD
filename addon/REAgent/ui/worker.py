"""One sequential worker; Qt signals wake the GUI, never dispatch work into it."""

from collections import deque
from queue import Full, Queue
from threading import Lock

from REAgent.application.fixed_session import FixedSession
from REAgent.application.session import Session, action_error
from REAgent.json_values import to_json_value
from REAgent.ui import QtCore
from REAgent.ui.input_images import prepare_inputs, render_input


_ACTIONS = frozenset(("start_task", "chat", "advance", "compare", "compact", "save", "close", "configure", "exec_fixed"))
def tool_fault(event):
    value = event.get("result") or {}
    if (value.get("caused_by_tool_call_id") or value.get("code") == "not_executed"
            or value.get("status") == "not_adopted"):
        return False
    if (value.get("image_delivery") or {}).get("status") == "unavailable":
        return True
    if event.get("function") == "agent_close_session" and value.get("ended"):
        if value.get("session_retained") or value.get("abandoned_roles"):
            return True
    return event.get("is_error") or event.get("side_effect") == "unknown"


class Worker(QtCore.QThread):
    ready = QtCore.Signal()

    def __init__(self, parent):
        super().__init__(parent)
        self._jobs = Queue(maxsize=1)
        self._lock = Lock()
        self._session = None
        self._busy = False
        self._stopping = False
        self._shutdown_complete = False
        self._pause_intent = False
        self._events = deque()
        self._event_chars = 0
        self._event_bytes = 0
        self._overflowed = False
        self._notified = False
        self._completion = None
        self._journal_event = None
        self._parked = False
        self._action_number = 0
        self._identity = {}
        self._action_terminal_error = None
        self._unbound_ping_result = None
        self._overflow_fault = self._overflow_terminal = self._overflow_reply = None

    def submit(self, action, arguments=None, *, presentation_task):
        if action not in _ACTIONS:
            raise ValueError("Unknown human action")
        with self._lock:
            if self._busy or self._completion is not None or self._stopping:
                raise RuntimeError("Collect the current worker result before another action")
            try:
                self._jobs.put_nowait((action, arguments if arguments is not None else {}))
            except Full:
                raise RuntimeError("The original worker already has a queued action; this action was not queued") from None
            self._action_number += 1
            self._identity = {"action_number": self._action_number, "presentation_task": presentation_task}
            self._busy = True
            self._pause_intent = False
            return self._action_number

    def pause(self):
        with self._lock:
            self._pause_intent = True
            session = self._session
        if session is not None:
            session.pause()

    def supplement(self, text):
        if not isinstance(text, str) or not text.strip():
            raise ValueError("Supplement must be non-empty text")
        with self._lock:
            session = self._session
        if session is None:
            raise RuntimeError("The task has not started")
        session.supplement(text)

    def snapshot(self):
        with self._lock:
            session = self._session
            manual_pause = self._pause_intent
        state = session.snapshot() if session is not None else {"status": "idle", "controller": None}
        pause = state.get("pause", {})
        if manual_pause:
            pause = {**pause, "paused": True, "manual": True}
        return {**state, **self._identity, "pause": pause}

    def shutdown(self):
        with self._lock:
            if self._busy or self._completion is not None or self._stopping:
                raise RuntimeError("The original worker is not ready to finish")
            if self._session is not None and self._session.snapshot().get("controller") is not None:
                raise RuntimeError("Close the retained task and complete its journal first")
            try:
                self._jobs.put_nowait(("shutdown", {}))
            except Full:
                raise RuntimeError("The original worker still has a queued action; shutdown was not queued") from None
            self._stopping = True

    def park(self):
        """Stop run before native Close, keeping this object's Session and queues."""
        with self._lock:
            if self._busy or self._completion is not None or self._stopping:
                raise RuntimeError("Collect the original worker result before parking")
            self._jobs.put_nowait(("park", {}))
            self._stopping = True

    def parked(self):
        with self._lock:
            return self._parked

    def reactivate(self):
        if self.isRunning():
            return
        with self._lock:
            if not self._parked or self._shutdown_complete:
                raise RuntimeError("Only the original parked worker can be reactivated")
            self._stopping = self._parked = False
        self.start()

    def shutdown_completed(self):
        with self._lock:
            return self._shutdown_complete

    def drain(self):
        """GUI-safe detached events plus the latest completed action, never live batches."""
        with self._lock:
            events = list(self._events)
            if self._journal_event is not None:
                events.append(self._journal_event)
                self._journal_event = None
            completion = self._completion
            self._events.clear()
            self._event_chars = 0
            self._event_bytes = 0
            self._completion = None
            self._notified = False
        return events, completion

    def run(self):
        # No QObject is created here. The QThread object itself belongs to GUI;
        # the blocking Python queue, Session and synchronous I/O run only here.
        while True:
            action, args = self._jobs.get()
            if action == "park":
                with self._lock:
                    self._parked = True
                return
            if action == "shutdown":
                with self._lock:
                    self._session = None
                    self._shutdown_complete = True
                return
            error = None
            value = None
            skipped = False
            self._overflowed = False
            self._overflow_fault = self._overflow_terminal = self._overflow_reply = None
            self._action_terminal_error = None
            self._unbound_ping_result = None
            previous_batch = self._session.last_batch if self._session is not None else None
            prev_comparison = self._session.comparison if self._session is not None else None
            try:
                with self._lock:
                    skipped = self._pause_intent and action in ("start_task", "chat", "advance", "compare", "compact")
                if not skipped:
                    value = self._perform(action, args)
            except BaseException as exc:
                # Credential/provider errors must not expose secret input or raw I/O diagnostics.
                error = action_error(exc)
                if self._session is not None:
                    self._session.pause()
            finally:
                credentials = args.pop("credentials", None)
                if credentials is not None:
                    credentials.clear()
                snapshot = self.snapshot()
                if self._unbound_ping_result is not None:
                    rows = (self._unbound_ping_result,)
                    identities = ({"function": "agent_ping"},)
                    new_results = True
                elif self._session is not None:
                    rows = self._session.last_batch
                    identities = self._session.last_result_identities
                    new_results = bool(rows) and rows is not previous_batch
                elif hasattr(value, "value") and hasattr(value, "is_error"):
                    rows = (value,)
                    identities = ({"function": args.get("command", action)},)
                    new_results = True
                else:
                    rows = ()
                    identities = ()
                    new_results = False
                terminal_error = error or self._action_terminal_error
                if terminal_error is None and action == "start_task" and snapshot.get("reason") == "session_start_failed":
                    if new_results and rows and getattr(rows[-1], "is_error", False):
                        val = to_json_value(rows[-1].value)
                        terminal_error = val if isinstance(val, dict) else {"code": "session_start_failed", "message": str(val)}
                    else:
                        terminal_error = {"code": "session_start_failed", "message": "CAD session failed to start"}
                if terminal_error is None and hasattr(value, "is_error") and getattr(value, "is_error", False):
                    val = to_json_value(value.value)
                    terminal_error = val if isinstance(val, dict) else {"code": "tool_failed", "message": str(val)}
                comp_changed = (
                    action == "compare"
                    and self._session is not None
                    and self._session.comparison is not prev_comparison
                )
                packet = {"action": action, "command": args.get("command") if action == "exec_fixed" else None,
                          **self._identity, "snapshot": snapshot, "error": error, "terminal_error": terminal_error,
                          "comparison": self._session.comparison if comp_changed else None,
                          "comparison_results": tuple({**identity, **self._identity,
                              "value": to_json_value(row.value), "images": tuple(row.images),
                              "is_error": row.is_error, "side_effect": row.side_effect}
                              for identity, row in (self._session.comparison_rows if comp_changed else ())),
                          "presentation_fallback": tuple(event for event in (
                              self._overflow_reply, self._overflow_fault, self._overflow_terminal) if event is not None),
                          "ok": terminal_error is None and not getattr(value, "is_error", False),
                          "results": tuple({**identity, **self._identity, "value": to_json_value(row.value),
                                            "images": tuple(row.images), "is_error": row.is_error,
                                            "side_effect": row.side_effect}
                                           for identity, row in zip(identities, rows)),
                          "new_results": new_results,
                          "presentation_backlog": self._overflowed,
                          "paused_before_start": skipped,
                          "credentials_consumed": action in ("start_task", "chat", "configure")}
                if isinstance(self._session, FixedSession) and not self._session.active:
                    with self._lock:
                        self._session = None
                with self._lock:
                    self._completion = packet
                    self._busy = False
                    wake = not self._notified
                    self._notified = True
                if wake:
                    self.ready.emit()

    def _event(self, event):
        event = {**event, **self._identity}
        kind = event["event"]
        if kind == "error":
            with self._lock:
                self._action_terminal_error = to_json_value({
                    k: event[k] for k in ("code", "message", "diagnostics", "request_number") if k in event
                })
        if kind == "journal_update":
            with self._lock:
                self._journal_event = event
                wake = not self._notified
                self._notified = True
            if wake:
                self.ready.emit()
            return True
        # Session retains all final ToolResults. A full presentation buffer pauses
        # new decisions; the final action packet still carries the real batch.
        text = event.get("text", "")
        cost = len(text) if kind in ("text_preview", "model_reply") else len(str(event.get("result", event)))
        image_bytes = sum(len(item.data) for item in event.get("images", ()))
        wake = False
        with self._lock:
            if (len(self._events) >= 64 or self._event_chars + cost > 131072
                    or self._event_bytes + image_bytes > 67108864):
                self._overflowed = True
                if kind == "tool_result" and self._overflow_fault is None and tool_fault(event):
                    value = event["result"]
                    summary = {key: value[key] for key in (
                        "operation_id", "status", "code", "message", "retry_action", "restored",
                        "ended", "session_retained", "abandoned_roles", "image_delivery") if key in value}
                    detail = value.get("detail") or {}
                    summary["detail"] = {key: detail[key] for key in (
                        "reason", "model_restored", "redo_changed", "committed") if key in detail}
                    self._overflow_fault = {**event, "result": summary, "images": ()}
                elif kind == "error":
                    self._overflow_terminal = event
                elif kind == "model_reply":
                    self._overflow_reply = {**event, "text": text[-32768:]}
                return False
            if (kind == "text_preview" and self._events
                    and self._events[-1]["event"] == kind
                    and self._events[-1].get("generation") == event.get("generation")
                    and self._events[-1].get("request_number") == event.get("request_number")):
                self._events[-1] = {**event, "text": self._events[-1]["text"] + text}
            else:
                self._events.append(event)
            self._event_chars += cost
            self._event_bytes += image_bytes
            if not self._notified:
                self._notified = wake = True
            paused = self._pause_intent
        if wake:
            self.ready.emit()
        # start/advance reset their own resume flag. The worker's independent
        # intent survives that boundary and reasserts pause before new I/O/CAD.
        return not paused

    def _perform(self, action, args):
        if action == "exec_fixed":
            command = args["command"]
            workflow = args.get("workflow") or "stp2cad"
            if command == "agent_ping" and (self._session is None or not isinstance(self._session, FixedSession)):
                if self._session is not None:
                    if self._session.mode is not None or self._session.snapshot().get("controller") is not None:
                        raise RuntimeError("Finish the active model session before running unbound agent_ping")
                res = FixedSession.probe_ping(workflow)
                self._unbound_ping_result = res
                return res
            if self._session is not None and not isinstance(self._session, FixedSession):
                if (
                    command == "agent_open_session"
                    and self._session.mode is None
                    and self._session.snapshot().get("controller") is None
                ):
                    with self._lock:
                        self._session = None
                else:
                    raise RuntimeError("Finish the active model session before running fixed commands")
            if self._session is None:
                if command != "agent_open_session":
                    raise RuntimeError("Open a fixed session first with agent_open_session")
                session = FixedSession(workflow, on_event=self._event,
                                       prepare_inputs=prepare_inputs, view_input=render_input)
                with self._lock:
                    self._session = session
            return self._session.execute(
                command,
                args.get("args"),
                workflow=args.get("workflow"),
                expected_target=args.get("expected_target"),
            )
        if action in ("start_task", "chat"):
            if self._session is not None and (
                    isinstance(self._session, FixedSession) or self._session.snapshot().get("controller") is not None):
                raise RuntimeError("Finish the retained task first")
            if self._session is None:
                session = Session(args["config"], on_event=self._event, **args["credentials"])
                with self._lock:
                    self._session = session
            if action == "chat":
                return self._session.chat(args["text"])
            return self._session.start_task(
                args["kind"], args["task"], args["objective"],
                prepare_inputs=prepare_inputs, view_input=render_input,
                expected_target=args.get("expected_target"),
            )
        if self._session is None:
            raise RuntimeError("Start a task first")
        if action == "close":
            return self._session.close()
        if not isinstance(self._session, Session):
            raise RuntimeError("This action requires a model session")
        if action == "advance":
            # The explicit user request is part of the protected requirement stream.
            self._session.supplement(args["intent"])
            return self._session.advance()
        if action == "compact":
            return self._session.compact()
        if action == "save":
            return self._session.save()
        if action == "compare":
            return self._session.compare(args["options"], args["view_request"])
        if action == "configure":
            return self._session.configure(args["config"], **args["credentials"])
        raise ValueError("Unknown human action")
