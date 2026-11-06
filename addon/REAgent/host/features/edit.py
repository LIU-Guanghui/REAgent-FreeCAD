"""In-place edits using the shared native transaction and verified dependency scope."""

import math

from REAgent.protocol.features import EDIT_FEATURE_TYPES, HISTORY_COMMANDS, PRIMITIVE_COMMANDS, validate_feature_relations
from REAgent.host.analysis.measurements import _shape_summary, _shape_summary_consistent
from REAgent.host.analysis.topology import _stable_digest
from REAgent.host.validation.structure import _single_solid_summary, result_state
from REAgent.host.validation.tolerance import _ir_tolerance_contract
from REAgent.host.workspace.model import (
    _body_objects, _feature_owner, _feature_predecessor, _owned_objects, _session_bodies, _world_shape,
)
from REAgent.host.workspace.properties import _CODECS, _native_link, _same_placement
from REAgent.host.workspace.state import _target_model_state
from .edit_parameters import prepare_parameters
from .edit_sketch import SketchEdit
from .support.construction import _closed_profile, _section_or_path, _distinct_section_planes, _whole_sketch_link
from .hole import _circle_profile


def _geometry_summary(obj):
    shape = _world_shape(obj)
    return _shape_summary(shape) if shape is not None and not shape.isNull() else None


def _model_dependencies(obj):
    if obj.TypeId == "PartDesign::Body":
        return (obj.Tip,) if obj.Tip is not None else ()
    if obj.TypeId in ("App::Origin", "App::Line", "App::Plane", "App::Point"):
        return ()
    children = getattr(obj, "OriginFeatures", ())
    return tuple(link for link in obj.OutList if link not in children)


def _downstream(document, group, seeds):
    owned = _owned_objects(group)
    dependencies = {obj.Name: _model_dependencies(obj) for obj in document.Objects}
    allowed = set(seeds)
    while True:
        following = {obj.Name for obj in document.Objects if any(link.Name in allowed
                     for link in dependencies[obj.Name])}
        if following <= allowed:
            break
        allowed.update(following)
    if allowed - {obj.Name for obj in owned}:
        raise ValueError("Edit has downstream consumers outside the owned Target")
    return allowed


def _reference_error(consumer, prop, index, owner, part, reason, description):
    path = f"{consumer}.{prop}[{index}] -> {owner}.{part}"
    error = ValueError(f"{path}: {description}. Re-observe this reference before retrying.")
    error.failure_detail = {"reason": reason, "object": consumer, "property": prop, "link_index": index,
                            "owner": owner, "subelement": part}
    return error


def _subelement_facts(obj, affected):
    rows = {}
    for prop in obj.PropertiesList:
        codec = _CODECS.get(obj.getTypeIdOfProperty(prop))
        if codec not in ("link_sub", "link_sub_list"):
            continue
        value = getattr(obj, prop)
        links = ([value] if value else []) if codec == "link_sub" else value
        for index, (owner, elements) in enumerate(links):
            if owner is None or owner.Name not in affected:
                continue
            parts = [part for part in elements if part.startswith(("Edge", "Face", "Vertex"))]
            if not parts:
                continue
            shape = getattr(owner, "Shape", None)
            if shape is None or shape.isNull():
                raise _reference_error(obj.Name, prop, index, owner.Name, parts[0],
                                       "subelement_missing", "the owner has no usable Shape")
            local = shape.copy()
            local.Placement = owner.Placement.inverse().multiply(local.Placement)
            for part in parts:
                try:
                    element = local.getElement(part)
                    if element is None or element.isNull() or not element.isValid():
                        raise ValueError("Missing or invalid element")
                except Exception as exc:
                    raise _reference_error(obj.Name, prop, index, owner.Name, part,
                                           "subelement_missing", "the native subelement is unavailable") from exc
                bounds = element.BoundBox
                is_edge = part.startswith("Edge")
                carrier = getattr(element, "Curve" if is_edge else "Surface", None)
                rows[(prop, index, owner.Name, part)] = {
                    "topology": (str(element.ShapeType), str(element.Orientation), type(carrier).__name__,
                                 len(element.Edges), len(element.Wires), len(element.Vertexes)),
                    "bounds": [getattr(bounds, axis + end) for end in ("Min", "Max") for axis in "XYZ"],
                    "length": element.Length, "area": element.Area,
                    "vertices": [(v.Point.x, v.Point.y, v.Point.z) for v in element.Vertexes],
                    "samples": [(p.x, p.y, p.z) for p in element.discretize(Number=9)] if is_edge else [],
                }
    return rows


