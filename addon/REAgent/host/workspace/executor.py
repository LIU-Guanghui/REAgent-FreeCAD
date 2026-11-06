"""Internal transaction, recovery and result-state primitives for native commands.

This module is not a Bridge method or a public modeling language. Domain
commands supply their own bounded preflight and native operation."""

import uuid

from REAgent.host.analysis.measurements import _shape_summary, _shape_summary_consistent
from REAgent.host.process_state import _operation_stage, _stamp_failure, PROCESS
from REAgent.host.validation.structure import result_state
from REAgent.host.view.presentation import _present_result, _presentation_snapshot, _restore_presentation
from REAgent.host.workspace.model import _body_objects, _owned_objects, _result_display_plan, _session_bodies, _target_state, _world_shape
from REAgent.host.workspace.recovery import _block, _facts, _preflight_error, _recompute_preserving_history, _transaction_ready, _verify_restoration
from REAgent.host.workspace.state import _target_geometry_state, _target_view_revision


def _body_shape_measurements(group):
    return {body.Name: _shape_summary(_world_shape(body))
            if not body.Shape.isNull() else None for body in _session_bodies(group)}


def _body_measurements_consistent(expected, actual):
    return expected.keys() == actual.keys() and all(
        _shape_summary_consistent(value, actual[name]) for name, value in expected.items())


def _body_edit_preflight(document, group, body_name):
    """Current feature commands do not propagate edits into another Body."""
    result_state(group)  # Structural validity is required; in_progress is allowed.
    body = document.getObject(body_name)
    if body is None:  # The domain preflight has checked new Body creation.
        return
    if body not in _session_bodies(group):
        raise ValueError("Receiving Body is not owned by this session")
    members = _body_objects(body)
    for other in _owned_objects(group):
        if other not in members and any(linked in members for linked in other.OutList):
            raise ValueError("Cross-Body downstream editing is not supported: "
                             + body.Name + " is referenced by " + other.Name)


def _check_body_isolation(group, before, body_name):
    """Check every Body outside the receiving scope against transaction facts."""
    bodies = _session_bodies(group)
    if {body.Name for body in bodies} != set(before["body_shapes"]) | {body_name}:
        raise RuntimeError("Native command changed registered Body scope")
    measurements = _body_shape_measurements(group)
    for body in bodies:
        if body.Name == body_name:
            continue
        if (body.Tip.Name if body.Tip else None) != before["tips"][body.Name]:
            raise RuntimeError("Native command changed another Body Tip: " + body.Name)
        expected = before["result_shape"] if body is group else before["body_shapes"][body.Name]
        actual = _target_geometry_state(group) if body is group else measurements[body.Name]
        if not _shape_summary_consistent(expected, actual):
            raise RuntimeError("Native command changed another Body geometry: " + body.Name)


def _finish_presentation(document, group, state, bodies, tips, *, preserve_history,
                         recompute_touched=True):
    """Finalize a real display change once, without masking persistent errors."""
    if recompute_touched and any("touched" in {str(value).lower() for value in obj.State}
           for obj in _owned_objects(group)):
        if preserve_history:
            _recompute_preserving_history(document)
        else:
            _operation_stage("recompute", mutating=True)
            count = document.recompute()
            if type(count) is not int or count < 0:
                raise RuntimeError("Display recompute returned an invalid object count")
    _operation_stage("result_check")
    actual = result_state(group)
    if (actual["state"] != state["state"]
            or not _shape_summary_consistent(state["result_geometry"], actual["result_geometry"])
            or not _body_measurements_consistent(bodies, _body_shape_measurements(group))
            or tips != {body.Name: body.Tip.Name if body.Tip else None
                        for body in _session_bodies(group)}):
        raise RuntimeError("Presentation changed native Result, Body geometry or Tip")
    return actual


