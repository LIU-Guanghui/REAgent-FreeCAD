"""Native restoration evidence, write blocking and history-preserving recovery."""

import FreeCAD as App
import FreeCADGui as Gui

from REAgent.host.analysis.measurements import summarize_shape
from REAgent.host.process_state import operation_stage, PROCESS
from REAgent.host.validation.structure import structure_audit
from REAgent.host.view.presentation import presentation_snapshot
from REAgent.host.workspace.model import owned_objects, placement_summary, session_bodies, copy_world_shape
from REAgent.host.workspace.properties import encode
from REAgent.host.workspace.state import target_geometry_state


def facts(document, group):
    """Small restoration evidence, not a model snapshot or replay payload."""
    sketches = {obj.Name: {"geometry_count": obj.GeometryCount,
                           "constraint_count": obj.ConstraintCount}
                for obj in owned_objects(group)
                if obj.TypeId == "Sketcher::SketchObject"}
    return {"objects": [obj.Name for obj in document.Objects],
            "groups": {obj.Name: [child.Name for child in obj.Group] for obj in owned_objects(group)
                       if obj is group or obj.TypeId == "PartDesign::Body"},
            "presentation": presentation_snapshot(owned_objects(group)),
            "placements": {obj.Name: placement_summary(obj)
                           for obj in owned_objects(group) if hasattr(obj, "Placement")},
            "registered_bodies": list(group.REAgentBodyNames),
            "result": group.Tip.Name if group.Tip else None,
            "result_shape": target_geometry_state(group),
            "tips": {obj.Name: obj.Tip.Name if obj.Tip else None for obj in owned_objects(group)
                     if obj.TypeId == "PartDesign::Body"},
            "body_shapes": {body.Name: (
                summarize_shape(copy_world_shape(body))
                if getattr(body, "Shape", None) is not None and not body.Shape.isNull()
                else None)
                for body in session_bodies(group)},
            "sketches": sketches}


def fact_differences(expected, actual, field="facts"):
    """Report existing restoration invariants without changing their equality semantics."""
    if expected == actual:
        return []
    if isinstance(expected, dict) and isinstance(actual, dict):
        rows = []
        for key in sorted(set(expected) | set(actual)):
            if key not in expected or key not in actual:
                rows.append({"field": field + "." + key, "reason": "missing_field",
                             "expected_present": key in expected, "actual_present": key in actual})
            else:
                rows.extend(fact_differences(expected[key], actual[key], field + "." + key))
        return rows
    if isinstance(expected, list) and isinstance(actual, list):
        rows = []
        for index in range(max(len(expected), len(actual))):
            path = field + "[%d]" % index
            if index >= len(expected) or index >= len(actual):
                rows.append({"field": path, "reason": "missing_item",
                             "expected": expected[index] if index < len(expected) else None,
                             "actual": actual[index] if index < len(actual) else None})
            else:
                rows.extend(fact_differences(expected[index], actual[index], path))
        return rows
    def compact(value):
        if isinstance(value, list):
            return {"items": value[:16], "count": len(value)}
        if isinstance(value, str):
            return value[:300]
        return value
    return [{"field": field, "expected": compact(expected), "actual": compact(actual)}]


def block_document_writes(document, reason):
    PROCESS.AUDIT_BLOCKS[document.Name] = (document, {"reason": reason, "restored": False})
    exc = RuntimeError(reason)
    exc.target_state_unknown = True
    exc.operation_stage = "recovery"
    exc.write_started = True
    return exc


def preflight_error(message, reason, **detail):
    error = ValueError(message)
    error.failure_detail = {"reason": reason, **detail}
    return error


def transaction_ready(document):
    operation_stage("bridge_preflight")
    if App.activeDocument() is not document:
        raise ValueError("Target must be the active document in its bound window")
    pending = [doc.Name for doc in App.listDocuments().values() if doc.HasPendingTransaction]
    if pending:
        raise preflight_error("A user transaction is active", "pending_transaction",
                               pending_documents=pending[:8], pending_transaction=True)
    gui_document = Gui.activeDocument()
    if gui_document is not None and gui_document.getInEdit() is not None:
        raise preflight_error("Finish or cancel the native edit in Target",
                               "gui_edit_in_progress", pending_transaction=False)
    if not document.UndoMode:
        raise ValueError("Native undo is disabled for this document")


def recompute_preserving_history(document):
    """Recompute restored dependencies without creating or consuming history."""
    if document.HasPendingTransaction:
        raise RuntimeError("Cannot finalize history during an open transaction")
    undo, redo = list(document.UndoNames), list(document.RedoNames)
    operation_stage("recompute", mutating=True)
    count = document.recompute()
    if not isinstance(count, int) or isinstance(count, bool) or count < 0:
        raise RuntimeError("Native recompute returned an invalid object count")
    if document.HasPendingTransaction or list(document.UndoNames) != undo or list(document.RedoNames) != redo:
        raise RuntimeError("Recompute changed the native history stack")
    return count


def verify_restoration(document, group, before, changes, undo_before):

    current = facts(document, group)
    mismatches = fact_differences(before, current)
    if document.HasPendingTransaction:
        mismatches.append({"field": "pending_transaction", "expected": False, "actual": True})
    if list(document.UndoNames) != undo_before:
        mismatches.extend(fact_differences(undo_before, list(document.UndoNames), "undo"))
    if mismatches:
        failure = block_document_writes(document, "Native rollback did not restore: "
                         + ", ".join(item["field"] for item in mismatches[:8]))
        failure.failure_detail = {"step": "restoration_check", "differences": mismatches[:16],
                                  "difference_count": len(mismatches),
                                  "differences_omitted": max(0, len(mismatches)-16),
                                  "redo": list(document.RedoNames)[:16]}
        PROCESS.AUDIT_BLOCKS[document.Name][1]["detail"] = failure.failure_detail
        raise failure
    audit = structure_audit(group)
    if not audit["passed"]:
        raise block_document_writes(document, "Restored objects are not ready: " + repr(audit["failures"]))
    seen = set()
    for name, prop, codec, original in changes:
        if name not in before["objects"] or (name, prop) in seen:
            continue
        seen.add((name, prop))
        if encode(getattr(document.getObject(name), prop), codec) != original:
            raise block_document_writes(document, "Native rollback did not restore " + name + "." + prop)
