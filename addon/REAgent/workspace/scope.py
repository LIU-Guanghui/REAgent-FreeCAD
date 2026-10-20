"""Owned observation lifetime and final recovery certification."""

import os
import uuid

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtGui

from ..runtime import _operation_stage, _stamp_failure, _state
from ..protocol.contract import ARTIFACT_NUMBER_LIMIT, artifact_filename
from ..validation.tolerance import _ir_tolerance_contract, _source_tolerance_contract
from ..view.camera import _camera_state_differences, _capture_complete_camera_state, _restore_camera_state
from .model import _compact_json, _source_objects, _workspace
from .recovery import _block, _fact_differences, _recompute_preserving_history, _transaction_ready
from .state import (
    _document_history_state,
    _geometry_differences,
    _presentation_state,
    _source_geometry_state,
    _source_model_state,
    _target_geometry_state,
    _target_model_state,
)


class ObservationScopeOperations:
    def _isolated_observation(self, document, render, params, *, temporary=True):
        """Certify one observation only after its owned transient scope exits."""
        if temporary:
            _transaction_ready(document)
        elif App.activeDocument() is not document or any(
                doc.HasPendingTransaction for doc in App.listDocuments().values()):
            raise ValueError("Observation requires the bound active document without a user transaction")
        if temporary and document.RedoNames:
            raise ValueError("Temporary observation cannot erase a native redo branch")
        if App.getActiveTransaction() is not None:
            raise ValueError("Finish the application transaction before an observation")
        group = _workspace(document) if _state.INSTANCE_ROLE == "rebuild" else None
        source = None if group else _source_objects(document.Name)[1]
        model_before = (_target_model_state(document, group, include_history=False) if group
                        else _source_model_state(source, include_history=False))
        geometry_before = (_target_geometry_state(group) if group
                           else _source_geometry_state(source))
        tolerance_contract = (_ir_tolerance_contract(group) if group
                              else _source_tolerance_contract(source))
        view = Gui.activeDocument().activeView()
        camera_before = _capture_complete_camera_state(view)
        display_before = _presentation_state(document)
        history_before = _document_history_state(document)
        scope = _ObservationScope(document, temporary)
        final_errors = _BoundedRestorationErrors()
        error, value = None, None
        history_ownership_confirmed = True
        App.addDocumentObserver(scope)
        try:
            _operation_stage("observation", mutating=True)
            if temporary:
                scope.phase = "opening"
                # FreeCAD materializes the document transaction on the first
                # model change, not when the application transaction is opened.
                scope.transaction_id = App.setActiveTransaction(scope.transaction_name, True)
                if not scope.owns_active_transaction():
                    raise RuntimeError("Observation transaction ownership was not established")
            scope.phase = "rendering"
            value = render(params, scope)
        except Exception as exc:
            error = _stamp_failure(exc)
        finally:
            try:
                if temporary:
                    if scope.owns_active_transaction():
                        materialized = scope.open_count == 1
                        scope.phase = "aborting"
                        # Also close an owned application transaction when setup
                        # failed before the first preview materialized it.
                        App.closeActiveTransaction(True, scope.transaction_id)
                        history_ownership_confirmed = (
                            scope.abort_count == int(materialized)
                            and not scope.unexpected_history_event
                            and App.getActiveTransaction() is None
                            and not document.HasPendingTransaction)
                        if group and materialized and history_ownership_confirmed:
                            _recompute_preserving_history(document)
                    else:
                        history_ownership_confirmed = False
                        final_errors.add("history: observation transaction consumed, replaced or unconfirmed")
                elif scope.unexpected_history_event:
                    history_ownership_confirmed = False
                    final_errors.add("history: unexpected transaction or undo/redo during observation")
            except Exception as exc:
                history_ownership_confirmed = False
                final_errors.add("history: scope exit failed: " + str(exc))
            finally:
                scope.phase = "finalizing"
        # No renderer declares durable restoration, and no arbitrary transaction
        # is aborted if ownership was lost. Presentation is restored after abort.
        presentation_attempt = _BoundedRestorationErrors()
        try:
            if history_ownership_confirmed and not document.HasPendingTransaction:
                presentation_attempt = _restore_view_operation(
                    document, view, camera_before, document, display_before)
        except Exception as exc:
            presentation_attempt.add("presentation: restore attempt failed: " + str(exc))
        try:
            QtGui.QApplication.processEvents(
                QtCore.QEventLoop.ExcludeUserInputEvents | QtCore.QEventLoop.ExcludeSocketNotifiers)
        except Exception as exc:
            final_errors.add("presentation: final refresh failed: " + str(exc))
            if error is None:
                error = _stamp_failure(exc)
        try:
            App.removeDocumentObserver(scope)
        except Exception as exc:
            history_ownership_confirmed = False
            final_errors.add("history: observer removal failed: " + str(exc))
        if scope.unexpected_history_event:
            history_ownership_confirmed = False
            final_errors.add("history: unexpected event during observation or final cleanup")
        scope.phase = "exited"
        recovery = {"checked_at": "after_observation_scope_exit",
                    "model_unchanged": None, "result_geometry_unchanged": None,
                    "history_restored": None, "presentation_restored": None}
        try:
            differences = _fact_differences(model_before,
                _target_model_state(document, group, include_history=False) if group
                else _source_model_state(source, include_history=False), "model")
            recovery["model_unchanged"] = not differences
            for item in differences[:8]:
                final_errors.add("model: " + item["field"] + ": " + item.get("reason", "value_changed"))
        except Exception as exc:
            final_errors.add("model: final state readback failed: " + str(exc)[:180])
        try:
            differences = _geometry_differences(geometry_before,
                _target_geometry_state(group) if group
                else _source_geometry_state(source),
                tolerance_contract)
            recovery["result_geometry_unchanged"] = not differences
            for item in differences[:8]:
                final_errors.add("geometry: " + _compact_json(item))
        except Exception as exc:
            final_errors.add("geometry: final Result readback failed: " + str(exc)[:180])
        try:
            same_history = (_document_history_state(document) == history_before
                            and App.getActiveTransaction() is None)
            recovery["history_restored"] = same_history if history_ownership_confirmed else None
            if not same_history:
                recovery["history_restored"] = False
                final_errors.add("history: final undo/redo/pending/application transaction differs from entry")
        except Exception as exc:
            final_errors.add("history: final readback failed: " + str(exc)[:180])
        try:
            differences = _camera_state_differences(view, camera_before["numeric"])
            presentation_same = (App.activeDocument() is document
                                 and _presentation_state(document) == display_before and not differences)
            recovery["presentation_restored"] = presentation_same
            for difference in differences:
                final_errors.add(difference)
            if not presentation_same:
                final_errors.add("presentation: final display/camera/active document differs from entry")
        except Exception as exc:
            final_errors.add("presentation_readback: " + str(exc)[:180])
        durable_facts = [recovery[key] for key in (
            "model_unchanged", "result_geometry_unchanged", "history_restored")]
        durable_restored = all(value is True for value in durable_facts)
        document_restored = True if durable_restored else False if False in durable_facts else None
        confirmed = durable_restored and recovery["presentation_restored"] is True
        recovery.update(document_restored=document_restored, restoration_confirmed=confirmed,
                        execution_status="failed" if error else "completed",
                        temporary_cleanup_error_count=scope.cleanup_errors.total_count,
                        temporary_cleanup_errors=list(scope.cleanup_errors),
                        temporary_cleanup_errors_omitted=max(0, scope.cleanup_errors.total_count - len(scope.cleanup_errors)),
                        presentation_restore_error_count=presentation_attempt.total_count,
                        presentation_restore_errors=list(presentation_attempt),
                        presentation_restore_errors_omitted=max(0, presentation_attempt.total_count - len(presentation_attempt)))
        if error is not None or not confirmed:
            residual = scope.cleanup_files()
            detail = {**dict(getattr(error, "failure_detail", {}) or {}), **recovery,
                      "original_error": str(error)[:500] if error else None,
                      "restoration_errors": list(final_errors),
                      "restoration_error_count": final_errors.total_count,
                      "restoration_errors_omitted": max(0, final_errors.total_count - len(final_errors)),
                      "residual_files": residual[:8], "residual_file_count": len(residual),
                      "residual_files_omitted": max(0, len(residual) - 8)}
            if not durable_restored:
                failure = _block(document, "Observation durable recovery is unconfirmed")
                detail["target_writes_blocked"] = True
                _state.AUDIT_BLOCKS[document.Name] = (document, detail)
            elif not confirmed:
                failure = RuntimeError("Observation presentation recovery is incomplete")
                failure.operation_stage = "recovery"
            else:
                failure = error
            failure.failure_detail = detail
            failure.restored = durable_restored
            failure.restoration_confirmed = confirmed
            if failure is error:
                failure.view_reason = _view_failure_reason(error)
                raise failure
            raise _stamp_failure(failure) from error
        return {**value, "restored": True, "recovery": recovery}


