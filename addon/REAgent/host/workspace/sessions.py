"""Create/resume native workspaces in the user-bound GUI processes."""

import os

import FreeCAD as App

from REAgent.host.analysis.measurements import _shape_summary
from REAgent.host.features.support.construction import create_body
from REAgent.host.workspace.properties import _add_read_only_string
from REAgent.host.process_state import _operation_stage, PROCESS
from REAgent.host.workspace.properties import _valid_name
from REAgent.protocol.artifacts import ARTIFACT_NUMBER_LIMIT, artifact_basename, artifact_filename
from REAgent.host.validation.tolerance import _ir_tolerance_contract, _resolve_tolerance_contract
from REAgent.host.view.presentation import _style_source_document
from REAgent.host.workspace.model import _compact_json, _step_source_path, _workspace, _read_source_snapshot
from REAgent.host.workspace.persistence import _existing_fcstd_path, _same_file_path


def _session_artifact_identity(params, source_path):
    artifacts = dict(params["artifacts"])
    stem = os.path.splitext(os.path.basename(source_path))[0]
    if artifacts["source_stem"] != stem or artifacts["basename"] not in {
            artifact_basename(stem, number) for number in range(ARTIFACT_NUMBER_LIMIT + 1)}:
        raise ValueError("Artifact identity does not match the explicit STEP source")
    directory = artifacts["output_dir"]
    if not os.path.isabs(directory) or not os.path.isdir(directory):
        raise ValueError("Bound output directory must already exist")
    for key, kind in (("model_path", "model"), ("journal_path", "journal")):
        expected = os.path.join(directory, artifact_filename(artifacts["basename"], kind))
        if not os.path.isabs(artifacts[key]) or not _same_file_path(expected, artifacts[key]):
            raise ValueError("Artifact path differs from the shared naming contract: " + key)
    if params.get("resume_path") and _same_file_path(artifacts["model_path"], params["resume_path"]):
        raise ValueError("Resume output must differ from its input checkpoint")
    return artifacts


def _bind_session(session_id, document, source_revision, artifacts):
    if not isinstance(session_id, str) or not session_id:
        raise ValueError("start_session requires a session_id binding")
    if session_id in PROCESS.SESSION_BINDINGS:
        raise ValueError("Session is already bound in this FreeCAD process")
    task = PROCESS.ACTIVE_TASK
    if task is None or task["session_id"] != session_id or task["document"] is not document:
        raise _binding_error("The starting task does not own this actual Document")
    PROCESS.SESSION_BINDINGS[session_id] = {
        "session_id": session_id,
        "role": PROCESS.INSTANCE_ROLE,
        "instance_id": PROCESS.INSTANCE_ID,
        "document": document,
        "document_name": document.Name,
        "source_revision": source_revision,
        "artifacts": dict(artifacts), "saved_path": None,
    }
    task["status"] = "active"


def _binding_error(message):
    exc = ValueError(message)
    exc.error_code = "binding_unavailable"
    exc.error_class = "precondition"
    exc.retry_action = "correct_request"
    exc.failure_detail = {"reason": "binding_unavailable"}
    return exc


def _cleanup_session_metadata(owner, session_id, binding):
    """Drop only metadata tied to this session and its original Document object."""
    errors = []
    document = binding["document"]
    name = binding["document_name"]
    history = getattr(owner, "_native_history", {})
    for key, entry in list(history.items()):
        if not isinstance(key, tuple) or not key or not isinstance(entry, dict):
            continue
        if (key[0] == name and entry.get("session_id") == session_id
                and entry.get("document") is document):
            try:
                del history[key]
            except Exception as exc:
                errors.append("history_cleanup: " + str(exc)[:200])
    return {
        "status": "cleaned" if not errors else "failed",
        "errors": errors[:8],
        "error_count": len(errors),
        "errors_omitted": max(0, len(errors) - 8),
    }


