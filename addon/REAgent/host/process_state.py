"""Process-wide Bridge role state, listener limits and native document helpers."""

import uuid

import FreeCAD as App


class ProcessState:
    def __init__(self):
        self.INSTANCE_ROLE = None
        self.INSTANCE_ID = uuid.uuid4().hex
        self.ACTIVE_TASK = None
        self.ACTIVE_OPERATION = None
        self.AUDIT_BLOCKS = {}
        # Session/document binding is process-local authorization evidence. It
        # is deliberately not persisted in FCStd or used as a model revision.
        # Its separate source_revision field pins the observed STEP basis,
        # not document ownership or geometric equivalence.
        self.SESSION_BINDINGS = {}
        # One process-local receipt confirms an idempotent end after a lost reply.
        self.LAST_ENDED_TASK = None


PROCESS = ProcessState()


def _active_task_state():
    task = PROCESS.ACTIVE_TASK
    if task is None:
        return None
    return {key: task.get(key) for key in ("session_id", "role", "instance_id",
                                         "status", "document_name")}


def _assert_role_release():
    if (PROCESS.ACTIVE_TASK is not None or PROCESS.SESSION_BINDINGS
            or PROCESS.ACTIVE_OPERATION is not None or PROCESS.AUDIT_BLOCKS):
        raise RuntimeError("Finish the active or unresolved native task before releasing this GUI role")


def _operation_stage(stage, *, mutating=False):
    if PROCESS.ACTIVE_OPERATION is not None:
        PROCESS.ACTIVE_OPERATION["stage"] = stage
        if mutating:
            PROCESS.ACTIVE_OPERATION["write_started"] = True


def _stamp_failure(exc):
    active = PROCESS.ACTIVE_OPERATION or {}
    if not hasattr(exc, "operation_stage"):
        exc.operation_stage = active.get("stage", "bridge_preflight")
    if not hasattr(exc, "write_started"):
        exc.write_started = active.get("write_started", False)
    if not hasattr(exc, "request_side_effect"):
        exc.request_side_effect = active.get("side_effect")
    return exc


HOST = "127.0.0.1"
MAX_REQUEST_BYTES = 64 * 1024
POLL_INTERVAL_MS = 25


def _document(name):
    if not isinstance(name, str) or not name:
        raise ValueError("document must be a non-empty string")
    document = App.listDocuments().get(name)
    if document is None:
        raise KeyError("Document not found: %s" % name)
    return document