class _ObservationScope:
    """One owner for transient history and newly reserved image paths."""

    def __init__(self, document, temporary):
        self.document = document
        self.temporary = temporary
        self.capture_id = uuid.uuid4().hex
        self.transaction_name = "Temporary observation " + self.capture_id
        self.transaction_id = None
        self.phase = "idle"
        self.open_count = self.abort_count = self.commit_count = 0
        self.unexpected_history_event = False
        self.paths = []
        self.cleanup_errors = _BoundedRestorationErrors()

    def slotOpenTransaction(self, document, name):
        if document is self.document:
            self.open_count += 1
            if (self.phase != "rendering" or name != self.transaction_name
                    or self.open_count != 1
                    or App.getActiveTransaction() != (self.transaction_name, self.transaction_id)):
                self.unexpected_history_event = True
        else:
            self.unexpected_history_event = True

    def slotAbortTransaction(self, document):
        if document is self.document:
            self.abort_count += 1
            if self.phase != "aborting" or self.abort_count != 1:
                self.unexpected_history_event = True
        else:
            self.unexpected_history_event = True

    def slotCommitTransaction(self, document):
        if document is self.document:
            self.commit_count += 1
        self.unexpected_history_event = True

    def slotUndoDocument(self, document):
        self.unexpected_history_event = True

    def slotRedoDocument(self, document):
        self.unexpected_history_event = True

    def owns_active_transaction(self):
        return (isinstance(self.transaction_id, int) and not isinstance(self.transaction_id, bool)
                and self.transaction_id > 0
                and App.getActiveTransaction() == (self.transaction_name, self.transaction_id)
                and self.abort_count == self.commit_count == 0
                and not self.unexpected_history_event
                and self.open_count in (0, 1)
                and bool(self.document.HasPendingTransaction) == (self.open_count == 1)
                and not any(doc is not self.document and doc.HasPendingTransaction
                            for doc in App.listDocuments().values()))

    def owns_pending_transaction(self):
        return self.open_count == 1 and self.owns_active_transaction()

    def create_preview(self, name):
        self.assert_render_safe()
        preview = self.document.addObject("Part::Feature", name)
        if not self.owns_pending_transaction():
            raise RuntimeError("Observation preview did not establish its owned document transaction")
        return preview

    def assert_render_safe(self):
        if (self.unexpected_history_event
                or (self.temporary and not self.owns_active_transaction())
                or (not self.temporary and self.document.HasPendingTransaction)
                or (not self.temporary and App.getActiveTransaction() is not None)
                or App.activeDocument() is not self.document
                or any(doc is not self.document and doc.HasPendingTransaction
                       for doc in App.listDocuments().values())):
            raise RuntimeError("Observation ownership or active document changed during rendering")

    def reserve_paths(self, paths):
        # Exclusive creation protects pre-existing files and records partial setup.
        for path in paths:
            try:
                stream = open(path, "xb")
            except FileExistsError as exc:
                exc.error_class = "precondition"
                exc.error_code = "view_output_exists"
                exc.retry_action = "correct_request"
                exc.field_path = "session.artifacts"
                exc.strerror = "Capture path is occupied; existing files are preserved."
                raise
            self.paths.append(path)
            with stream:
                pass

    def reserve_artifacts(self, artifacts, kinds):
        """Claim a whole capture at one number; never split a registered pair."""
        if self.paths:
            raise RuntimeError("Capture artifact paths have already been reserved")
        for number in range(ARTIFACT_NUMBER_LIMIT + 1):
            paths = {kind: os.path.join(artifacts["output_dir"], artifact_filename(artifacts["basename"], kind, number))
                     for kind in kinds}
            try:
                self.reserve_paths(paths.values())
            except FileExistsError as exc:
                if getattr(exc, "error_code", None) != "view_output_exists":
                    raise
                residual = self.cleanup_files()
                if residual:
                    failure = RuntimeError("Capture file cleanup is unconfirmed; no further names were tried")
                    failure.failure_detail = {"reason": "capture_cleanup_unconfirmed", "residual_files": residual[:8],
                                              "residual_file_count": len(residual)}
                    raise failure
                self.paths.clear()
                continue
            self.capture_number = number
            return paths
        failure = FileExistsError("Capture batch names through _999 are occupied")
        failure.error_class, failure.error_code = "precondition", "view_output_exists"
        failure.retry_action, failure.field_path = "correct_request", "session.artifacts"
        raise failure

    def cleanup_files(self):
        residual = []
        for path in self.paths:
            try:
                os.remove(path)
            except FileNotFoundError:
                pass
            except OSError:
                residual.append(path)
        return residual


