"""Bound FCStd persistence and checkpoint path protection; no implicit validation."""

import os

from REAgent.host.process_state import _document, _operation_stage, PROCESS
from REAgent.host.validation.structure import result_state
from REAgent.host.file_digest import _file_sha256
from REAgent.host.workspace.model import _workspace
from REAgent.host.workspace.state import _target_view_revision


def _same_file_path(left, right):
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
    if exists and not _same_file_path(
        path, owned_checkpoint_path
    ):
        raise ValueError("Output file already exists: %s" % path)
    return path


def _existing_fcstd_path(value):
    if not isinstance(value, str) or not value:
        raise ValueError("resume_path must be a non-empty absolute path")
    if not os.path.isabs(value):
        raise ValueError("resume_path must be an absolute path")
    path = os.path.normpath(value)
    if os.path.splitext(path)[1].lower() != ".fcstd":
        raise ValueError("resume_path must use the .FCStd extension")
    if not os.path.isfile(path):
        raise ValueError("Reconstruction FCStd does not exist: %s" % path)
    return path


class PersistenceOperations:
    def _save(self, params):
        document = _document(params["document"])
        group = _workspace(document)
        config = group
        binding = PROCESS.SESSION_BINDINGS.get(params["session_id"])
        if (binding is None or binding.get("document") is not document
                or binding.get("role") != "rebuild"):
            raise ValueError("Save requires the exact bound Target session")
        if document.HasPendingTransaction: raise ValueError("Cannot save during an active transaction")
        model_state = result_state(group)
        provenance = {"document": document.Name,
                      "target_revision": _target_view_revision(document, group, config)}
        try:
            provenance["source_revision"] = _file_sha256(group.SourceFile)
        except (OSError, ValueError, TypeError):
            provenance["source_revision"] = None
        path = binding["artifacts"]["model_path"]
        owned_path = binding["saved_path"] if _same_file_path(binding["saved_path"], document.FileName) else None
        path = _new_fcstd_path(path, owned_checkpoint_path=owned_path)
        _operation_stage("persistence", mutating=True)
        if document.FileName and _same_file_path(path, document.FileName): document.save()
        else: document.saveAs(path)
        binding["saved_path"] = path
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
        }

    def contribute(self):
        return {'agent_save': self._save}