def _capture_native_failure(exc, group):
    """Read bounded native diagnostics before abort; never solve or mutate here."""
    try:
        objects = _owned_objects(group)
        objects.sort(key=lambda obj: not any(str(s).lower() in ("invalid", "error", "touched")
                                            for s in obj.State))
        rows = []
        for obj in objects[:16]:
            row = {"object": obj.Name, "type_id": obj.TypeId}
            try:
                row["state"] = [str(s) for s in obj.State][:8]
                try:
                    row["native_status"] = str(obj.getStatusString())[:300]
                except Exception as status_error:
                    row["status_unavailable"] = str(status_error)[:120]
                for prop in ("Profile", "BaseFeature", "Tip"):
                    if prop in obj.PropertiesList:
                        value = getattr(obj, prop)
                        linked = value[0] if isinstance(value, tuple) else value
                        row[prop] = linked.Name if linked is not None else None
                if "Originals" in obj.PropertiesList:
                    originals = list(obj.Originals)
                    row["Originals"] = [
                        linked.Name for linked in originals[:8]
                    ]
                    row["originals_omitted"] = max(0, len(originals) - 8)
                if obj.TypeId == "Sketcher::SketchObject":
                    row["geometry_count"] = obj.GeometryCount
                    row["constraint_count"] = obj.ConstraintCount
                    row["fully_constrained"] = bool(obj.FullyConstrained)
            except Exception as read_error:
                row["diagnostic_error"] = str(read_error)[:200]
            rows.append(row)
        detail = dict(getattr(exc, "failure_detail", {}))
        detail["native_objects"] = rows
        detail["objects_omitted"] = max(0, len(objects)-16)
        exc.failure_detail = detail
    except Exception:
        # The original error and mandatory recovery always take priority.
        pass


def _record_failed_operation_recovery(document, exc):
    """Record a confirmed native document rollback."""
    detail = dict(getattr(exc, "failure_detail", {}) or {})
    detail["document_restored"] = True
    exc.failure_detail = detail
    exc.restored = True


def _committed_history_retention(transaction, undo_before, undo_after, redo_after):
    """Accept only an intact commit or FreeCAD's one-oldest-entry eviction."""
    if redo_after:
        return False, None
    expected = [transaction, *undo_before]
    if undo_after == expected:
        return True, None
    if undo_before and undo_after == [transaction, *undo_before[:-1]]:
        return True, undo_before[-1]
    return False, None


