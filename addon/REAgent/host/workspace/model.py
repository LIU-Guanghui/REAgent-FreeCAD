"""Native workspace ownership and explicit result selection."""

import json
import os

import Part

from REAgent.host.process_state import _document, PROCESS
from REAgent.host.file_digest import _file_sha256
from REAgent.host.numeric import _vector_dict


def _workspace(document):
    group = document.getObject("REAgentWorkspace")
    if (group is None or group.TypeId != "PartDesign::Body"
            or not all(hasattr(group, key) for key in
                       ("REAgentBodyNames", "Origin", "SourceFile", "ToleranceContractJSON"))):
        raise ValueError("Document has no native REAgent workspace; old checkpoints are not supported")
    return group


def _origin_objects(container):
    origin = getattr(container, "Origin", None)
    return [] if origin is None else [origin, *origin.OriginFeatures]


def _session_bodies(group):
    bodies = [group]
    for name in group.REAgentBodyNames:
        if not isinstance(name, str) or not name:
            raise ValueError("Invalid tool Body registration name")
        body = group.Document.getObject(name)
        if body is group or body is None or body.Document is not group.Document or body.TypeId != "PartDesign::Body":
            raise ValueError("Invalid tool Body registration")
        if body in bodies:
            raise ValueError("Duplicate tool Body registration")
        bodies.append(body)
    return bodies


def _enforce_single_solid_body(body):
    if "AllowCompound" in body.PropertiesList:
        body.AllowCompound = False
        if bool(body.AllowCompound):
            raise RuntimeError("PartDesign Body did not disable AllowCompound")


def _is_origin_object(group, obj):
    return any(obj in _origin_objects(body) for body in _session_bodies(group))


def _body_objects(body):
    """Native members and origins of one Body, without following dependencies."""
    result = []
    for obj in [body, *body.Group, *_origin_objects(body)]:
        if obj not in result:
            result.append(obj)
    for obj in body.Group:
        if obj.TypeId == "Part::LocalCoordinateSystem":
            for child in getattr(obj, "OriginFeatures", []):
                if child is not None and child not in result:
                    result.append(child)
    return result


def _owned_objects(group):
    result = []
    for body in _session_bodies(group):
        for obj in _body_objects(body):
            if obj not in result:
                result.append(obj)
    return result


def _depends_on(start, target):
    """Follow native outbound links without assuming a Python recursive-list API."""
    pending, visited = [start], set()
    while pending:
        item = pending.pop()
        if item is target:
            return True
        if item.Name in visited:
            continue
        visited.add(item.Name)
        pending.extend(item.OutList)
    return False


def _validate_boolean_tools(group, feature, tools):
    bodies = _session_bodies(group)
    owner = next((body for body in bodies if feature in body.Group), None)
    if owner is None or not tools:
        raise ValueError("Boolean requires an owned receiving Body and non-empty tool Bodies")
    if len({tool.Name for tool in tools}) != len(tools):
        raise ValueError("Boolean tool Bodies must be distinct")
    for tool in tools:
        if tool not in bodies or tool is owner or tool is group:
            raise ValueError("Boolean tools must be registered non-main Bodies distinct from the receiver")
        if tool.Tip is None:
            raise ValueError("Boolean tool Body has no Tip: " + tool.Name)
    graph = {body.Name: [] for body in bodies}
    for body in bodies:
        for item in body.Group:
            if item.TypeId == "PartDesign::Boolean":
                operands = tools if item is feature else list(item.Group)
                for operand in operands:
                    if operand not in bodies or operand is body or operand is group:
                        raise ValueError("Invalid Boolean Body dependency")
                    graph[body.Name].append(operand.Name)
    visiting, finished = set(), set()
    def visit(name):
        if name in visiting:
            raise ValueError("Cyclic Boolean Body dependency")
        if name in finished:
            return
        visiting.add(name)
        for child in graph[name]:
            visit(child)
        visiting.remove(name)
        finished.add(name)
    for name in graph:
        visit(name)