class _BoundedRestorationErrors(list):
    """Keep restoration evidence bounded while retaining its total count."""

    limit = 8

    def __init__(self):
        super().__init__()
        self.total_count = 0

    def add(self, message):
        self.total_count += 1
        if len(self) < self.limit:
            self.append(str(message)[:300])

    def merge(self, other):
        self.total_count += getattr(other, "total_count", len(other))
        for message in other:
            if len(self) >= self.limit:
                break
            self.append(message)


def _view_failure_reason(exc):
    """Classify only ordinary render/artifact failures."""
    if not isinstance(exc, RuntimeError):
        return None
    message = str(exc).lower()
    if any(token in message for token in (
        "restoration", "camera pose", "camera state", "model", "geometry", "active document",
        "display mode", "canonical rendering", "tip has no gui",
    )):
        return None
    if "blank" in message:
        return "render_blank"
    if any(token in message for token in (
        "did not create", "missing", "empty", "unreadable", "output image",
    )):
        return "output_unavailable"
    return "render_failed"


def _restore_view_operation(
    document,
    view,
    camera_state,
    previous_active,
    presentation,
):
    """Restore presentation only; the coordinator owns history and certification."""
    errors = _BoundedRestorationErrors()

    def add_error(scope, exc):
        errors.add("%s: %s" % (scope, str(exc)[:200]))

    # Style first and visibility last: native container visibility may
    # propagate to children, while style properties must be restored exactly.
    for name, expected in presentation.items():
        try:
            obj = document.getObject(name)
            if obj is None:
                errors.add("presentation: original object is missing: " + name)
                continue
            for key in ("DisplayModeBody", "DisplayMode", "Transparency"):
                if key in expected and getattr(obj.ViewObject, key) != expected[key]:
                    setattr(obj.ViewObject, key, expected[key])
        except Exception as exc:
            add_error("presentation.%s" % name, exc)
    for name, expected in presentation.items():
        if "Visibility" not in expected:
            continue
        try:
            obj = document.getObject(name)
            if (obj is not None
                    and bool(obj.ViewObject.Visibility) != bool(expected["Visibility"])):
                obj.ViewObject.Visibility = expected["Visibility"]
        except Exception as exc:
            add_error("visibility.%s" % name, exc)
    _restore_camera_state(view, camera_state, errors=errors)
    if previous_active is not None:
        try:
            App.setActiveDocument(previous_active.Name)
        except Exception as exc:
            add_error("active_document", exc)
    else:
        try:
            if App.activeDocument() is not None:
                errors.add("active_document: previous active document was empty")
        except Exception as exc:
            add_error("active_document_readback", exc)

    for name, expected in presentation.items():
        try:
            obj = document.getObject(name)
        except Exception as exc:
            add_error("presentation_lookup.%s" % name, exc)
            continue
        if obj is None:
            continue
        for key, expected_value in expected.items():
            try:
                actual = getattr(obj.ViewObject, key)
            except Exception as exc:
                add_error("presentation_readback.%s.%s" % (name, key), exc)
            else:
                if actual != expected_value:
                    errors.add(
                        "presentation.%s.%s: expected %s, actual %s"
                        % (name, key, expected_value, actual)
                    )
    if view is not None and camera_state is not None:
        try:
            for difference in _camera_state_differences(view, camera_state["numeric"]):
                errors.add(difference)
        except Exception as exc:
            add_error("camera_readback", exc)
    if previous_active is not None:
        try:
            if App.activeDocument() is not previous_active:
                errors.add("active_document: restored document differs")
        except Exception as exc:
            add_error("active_document_readback", exc)
    return errors


def _cleanup_observation_previews(document, scope, previews):
    for preview in previews:
        if preview is None:
            continue
        if not scope.owns_pending_transaction():
            scope.cleanup_errors.add("temporary_objects: transaction ownership unavailable")
            break
        try:
            if document.getObject(preview.Name) is preview:
                document.removeObject(preview.Name)
        except Exception as exc:
            scope.cleanup_errors.add("temporary_object: " + str(exc))
