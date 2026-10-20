"""Bounded native Hole from a circle-only Sketch in an owned solid Body."""

from ..workspace.executor import ExecutorOperations
from ..protocol.contract import validate_feature_request
from ..workspace.properties import _metadata, _number_readback, _set_bool, _set_enum, _set_number
from .common.construction import (
    _history_context,
    _profile_readback,
    _set_profile,
    _shape_readback,
)

_HOLE_ENUM = {
    "DepthType": {"dimension": "Dimension", "through_all": "ThroughAll"},
    "DrillPoint": {"flat": "Flat", "angled": "Angled"},
    "HoleCutType": {"none": "None", "counterbore": "Counterbore",
                    "countersink": "Countersink"},
}


def _set_circle_centers(feature):
    meta = _metadata(feature, "BaseProfileType")
    if not meta["writable"] or meta["codec"] != "integer":
        raise ValueError("Native Hole center selection is unavailable")
    # FreeCAD 1.1.1: OnCircles=1<<1. Never inherit the user preference,
    # which may select sketch points or arcs instead of circles.
    feature.BaseProfileType = 2
    if feature.BaseProfileType != 2:
        raise RuntimeError("Native Hole did not select circle centers only")


class HoleOperations(ExecutorOperations):
    def _create_hole(self, params):
        name = params["feature_name"]
        context = [None]
        profile_circle_count = [None]
        enum_values = [None]

        def preflight(document, group, request):
            validate_feature_request("PartDesign_Hole", request)
            context[0] = _history_context(
                document, group, request, require_solid=True, sketch=True)
            sketch = context[0][1]
            geometry = list(sketch.Geometry)
            if any(sketch.getConstruction(i) or type(item).__name__ != "Circle"
                   for i, item in enumerate(geometry)):
                raise ValueError("Hole Profile must contain only non-construction circles")
            centers = [item.Center for item in geometry]
            if len(centers) != len({(round(c.x, 10), round(c.y, 10)) for c in centers}):
                raise ValueError("Hole Profile contains duplicate circle centers")
            profile_circle_count[0] = len(geometry)

        def mutate(document, group, request, changes):
            body, sketch, _tip = context[0]
            feature = document.addObject("PartDesign::Hole", name)
            if feature.Name != name or feature.TypeId != "PartDesign::Hole":
                raise RuntimeError("Native Hole type or identity differs")
            body.addObject(feature)
            _set_profile(feature, sketch)
            _set_circle_centers(feature)
            _set_enum(feature, "ThreadType", "None")
            _set_number(feature, "Diameter", request["diameter_mm"])
            values = {}
            for prop, choice in (("DepthType", request["depth_mode"]),
                                 ("HoleCutType", request["mouth_mode"])):
                values[prop] = _HOLE_ENUM[prop][choice]
                _set_enum(feature, prop, values[prop])
            if request["depth_mode"] == "dimension":
                _set_number(feature, "Depth", request["depth_mm"])
                values["DrillPoint"] = _HOLE_ENUM["DrillPoint"][request["drill_point"]]
                _set_enum(feature, "DrillPoint", values["DrillPoint"])
                if request["drill_point"] == "angled":
                    _set_number(feature, "DrillPointAngle", request["drill_point_angle_deg"])
            angled_depth = (request["depth_mode"] == "dimension"
                            and request["drill_point"] == "angled")
            if angled_depth:
                _set_bool(feature, "DrillForDepth", True)
            if request["mouth_mode"] != "none":
                _set_bool(feature, "HoleCutCustomValues", True)
                _set_number(feature, "HoleCutDiameter", request["mouth_diameter_mm"])
                if request["mouth_mode"] == "counterbore":
                    _set_number(feature, "HoleCutDepth", request["mouth_depth_mm"])
                else:
                    _set_number(feature, "HoleCutCountersinkAngle", request["mouth_angle_deg"])
            values["ThreadType"] = "None"
            _set_bool(feature, "Tapered", False)
            if "Refine" in feature.PropertiesList:
                _set_bool(feature, "Refine", False)
            _set_bool(feature, "Reversed", request["reversed"])
            enum_values[0] = values
            return feature

        def readback(document, group, feature, _state):
            body, sketch, tip = context[0]
            body_shape = _shape_readback(document, body, feature, name,
                                         "PartDesign::Hole", tip)
            _profile_readback(feature, sketch)
            if feature.BaseProfileType != 2:
                raise RuntimeError("Native Hole circle-center mode changed")
            _number_readback(feature, "Diameter", params["diameter_mm"])
            for prop, value in enum_values[0].items():
                if getattr(feature, prop) != value:
                    raise RuntimeError("Native Hole enumeration changed: " + prop)
            if params["depth_mode"] == "dimension":
                _number_readback(feature, "Depth", params["depth_mm"])
                if params["drill_point"] == "angled":
                    _number_readback(feature, "DrillPointAngle", params["drill_point_angle_deg"])
            if params["mouth_mode"] != "none":
                if not bool(feature.HoleCutCustomValues):
                    raise RuntimeError("Native Hole custom entrance dimensions were disabled")
                _number_readback(feature, "HoleCutDiameter", params["mouth_diameter_mm"])
                if params["mouth_mode"] == "counterbore":
                    _number_readback(feature, "HoleCutDepth", params["mouth_depth_mm"])
                else:
                    _number_readback(feature, "HoleCutCountersinkAngle", params["mouth_angle_deg"])
            elif bool(feature.HoleCutCustomValues):
                raise RuntimeError("Native plain Hole enabled custom entrance dimensions")
            for prop in ("Threaded", "ModelThread", "Tapered", "Refine"):
                if prop in feature.PropertiesList and bool(getattr(feature, prop)):
                    raise RuntimeError("Native Hole fixed mode changed: " + prop)
            expected_depth = (params["depth_mode"] == "dimension"
                              and params["drill_point"] == "angled")
            if expected_depth or "DrillForDepth" in feature.PropertiesList:
                if bool(feature.DrillForDepth) != expected_depth:
                    raise RuntimeError("Native Hole drill-depth mode changed")
            if bool(feature.Reversed) != params["reversed"]:
                raise RuntimeError("Native Hole direction changed")
            return {"feature": feature.Name, "body": body.Name,
                    "type_id": feature.TypeId, "profile": sketch.Name,
                    "profile_circle_count": profile_circle_count[0], "tip": body.Tip.Name,
                    "depth_mode": params["depth_mode"], "mouth_mode": params["mouth_mode"],
                    "reversed": bool(feature.Reversed), "body_shape": body_shape}

        return self._execute_native_command(
            params, command="PartDesign_Hole", preflight=preflight,
            mutate=mutate, readback=readback,
        )