def _same_points(before, after, epsilon):
    remaining = list(after)
    if len(before) != len(remaining):
        return False
    for point in before:
        match = next((index for index, candidate in enumerate(remaining)
                      if math.dist(point, candidate) <= epsilon), None)
        if match is None:
            return False
        remaining.pop(match)
    return True


def _verify_subelements(obj, affected, before, tolerance):
    after = _subelement_facts(obj, affected)
    linear = tolerance["numeric_linear_epsilon_mm"]
    for key, original in before.items():
        prop, index, owner, part = key
        actual = after.get(key)
        reason, description = None, None
        if actual is None:
            reason, description = "subelement_link_changed", "the native link no longer resolves to this owner and element"
        elif original["topology"] != actual["topology"]:
            reason, description = "subelement_topology_changed", "the referenced topology or orientation changed"
        elif (not math.isclose(original["length"], actual["length"], abs_tol=linear, rel_tol=0)
              or not math.isclose(original["area"], actual["area"], rel_tol=0,
                                  abs_tol=tolerance.get("numeric_area_epsilon_mm2",
                                                        max(1e-7, abs(original["area"]) * 1e-12)))
              or any(not math.isclose(a, b, abs_tol=linear, rel_tol=0)
                     for a, b in zip(original["bounds"], actual["bounds"]))
              or not _same_points(original["vertices"], actual["vertices"], linear)
              or not _same_points(original["samples"], actual["samples"], linear)):
            reason, description = "subelement_geometry_changed", "the index still exists but its geometry changed beyond numeric tolerance"
        if reason:
            raise _reference_error(obj.Name, prop, index, owner, part, reason, description)


def _attachment_owners(state):
    support = state["properties"].get("AttachmentSupport") or []
    return {row["object"]["object"] for row in (support if isinstance(support, list) else [support]) if row}

def _verify_sketch_consumption(document, group, allowed):
    for obj in _owned_objects(group):
        if obj.Name not in allowed or obj.TypeId not in EDIT_FEATURE_TYPES:
            continue
        spec = HISTORY_COMMANDS.get(EDIT_FEATURE_TYPES[obj.TypeId], {})
        family = spec.get("domain")
        if family not in ("extrude", "revolve", "hole", "loft", "pipe"):
            continue
        body = _feature_owner(group, obj)
        profile = _native_link(obj.Profile)
        if profile is None or not _whole_sketch_link(obj.Profile, profile):
            raise ValueError("Downstream native Profile is no longer a whole Sketch: " + obj.Name)
        if family in ("loft", "pipe"):
            first, _ = _section_or_path(document, body, profile.Name, closed=True)
            second = _native_link(obj.Sections[0] if family == "loft" else obj.Spine)
            path = family == "pipe"
            second, _ = _section_or_path(document, body, second.Name, closed=not path, path=path)
            if family == "loft":
                _distinct_section_planes(first, second)
        else:
            _closed_profile(document, body, profile.Name)
            if family == "hole":
                _circle_profile(profile)


