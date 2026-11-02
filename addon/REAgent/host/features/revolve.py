"""Bounded Revolution and Groove with a native, owned ReferenceAxis."""

from functools import partial

from REAgent.host.workspace.properties import _metadata, _native_link, _number_readback, _set_bool, _set_enum, _set_number
from REAgent.host.features.support.construction import _history_context, _profile_readback, _set_profile, _shape_readback
from REAgent.host.features.support.references import _role_object

_REVOLVE_TYPES = {
    "PartDesign_Revolution": "PartDesign::Revolution",
    "PartDesign_Groove": "PartDesign::Groove",
}


def _reference_axis(document, body, sketch, request):
    kind = request["kind"]
    if kind == "sketch_axis":
        return sketch, "H_Axis" if request["axis"] == "horizontal" else "V_Axis"
    if kind == "datum_line":
        obj = document.getObject(request["datum_name"])
        if obj is None or obj.TypeId != "Part::DatumLine" or obj not in body.Group:
            raise ValueError("Revolution axis requires a same-Body Datum Line")
        return obj, ""
    if kind == "coordinate_system_axis":
        cs = document.getObject(request["coordinate_system_name"])
        if (cs is None or cs.TypeId != "Part::LocalCoordinateSystem"
                or cs.Document is not document or cs not in body.Group):
            raise ValueError("Revolution axis requires a same-Body Coordinate System")
        return _role_object(cs, request["role"], "App::Line", document), ""
    raise ValueError("Unsupported revolution axis kind")


class RevolveOperations:
    def __init__(self, *, execute_native):
        self.execute_native = execute_native

    def _create_revolve(self, params, *, command):
        if command not in _REVOLVE_TYPES:
            raise ValueError("Unknown native revolve command")
        additive = command == "PartDesign_Revolution"
        type_id = _REVOLVE_TYPES[command]
        name = params["feature_name"]
        context = [None]
        axis = [None]

        def preflight(document, group, request):
            context[0] = _history_context(
                document, group, request, require_solid=not additive, sketch=True)
            body, sketch, _tip = context[0]
            axis[0] = _reference_axis(document, body, sketch, request["axis"])

        def mutate(document, group, request, changes):
            body, sketch, _tip = context[0]
            feature = document.addObject(type_id, name)
            if feature.Name != name or feature.TypeId != type_id:
                raise RuntimeError("Native revolve type or identity differs")
            body.addObject(feature)
            _set_profile(feature, sketch)
            reference = _metadata(feature, "ReferenceAxis")
            if not reference["writable"] or reference["codec"] != "link_sub":
                raise ValueError("Native ReferenceAxis is not writable as a semantic link")
            feature.ReferenceAxis = (axis[0][0], [axis[0][1]])
            _set_enum(feature, "Type", "Angle")
            _set_number(feature, "Angle", request["angle_deg"])
            _set_bool(feature, "Reversed", request["reversed"])
            if "Midplane" in feature.PropertiesList:
                _set_bool(feature, "Midplane", False)
            if "Refine" in feature.PropertiesList:
                _set_bool(feature, "Refine", False)
            return feature

        def readback(document, group, feature, _state):
            body, sketch, tip = context[0]
            body_shape = _shape_readback(document, body, feature, name, type_id, tip)
            _profile_readback(feature, sketch)
            value = feature.ReferenceAxis
            if (_native_link(value) is not axis[0][0]
                    or not isinstance(value, (tuple, list))
                    or list(value[1]) != [axis[0][1]]):
                raise RuntimeError("Native ReferenceAxis did not retain its semantic link")
            if feature.Type != "Angle" or bool(feature.Reversed) != params["reversed"]:
                raise RuntimeError("Native revolve mode or direction did not read back")
            angle = _number_readback(feature, "Angle", params["angle_deg"])
            for prop in ("Midplane", "Refine"):
                if prop in feature.PropertiesList and bool(getattr(feature, prop)):
                    raise RuntimeError("Native revolve fixed mode changed: " + prop)
            return {"feature": feature.Name, "body": body.Name, "type_id": feature.TypeId,
                    "profile": sketch.Name, "axis_kind": params["axis"]["kind"],
                    "axis_object": axis[0][0].Name, "axis_subelement": axis[0][1],
                    "tip": body.Tip.Name, "mode": feature.Type, "angle_deg": angle,
                    "reversed": bool(feature.Reversed), "body_shape": body_shape}

        return self.execute_native(
            params, command=command, preflight=preflight,
            mutate=mutate, readback=readback,
        )

    def contribute(self):
        return {
            'PartDesign_Revolution': partial(self._create_revolve, command='PartDesign_Revolution'),
            'PartDesign_Groove': partial(self._create_revolve, command='PartDesign_Groove'),
        }
