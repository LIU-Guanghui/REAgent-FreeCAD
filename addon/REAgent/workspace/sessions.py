"""Create/resume native workspaces in the user-bound GUI processes."""

import os

import FreeCAD as App

from ..analysis.measurements import _shape_summary
from ..features.common.construction import create_body
from ..runtime import _add_read_only_string, _operation_stage, _state, _valid_name
from ..protocol.contract import ARTIFACT_NUMBER_LIMIT, artifact_basename, artifact_filename
from ..validation.tolerance import _ir_tolerance_contract, _resolve_tolerance_contract
from ..view.presentation import _style_source_document
from .model import _compact_json, _step_source_path, _workspace, _read_source_snapshot
from .persistence import _existing_fcstd_path, _same_file_path


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
    if session_id in _state.SESSION_BINDINGS:
        raise ValueError("Session is already bound in this FreeCAD process")
    _state.SESSION_BINDINGS[session_id] = {
        "session_id": session_id,
        "role": _state.INSTANCE_ROLE,
        "document": document,
        "document_name": document.Name,
        "source_revision": source_revision,
        "artifacts": dict(artifacts), "saved_path": None,
    }


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
    block = _state.AUDIT_BLOCKS.get(name)
    if block is not None and block[0] is document:
        try:
            del _state.AUDIT_BLOCKS[name]
        except Exception as exc:
            errors.append("audit_block_cleanup: " + str(exc)[:200])
    return {
        "status": "cleaned" if not errors else "failed",
        "errors": errors[:8],
        "error_count": len(errors),
        "errors_omitted": max(0, len(errors) - 8),
    }


def _close_result(
    status, document_name, cleanup, warning=None, *, write_state_was_blocked=False
):
    result = {"closed": True, "status": status, "document": document_name,
              "cleanup": cleanup,
              "write_state_resolution": (
                  "discarded_unverified" if write_state_was_blocked else "not_blocked"
              )}
    if warning:
        result["close_warning"] = warning[:300]
    return result


def _finish_close_metadata(owner, session_id, binding, close_status):
    cleanup = _cleanup_session_metadata(owner, session_id, binding)
    if cleanup["status"] != "cleaned":
        failure = RuntimeError(
            "Document close was confirmed but session metadata cleanup failed"
        )
        failure.committed_side_effect = True
        # Repeating the same close request cannot close the document again:
        # the already-closed path only retries this retained metadata cleanup.
        failure.retry_action = "retry_safe"
        failure.operation_stage = "native_execution"
        failure.write_started = True
        failure.failure_detail = {
            "step": "close_metadata_cleanup",
            "close_status": close_status,
            "document": binding["document_name"],
            "cleanup": cleanup,
        }
        raise failure
    del _state.SESSION_BINDINGS[session_id]
    return cleanup


class SessionOperations:
    def _start_session(self, params):
        role = _state.INSTANCE_ROLE
        session_id = params.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            raise _binding_error("start_session requires a session_id binding")
        if session_id in _state.SESSION_BINDINGS:
            raise _binding_error("Session is already bound in this FreeCAD process")
        if any(doc.HasPendingTransaction for doc in App.listDocuments().values()):
            raise ValueError("Finish the active native transaction before opening a session")
        name = _valid_name(params.get("document", "Model"), "document")
        if App.listDocuments().get(name) is not None:
            raise ValueError("Document already exists: " + name)
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
        binding = _state.SESSION_BINDINGS.get(session_id)
        if binding is None or binding.get("role") != _state.INSTANCE_ROLE:
            raise _binding_error("Session-to-document binding is unavailable; do not re-identify by name")
        document_name = params["document"]
        if document_name != binding["document_name"]:
            raise _binding_error("Requested document is not the document bound to this session")
        block = _state.AUDIT_BLOCKS.get(document_name)
        write_state_was_blocked = bool(
            block is not None and block[0] is binding["document"]
        )
        current = App.listDocuments().get(document_name)
        if current is None or current is not binding["document"]:
            cleanup = _finish_close_metadata(
                self, session_id, binding, "already_closed"
            )
            return _close_result(
                "already_closed", document_name, cleanup,
                write_state_was_blocked=write_state_was_blocked,
            )
        document = current
        # Explicit discard is authorized at the public boundary. In a blocked
        # state, do not inspect/recompute/abort/save the document: close only
        # this exact process-local object and let FreeCAD discard its state.
        _operation_stage("native_execution", mutating=True)
        warning = None
        try:
            App.closeDocument(document.Name)
        except Exception as exc:
            if App.listDocuments().get(document_name) is None:
                warning = str(exc)
            else:
                exc.target_state_unknown = True
                exc.operation_stage = "native_execution"
                exc.write_started = True
                exc.failure_detail = {"step": "close_document", "document": document_name}
                raise
        if App.listDocuments().get(document_name) is not None:
            failure = RuntimeError("Native document close could not be confirmed")
            failure.target_state_unknown = True
            failure.operation_stage = "recovery"
            failure.write_started = True
            failure.failure_detail = {"step": "close_confirmation", "document": document_name}
            raise failure
        cleanup = _finish_close_metadata(self, session_id, binding, "closed")
        return _close_result(
            "closed", document_name, cleanup, warning,
            write_state_was_blocked=write_state_was_blocked,
        )
