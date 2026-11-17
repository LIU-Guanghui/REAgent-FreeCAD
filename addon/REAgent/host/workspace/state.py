"""Read-only persistent model, geometry and revision facts."""

import math

from REAgent.host.analysis.measurements import summarize_shape
from REAgent.host.analysis.topology import stable_digest
from REAgent.host.process_state import PROCESS
from REAgent.host.workspace.model import copy_world_shape, owned_objects, placement_summary, result_shape
from REAgent.host.workspace.properties import _CODECS, encode, sketch_content_state


class NativeSourceReference:
    """Notice changes to the imported Source outside owned observation."""

    def __init__(self, document):
        self.document = document
        self.changed = False

    def slotChangedObject(self, obj, prop=None):
        if prop in ("Label", "Label2", "Visibility"):
            return
        operation = PROCESS.ACTIVE_OPERATION or {}
        if obj.Document is self.document and operation.get("stage") != "observation":
            self.changed = True

    slotCreatedObject = slotDeletedObject = slotChangedObject


def source_view_revision(source):
    binding = PROCESS.SESSION_BINDINGS[PROCESS.ACTIVE_TASK["session_id"]]
    if binding["document"] is not source.Document or binding["source_reference"].changed:
        raise ValueError("The native Source changed. Start a new task from the reference file.")
    return binding["source_revision"]


def document_history_state(document):
    return {
        "pending_transaction": bool(document.HasPendingTransaction),
        "undo": list(document.UndoNames),
        "redo": list(document.RedoNames),
    }


def source_model_state(source, *, include_history=True):
    document = getattr(source, "Document", None)
    return {
        "revision": source_view_revision(source),
        "placement": placement_summary(source),
        "document_objects": (
            [{"name": obj.Name, "type_id": obj.TypeId} for obj in document.Objects]
            if document is not None else None
        ),
        "history": document_history_state(document) if document is not None and include_history else None,
    }


def source_geometry_state(source):
    if source.TypeId == "App::DocumentObjectGroup":
        return None
    if source.TypeId == "Mesh::Feature":
        mesh = source.Mesh
        box = mesh.BoundBox
        return {"facet_count": mesh.CountFacets, "bounding_box_mm": {
            axis.lower() + "_" + suffix.lower(): float(getattr(box, axis + suffix))
            for axis in "XYZ" for suffix in ("Min", "Length")
        }}
    return summarize_shape(source.Shape)


def target_model_state(document, target_body, *, include_history=True):
    """Capture persistent model facts; never include derived Shape or GUI state."""
    # Read only native property contents; no feature handler is needed.
    objects = {}
    for obj in owned_objects(target_body):
        properties = {}
        property_types = {}
        for name in sorted(obj.PropertiesList):
            if name in ("Visibility", "Label"):
                continue
            if name == "REAgentBodyNames":
                properties[name] = list(obj.REAgentBodyNames)
                continue
            type_id = obj.getTypeIdOfProperty(name)
            property_types[name] = type_id
            codec = _CODECS.get(type_id)
            if codec:
                properties[name] = encode(getattr(obj, name), codec)
            elif type_id in ("App::PropertyFloatList", "App::PropertyIntegerList", "App::PropertyStringList"):
                properties[name] = list(getattr(obj, name))
        objects[obj.Name] = {
            "type_id": obj.TypeId,
            "label": str(obj.Label),
            "properties": properties,
            "property_types": property_types,
            "expressions": list(getattr(obj, "ExpressionEngine", [])),
        }
        if obj.TypeId == "Sketcher::SketchObject":
            objects[obj.Name]["sketch_content_digest"] = stable_digest(
                sketch_content_state(obj), length=24,
            )
    state = {
        "document": document.Name,
        "document_objects": [
            {"name": obj.Name, "type_id": obj.TypeId}
            for obj in document.Objects
        ],
        "objects": objects,
        "registered_bodies": list(target_body.REAgentBodyNames),
        "result": target_body.Tip.Name if target_body.Tip else None,
        "tips": {
            obj.Name: obj.Tip.Name if obj.Tip else None
            for obj in owned_objects(target_body)
            if obj.TypeId == "PartDesign::Body"
        },
    }
    if include_history:
        state["history"] = document_history_state(document)
    return state


def target_geometry_state(target_body):
    shape = result_shape(target_body)
    return None if shape is None or shape.isNull() else summarize_shape(shape)


def object_geometry_state(obj):
    shape = copy_world_shape(obj)
    return None if shape is None or shape.isNull() else summarize_shape(shape)


def owned_geometry_state(target_body):
    return {obj.Name: object_geometry_state(obj) for obj in owned_objects(target_body)}


def geometry_digest(shapes):
    return stable_digest(shapes, length=24)


def target_view_revision(document, target_body):
    # Active observation transactions and derived geometry are recovery
    # evidence, not part of the persistent model revision.
    state = target_model_state(document, target_body, include_history=False)
    return "observation-" + stable_digest(state, length=24)


def geometry_differences(expected, actual, tolerance_contract, field="result_geometry"):
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


def presentation_state(document):
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