def _target_state(document_name):
    _require_instance_role("rebuild")
    document = _document(document_name)
    group = _workspace(document)
    path = _step_source_path(group.SourceFile)
    source = _read_source_snapshot(path)
    return document, source, group, group


def _read_source_snapshot(path):
    """Pair a STEP import with equal observed hashes; never claim atomic I/O."""
    path = _step_source_path(path)
    before = _file_sha256(path)
    shape = Part.read(path)
    after = _file_sha256(path)
    if before != after:
        raise ValueError("Source STEP changed during import; choose a stable source before starting a session")
    return _SourceReference(shape, path, after)


def _result_object(group):
    tip = group.Tip
    if tip is None:
        return None
    if tip not in group.Group:
        raise ValueError("Main Body Tip is not in its native history")
    return tip


def _world_shape(obj):
    """Copy a placed native Shape and apply only its missing parent transform."""
    shape = getattr(obj, "Shape", None)
    if shape is None:
        return None
    shape = shape.copy()
    if shape.isNull():
        return shape
    parent = obj.getGlobalPlacement().multiply(obj.Placement.inverse())
    shape.Placement = parent.multiply(shape.Placement)
    return shape


def _result_shape(group):
    # Body.Shape is the native cumulative result selected by the native Tip.
    return _world_shape(group) if _result_object(group) is not None else None


def _comparison_inputs_state(group):
    """Read only inputs of the selected result, without auditing unrelated history."""
    import FreeCAD as App

    document = group.Document
    if _document(document.Name) is not document or _workspace(document) is not group:
        raise ValueError("Comparison workspace binding changed")
    if document.HasPendingTransaction or App.getActiveTransaction() is not None:
        raise ValueError("Finish the native transaction before comparing geometry")
    block = PROCESS.AUDIT_BLOCKS.get(document.Name)
    if block and block[0] is document:
        raise ValueError("Comparison geometry is unavailable while Target state is unknown")
    tip = _result_object(group)
    if tip is None:
        raise ValueError("Target has no current Result; observe global workspace state before constructing geometry")
    owned = _owned_objects(group)
    pending, visited, rows = [group], set(), []
    while pending:
        obj = pending.pop()
        if obj.Document is not document or document.getObject(obj.Name) is not obj or obj not in owned:
            raise ValueError("Comparison input is missing or outside the bound workspace: " + obj.Name)
        if obj.Name in visited:
            continue
        visited.add(obj.Name)
        states = tuple(str(value).lower() for value in obj.State)
        if set(states) & {"invalid", "error", "touched"}:
            raise ValueError("Comparison input requires a valid recompute: " + obj.Name + " " + repr(states))
        excluded = []
        if obj.TypeId == "PartDesign::Body":
            if obj.Tip is None or obj.Tip not in obj.Group:
                raise ValueError("Comparison input Body has no owned Tip: " + obj.Name)
            excluded.extend(obj.Group)
            excluded.extend(_origin_objects(obj))
        elif obj.TypeId == "Part::LocalCoordinateSystem":
            excluded.extend(getattr(obj, "OriginFeatures", []))
        inputs = [linked for linked in obj.OutList if linked not in excluded]
        # Preserve genuine input links even if the same object is also a member
        # of a container list. Boolean.Group is an operand list and is not skipped.
        if excluded:
            for name in obj.PropertiesList:
                if name in ("Group", "Origin", "OriginFeatures"):
                    continue
                kind = obj.getTypeIdOfProperty(name)
                if not kind.startswith(("App::PropertyLink", "App::PropertyXLink")):
                    continue
                values = [getattr(obj, name)]
                while values:
                    value = values.pop()
                    if isinstance(value, (list, tuple)):
                        values.extend(value)
                    elif value is not None and hasattr(value, "Document") and value not in inputs:
                        inputs.append(value)
            if getattr(obj, "ExpressionEngine", []) and any(link in excluded for link in obj.OutList):
                raise ValueError("Cannot separate container expression inputs from history links: " + obj.Name)
        if obj.TypeId == "PartDesign::Body" and obj.Tip not in inputs:
            inputs.append(obj.Tip)
        placement = obj.getGlobalPlacement() if hasattr(obj, "Placement") else None
        frame = None if placement is None else (
            placement.Base.x, placement.Base.y, placement.Base.z, tuple(placement.Rotation.Q))
        rows.append((obj, states, tuple(inputs), frame))
        pending.extend(reversed(inputs))
    return document, group, tip, tuple(rows)


