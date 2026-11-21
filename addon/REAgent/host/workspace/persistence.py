"""Bound FCStd persistence and checkpoint path protection; no implicit validation."""

import os

import FreeCADGui as Gui

from REAgent.host.process_state import require_document, operation_stage, sync_temp_doc_state, PROCESS
from REAgent.host.validation.structure import result_state
from REAgent.host.file_digest import file_sha256
from REAgent.host.workspace.model import workspace
from REAgent.host.workspace.state import target_view_revision


def same_file_path(left, right):
    if not left or not right:
        return False
    return os.path.normcase(os.path.abspath(left)) == os.path.normcase(
        os.path.abspath(right)
    )


def _new_fcstd_path(value, owned_checkpoint_path=None):
    if not isinstance(value, str) or not value:
        raise ValueError("Allocated model path must be a non-empty absolute path")
    if not os.path.isabs(value):
        raise ValueError("Allocated model path must be an absolute path")
    path = os.path.normpath(value)
    if os.path.splitext(path)[1].lower() != ".fcstd":
        raise ValueError("Allocated model path must use the .FCStd extension")
    parent = os.path.dirname(path)
    if not os.path.isdir(parent):
        raise ValueError("output directory does not exist: %s" % parent)
    try:
        os.lstat(path)
        exists = True
    except FileNotFoundError:
        exists = False
    if exists and not same_file_path(
        path, owned_checkpoint_path
    ):
        raise ValueError("Output file already exists: %s" % path)
    return path


class PersistenceOperations:
    def contribute(self):
        return {'agent_save': self._save}

    def _save(self, params):
        document = require_document(params["document"])
        group = workspace(document)
        binding = PROCESS.SESSION_BINDINGS.get(params["session_id"])
        if (binding is None or binding.get("document") is not document
                or binding.get("role") != "target"):
            raise ValueError("Save requires the exact bound Target session")
        if document.HasPendingTransaction: raise ValueError("Cannot save during an active transaction")
        model_state = result_state(group)
        provenance = {"document": document.Name,
                      "target_revision": target_view_revision(document, group)}
        provenance["source_revision"] = None
        if binding["source_revision"]:
            try:
                provenance["source_revision"] = file_sha256(group.SourceFile)
            except (OSError, ValueError, TypeError):
                pass
        path = binding["artifacts"]["model_path"]
        owned_path = binding["saved_path"] if same_file_path(binding["saved_path"], document.FileName) else None
        path = _new_fcstd_path(path, owned_checkpoint_path=owned_path)
        operation_stage("persistence", mutating=True)
        gui_doc = Gui.getDocument(document.Name)
        if document.FileName and same_file_path(path, document.FileName):
            if gui_doc is not None:
                if not gui_doc.save():
                    raise RuntimeError("FreeCAD GUI document save did not complete")
            else:
                document.save()
        else:
            document.saveAs(path)
            gui_doc = Gui.getDocument(document.Name)
            if gui_doc is not None and getattr(gui_doc, "Modified", False) and same_file_path(path, document.FileName):
                if not gui_doc.save():
                    raise RuntimeError("FreeCAD GUI document save sync did not complete")
        binding["saved_path"] = path
        sync_temp_doc_state(document, saved_path=path)
        # The write has happened. A failed readback only leaves applicability unknown.
        try:
            current_revision = target_view_revision(document, group)
        except Exception:
            current_revision = None
        return {
            "saved": True,
            "status": "saved",
            "saved_to": path,
            "model_state": model_state,
            "checkpoint": model_state["state"] == "in_progress",
            "result": group.Tip.Name if group.Tip else None,
            "result_body": group.Name,
            "tip": group.Tip.Name if group.Tip else None,
            "validation_provenance": provenance,
            "saved_revision": provenance["target_revision"] if current_revision == provenance["target_revision"] else None,
            "revision_after": current_revision,
        }
