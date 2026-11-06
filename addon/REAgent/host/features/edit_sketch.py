"""Revision-scoped edits to the existing native Sketch and its solver."""

import math

import FreeCAD as App
import Sketcher

from REAgent.host.workspace.model import _depends_on
from REAgent.host.workspace.properties import sketch_content_state
from .sketch import (_POINT_POSITIONS, _constraints_preflight, _geometry_preflight,
                     _native_constraint, _native_geometry)


_GEOMETRY_KINDS = {"LineSegment": "line", "Circle": "circle", "ArcOfCircle": "arc",
                   "Ellipse": "ellipse", "ArcOfEllipse": "ellipse_arc"}
_POINT_KEYS = {"start": "StartPoint", "end": "EndPoint", "center": "Center"}
_SHAPE_OPS = {"move_point", "set_radius", "set_length"}
_GEOMETRY_OPS = {"delete_geometry", "construction", *_SHAPE_OPS}


def _geometry_id(index):
    return "Native" + str(index)


def _translate_constraint(value):
    def address(ref):
        return _geometry_id(ref["index"]) if ref["kind"] == "existing" else ref["id"]
    result = dict(value)
    for name in ("geometry_id", "first_geometry", "second_geometry"):
        if name in result:
            result[name] = address(result[name])
    for name in ("first_point", "second_point", "point"):
        if name in result:
            ref = result[name]
            result[name] = {"geometry_id": address(ref["geometry"]), "position": ref["position"]}
    return result


def _references(row):
    return {row[key] for key in ("First", "Second", "Third") if key in row and row[key] >= 0}


def _external_points(sketch, content):
    # External indexes start at -3; -1 and -2 are the built-in axes. Verify
    # the actual indexed points used by constraints as well as ordered links.
    points = {}
    external_indices = {row["index"]: row for row in content["external_indices"]}
    for row in content["constraints"]:
        for key in ("First", "Second", "Third"):
            index, position = row.get(key, -1), row.get(key + "Pos", 0)
            if index <= -3 and position in (1, 2, 3):
                point = sketch.getPoint(index, position)
                points[(index, position)] = (point.x, point.y, point.z)
            elif index <= -3:
                row = external_indices.get(index)
                if not row or not set(row) & {"StartPoint", "Center", "poles"}:
                    raise ValueError("External curve at index " + str(index) + " cannot be fully verified")
    return content["external_geometry"], content["external_indices"], points