def _ended_receipt(params):
    receipt = PROCESS.LAST_ENDED_TASK
    if (receipt is not None and receipt["session_id"] == params["session_id"]
            and receipt["document_name"] == params["document"]
            and receipt["role"] == PROCESS.INSTANCE_ROLE
            and receipt["instance_id"] == PROCESS.INSTANCE_ID):
        return receipt["result"]
    return None


def _finish_end_metadata(owner, session_id, binding):
    task = PROCESS.ACTIVE_TASK
    if (task is None or task["session_id"] != session_id
            or task["document"] is not binding["document"]):
        raise _binding_error("Task identity changed during session ending")
    cleanup = _cleanup_session_metadata(owner, session_id, binding)
    if cleanup["status"] != "cleaned":
        failure = RuntimeError(
            "Session metadata cleanup failed; documents remain untouched"
        )
        failure.committed_side_effect = True
        failure.retry_action = "retry_safe"
        failure.operation_stage = "native_execution"
        failure.write_started = True
        failure.failure_detail = {
            "step": "end_metadata_cleanup",
            "document": binding["document_name"],
            "cleanup": cleanup,
        }
        raise failure
    del PROCESS.SESSION_BINDINGS[session_id]
    PROCESS.ACTIVE_TASK = None
    return cleanup


def _attach_starting_document(document):
    task = PROCESS.ACTIVE_TASK
    task["document"] = document
    task["document_name"] = document.Name


