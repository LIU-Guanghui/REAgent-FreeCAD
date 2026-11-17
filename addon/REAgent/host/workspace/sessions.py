"""Create/resume native workspaces in the user-bound GUI processes."""

import os

import FreeCAD as App

from REAgent.host.analysis.measurements import summarize_shape
from REAgent.host.features.support.construction import create_body
from REAgent.host.file_digest import file_sha256
from REAgent.host.workspace.properties import add_read_only_string
from REAgent.host.process_state import operation_stage, PROCESS
from REAgent.host.workspace.properties import valid_name
from REAgent.protocol.artifacts import ARTIFACT_NUMBER_LIMIT, artifact_basename, artifact_filename
from REAgent.protocol.definitions import NATIVE_SOURCE_EXTENSIONS
from REAgent.host.validation.tolerance import ir_tolerance_contract, resolve_tolerance_contract
from REAgent.host.view.presentation import style_source_document
from REAgent.host.workspace.model import compact_json, native_source_path, read_mesh, read_source_snapshot, workspace
from REAgent.host.workspace.persistence import existing_fcstd_path, same_file_path
from REAgent.host.workspace.state import NativeSourceReference


def _session_artifact_identity(params, source_path):
    artifacts = dict(params["artifacts"])
    stem = artifacts["stem"]
    if source_path and stem != os.path.splitext(os.path.basename(source_path))[0]:
        raise ValueError("Artifact stem differs from the native reference filename")
    if artifacts["basename"] not in {
            artifact_basename(stem, number) for number in range(ARTIFACT_NUMBER_LIMIT + 1)}:
        raise ValueError("Artifact basename differs from the shared naming contract")
    directory = artifacts["output_dir"]
    if not os.path.isabs(directory) or not os.path.isdir(directory):
        raise ValueError("Bound output directory must already exist")
    for key, kind in (("model_path", "model"), ("journal_path", "journal")):
        expected = os.path.join(directory, artifact_filename(artifacts["basename"], kind))
        if not os.path.isabs(artifacts[key]) or not same_file_path(expected, artifacts[key]):
            raise ValueError("Artifact path differs from the shared naming contract: " + key)
    if params.get("resume_path") and same_file_path(artifacts["model_path"], params["resume_path"]):
        raise ValueError("Resume output must differ from its input checkpoint")
    return artifacts


def _read_drawing(document, path, revision):
    import ImportGui
    from importDXF import DxfDraftPostProcessor

    source = document.addObject("App::DocumentObjectGroup", "Source")
    option_path = "User parameter:BaseApp/Preferences/Mod/REAgent"
    parent = App.ParamGet(option_path)
    options = parent.GetGroup("DxfImport")
    individual_shapes = 2
    try:
        options.SetInt("DxfImportMode", individual_shapes)
        options.SetBool("dxftext", True)
        options.SetBool("dxflayout", True)
        stats = ImportGui.readDXF(path, document.Name, False, option_path + "/DxfImport")
    finally:
        parent.RemGroup("DxfImport")
    if file_sha256(path) != revision:
        raise ValueError("DXF changed during import")
    if not stats["totalEntitiesCreated"]:
        raise ValueError("DXF must contain a visible engineering drawing")
    # FreeCAD reconstructs DIMENSION entities from points and drops the original callout.
    if stats["entityCounts"].get("DIMENSION"):
        raise ValueError("DXF contains DIMENSION entities. Export the full sheet with dimensions and tolerances as geometry.")
    imported = set(document.Objects) - {source}
    DxfDraftPostProcessor(document, imported, individual_shapes).run()
    source.Group = [obj for obj in document.Objects if obj is not source and not obj.InList]
    for obj in document.Objects:
        view = obj.ViewObject
        if hasattr(view, "Selectable"):
            view.Selectable = False
        for container in (obj, view):
            if container is not None:
                for name in container.PropertiesList:
                    if not container.getEditorMode(name):
                        container.setEditorMode(name, 1)
    return source


def _bind_session(session_id, document, source_revision, artifacts):
    if not isinstance(session_id, str) or not session_id:
        raise ValueError("start_session requires a session_id binding")
    if session_id in PROCESS.SESSION_BINDINGS:
        raise ValueError("Session is already bound in this FreeCAD process")
    task = PROCESS.ACTIVE_TASK
    if task is None or task["session_id"] != session_id or task["document"] is not document:
        raise _binding_error("The starting task does not own this actual Document")
    binding = {
        "session_id": session_id,
        "role": PROCESS.INSTANCE_ROLE,
        "instance_id": PROCESS.INSTANCE_ID,
        "document": document,
        "document_name": document.Name,
        "source_revision": source_revision,
        "artifacts": dict(artifacts), "saved_path": None,
    }
    if PROCESS.INSTANCE_ROLE == "source":
        reference = NativeSourceReference(document)
        App.addDocumentObserver(reference)
        binding["source_reference"] = reference
    PROCESS.SESSION_BINDINGS[session_id] = binding
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
    reference = binding.get("source_reference")
    if not errors and reference is not None:
        try:
            App.removeDocumentObserver(reference)
        except Exception as exc:
            errors.append("source_observer_cleanup: " + str(exc)[:200])
    return {
        "status": "cleaned" if not errors else "failed",
        "errors": errors[:8],
        "error_count": len(errors),
        "errors_omitted": max(0, len(errors) - 8),
    }


