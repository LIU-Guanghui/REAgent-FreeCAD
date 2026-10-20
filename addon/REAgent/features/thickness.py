"""Native Skin Thickness with explicit openings, side and corner join."""

from ..workspace.executor import ExecutorOperations
from ..workspace.properties import _fixed_dressup_false, _number_readback, _set_bool, _set_enum, _set_number
from .common.construction import _history_context, _shape_readback
from .common.references import _current_faces, _link_sub_readback, _set_face_base

_JOIN = {"arc": "Arc", "intersection": "Intersection"}


class ThicknessOperations(ExecutorOperations):
    def _create_thickness(self, params):
        name = params["feature_name"]
        context = [None]
        openings = [None]

        def preflight(document, group, request):
            context[0] = _history_context(
                document, group, request, require_solid=True)
            body, _profile, tip = context[0]
            openings[0], _evidence = _current_faces(
                body, group, request, tip, request["face_refs"],
                "thickness_opening", "face_refs")

        def mutate(document, group, request, changes):
            body, _profile, tip = context[0]
            feature = document.addObject("PartDesign::Thickness", name)
            if feature.Name != name or feature.TypeId != "PartDesign::Thickness":
                raise RuntimeError("Native Thickness type or identity differs")
            body.addObject(feature)
            _set_face_base(feature, tip, openings[0])
            _set_number(feature, "Value", request["thickness_mm"])
            _set_enum(feature, "Mode", "Skin")
            _set_enum(feature, "Join", _JOIN[request["join"]])
            _set_bool(feature, "Reversed", request["direction"] == "inward")
            _set_bool(feature, "Intersection", False)
            for prop in ("Refine", "SupportTransform"):
                _fixed_dressup_false(feature, prop)
            return feature

        def readback(document, group, feature, _state):
            body, _profile, tip = context[0]
            body_shape = _shape_readback(
                document, body, feature, name, "PartDesign::Thickness", tip)
            _link_sub_readback(feature, "Base", tip, openings[0])
            thickness = _number_readback(
                feature, "Value", params["thickness_mm"])
            if feature.Mode != "Skin" or feature.Join != _JOIN[params["join"]]:
                raise RuntimeError("Native Thickness mode or join changed")
            inward = params["direction"] == "inward"
            if bool(feature.Reversed) != inward or bool(feature.Intersection):
                raise RuntimeError("Native Thickness direction or intersection changed")
            for prop in ("Refine", "SupportTransform"):
                if prop in feature.PropertiesList and bool(getattr(feature, prop)):
                    raise RuntimeError("Native Thickness fixed option changed: " + prop)
            return {"feature": name, "body": body.Name, "type_id": feature.TypeId,
                    "predecessor": tip.Name, "faces": openings[0],
                    "thickness_mm": thickness, "direction": params["direction"],
                    "join": params["join"], "mode": feature.Mode,
                    "intersection": bool(feature.Intersection),
                    "tip": body.Tip.Name, "body_shape": body_shape}

        return self._execute_native_command(
            params, command="PartDesign_Thickness", preflight=preflight,
            mutate=mutate, readback=readback)
