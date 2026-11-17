"""Owned observation lifetime and final recovery certification."""

import os
import tempfile
import uuid

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtGui

from REAgent.host.process_state import operation_stage, stamp_failure, PROCESS
from REAgent.protocol.artifacts import ARTIFACT_NUMBER_LIMIT, artifact_filename
from REAgent.host.validation.tolerance import ir_tolerance_contract, source_tolerance_contract
from REAgent.host.view.camera import camera_state_differences, capture_complete_camera_state, restore_camera_state
from REAgent.host.workspace.model import compact_json, source_objects, workspace
from REAgent.host.workspace.recovery import block_document_writes, fact_differences, recompute_preserving_history, transaction_ready
from REAgent.host.workspace.state import document_history_state, geometry_differences, presentation_state, source_geometry_state, source_model_state, target_geometry_state, target_model_state


def _observation_baseline(document, group, source):
    model = (target_model_state(document, group, include_history=False) if group
             else source_model_state(source, include_history=False))
    geometry = target_geometry_state(group) if group else source_geometry_state(source)
    tolerance = ir_tolerance_contract(group) if group else source_tolerance_contract(source)
    view = Gui.activeDocument().activeView()
    return {"model": model, "geometry": geometry, "tolerance": tolerance, "view": view,
            "camera": capture_complete_camera_state(view), "display": presentation_state(document),
            "history": document_history_state(document)}


def _observation_readback(document, group, source, before, history_owned, errors):
    recovery = {"checked_at": "after_observation_scope_exit",
                "model_unchanged": None, "result_geometry_unchanged": None,
                "history_restored": None, "presentation_restored": None}
    try:
        differences = fact_differences(before["model"],
            target_model_state(document, group, include_history=False) if group
            else source_model_state(source, include_history=False), "model")
        recovery["model_unchanged"] = not differences
        for item in differences[:8]:
            errors.add("model: " + item["field"] + ": " + item.get("reason", "value_changed"))
    except Exception as exc:
        errors.add("model: final state readback failed: " + str(exc)[:180])
    try:
        differences = geometry_differences(before["geometry"],
            target_geometry_state(group) if group else source_geometry_state(source), before["tolerance"])
        recovery["result_geometry_unchanged"] = not differences
        for item in differences[:8]:
            errors.add("geometry: " + compact_json(item))
    except Exception as exc:
        errors.add("geometry: final Result readback failed: " + str(exc)[:180])
    try:
        same_history = document_history_state(document) == before["history"] and App.getActiveTransaction() is None
        recovery["history_restored"] = same_history if history_owned else None
        if not same_history:
            recovery["history_restored"] = False
            errors.add("history: final undo/redo/pending/application transaction differs from entry")
    except Exception as exc:
        errors.add("history: final readback failed: " + str(exc)[:180])
    try:
        differences = camera_state_differences(before["view"], before["camera"]["numeric"])
        presentation_same = (App.activeDocument() is document
                             and presentation_state(document) == before["display"] and not differences)
        recovery["presentation_restored"] = presentation_same
        for difference in differences:
            errors.add(difference)
        if not presentation_same:
            errors.add("presentation: final display/camera/active document differs from entry")
    except Exception as exc:
        errors.add("presentation_readback: " + str(exc)[:180])
    return recovery


class ObservationScopeOperations:
    def isolated_observation(self, document, render, params, *, temporary=True):
        """Certify one observation only after its owned transient scope exits."""
        if temporary:
            transaction_ready(document)
        elif App.activeDocument() is not document or any(
                doc.HasPendingTransaction for doc in App.listDocuments().values()):
            raise ValueError("Observation requires the bound active document without a user transaction")
        if temporary and document.RedoNames:
            raise ValueError("Temporary observation cannot erase a native redo branch")
        if App.getActiveTransaction() is not None:
            raise ValueError("Finish the application transaction before an observation")
        group = workspace(document) if PROCESS.INSTANCE_ROLE == "target" else None
        source = None if group else source_objects(document.Name)[1]
        before = _observation_baseline(document, group, source)
        view = before["view"]
        scope = _ObservationScope(document, temporary)
        final_errors = _BoundedRestorationErrors()
        error, value = None, None
        history_ownership_confirmed = True
        App.addDocumentObserver(scope)
        try:
            operation_stage("observation", mutating=True)
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
            error = stamp_failure(exc)
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
                            recompute_preserving_history(document)
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
                    document, view, before["camera"], document, before["display"])
        except Exception as exc:
            presentation_attempt.add("presentation: restore attempt failed: " + str(exc))
        try:
            QtGui.QApplication.processEvents(
                QtCore.QEventLoop.ExcludeUserInputEvents | QtCore.QEventLoop.ExcludeSocketNotifiers)
        except Exception as exc:
            final_errors.add("presentation: final refresh failed: " + str(exc))
            if error is None:
                error = stamp_failure(exc)
        try:
            App.removeDocumentObserver(scope)
        except Exception as exc:
            history_ownership_confirmed = False
            final_errors.add("history: observer removal failed: " + str(exc))
        if scope.unexpected_history_event:
            history_ownership_confirmed = False
            final_errors.add("history: unexpected event during observation or final cleanup")
        scope.phase = "exited"
        recovery = _observation_readback(document, group, source, before, history_ownership_confirmed, final_errors)
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
                failure = block_document_writes(document, "Observation durable recovery is unconfirmed")
                detail["target_writes_blocked"] = True
                PROCESS.AUDIT_BLOCKS[document.Name] = (document, detail)
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
            raise stamp_failure(failure) from error
        cleanup = scope.cleanup_temporary_images()
        return {**value, "restored": True, "recovery": recovery, "image_cleanup": cleanup}


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
        self.temporary_paths = []
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

    def create_preview(self, name, type_id="Part::Feature"):
        self.assert_render_safe()
        preview = self.document.addObject(type_id, name)
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
        for path in (*self.paths, *self.temporary_paths):
            try:
                os.remove(path)
            except FileNotFoundError:
                pass
            except OSError:
                residual.append(path)
        return residual

    def temporary_image_path(self, output_dir):
        """One exclusively owned native frame, reused but never registered."""
        if self.temporary_paths:
            return self.temporary_paths[0]
        descriptor, path = tempfile.mkstemp(prefix=".reagent-" + self.capture_id + "-", suffix=".png", dir=output_dir)
        self.temporary_paths.append(path)
        os.close(descriptor)
        return path

    def cleanup_temporary_images(self):
        rows = []
        for path in self.temporary_paths:
            try:
                os.remove(path)
                status = "deleted"
            except FileNotFoundError:
                status = "missing"
            except OSError:
                status = "retained"
            rows.append({"path": path, "status": status})
        return {"status": "partial" if any(row["status"] == "retained" for row in rows) else "completed",
                "candidate_count": len(rows), "deleted_count": sum(row["status"] == "deleted" for row in rows),
                "retained_count": sum(row["status"] == "retained" for row in rows), "files": rows}


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
    restore_camera_state(view, camera_state, errors=errors)
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
            for difference in camera_state_differences(view, camera_state["numeric"]):
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


def cleanup_observation_previews(document, scope, previews):
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
