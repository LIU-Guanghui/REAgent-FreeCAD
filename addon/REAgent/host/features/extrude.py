"""Bounded native Pad and Pocket with an explicit Sketch Profile."""

from functools import partial

from REAgent.host.workspace.properties import _number_readback, _set_bool, _set_enum, _set_number
from REAgent.host.features.support.construction import _history_context, _profile_readback, _set_profile, _shape_readback

_EXTRUDE_TYPES = {
    "PartDesign_Pad": "PartDesign::Pad",
    "PartDesign_Pocket": "PartDesign::Pocket",
}


class ExtrudeOperations:
    def __init__(self, *, execute_native):
        self.execute_native = execute_native

    def _create_extrude(self, params, *, command):
        if command not in _EXTRUDE_TYPES:
            raise ValueError("Unknown native extrude command")
        additive = command == "PartDesign_Pad"
        type_id = _EXTRUDE_TYPES[command]
        name = params["feature_name"]
        context = [None]

        def preflight(document, group, request):
            context[0] = _history_context(
                document, group, request, require_solid=not additive, sketch=True)

        def mutate(document, group, request, changes):
            body, sketch, _tip = context[0]
            feature = document.addObject(type_id, name)
            if feature.Name != name or feature.TypeId != type_id:
                raise RuntimeError("Native extrude type or identity differs")
            body.addObject(feature)
            _set_profile(feature, sketch)
            _set_enum(feature, "Type", "Length")
            _set_number(feature, "Length", request["length_mm"])
            _set_bool(feature, "Reversed", request["reversed"])
            _set_enum(feature, "SideType", "One side")
            _set_bool(feature, "UseCustomVector", False)
            _set_number(feature, "TaperAngle", 0.0)
            if "Refine" in feature.PropertiesList:
                _set_bool(feature, "Refine", False)
            return feature

        def readback(document, group, feature, _state):
            body, sketch, tip = context[0]
            body_shape = _shape_readback(document, body, feature, name, type_id, tip)
            _profile_readback(feature, sketch)
            if feature.Type != "Length" or bool(feature.Reversed) != params["reversed"]:
                raise RuntimeError("Native extrude mode or direction did not read back")
            length = _number_readback(feature, "Length", params["length_mm"])
            for prop in ("UseCustomVector", "Refine"):
                if prop in feature.PropertiesList and bool(getattr(feature, prop)):
                    raise RuntimeError("Native extrude fixed mode changed: " + prop)
            if feature.SideType != "One side":
                raise RuntimeError("Native extrude side mode changed")
            _number_readback(feature, "TaperAngle", 0.0)
            return {"feature": feature.Name, "body": body.Name, "type_id": feature.TypeId,
                    "profile": sketch.Name, "tip": body.Tip.Name,
                    "mode": feature.Type, "length_mm": length,
                    "reversed": bool(feature.Reversed), "body_shape": body_shape}

        return self.execute_native(
            params, command=command, preflight=preflight,
            mutate=mutate, readback=readback,
        )

    def contribute(self):
        return {
            'PartDesign_Pad': partial(self._create_extrude, command='PartDesign_Pad'),
            'PartDesign_Pocket': partial(self._create_extrude, command='PartDesign_Pocket'),
        }