def ended_receipt(params):
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
    def contribute(self):
        return {'agent_start_session': self._start_session, 'agent_close_session': self._close_session}

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
        base = valid_name(params.get("document", "Model"), "document")
        documents = App.listDocuments()
        name, number = base, 1
        while name in documents:
            suffix = "_%d" % number
            name = base[:100 - len(suffix)] + suffix
            number += 1
        source_path = params.get("source_path")
        source_kind = None
        if source_path:
            extensions = tuple(suffix for suffixes in NATIVE_SOURCE_EXTENSIONS.values() for suffix in suffixes)
            source_path = native_source_path(source_path, extensions)
            suffix = os.path.splitext(source_path)[1].lower()
            source_kind = next(kind for kind, suffixes in NATIVE_SOURCE_EXTENSIONS.items() if suffix in suffixes)
        elif role == "source":
            raise ValueError("A Source workspace requires STP, STL or DXF")
        artifacts = _session_artifact_identity(params, source_path)
        resume = params.get("resume_path")
        if resume:
            if role != "target" or source_kind != "stp":
                raise ValueError("Resume requires a Target and its explicit STP source")
            path = existing_fcstd_path(resume)
            if any(same_file_path(doc.FileName, path) for doc in App.listDocuments().values()):
                raise ValueError("Checkpoint is already open in this process")
            operation_stage("native_execution", mutating=True)
            document = App.openDocument(path)
            _attach_starting_document(document)
            try:
                group = workspace(document)
                if not same_file_path(getattr(group, "SourceFile", None), source_path):
                    raise ValueError("Checkpoint SourceFile differs from explicit source_path")
                snapshot = read_source_snapshot(group.SourceFile)
                tolerance = ir_tolerance_contract(group)
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
        shape = mesh = summary = revision = revision_basis = None
        if source_kind == "stp":
            snapshot = read_source_snapshot(source_path)
            shape = snapshot.Shape
            summary = summarize_shape(shape)
            if not summary["is_valid"] or summary["solid_count"] != 1:
                raise ValueError("Source must contain one valid solid")
            revision, revision_basis = snapshot.SourceSHA256, "step_sha256"
        elif source_path:
            revision, revision_basis = file_sha256(source_path), "file_sha256"
            if role == "source" and source_kind == "mesh":
                mesh = read_mesh(source_path, revision)
        tolerance = resolve_tolerance_contract(params, summary)
        operation_stage("native_execution", mutating=True)
        document = App.newDocument(name)
        _attach_starting_document(document)
        try:
            App.setActiveDocument(document.Name)
            source = (_read_drawing(document, source_path, revision)
                      if role == "source" and source_kind == "drawing" else None)
            # This is a newly created, REAgent-owned document; enable its native history.
            document.UndoMode = 1
            document.openTransaction("Initialize REAgent workspace")
            if role == "source":
                if source_kind == "stp":
                    source = document.addObject("Part::Feature", "Source")
                    source.Shape = shape
                    source.setEditorMode("Shape", 1)
                elif source_kind == "mesh":
                    source = document.addObject("Mesh::Feature", "Source")
                    source.Mesh = mesh
                    source.setEditorMode("Mesh", 1)
                for key, value in (("SourceFile", source_path), ("SourceSHA256", revision),
                                   ("ToleranceContractJSON", compact_json(tolerance))):
                    add_read_only_string(source, key, value, "Source")
                if source_kind != "drawing":
                    source.setEditorMode("Placement", 1)
            else:
                group = create_body(document, "REAgentWorkspace", main=True)
                if source_path:
                    add_read_only_string(group, "SourceFile", source_path, "REAgent")
                add_read_only_string(group, "ToleranceContractJSON", compact_json(tolerance), "REAgent")
            document.recompute()
            if role == "source":
                style_source_document(source)
            document.commitTransaction()
            _bind_session(session_id, document, revision, artifacts)
            return {"document": document.Name, "source_path": source_path,
                    "source_revision": revision, "source_revision_basis": revision_basis,
                    "tolerance_contract": tolerance, "checkpoint_path": None, "artifacts": dict(artifacts),
                    "main_body": "REAgentWorkspace" if role == "target" else None}
        except Exception:
            if document.HasPendingTransaction:
                document.abortTransaction()
            App.closeDocument(document.Name)
            raise

    def _close_session(self, params):
        session_id = params["session_id"]
        receipt = ended_receipt(params)
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
        operation_stage("native_execution", mutating=True)
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
