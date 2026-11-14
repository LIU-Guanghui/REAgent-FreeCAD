"""Bounded native Hole from a circle-only Sketch in an owned solid Body."""

from REAgent.protocol.features import validate_feature_relations
from REAgent.host.workspace.properties import metadata, number_readback, set_bool, set_enum, set_number
from REAgent.host.features.support.construction import history_context, profile_readback, set_profile, shape_readback

_HOLE_ENUM = {
    "DepthType": {"dimension": "Dimension", "through_all": "ThroughAll"},
    "DrillPoint": {"flat": "Flat", "angled": "Angled"},
    "HoleCutType": {"none": "None", "counterbore": "Counterbore",
                    "countersink": "Countersink"},
}


def _set_circle_centers(feature):
    meta = metadata(feature, "BaseProfileType")
    if not meta["writable"] or meta["codec"] != "integer":
        raise ValueError("Native Hole center selection is unavailable")
    # FreeCAD 1.1.1: OnCircles=1<<1. Never inherit the user preference,
    # which may select sketch points or arcs instead of circles.
    feature.BaseProfileType = 2
    if feature.BaseProfileType != 2:
        raise RuntimeError("Native Hole did not select circle centers only")


def circle_profile(sketch):
    geometry = list(sketch.Geometry)
    if any(sketch.getConstruction(i) or type(item).__name__ != "Circle"
           for i, item in enumerate(geometry)):
        raise ValueError("Hole Profile must contain only non-construction circles")
    centers = [item.Center for item in geometry]
    if len(centers) != len({(round(c.x, 10), round(c.y, 10)) for c in centers}):
        raise ValueError("Hole Profile contains duplicate circle centers")
    return len(geometry)


class HoleOperations:
    def __init__(self, *, execute_native):
        self.execute_native = execute_native

    def contribute(self):
        return {
            'PartDesign_Hole': self._create_hole,
        }

    def _create_hole(self, params):
        name = params["feature_name"]
        context = [None]
        profile_circle_count = [None]
        enum_values = [None]

        def preflight(document, group, request):
            validate_feature_relations("PartDesign_Hole", request)
            context[0] = history_context(
                document, group, request, require_solid=True, sketch=True)
            profile_circle_count[0] = circle_profile(context[0][1])

        def mutate(document, group, request, changes):
            body, sketch, _tip = context[0]
            feature = document.addObject("PartDesign::Hole", name)
            if feature.Name != name or feature.TypeId != "PartDesign::Hole":
                raise RuntimeError("Native Hole type or identity differs")
            body.addObject(feature)
            set_profile(feature, sketch)
            _set_circle_centers(feature)
            set_enum(feature, "ThreadType", "None")
            set_number(feature, "Diameter", request["diameter_mm"])
            values = {}
            for prop, choice in (("DepthType", request["depth_mode"]),
                                 ("HoleCutType", request["mouth_mode"])):
                values[prop] = _HOLE_ENUM[prop][choice]
                set_enum(feature, prop, values[prop])
            if request["depth_mode"] == "dimension":
                set_number(feature, "Depth", request["depth_mm"])
                values["DrillPoint"] = _HOLE_ENUM["DrillPoint"][request["drill_point"]]
                set_enum(feature, "DrillPoint", values["DrillPoint"])
                if request["drill_point"] == "angled":
                    set_number(feature, "DrillPointAngle", request["drill_point_angle_deg"])
            angled_depth = (request["depth_mode"] == "dimension"
                            and request["drill_point"] == "angled")
            if angled_depth:
                set_bool(feature, "DrillForDepth", True)
            if request["mouth_mode"] != "none":
                set_bool(feature, "HoleCutCustomValues", True)
                set_number(feature, "HoleCutDiameter", request["mouth_diameter_mm"])
                if request["mouth_mode"] == "counterbore":
                    set_number(feature, "HoleCutDepth", request["mouth_depth_mm"])
                else:
                    set_number(feature, "HoleCutCountersinkAngle", request["mouth_angle_deg"])
            values["ThreadType"] = "None"
            set_bool(feature, "Tapered", False)
            if "Refine" in feature.PropertiesList:
                set_bool(feature, "Refine", False)
            set_bool(feature, "Reversed", request["reversed"])
            enum_values[0] = values
            return feature

        def readback(document, group, feature, _state):
            body, sketch, tip = context[0]
            body_shape = shape_readback(document, body, feature, name,
                                         "PartDesign::Hole", tip)
            profile_readback(feature, sketch)
            if feature.BaseProfileType != 2:
                raise RuntimeError("Native Hole circle-center mode changed")
            number_readback(feature, "Diameter", params["diameter_mm"])
            for prop, value in enum_values[0].items():
                if getattr(feature, prop) != value:
                    raise RuntimeError("Native Hole enumeration changed: " + prop)
            if params["depth_mode"] == "dimension":
                number_readback(feature, "Depth", params["depth_mm"])
                if params["drill_point"] == "angled":
                    number_readback(feature, "DrillPointAngle", params["drill_point_angle_deg"])
            if params["mouth_mode"] != "none":
                if not bool(feature.HoleCutCustomValues):
                    raise RuntimeError("Native Hole custom entrance dimensions were disabled")
                number_readback(feature, "HoleCutDiameter", params["mouth_diameter_mm"])
                if params["mouth_mode"] == "counterbore":
                    number_readback(feature, "HoleCutDepth", params["mouth_depth_mm"])
                else:
                    number_readback(feature, "HoleCutCountersinkAngle", params["mouth_angle_deg"])
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

        return self.execute_native(
            params, command="PartDesign_Hole", preflight=preflight,
            mutate=mutate, readback=readback,
        )
