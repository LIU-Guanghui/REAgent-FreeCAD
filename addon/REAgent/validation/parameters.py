"""Explicit native parameter perturbation with verified restoration."""

import FreeCAD as App

from ..analysis.measurements import _shape_summary, _shape_summary_consistent
from ..runtime import _operation_stage
from ..workspace.model import _is_origin_object, _owned_objects, _result_shape
from ..workspace.properties import _metadata, _property_readback
from ..workspace.recovery import (
    _block,
    _facts,
    _recompute_preserving_history,
    _transaction_ready,
    _verify_restoration,
)
from .structure import _check_result, _structure_audit


def _parametric_audit(group):
    document = group.Document
    _transaction_ready(document)
    if document.RedoNames:
        raise ValueError("Parameter audit cannot erase native redo history")
    results = []
    for obj in _owned_objects(group):
        if _is_origin_object(group, obj):
            continue
        for name in obj.PropertiesList:
            meta = _metadata(obj, name)
            if not meta["writable"] or meta["codec"] not in ("quantity", "integer", "number"):
                continue
            value = getattr(obj, name)
            numeric = float(value.Value) if meta["codec"] == "quantity" else value
            if not isinstance(numeric, (float, int)) or isinstance(numeric, bool) or numeric <= 0:
                continue
            before, undo = _facts(document, group), list(document.UndoNames)
            shape = _result_shape(group)
            signature = _shape_summary(shape) if shape is not None and not shape.isNull() else None
            _operation_stage("parameter_audit", mutating=True)
            document.openTransaction("Audit native property")
            error = None
            detail = {"object": obj.Name, "property": name, "property_type": meta["type_id"],
                      "step": "parameter_construct"}
            try:
                perturbed = numeric + 1 if meta["codec"] == "integer" else numeric * 1.001
                if meta["codec"] == "quantity":
                    perturbed = App.Units.Quantity(perturbed, value.Unit)
                detail["step"] = "parameter_setter"
                setattr(obj, name, perturbed)
                detail["step"] = "parameter_readback"
                requested, actual = _property_readback(obj, name, meta["codec"], perturbed)
                detail["requested"] = requested
                detail["actual"] = actual
                if actual != requested:
                    detail["reason"] = "native_value_adjusted"
                    raise ValueError(
                        "Native parameter setter changed the requested value"
                    )
                detail["step"] = "parameter_recompute"
                document.recompute()
                detail["step"] = "parameter_result_check"
                _check_result(group)
                if not _structure_audit(group)["passed"]:
                    raise ValueError("Native objects failed after parameter edit")
            except Exception as exc:
                error = str(exc)[:500]
                detail["exception_type"] = type(exc).__name__
            finally:
                try:
                    document.abortTransaction()
                    _recompute_preserving_history(document)
                    _verify_restoration(document, group, before,
                        [(obj.Name, name, meta["codec"], meta["value"])], undo)
                    shape = _result_shape(group)
                    restored = _shape_summary(shape) if shape is not None and not shape.isNull() else None
                    if not _shape_summary_consistent(signature, restored):
                        raise RuntimeError("Result summary changed after abort")
                except Exception as exc:
                    failure = _block(document, "Parameter audit recovery unverified")
                    failure.failure_detail = detail
                    failure.diagnostics = {"kind": "recovery", "original_error": error,
                                           "recovery_error": str(exc)[:500]}
                    raise failure from exc
            results.append({"object": obj.Name, "property": name, "status": "failed" if error else "passed",
                            "error": error, "detail": detail if error else None, "restored": True})
            break
    return {"status": "failed" if any(r["error"] for r in results) else "passed" if results else "not_applicable",
            "features": results, "property_count": len(results), "restoration": {"restored": True},
            "scope": "one_supported_positive_scalar_per_object"}
