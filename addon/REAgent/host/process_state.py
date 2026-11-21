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
        # Its separate source_revision field pins the observed native reference revision,
        # not document ownership or geometric equivalence.
        self.SESSION_BINDINGS = {}
        # One process-local receipt confirms an idempotent end after a lost reply.
        self.LAST_ENDED_TASK = None
        # Track native Documents created by REAgent in this process for exact temp cleanup.
        self.SESSION_TEMP_DOCS = {}
        self.TEMP_DOC_OBSERVER = None


PROCESS = ProcessState()


class _TempDocObserver:
    """Track external mutations or history changes on REAgent-created documents."""

    def _mark_external(self, doc):
        if PROCESS.ACTIVE_OPERATION is not None or doc is None:
            return
        entry = PROCESS.SESSION_TEMP_DOCS.get(id(doc))
        if entry is not None and entry.get("document") is doc:
            entry["externally_modified"] = True

    def slotCreatedObject(self, obj):
        self._mark_external(getattr(obj, "Document", None))

    def slotDeletedObject(self, obj):
        self._mark_external(getattr(obj, "Document", None))

    def slotChangedObject(self, obj, _prop=None):
        self._mark_external(getattr(obj, "Document", None))

    def slotUndoDocument(self, doc):
        self._mark_external(doc)

    def slotRedoDocument(self, doc):
        self._mark_external(doc)

    def slotCommitTransaction(self, doc):
        self._mark_external(doc)

    def slotDeletedDocument(self, doc):
        if doc is not None:
            PROCESS.SESSION_TEMP_DOCS.pop(id(doc), None)
        _detach_temp_doc_observer_if_empty()


def _ensure_temp_doc_observer():
    if PROCESS.TEMP_DOC_OBSERVER is None:
        observer = _TempDocObserver()
        App.addDocumentObserver(observer)
        PROCESS.TEMP_DOC_OBSERVER = observer


def _detach_temp_doc_observer_if_empty():
    if not PROCESS.SESSION_TEMP_DOCS and PROCESS.TEMP_DOC_OBSERVER is not None:
        observer = PROCESS.TEMP_DOC_OBSERVER
        try:
            App.removeDocumentObserver(observer)
        except Exception:
            pass
        PROCESS.TEMP_DOC_OBSERVER = None


def register_temp_doc(session_id, document):
    PROCESS.SESSION_TEMP_DOCS[id(document)] = {
        "session_id": session_id,
        "document": document,
        "document_name": document.Name,
        "saved_path": None,
        "externally_modified": False,
        "last_undo": list(document.UndoNames),
        "last_redo": list(document.RedoNames),
        "last_objects": [obj.Name for obj in document.Objects],
    }
    _ensure_temp_doc_observer()


def sync_temp_doc_state(document, *, saved_path=None):
    entry = PROCESS.SESSION_TEMP_DOCS.get(id(document))
    if entry is None or entry.get("document") is not document:
        return
    if saved_path is not None:
        entry["saved_path"] = saved_path
    if not entry.get("externally_modified"):
        entry["last_undo"] = list(document.UndoNames)
        entry["last_redo"] = list(document.RedoNames)
        entry["last_objects"] = [obj.Name for obj in document.Objects]


def cleanup_unmodified_temp_docs(*, session_ids=None, gui=None):
    live_docs = list(App.listDocuments().values())
    for doc_id, entry in list(PROCESS.SESSION_TEMP_DOCS.items()):
        doc = entry.get("document")
        if not any(live is doc for live in live_docs):
            PROCESS.SESSION_TEMP_DOCS.pop(doc_id, None)
            continue
        if session_ids is not None and entry.get("session_id") not in session_ids:
            continue
        if entry.get("externally_modified"):
            continue
        if (
            entry.get("saved_path")
            or getattr(doc, "FileName", "")
            or getattr(doc, "HasPendingTransaction", False)
        ):
            continue
        name = getattr(doc, "Name", None)
        if not name or name in PROCESS.AUDIT_BLOCKS:
            continue
        if gui is not None:
            try:
                gui_doc = gui.getDocument(name)
                if gui_doc is not None and gui_doc.getInEdit() is not None:
                    continue
            except Exception:
                continue
        if (
            list(doc.UndoNames) != entry.get("last_undo")
            or list(doc.RedoNames) != entry.get("last_redo")
            or [obj.Name for obj in doc.Objects] != entry.get("last_objects")
        ):
            continue
        if App.listDocuments().get(name) is doc:
            App.closeDocument(name)
        PROCESS.SESSION_TEMP_DOCS.pop(doc_id, None)
    _detach_temp_doc_observer_if_empty()


def active_task_state():
    task = PROCESS.ACTIVE_TASK
    if task is None:
        return None
    value = {key: task.get(key) for key in ("session_id", "role", "instance_id",
                                           "status", "document_name")}
    if task["role"] == "source" and task["status"] == "active":
        binding = PROCESS.SESSION_BINDINGS[task["session_id"]]
        value["reference_current"] = (
            App.listDocuments().get(task["document_name"]) is binding["document"]
            and not binding["source_reference"].changed)
    return value


def assert_role_release():
    if (PROCESS.ACTIVE_TASK is not None or PROCESS.SESSION_BINDINGS
            or PROCESS.ACTIVE_OPERATION is not None or PROCESS.AUDIT_BLOCKS):
        raise RuntimeError("Finish the active or unresolved native task before releasing this GUI role")


def operation_stage(stage, *, mutating=False):
    if PROCESS.ACTIVE_OPERATION is not None:
        PROCESS.ACTIVE_OPERATION["stage"] = stage
        if mutating:
            PROCESS.ACTIVE_OPERATION["write_started"] = True


def stamp_failure(exc):
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


def require_document(name):
    if not isinstance(name, str) or not name:
        raise ValueError("document must be a non-empty string")
    document = App.listDocuments().get(name)
    if document is None:
        raise KeyError("Document not found: %s" % name)
    return document
