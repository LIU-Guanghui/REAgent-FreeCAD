"""One sequential worker; Qt signals wake the GUI, never dispatch work into it."""

from collections import deque
from queue import Full, Queue
from threading import Lock

from REAgent.session.coordinator import Session
from REAgent.model.errors import ModelError
from REAgent.values import to_json_value
from REAgent.ui import QtCore


_ACTIONS = frozenset(("start", "chat", "advance", "compact", "save", "close", "configure"))
_PREVIEWS = frozenset(("text_preview", "summary_preview"))


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

    def submit(self, action, arguments=None):
        if action not in _ACTIONS:
            raise ValueError("Unknown human action")
        with self._lock:
            if self._busy or self._completion is not None or self._stopping:
                raise RuntimeError("Collect the current worker result before another action")
            try:
                self._jobs.put_nowait((action, arguments if arguments is not None else {}))
            except Full:
                raise RuntimeError("The original worker already has a queued action; this action was not queued") from None
            self._busy = True
            self._pause_intent = False

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
            self._pause_intent = True
        if session is None:
            raise RuntimeError("The task has not started")
        session.supplement(text)

    def snapshot(self):
        with self._lock:
            session = self._session
        return session.snapshot() if session is not None else {"status": "idle", "controller": None}

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

    def _event(self, event):
        if event["event"] == "journal_update":
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
        cost = len(text) if event["event"] in _PREVIEWS else len(str(event.get("result", event)))
        image_bytes = sum(len(item.data) for item in event.get("images", ()))
        wake = False
        with self._lock:
            if (len(self._events) >= 64 or self._event_chars + cost > 131072
                    or self._event_bytes + image_bytes > 67108864):
                self._overflowed = True
                return False
            if (event["event"] in _PREVIEWS and self._events
                    and self._events[-1]["event"] == event["event"]
                    and self._events[-1].get("generation") == event.get("generation")
                    and self._events[-1].get("request") == event.get("request")):
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
        if action in ("start", "chat"):
            if self._session is not None and self._session.snapshot().get("controller") is not None:
                raise RuntimeError("Finish the retained task first")
            if self._session is None:
                try:
                    session = Session(args["config"], on_event=self._event, **args["credentials"])
                except ValueError as exc:
                    raise ModelError("invalid_credentials", str(exc)) from None
                with self._lock:
                    self._session = session
            if action == "chat":
                return self._session.chat(args["text"])
            return self._session.start(args["task"], args["objective"])
        if self._session is None:
            raise RuntimeError("Start a task first")
        if action == "advance":
            # The explicit user request is part of the protected requirement stream.
            self._session.supplement(args["intent"])
            return self._session.advance()
        if action == "compact":
            return self._session.compact()
        if action == "save":
            return self._session.save()
        if action == "close":
            return self._session.close()
        if action == "configure":
            try:
                return self._session.configure(args["config"], **args["credentials"])
            except ValueError as exc:
                raise ModelError("invalid_credentials", str(exc)) from None
        raise ValueError("Unknown human action")

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
            previous_batch = self._session.last_batch if self._session is not None else None
            try:
                with self._lock:
                    skipped = self._pause_intent and action in ("start", "chat", "advance", "compact")
                if not skipped:
                    value = self._perform(action, args)
            except BaseException as exc:
                # Credential/provider errors must not expose secret input or raw I/O diagnostics.
                error = exc.detail() if isinstance(exc, ModelError) else {
                    "code": "worker_action_failed", "message": type(exc).__name__}
                if self._session is not None:
                    self._session.pause()
            finally:
                credentials = args.pop("credentials", None)
                if credentials is not None:
                    credentials.clear()
                snapshot = self.snapshot()
                rows = self._session.last_batch if self._session is not None else ()
                packet = {"action": action, "snapshot": snapshot, "error": error,
                          "ok": error is None and not getattr(value, "is_error", False),
                          "results": tuple({"value": to_json_value(row.value), "images": tuple(row.images),
                                            "is_error": row.is_error, "side_effect": row.side_effect} for row in rows),
                          "new_results": bool(rows) and rows is not previous_batch,
                          "presentation_backlog": self._overflowed,
                          "paused_before_start": skipped,
                          "credentials_consumed": action in ("start", "chat", "configure")}
                with self._lock:
                    self._completion = packet
                    self._busy = False
                    wake = not self._notified
                    self._notified = True
                if wake:
                    self.ready.emit()