def _comparison_shape(group):
    inputs = _comparison_inputs_state(group)
    shape = _result_shape(group)
    if shape is None or shape.isNull() or not shape.isValid() or len(shape.Solids) != 1:
        raise ValueError("Target comparison requires a nonempty valid single-solid Result")
    if _comparison_inputs_state(group) != inputs:
        raise ValueError("Target comparison inputs changed while copying the Shape")
    return shape, inputs


def _predecessor_shape(body):
    """Selection geometry belongs to the native Tip carrying its LinkSub names."""
    tip = _result_object(body)
    return _world_shape(tip) if tip is not None else None


def _result_display_plan(group):
    """Separate the visible chain from the Tip carrying its style."""
    tip = _result_object(group)
    if tip is None:
        raise ValueError("Target has no Result to render; use Target global observation for workspace evidence before the first feature")
    root_view = getattr(group, "ViewObject", None)
    tip_view = getattr(tip, "ViewObject", None)
    if root_view is None:
        raise ValueError("Result Body has no GUI ViewObject: " + group.Name)
    if tip_view is None:
        raise ValueError("Result Tip has no GUI ViewObject: " + tip.Name)
    if not hasattr(root_view, "DisplayModeBody"):
        raise ValueError("Result Body has no DisplayModeBody: " + group.Name)
    try:
        tip_modes = list(tip_view.listDisplayModes())
    except Exception:
        tip_modes = []
    if not tip_modes:
        raise ValueError("Result Tip has no supported display modes: " + tip.Name)
    return {
        "render": (root_view, tip_view),
        "style": (tip_view,),
        "body": root_view,
        "body_display_mode": "Through",
        "result": tip_view,
    }


def _compact_json(value):
    return json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )


def _step_source_path(value):
    if not isinstance(value, str) or not value:
        raise ValueError("source_path must be a non-empty absolute path")
    if not os.path.isabs(value):
        raise ValueError("source_path must be an absolute path")
    path = os.path.normpath(value)
    if os.path.splitext(path)[1].lower() not in (".stp", ".step"):
        raise ValueError("source_path must use the .stp or .step extension")
    if not os.path.isfile(path):
        raise ValueError("STEP source file does not exist: %s" % path)
    return path


def _source_objects(document_name):
    _require_instance_role("source")
    document = _document(document_name)
    source = document.getObject("Source")
    if source is None or getattr(source, "TypeId", None) != "Part::Feature":
        raise KeyError("Reconstruction Source object not found")
    if document.getObject("REAgentWorkspace") is not None:
        raise ValueError("Source document must not contain a Target workspace")
    shape = getattr(source, "Shape", None)
    if shape is None or shape.isNull():
        raise ValueError("Reconstruction Source has no shape")
    return document, source


class _SourceReference:
    def __init__(self, shape, path, sha256):
        self.Shape = shape
        self.SourceFile = path
        self.SourceSHA256 = sha256


def _require_instance_role(expected):
    if PROCESS.INSTANCE_ROLE != expected:
        raise ValueError(
            "Bridge role %s cannot perform %s operations"
            % (PROCESS.INSTANCE_ROLE, expected)
        )


def _placement_summary(obj):
    placement = getattr(obj, "Placement", None)
    if placement is None:
        return None
    return {
        "base_mm": _vector_dict(placement.Base),
        "rotation_xyzw": [float(value) for value in placement.Rotation.Q],
    }