class ExecutorOperations:
    """Session-owned native history and verified rollback, shared with audit."""

    def _native_store(self):
        if not hasattr(self, "_native_history"):
            self._native_history = {}
        return self._native_history

    def _native_context(self, params):
        document, source, group, _config = _target_state(params["document"])
        self._native_store()
        return document, source, group

    def _new_transaction(self, document, session_id):
        _transaction_ready(document)
        redo = list(document.RedoNames)
        foreign = [name for name in redo if not self._owns(document, session_id, name)]
        if foreign:
            raise _preflight_error("Native redo branch contains entries outside this session",
                "foreign_redo", redo_top=redo[0][:160], redo_count=len(redo),
                session_owned_count=len(redo)-len(foreign), unowned_entries=[name[:160] for name in foreign[:8]],
                pending_transaction=False)
        name = "REAgent " + uuid.uuid4().hex
        _operation_stage("native_execution", mutating=True)
        document.openTransaction(name)
        return name, redo

    def _owns(self, document, session_id, transaction):
        row = self._native_store().get((document.Name, transaction), {})
        return row.get("document") is document and row.get("session_id") == session_id

    def _execute_native_command(self, params, *, command, preflight, mutate,
                                readback, edit=None):
        """Run one dedicated internal command; callbacks are not Bridge inputs.

        The command adapter must validate its own references and parameters in
        preflight. The Controller must establish that evidence IDs belong to this session.
        Registered domain commands share this transaction and recovery path.
        """

        if not isinstance(command, str) or not command or not all(
                callable(callback) for callback in (preflight, mutate, readback)):
            raise ValueError("A dedicated native command and callbacks are required")
        if not isinstance(params.get("evidence"), list) or not params["evidence"]:
            raise ValueError("Native command requires observation evidence")
        document, source, group, config = _target_state(params["document"])
        binding = PROCESS.SESSION_BINDINGS.get(params["session_id"])
        if (binding is None or binding.get("document") is not document
                or binding.get("role") != "rebuild"):
            raise ValueError("Native command requires the exact bound Target session")
        if source.SourceSHA256 != binding["source_revision"]:
            raise ValueError("Source STEP revision differs from this session; start a new session for the chosen source")
        self._native_store()
        _transaction_ready(document)
        revision_before = _target_view_revision(document, group, config)
        if params.get("expected_revision") != revision_before:
            raise ValueError("Target revision changed; re-observe before writing")
        before = _facts(document, group)
        undo_before, redo_before = list(document.UndoNames), list(document.RedoNames)
        _operation_stage("command_preflight")
        try:
            preflight(document, group, params)
            if edit is None:
                _body_edit_preflight(document, group, params["body_name"])
        except Exception as exc:
            try:
                unchanged = (_facts(document, group) == before
                             and list(document.UndoNames) == undo_before
                             and list(document.RedoNames) == redo_before
                             and not document.HasPendingTransaction
                             and _target_view_revision(document, group, config) == revision_before)
            except Exception:
                unchanged = False
            if not unchanged:
                raise _block(document, "Native command preflight recovery is unknown") from exc
            raise
        if (_facts(document, group) != before or list(document.UndoNames) != undo_before
                or list(document.RedoNames) != redo_before or document.HasPendingTransaction
                or _target_view_revision(document, group, config) != revision_before):
            raise _block(document, "Native command preflight changed document state")
        if edit is not None and not edit.has_changes:
            state = result_state(group)
            return {"command": command, "status": "unchanged", "committed": False,
                    "revision_before": revision_before, "revision_after": revision_before,
                    "state": state["state"], "result_geometry": state["result_geometry"],
                    "readback": readback(document, group, edit.target, state)}
        changes = []
        transaction, abandoned_redo = self._new_transaction(document, params["session_id"])
        committed = False
        try:
            _operation_stage("native_execution", mutating=True)
            created = mutate(document, group, params, changes)
            _operation_stage("recompute", mutating=True)
            count = document.recompute()
            if not isinstance(count, int) or isinstance(count, bool) or count < 0:
                raise RuntimeError("Native recompute returned an invalid object count")
            _operation_stage("result_check")
            if edit is not None:
                edit.check_references(document)
            state = result_state(group)
            body_measurements = _body_shape_measurements(group)
            if edit is None:
                _check_body_isolation(group, before, params["body_name"])
            else:
                edit.check_scope(document, group, before)
            revision_after_mutation = _target_view_revision(document, group, config)
            observed = readback(document, group, created, state)
            if not isinstance(observed, dict):
                raise ValueError("Native readback must return bounded command facts")
            if _target_view_revision(document, group, config) != revision_after_mutation:
                raise RuntimeError("Native readback changed persistent model state")
            after_readback = result_state(group)
            bodies_after_readback = _body_shape_measurements(group)
            if (after_readback["state"] != state["state"] or not _shape_summary_consistent(
                    state["result_geometry"], after_readback["result_geometry"])
                    or not _body_measurements_consistent(body_measurements, bodies_after_readback)):
                raise RuntimeError("Result geometry measurements changed during readback")
            if "body_shape" in observed and (observed.get("body") not in bodies_after_readback
                    or not _shape_summary_consistent(bodies_after_readback[observed["body"]], observed["body_shape"])):
                raise RuntimeError("Receiving Body readback disagrees with world geometry measurements")
            state = after_readback
            tips_after_readback = {body.Name: body.Tip.Name if body.Tip else None
                                   for body in _session_bodies(group)}
            if state["state"] == "solid_ready":
                _present_result(_owned_objects(group), _result_display_plan(group))
            presentation_after = _presentation_snapshot(_owned_objects(group))
            state = _finish_presentation(document, group, state, bodies_after_readback,
                                         tips_after_readback, preserve_history=False)
            revision_final = _target_view_revision(document, group, config)
            if revision_final != revision_after_mutation:
                raise RuntimeError("Final presentation changed persistent modeling properties")
            observed = readback(document, group, created, state)
            if (not isinstance(observed, dict)
                    or _target_view_revision(document, group, config) != revision_final):
                raise RuntimeError("Final native readback changed persistent model state")
            if "body_shape" in observed and (observed.get("body") not in bodies_after_readback
                    or not _shape_summary_consistent(
                        _body_shape_measurements(group)[observed["body"]], observed["body_shape"])):
                raise RuntimeError("Final receiving Body readback disagrees with world geometry")
            state = _finish_presentation(document, group, state, bodies_after_readback,
                                         tips_after_readback, preserve_history=False,
                                         recompute_touched=False)
            if _presentation_snapshot(_owned_objects(group)) != presentation_after:
                raise RuntimeError("Final native display changed during readback")
            if edit is None:
                _check_body_isolation(group, before, params["body_name"])
            else:
                edit.check_references(document)
                edit.check_scope(document, group, before)
            if _target_view_revision(document, group, config) != revision_final:
                raise RuntimeError("Final precommit checks changed persistent model state")
            _operation_stage("native_commit", mutating=True)
            document.commitTransaction()
            committed = True
            confirmed, trimmed = _committed_history_retention(
                transaction, undo_before, list(document.UndoNames), list(document.RedoNames)
            )
            if not confirmed:
                raise _block(document, "Committed native history could not be confirmed")
            undo_committed, redo_committed = list(document.UndoNames), list(document.RedoNames)
            if trimmed is not None:
                self._native_history.pop((document.Name, trimmed), None)
            self._native_history[(document.Name, transaction)] = {
                "session_id": params["session_id"], "document": document,
                "presentation_before": before["presentation"],
                "presentation_after": presentation_after,
                **({"edit_revisions": (revision_before, revision_final),
                    "edit_shapes": (edit.geometry_before, edit.geometry_after)} if edit is not None else {}),
            }
            for name in abandoned_redo:
                self._native_history.pop((document.Name, name), None)
            _operation_stage("result_check")
            committed_state = result_state(group)
            committed_revision = _target_view_revision(document, group, config)
            if (committed_revision != revision_final
                    or committed_state["state"] != state["state"]
                    or not _shape_summary_consistent(state["result_geometry"], committed_state["result_geometry"])
                    or not _body_measurements_consistent(bodies_after_readback, _body_shape_measurements(group))
                    or _presentation_snapshot(_owned_objects(group)) != presentation_after):
                raise RuntimeError("Committed command differs from final native readback")
            observed_committed = readback(document, group, created, committed_state)
            if (not isinstance(observed_committed, dict)
                    or _target_view_revision(document, group, config) != committed_revision
                    or _presentation_snapshot(_owned_objects(group)) != presentation_after):
                raise RuntimeError("Committed command readback changed model or presentation")
            result_state(group)
            if not _body_measurements_consistent(bodies_after_readback, _body_shape_measurements(group)):
                raise RuntimeError("Committed readback changed Body geometry")
            if edit is not None and edit.geometry(group) != edit.geometry_after:
                raise RuntimeError("Committed readback changed downstream geometry")
            if (document.HasPendingTransaction or list(document.UndoNames) != undo_committed
                    or list(document.RedoNames) != redo_committed):
                raise RuntimeError("Committed readback changed native history")
            observed = observed_committed
            return {"command": command, "status": "committed", "transaction": transaction,
                    "result_body": group.Name, "tip_before": before["result"],
                    "tip": group.Tip.Name if group.Tip else None,
                    "revision_before": revision_before,
                    "revision_after": committed_revision,
                    "state": state["state"], "result_geometry": state["result_geometry"],
                    "readback": observed,
                    "abandoned_redo_transactions": abandoned_redo}
        except Exception as exc:
            _stamp_failure(exc)
            if committed:
                exc.committed_side_effect = True
                exc.failure_detail = {**dict(getattr(exc, "failure_detail", {}) or {}),
                                      "committed": True, "transaction": transaction,
                                      "original_stage": exc.operation_stage}
            if not getattr(exc, "target_state_unknown", False):
                _capture_native_failure(exc, group)
            if getattr(exc, "target_state_unknown", False):
                raise
            if committed:
                failure = _block(document, "Committed native command postcheck failed")
                failure.committed_side_effect = True
                failure.failure_detail = {**exc.failure_detail, "original_error": str(exc)[:300]}
                raise failure from exc
            try:
                document.abortTransaction()
                _recompute_preserving_history(document)
                _restore_presentation(_owned_objects(group), before["presentation"])
                if any("touched" in {str(value).lower() for value in obj.State}
                       for obj in _owned_objects(group)):
                    _recompute_preserving_history(document)
                _verify_restoration(document, group, before, changes, undo_before)
                if edit is not None and (_target_view_revision(document, group, config) != revision_before
                        or edit.geometry(group) != edit.geometry_before):
                    raise RuntimeError("Edited properties, Sketch contents or downstream geometry did not restore")
                redo_after = list(document.RedoNames)
                if redo_after not in (redo_before, []):
                    raise RuntimeError("Native redo branch was partly changed")
            except Exception as restore_error:
                failure = _block(document, "Native command recovery could not be confirmed")
                failure.diagnostics = {"kind": "recovery", "original_error": str(exc)[:500],
                                       "recovery_error": str(restore_error)[:500]}
                raise failure from exc
            exc.failure_detail = {**dict(getattr(exc, "failure_detail", {}) or {}),
                                  "model_restored": True, "redo_changed": redo_before != redo_after}
            if redo_before and not redo_after:
                exc.committed_side_effect = True
                for name in redo_before:
                    self._native_history.pop((document.Name, name), None)
            _record_failed_operation_recovery(document, exc)
            raise


    def _history(self, params, redo):
        document, source, group = self._native_context(params)
        _transaction_ready(document)
        stack = list(document.RedoNames if redo else document.UndoNames)
        expected = params["transaction"]
        top = stack[0] if stack else None
        session_id = params["session_id"]
        owned = top is not None and self._owns(document, session_id, top)
        if top != expected or not owned:
            known_top = top is not None and self._native_history.get((document.Name, top), {}).get("document") is document
            raise _preflight_error(
                "Requested transaction is unavailable at the native " + ("redo" if redo else "undo") + " stack top",
                "empty_stack" if top is None else "stack_top_mismatch" if top != expected else "transaction_not_owned",
                expected=expected[:160], top=top[:160] if top else None,
                top_ownership="session" if owned else "other" if known_top else "unknown",
                pending_transaction=False)
        history = self._native_history[(document.Name, expected)]
        if "edit_revisions" in history:
            from REAgent.host.features.edit import EditScope
            current = 0 if redo else 1
            if (_target_view_revision(document, group, group) != history["edit_revisions"][current]
                    or EditScope.geometry(group) != history["edit_shapes"][current]):
                raise _preflight_error("Current model differs from the recorded edit history boundary",
                                       "history_state_changed", expected=expected[:160], top=top[:160],
                                       top_ownership="session", pending_transaction=False)
        result = self._move_history(document, group, expected, redo)
        return {
            "status": "redone" if redo else "undone",
            "state": result["state"], "result_geometry": result["result_geometry"],
            "transaction": expected,
            "revision_after": result["revision_after"],
            "result": group.Tip.Name if group.Tip else None,
            "result_body": group.Name,
            "tip": group.Tip.Name if group.Tip else None,
        }

    def _move_history(self, document, group, name, redo):
        detail = {"step": "history_apply", "direction": "redo" if redo else "undo",
                  "history_moved": False, "transaction": name}
        undo_before, redo_before = list(document.UndoNames), list(document.RedoNames)
        try:
            _operation_stage("native_execution", mutating=True)
            document.redo() if redo else document.undo()
            detail["step"] = "history_stack_check"
            expected_undo = [name, *undo_before] if redo else undo_before[1:]
            expected_redo = redo_before[1:] if redo else [name, *redo_before]
            if list(document.UndoNames) != expected_undo or list(document.RedoNames) != expected_redo:
                raise RuntimeError("Native history did not move the expected transaction")
            detail["history_moved"] = True
            detail["step"] = "recompute"
            detail["recomputed_objects"] = _recompute_preserving_history(document)
            revision_before_display = _target_view_revision(document, group, group)
            state_before_display = result_state(group)
            bodies_before_display = _body_shape_measurements(group)
            tips_before_display = {body.Name: body.Tip.Name if body.Tip else None
                                   for body in _session_bodies(group)}
            presentation = self._native_history.get((document.Name, name))
            if presentation is None:
                raise RuntimeError("Native history has no presentation snapshot")
            _restore_presentation(
                _owned_objects(group),
                presentation[
                    "presentation_after" if redo else "presentation_before"
                ],
            )
            detail["step"] = "result_check"
            _operation_stage("result_check")
            summary = _finish_presentation(
                document, group, state_before_display, bodies_before_display,
                tips_before_display, preserve_history=True)
            if _presentation_snapshot(_owned_objects(group)) != presentation[
                    "presentation_after" if redo else "presentation_before"]:
                raise RuntimeError("History display changed after final recompute")
            detail["step"] = "history_postcheck"
            if document.HasPendingTransaction or list(document.UndoNames) != expected_undo or list(document.RedoNames) != expected_redo:
                raise RuntimeError("History postconditions changed during result checking")
            if _target_view_revision(document, group, group) != revision_before_display:
                raise RuntimeError("History presentation changed persistent modeling properties")
            if "edit_revisions" in presentation:
                from REAgent.host.features.edit import EditScope
                index = int(redo)
                if (revision_before_display != presentation["edit_revisions"][index]
                        or EditScope.geometry(group) != presentation["edit_shapes"][index]):
                    raise RuntimeError("History move did not restore recorded edit properties and geometry")
            return {**summary, "revision_after": revision_before_display}
        except Exception as exc:
            _stamp_failure(exc)
            detail["exception_type"] = type(exc).__name__
            detail["original_stage"] = exc.operation_stage
            failure = _block(document, "Native history could not be finalized: " + str(exc))
            failure.operation_stage = exc.operation_stage
            failure.failure_detail = detail
            PROCESS.AUDIT_BLOCKS[document.Name][1]["detail"] = detail
            try:
                states = [{"object": obj.Name, "state": list(obj.State)}
                          for obj in _owned_objects(group)
                          if any(str(s).lower() in ("touched", "invalid", "error") for s in obj.State)]
                detail.update(object_states=states[:16], objects_omitted=max(0, len(states)-16))
            except Exception as diagnostic_error:
                detail["state_read_error"] = str(diagnostic_error)[:200]
            raise failure from exc

    def _undo(self, params): return self._history(params, False)
    def _redo(self, params): return self._history(params, True)

    def contribute(self):
        return {'agent_undo': self._undo, 'agent_redo': self._redo}
