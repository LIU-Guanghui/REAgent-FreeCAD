"""Bounded native Pad and Pocket with an explicit Sketch Profile."""

from functools import partial

from REAgent.host.workspace.properties import number_readback, set_bool, set_enum, set_number
from REAgent.host.features.construction import history_context, profile_readback, set_profile, shape_readback

_EXTRUDE_TYPES = {
    "PartDesign_Pad": "PartDesign::Pad",
    "PartDesign_Pocket": "PartDesign::Pocket",
}


class ExtrudeOperations:
    def __init__(self, *, execute_native):
        self.execute_native = execute_native

    def contribute(self):
        return {
            'PartDesign_Pad': partial(self._create_extrude, command='PartDesign_Pad'),
            'PartDesign_Pocket': partial(self._create_extrude, command='PartDesign_Pocket'),
        }

    def _create_extrude(self, params, *, command):
        if command not in _EXTRUDE_TYPES:
            raise ValueError("Unknown native extrude command")
        additive = command == "PartDesign_Pad"
        type_id = _EXTRUDE_TYPES[command]
        name = params["feature_name"]
        context = [None]

        def preflight(document, group, request):
            context[0] = history_context(
                document, group, request, require_solid=not additive, sketch=True)

        def mutate(document, group, request, changes):
            body, sketch, _tip = context[0]
            feature = document.addObject(type_id, name)
            if feature.Name != name or feature.TypeId != type_id:
                raise RuntimeError("Native extrude type or identity differs")
            body.addObject(feature)
            set_profile(feature, sketch)
            set_enum(feature, "Type", "Length")
            set_number(feature, "Length", request["length_mm"])
            set_bool(feature, "Reversed", request["reversed"])
            set_enum(feature, "SideType", "One side")
            set_bool(feature, "UseCustomVector", False)
            set_number(feature, "TaperAngle", 0.0)
            if "Refine" in feature.PropertiesList:
                set_bool(feature, "Refine", False)
            return feature

        def readback(document, group, feature, _state):
            body, sketch, tip = context[0]
            body_shape = shape_readback(document, body, feature, name, type_id, tip)
            profile_readback(feature, sketch)
            if feature.Type != "Length" or bool(feature.Reversed) != params["reversed"]:
                raise RuntimeError("Native extrude mode or direction did not read back")
            length = number_readback(feature, "Length", params["length_mm"])
            for prop in ("UseCustomVector", "Refine"):
                if prop in feature.PropertiesList and bool(getattr(feature, prop)):
                    raise RuntimeError("Native extrude fixed mode changed: " + prop)
            if feature.SideType != "One side":
                raise RuntimeError("Native extrude side mode changed")
            number_readback(feature, "TaperAngle", 0.0)
            return {"feature": feature.Name, "body": body.Name, "type_id": feature.TypeId,
                    "profile": sketch.Name, "tip": body.Tip.Name,
                    "mode": feature.Type, "length_mm": length,
                    "reversed": bool(feature.Reversed), "body_shape": body_shape}

        return self.execute_native(
            params, command=command, preflight=preflight,
            mutate=mutate, readback=readback,
        )
