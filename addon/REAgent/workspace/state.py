"""Read-only persistent model, geometry and revision facts."""

import math

from ..analysis.measurements import _shape_summary
from ..analysis.topology import _stable_digest
from .model import _owned_objects, _placement_summary, _result_shape
from .properties import _CODECS, _encode, sketch_content_state


def _source_view_revision(source):
    revision = getattr(source, "SourceSHA256", None)
    if not isinstance(revision, str) or not revision:
        raise ValueError("Source has no STEP SHA-256 revision")
    return revision


def _document_history_state(document):
    return {
        "pending_transaction": bool(document.HasPendingTransaction),
        "undo": list(document.UndoNames),
        "redo": list(document.RedoNames),
    }


def _source_model_state(source, *, include_history=True):
    document = getattr(source, "Document", None)
    return {
        "revision": _source_view_revision(source),
        "placement": _placement_summary(source),
        "document_objects": (
            [{"name": obj.Name, "type_id": obj.TypeId} for obj in document.Objects]
            if document is not None else None
        ),
        "history": _document_history_state(document) if document is not None and include_history else None,
    }


def _source_geometry_state(source):
    return _shape_summary(source.Shape)


def _target_model_state(document, rebuild, *, include_history=True):
    """Capture persistent model facts; never include derived Shape or GUI state."""
    # Read only native property contents; no feature handler is needed.
    objects = {}
    for obj in _owned_objects(rebuild):
        properties = {}
        for name in sorted(obj.PropertiesList):
            if name in ("Visibility", "Label", "Label2"):
                continue
            if name == "REAgentBodyNames":
                properties[name] = list(obj.REAgentBodyNames)
                continue
            codec = _CODECS.get(obj.getTypeIdOfProperty(name))
            if codec:
                properties[name] = _encode(getattr(obj, name), codec)
        objects[obj.Name] = {
            "type_id": obj.TypeId,
            "label": str(obj.Label),
            "properties": properties,
            "expressions": list(getattr(obj, "ExpressionEngine", [])),
        }
        if obj.TypeId == "Sketcher::SketchObject":
            objects[obj.Name]["sketch_content_digest"] = _stable_digest(
                sketch_content_state(obj), length=24,
            )
    state = {
        "document": document.Name,
        "document_objects": [
            {"name": obj.Name, "type_id": obj.TypeId}
            for obj in document.Objects
        ],
        "objects": objects,
        "registered_bodies": list(rebuild.REAgentBodyNames),
        "result": rebuild.Tip.Name if rebuild.Tip else None,
        "tips": {
            obj.Name: obj.Tip.Name if obj.Tip else None
            for obj in _owned_objects(rebuild)
            if obj.TypeId == "PartDesign::Body"
        },
    }
    if include_history:
        state["history"] = _document_history_state(document)
    return state


def _target_geometry_state(rebuild):
    shape = _result_shape(rebuild)
    return None if shape is None or shape.isNull() else _shape_summary(shape)


def _target_view_revision(document, rebuild, reconstruction_ir):
    # Active observation transactions and derived geometry are recovery
    # evidence, not part of the persistent model revision.
    state = _target_model_state(document, rebuild, include_history=False)
    return "observation-" + _stable_digest(state, length=24)


def _geometry_differences(expected, actual, tolerance_contract, field="result_geometry"):
    if expected == actual:
        return []
    if expected is None or actual is None:
        return [{"field": field, "expected": expected, "actual": actual}]
    linear = tolerance_contract["numeric_linear_epsilon_mm"]
    volume = tolerance_contract["numeric_volume_epsilon_mm3"]
    area = tolerance_contract.get(
        "numeric_area_epsilon_mm2",
        max(1e-7, abs(float(expected.get("area_mm2", 0.0))) * 1e-12),
    )
    rows = []

    def compare(left, right, path):
        if isinstance(left, dict) and isinstance(right, dict):
            for key in sorted(set(left) | set(right)):
                child = path + "." + key
                if key not in left or key not in right:
                    rows.append({"field": child, "reason": "missing_field"})
                else:
                    compare(left[key], right[key], child)
            return
        if (isinstance(left, (int, float)) and not isinstance(left, bool)
                and isinstance(right, (int, float)) and not isinstance(right, bool)):
            threshold = (
                0.0 if path.endswith("_count")
                else volume if path.endswith("volume_mm3")
                else area if path.endswith("area_mm2")
                else linear
            )
            delta = abs(float(left) - float(right))
            if not math.isfinite(delta) or delta > threshold:
                rows.append({"field": path, "expected": left, "actual": right,
                             "delta": delta, "numeric_tolerance": threshold})
            return
        if left != right:
            rows.append({"field": path, "expected": left, "actual": right})

    compare(expected, actual, field)
    return rows


def _presentation_state(document):
    return {
        obj.Name: {
            key: getattr(obj.ViewObject, key)
            for key in (
                "Visibility", "DisplayMode", "DisplayModeBody", "Transparency"
            )
            if hasattr(obj.ViewObject, key)
        }
        for obj in document.Objects if hasattr(obj, "ViewObject")
    }