class SessionOperations:
    def _start_session(self, params):
        session_id = params.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            raise _binding_error("start_session requires a session_id binding")
        if PROCESS.ACTIVE_TASK is not None or PROCESS.SESSION_BINDINGS:
            raise _binding_error("This GUI retains an active or unresolved task")
        task = {"session_id": session_id, "role": PROCESS.INSTANCE_ROLE,
                "instance_id": PROCESS.INSTANCE_ID, "status": "starting",
                "document": None, "document_name": None}
        PROCESS.ACTIVE_TASK = task
        try:
            return self._create_session(params)
        except Exception:
            try:
                document = task["document"]
                no_write = not (PROCESS.ACTIVE_OPERATION or {}).get("write_started", False)
                confirmed_closed = (document is not None and
                                    App.listDocuments().get(task["document_name"]) is not document)
                if (no_write or confirmed_closed) and session_id not in PROCESS.SESSION_BINDINGS:
                    PROCESS.ACTIVE_TASK = None
                else:
                    task["status"] = "cleanup_pending" if document is not None else "creation_unknown"
            except Exception:
                task["status"] = "cleanup_unknown"
            raise

    def _create_session(self, params):
        role = PROCESS.INSTANCE_ROLE
        session_id = params.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            raise _binding_error("start_session requires a session_id binding")
        if session_id in PROCESS.SESSION_BINDINGS:
            raise _binding_error("Session is already bound in this FreeCAD process")
        if any(doc.HasPendingTransaction for doc in App.listDocuments().values()):
            raise ValueError("Finish the active native transaction before opening a session")
        base = _valid_name(params.get("document", "Model"), "document")
        documents = App.listDocuments()
        name, number = base, 1
        while name in documents:
            suffix = "_%d" % number
            name = base[:100 - len(suffix)] + suffix
            number += 1
        source_path = _step_source_path(params["source_path"])
        artifacts = _session_artifact_identity(params, source_path)
        resume = params.get("resume_path")
        if resume:
            if role != "rebuild":
                raise ValueError("Only Target can resume a workspace")
            path = _existing_fcstd_path(resume)
            if any(_same_file_path(doc.FileName, path) for doc in App.listDocuments().values()):
                raise ValueError("Checkpoint is already open in this process")
            _operation_stage("native_execution", mutating=True)
            document = App.openDocument(path)
            _attach_starting_document(document)
            try:
                group = _workspace(document)
                if not _same_file_path(group.SourceFile, source_path):
                    raise ValueError("Checkpoint SourceFile differs from explicit source_path")
                snapshot = _read_source_snapshot(group.SourceFile)
                tolerance = _ir_tolerance_contract(group)
                tip_name = group.Tip.Name if group.Tip else None
                _bind_session(session_id, document, snapshot.SourceSHA256, artifacts)
                return {"document": document.Name, "source_path": source_path,
                        "source_revision": snapshot.SourceSHA256, "source_revision_basis": "step_sha256",
                        "checkpoint_path": document.FileName, "artifacts": dict(artifacts),
                        "tolerance_contract": tolerance,
                        "main_body": group.Name,
                        "tip": tip_name}
            except Exception:
                App.closeDocument(document.Name)
                raise
        snapshot = _read_source_snapshot(source_path)
        shape = snapshot.Shape
        summary = _shape_summary(shape)
        if not summary["is_valid"] or summary["solid_count"] != 1:
            raise ValueError("Source must contain one valid solid")
        tolerance = _resolve_tolerance_contract(params, summary)
        _operation_stage("native_execution", mutating=True)
        document = App.newDocument(name)
        _attach_starting_document(document)
        try:
            App.setActiveDocument(document.Name)
            # This is a newly created, REAgent-owned document; enable its native history.
            document.UndoMode = 1
            document.openTransaction("Initialize REAgent workspace")
            if role == "source":
                source = document.addObject("Part::Feature", "Source")
                source.Shape = shape
                for key, value in (("SourceFile", source_path), ("SourceSHA256", snapshot.SourceSHA256),
                                   ("ToleranceContractJSON", _compact_json(tolerance))):
                    _add_read_only_string(source, key, value, "Source")
                source.setEditorMode("Shape", 1)
                source.setEditorMode("Placement", 1)
                _style_source_document(source)
            else:
                group = create_body(document, "REAgentWorkspace", main=True)
                for key, value in (("SourceFile", source_path),
                                   ("ToleranceContractJSON", _compact_json(tolerance))):
                    _add_read_only_string(group, key, value, "REAgent")
            document.recompute()
            document.commitTransaction()
            _bind_session(session_id, document, snapshot.SourceSHA256, artifacts)
            return {"document": document.Name, "source_path": source_path,
                    "source_revision": snapshot.SourceSHA256, "source_revision_basis": "step_sha256",
                    "tolerance_contract": tolerance, "checkpoint_path": None, "artifacts": dict(artifacts),
                    "main_body": "REAgentWorkspace" if role == "rebuild" else None}
        except Exception:
            if document.HasPendingTransaction:
                document.abortTransaction()
            App.closeDocument(document.Name)
            raise

    def _close_session(self, params):
        session_id = params["session_id"]
        receipt = _ended_receipt(params)
        if receipt is not None:
            return dict(receipt)
        binding = PROCESS.SESSION_BINDINGS.get(session_id)
        if (binding is None or binding.get("role") != PROCESS.INSTANCE_ROLE
                or binding.get("instance_id") != PROCESS.INSTANCE_ID):
            raise _binding_error("Session-to-document binding is unavailable; do not re-identify by name")
        document_name = params["document"]
        if document_name != binding["document_name"]:
            raise _binding_error("Requested document is not the document bound to this session")
        current = App.listDocuments().get(document_name)
        if current is binding["document"] and current.HasPendingTransaction:
            raise ValueError("Finish the native transaction before ending this session")
        # End ownership only; never close or modify the native Document.
        _operation_stage("native_execution", mutating=True)
        cleanup = _finish_end_metadata(self, session_id, binding)
        block = PROCESS.AUDIT_BLOCKS.get(document_name)
        result = {"ended": True, "status": "released", "document": document_name,
                  "document_retained": current is binding["document"],
                  "original_document_closed": current is not binding["document"], "cleanup": cleanup,
                  "unknown_state_preserved": bool(block and block[0] is binding["document"])}
        PROCESS.LAST_ENDED_TASK = {"session_id": session_id, "document_name": document_name,
                                 "document": binding["document"], "role": PROCESS.INSTANCE_ROLE,
                                 "instance_id": PROCESS.INSTANCE_ID, "result": result}
        return dict(result)

    def contribute(self):
        return {'agent_start_session': self._start_session, 'agent_close_session': self._close_session}