class EditScope:
    def __init__(self):
        self.target = None
        self.parameters = self.sketch = None
        self.companion_changes = []

    @staticmethod
    def geometry(group):
        return _stable_digest({obj.Name: _geometry_summary(obj) for obj in _owned_objects(group)}, length=24)

    @property
    def has_changes(self):
        return bool(self.parameters.writes or self.parameters.install_axis_parent
                    or self.sketch is not None and self.sketch.has_changes)

    def preflight(self, document, group, request):
        result_state(group)
        obj = document.getObject(request["object_name"])
        if obj is None or obj.TypeId != request["type_id"] or obj.TypeId not in EDIT_FEATURE_TYPES:
            raise ValueError("Observed native edit object or TypeId no longer matches")
        body = _feature_owner(group, obj)
        if body.Name != request["body_name"]:
            raise ValueError("Observed Body does not own this edit object")
        self.target = obj
        self.body = body
        self.prior = prior = _feature_predecessor(body, obj)
        changes = request["changes"]
        self.parameters = prepare_parameters(document, group, body, obj, prior, changes)
        self.tolerance = _ir_tolerance_contract(group)
        if "operations" in changes:
            self.sketch = SketchEdit(obj, changes["operations"], self.tolerance)
        # Moving a Body or Coordinate System changes the frames of its native
        # members. Ordinary membership is excluded from dependency traversal.
        seeds = {obj.Name}
        if obj is body and "Placement" in self.parameters.writes:
            seeds.update(member.Name for member in _body_objects(body))
        elif obj.TypeId == "Part::LocalCoordinateSystem":
            seeds.update(child.Name for child in obj.OriginFeatures)
        self.affected = _downstream(document, group, seeds)
        self.model_before = _target_model_state(document, group, include_history=False)
        self.shapes_before = {item.Name: _geometry_summary(item) for item in _owned_objects(group)}
        self.geometry_before = _stable_digest(self.shapes_before, length=24)
        self.geometry_after = self.geometry_before
        self.sub_before = {}
        for item in _owned_objects(group):
            if item is not obj:
                facts = _subelement_facts(item, self.affected)
                if facts:
                    self.sub_before[item.Name] = facts

    def mutate(self, document, group, request, changes):
        self.parameters.write(changes)
        if self.sketch is not None and self.sketch.has_changes:
            self.sketch.write()
        return self.target

    def check_references(self, document):
        for name, refs in self.sub_before.items():
            obj = document.getObject(name)
            if obj is None:
                prop, index, owner, part = next(iter(refs))
                raise _reference_error(name, prop, index, owner, part,
                                       "subelement_consumer_missing", "the referencing feature is unavailable")
            _verify_subelements(obj, self.affected, refs, self.tolerance)

    def check_scope(self, document, group, before):
        after = _target_model_state(document, group, include_history=False)
        if any(after[key] != self.model_before[key] for key in (
                "document_objects", "registered_bodies", "result", "tips")):
            raise RuntimeError("In-place edit changed native object identity, registration or Body Tip")
        if any([child.Name for child in document.getObject(name).Group] != members
               for name, members in before["groups"].items()):
            raise RuntimeError("In-place edit changed native Body membership")
        shapes = {}
        self.companion_changes = []
        for item in _owned_objects(group):
            name = item.Name
            shapes[name] = _geometry_summary(item)
            previous = self.model_before["objects"][name]
            current = after["objects"][name]
            allowed = set()
            added_property = None
            if item is self.target:
                allowed.update(self.parameters.writes)
                if self.parameters.install_axis_parent:
                    added_property = self.parameters.axis_parent_property
                    allowed.add(added_property)
            companion = None
            attached = _attachment_owners(current) if getattr(item, "MapMode", "Deactivated") != "Deactivated" else set()
            if name in self.parameters.tool_placements:
                companion = "boolean_operand_frame"
            elif attached and (item is self.target and allowed & {"MapMode", "AttachmentSupport", "AttachmentOffset"}
                               or item is not self.target and attached & self.affected):
                companion = "derived_attachment_placement"
            if companion and "Placement" not in allowed:
                old = previous["properties"].get("Placement")
                new = current["properties"].get("Placement")
                if old != new and old is not None and new is not None:
                    allowed.add("Placement")
                    self.companion_changes.append({"object": name, "property": "Placement", "reason": companion,
                                                   "before": old, "after": new})
            properties = (previous["properties"].keys() | current["properties"].keys()
                          | previous["property_types"].keys() | current["property_types"].keys())
            missing = object()
            unrequested = [
                prop for prop in sorted(properties)
                if (prop not in allowed and previous["properties"].get(prop, missing)
                    != current["properties"].get(prop, missing))
                or (previous["property_types"].get(prop) != current["property_types"].get(prop)
                    and not (prop == added_property and prop not in previous["property_types"]
                             and current["property_types"].get(prop) == "App::PropertyLink"))
            ]
            unrequested.extend(key for key in ("type_id", "label", "expressions", "sketch_content_digest")
                               if previous.get(key) != current.get(key)
                               and not (key == "label" and "Label" in allowed)
                               and not (key == "sketch_content_digest" and item is self.target
                                        and self.sketch is not None and self.sketch.has_changes))
            if unrequested:
                error = RuntimeError("Edit changed an unrequested native field on " + name + ": " + unrequested[0])
                error.failure_detail = {"reason": "unrequested_change", "object": name,
                                        "property": unrequested[0], "changed_fields": unrequested[:16]}
                raise error
            if name not in self.affected and not _shape_summary_consistent(self.shapes_before[name], shapes[name]):
                raise RuntimeError("Edit changed unrelated geometry on " + name)
            command = EDIT_FEATURE_TYPES.get(item.TypeId)
            if name in self.affected and (command in PRIMITIVE_COMMANDS or command in HISTORY_COMMANDS):
                _single_solid_summary(item.Shape, name)
        _verify_sketch_consumption(document, group, self.affected)
        self.geometry_after = _stable_digest(shapes, length=24)

    def readback(self, document, group, target, state):
        body, prior = self.body, self.prior
        if (document.getObject(target.Name) is not target or _feature_owner(group, target) is not body
                or _feature_predecessor(body, target) is not prior):
            raise RuntimeError("Native edit changed identity, ownership or predecessor")
        changed = self.parameters.readback()
        sketch = self.sketch.readback() if self.sketch is not None else None
        result = {"feature": target.Name, "type_id": target.TypeId, "body": body.Name,
                  "tip": body.Tip.Name if body.Tip else None,
                  "predecessor": prior.Name if prior else None, "changes": changed,
                  "affected_objects": sorted(self.affected)[:64],
                  "affected_object_count": len(self.affected),
                  "affected_bodies": [item.Name for item in _session_bodies(group) if item.Name in self.affected]}
        if self.companion_changes:
            result["companion_changes"] = self.companion_changes[:16]
            result["companion_change_count"] = len(self.companion_changes)
        command = EDIT_FEATURE_TYPES[target.TypeId]
        if command in PRIMITIVE_COMMANDS or command in HISTORY_COMMANDS:
            result["feature_shape"] = _single_solid_summary(target.Shape, target.Name)
            if body.Tip is target:
                body_shape = _single_solid_summary(_world_shape(body), body.Name)
                volume = body_shape["volume_mm3"]
                if abs(result["feature_shape"]["volume_mm3"] - volume) > max(1e-9, volume * 1e-12):
                    raise RuntimeError("Edited Tip and Body have different volume")
                result["body_shape"] = body_shape
        if "Placement" in self.parameters.expected:
            if body is target:
                if not _same_placement(target.getGlobalPlacement(), target.Placement):
                    raise RuntimeError("Body world placement does not match its native placement")
            elif not _same_placement(target.getGlobalPlacement(),
                                     body.getGlobalPlacement().multiply(target.Placement)):
                raise RuntimeError("Edited local placement disagrees with its Body frame")
        if sketch is not None:
            result["sketch"] = sketch
        return result


class EditOperations:
    def __init__(self, *, execute_native):
        self.execute_native = execute_native

    def _edit_feature(self, request):
        validate_feature_relations("EditFeature", request)
        scope = EditScope()
        return self.execute_native(request, command="EditFeature", edit=scope, preflight=scope.preflight,
                                   mutate=scope.mutate, readback=scope.readback)

    def contribute(self):
        return {"EditFeature": self._edit_feature}