class SketchEdit:
    def __init__(self, sketch, operations, tolerance):
        self.sketch = sketch
        self.before = before = sketch_content_state(sketch)
        self.epsilon = epsilon = tolerance["linear_tolerance_mm"]
        self.numeric_epsilon = tolerance["numeric_linear_epsilon_mm"]
        self.external = _external_points(sketch, before)
        self.actions = []
        self.checks = []
        self.added_geometry = []
        self.added_constraints = []
        self.deleted_geometry = set()
        self.deleted_constraints = set()
        geometry_count, constraint_count = len(before["geometry"]), len(before["constraints"])
        consumers = [obj for obj in sketch.Document.Objects if obj is not sketch
                     and obj.TypeId != "PartDesign::Body" and _depends_on(obj, sketch)]
        by_id = {_geometry_id(i): {"kind": _GEOMETRY_KINDS.get(type(geo).__name__)}
                 for i, geo in enumerate(sketch.Geometry)}
        original_ids = set(by_id)
        seen = set()
        for operation in operations:
            op, index = operation["op"], operation.get("index")
            if index is not None:
                is_geometry = op in _GEOMETRY_OPS
                group = "geometry" if is_geometry else "constraints"
                if index >= (geometry_count if is_geometry else constraint_count):
                    raise ValueError("Sketch index is outside the observed " + group)
                key = ("geometry_shape" if op in _SHAPE_OPS else op, index)
                if key in seen:
                    if op in _SHAPE_OPS:
                        raise ValueError(f"Sketch geometry {index} allows one point move or size change per edit")
                    raise ValueError(f"Sketch operation {op} repeats index {index}")
                seen.add(key)
            if op == "add_geometry":
                self.added_geometry.append(operation["geometry"])
            elif op == "add_constraint":
                self.added_constraints.append(_translate_constraint(operation["constraint"]))
            elif op == "delete_geometry":
                self.deleted_geometry.add(index)
            elif op == "delete_constraint":
                self.deleted_constraints.add(index)
            else:
                self._prepare_action(operation, before[group][index])
        if any(action["index"] in (self.deleted_geometry if action["op"] in _GEOMETRY_OPS
                                   else self.deleted_constraints) for action in self.checks):
            raise ValueError("Cannot modify and delete the same Sketch element")
        if self.deleted_constraints:
            if sketch.ExpressionEngine or any(getattr(obj, "ExpressionEngine", ()) for obj in consumers):
                raise ValueError("Constraint deletion would risk shifting a Sketch expression reference")
            if any(before["constraints"][i].get("Name") for i in self.deleted_constraints):
                raise ValueError("Cannot delete a named constraint without proving its consumers")
        if self.deleted_geometry:
            if (self.external[0] or self.external[1]) or consumers and any(not before["geometry"][i]["construction"] for i in self.deleted_geometry):
                raise ValueError("Cannot remove geometry while affected native element references are unconfirmed")
            if any(self.deleted_geometry & _references(row) for index, row in enumerate(before["constraints"])
                   if index not in self.deleted_constraints):
                raise ValueError("Remove constraints on a deleted geometry explicitly in the same edit")
            for index in self.deleted_geometry:
                del by_id[_geometry_id(index)]
        for action in self.actions:
            if action["op"] in ("set_radius", "set_length") and any(
                action["index"] in _references(row) for i, row in enumerate(before["constraints"])
                if i not in self.deleted_constraints
            ):
                raise ValueError(f"Sketch geometry {action['index']} has constraints. Edit its driving dimension instead.")
        added = _geometry_preflight(self.added_geometry, epsilon)
        if original_ids & added.keys():
            raise ValueError("New Sketch geometry IDs must differ from existing element identifiers")
        for item in self.added_geometry:
            if item["kind"] in ("arc", "ellipse_arc") and not 0 < item["end_deg"] - item["start_deg"] < 360:
                raise ValueError("Sketch arc requires a positive span under 360 degrees")
        by_id.update(added)
        _constraints_preflight(self.added_constraints, by_id)
        if geometry_count - len(self.deleted_geometry) + len(self.added_geometry) > 256:
            raise ValueError("Edited Sketch would exceed 256 native geometry elements")
        if constraint_count - len(self.deleted_constraints) + len(self.added_constraints) > 512:
            raise ValueError("Edited Sketch would exceed 512 native constraints")
        self.indices = {_geometry_id(index): final for final, index in enumerate(
            index for index in range(geometry_count) if index not in self.deleted_geometry)}
        self.constraint_indices = {index: final for final, index in enumerate(
            index for index in range(constraint_count) if index not in self.deleted_constraints)}

    def _prepare_action(self, operation, row):
        op, index = operation["op"], operation["index"]
        prepared = dict(operation)
        self.checks.append(prepared)
        if op == "move_point":
            attr = _POINT_KEYS[operation["position"]]
            if attr not in row:
                raise ValueError("Selected Sketch geometry has no requested point")
            if math.dist(row[attr], [*operation["point_mm"], 0.0]) <= self.epsilon:
                return
        elif op == "set_radius":
            if _GEOMETRY_KINDS.get(type(self.sketch.Geometry[index]).__name__) not in ("circle", "arc"):
                raise ValueError("set_radius requires a native Sketch circle or circular arc")
            radius = operation["radius_mm"]
            if radius <= self.epsilon:
                raise ValueError("Sketch radius is below the geometry tolerance")
            if math.isclose(row["Radius"], radius, abs_tol=self.numeric_epsilon, rel_tol=0):
                return
        elif op == "set_length":
            if _GEOMETRY_KINDS.get(type(self.sketch.Geometry[index]).__name__) != "line":
                raise ValueError("set_length requires a native Sketch line segment")
            length = operation["length_mm"]
            start, end = row["StartPoint"], row["EndPoint"]
            current = math.dist(start, end)
            if min(current, length) <= self.epsilon:
                raise ValueError("Sketch line is below the geometry tolerance")
            prepared["end"] = [a + (b-a)*length/current for a, b in zip(start, end)]
            if math.isclose(current, length, abs_tol=self.numeric_epsilon, rel_tol=0):
                return
        elif op == "construction":
            if row["construction"] == operation["enabled"]:
                return
        else:
            if row["expression"]:
                raise ValueError("The selected Sketch constraint is expression-driven")
            if op == "set_constraint_value":
                if row["driving"] is not True or not row["active"] or not row.get("value_unit"):
                    raise ValueError("Only active driving dimensional constraints have an editable value")
                value = operation["value"]
                if row["value_unit"] == "rad":
                    value = math.radians(value)
                prepared["value"] = value
                if math.isclose(row["Value"], value, rel_tol=1e-12, abs_tol=1e-9):
                    return
            elif op == "constraint_driving":
                if row["driving"] is None:
                    raise ValueError("Only dimensional constraints have a driving mode")
                if row["driving"] == operation["enabled"]:
                    return
            elif row["active"] == operation["enabled"]:
                return
        self.actions.append(prepared)

    @property
    def has_changes(self):
        return bool(self.actions or self.added_geometry or self.added_constraints
                    or self.deleted_geometry or self.deleted_constraints)

    def write(self):
        sketch = self.sketch
        for index in sorted(self.deleted_constraints, reverse=True):
            sketch.delConstraint(index)
        for index in sorted(self.deleted_geometry, reverse=True):
            sketch.delGeometry(index)
        for item in self.added_geometry:
            index = sketch.addGeometry(_native_geometry(item), item.get("construction", False))
            if type(index) is not int or index != len(self.indices):
                raise RuntimeError("Native Sketch geometry did not append at the expected index")
            self.indices[item["id"]] = index
        for operation in self.actions:
            op, original = operation["op"], operation["index"]
            if op in _GEOMETRY_OPS:
                index = self.indices[_geometry_id(original)]
                if op == "move_point":
                    sketch.moveGeometry(index, _POINT_POSITIONS[operation["position"]],
                                        App.Vector(*operation["point_mm"], 0.0))
                elif op == "set_length":
                    sketch.moveGeometry(index, _POINT_POSITIONS["end"], App.Vector(*operation["end"]))
                elif op == "set_radius":
                    count = sketch.ConstraintCount
                    temp = sketch.addConstraint(Sketcher.Constraint("Radius", index, operation["radius_mm"]))
                    if temp != count or sketch.solve() != 0:
                        raise ValueError(f"Native solver could not resize Sketch geometry {original}")
                    sketch.delConstraint(temp)
                    if sketch.ConstraintCount != count:
                        raise RuntimeError("Temporary resize dimension was not removed")
                else:
                    sketch.setConstruction(index, operation["enabled"])
            else:
                index = self.constraint_indices[original]
                if op == "set_constraint_value":
                    sketch.setDatum(index, operation["value"])
                elif op == "constraint_driving":
                    sketch.setDriving(index, operation["enabled"])
                else:
                    sketch.setActive(index, operation["enabled"])
        for item in self.added_constraints:
            sketch.addConstraint(_native_constraint(item, self.indices))
        if sketch.solve() != 0:
            raise ValueError("Native Sketch solver rejected the edit")

    def readback(self):
        sketch = self.sketch
        actual = sketch_content_state(sketch)
        if (len(actual["geometry"]) != len(self.indices)
                or len(actual["constraints"]) != len(self.constraint_indices) + len(self.added_constraints)):
            raise RuntimeError("Sketch edit did not preserve the planned element counts")
        if _external_points(sketch, actual) != self.external:
            raise RuntimeError("Sketch external links or observed indexed points changed")
        requested = {(item["op"], item["index"]) for item in self.checks}
        for original, index in self.constraint_indices.items():
            expected = self.before["constraints"][original]
            row = actual["constraints"][index]
            for key in ("Type", "Name", "First", "Second", "Third", "FirstPos", "SecondPos", "ThirdPos", "expression"):
                value = expected.get(key)
                if key in ("First", "Second", "Third") and value is not None and value >= 0:
                    value = self.indices[_geometry_id(value)]
                if row.get(key) != value:
                    raise RuntimeError("Existing Sketch constraint identity or reference changed")
            for field, op in (("active", "constraint_active"), ("driving", "constraint_driving")):
                if (op, original) not in requested and expected.get(field) != row.get(field):
                    raise RuntimeError(f"Sketch constraint {original} changed {field} without a request")
            if ((expected["driving"] is not False and row["driving"] is not False)
                    and ("set_constraint_value", original) not in requested
                    and not math.isclose(expected.get("Value", 0), row.get("Value", 0), abs_tol=1e-9, rel_tol=1e-12)):
                raise RuntimeError(f"Sketch constraint {original} changed its stored value without a request")
        for original, previous in enumerate(self.before["geometry"]):
            if original in self.deleted_geometry:
                continue
            row = actual["geometry"][self.indices[_geometry_id(original)]]
            if (previous["type_id"] != row["type_id"]
                    or ("construction", original) not in requested and previous["construction"] != row["construction"]):
                raise RuntimeError(f"Sketch geometry {original} changed its kind or construction state")
        for i, item in enumerate(self.added_constraints, start=len(self.constraint_indices)):
            row = actual["constraints"][i]
            native = _native_constraint(item, self.indices)
            if any(row.get(key) != getattr(native, key) for key in (
                    "Type", "First", "Second", "Third", "FirstPos", "SecondPos", "ThirdPos")):
                raise RuntimeError("New Sketch constraint did not retain its intended references")
            if "value" in item and not math.isclose(row["Value"], native.Value, abs_tol=1e-8):
                raise RuntimeError("New Sketch constraint value did not read back")
        for item in self.added_geometry:
            index = self.indices[item["id"]]
            if (_GEOMETRY_KINDS.get(type(sketch.Geometry[index]).__name__) != item["kind"]
                    or actual["geometry"][index]["construction"] != item.get("construction", False)):
                raise RuntimeError("New Sketch geometry kind or construction state changed")
        for operation in self.checks:
            op, original = operation["op"], operation["index"]
            if op in _GEOMETRY_OPS:
                row = actual["geometry"][self.indices[_geometry_id(original)]]
                if op == "move_point":
                    wanted = [*operation["point_mm"], 0.0]
                    matched = math.dist(row[_POINT_KEYS[operation["position"]]], wanted) <= self.epsilon
                elif op == "set_radius":
                    previous = self.before["geometry"][original]
                    matched = (math.isclose(row["Radius"], operation["radius_mm"],
                                            abs_tol=self.numeric_epsilon, rel_tol=0)
                               and math.dist(previous["Center"], row["Center"]) <= self.numeric_epsilon
                               and all(math.isclose(row[key], previous[key], abs_tol=1e-7, rel_tol=0)
                                       for key in ("FirstParameter", "LastParameter") if key in previous))
                elif op == "set_length":
                    matched = (math.dist(row["StartPoint"], self.before["geometry"][original]["StartPoint"]) <= self.numeric_epsilon
                               and math.dist(row["EndPoint"], operation["end"]) <= self.numeric_epsilon)
                else:
                    matched = row["construction"] == operation["enabled"]
            else:
                row = actual["constraints"][self.constraint_indices[original]]
                if op == "set_constraint_value":
                    matched = math.isclose(row["Value"], operation["value"], abs_tol=1e-8)
                else:
                    matched = row["active" if op == "constraint_active" else "driving"] == operation["enabled"]
            if not matched:
                raise RuntimeError("Native Sketch edit did not read back " + op + " at " + str(original))
        shape = sketch.Shape
        if (not sketch.evaluateConstraints() or sketch.detectDegeneratedGeometries(self.epsilon) != 0
                or not shape.isNull() and not shape.isValid()):
            raise RuntimeError("Edited Sketch has invalid constraints or geometry")
        updated = []
        for item in self.actions:
            op, original = item["op"], item["index"]
            summary = {"op": op, "index": original}
            if op in ("set_radius", "set_length"):
                old = self.before["geometry"][original]
                new = actual["geometry"][self.indices[_geometry_id(original)]]
                if op == "set_radius":
                    summary.update(before_mm=old["Radius"], after_mm=new["Radius"])
                else:
                    summary.update(before_mm=math.dist(old["StartPoint"], old["EndPoint"]),
                                   after_mm=math.dist(new["StartPoint"], new["EndPoint"]))
            updated.append(summary)
        return {"geometry_count": len(actual["geometry"]), "constraint_count": len(actual["constraints"]),
                "added_geometry": len(self.added_geometry), "removed_geometry": len(self.deleted_geometry),
                "added_constraints": len(self.added_constraints), "removed_constraints": len(self.deleted_constraints),
                "updated": updated}
