"""Native Skin Thickness with explicit openings, side and corner join."""

from REAgent.host.workspace.properties import fixed_dressup_false, number_readback, set_bool, set_enum, set_number
from REAgent.host.features.support.construction import history_context, shape_readback
from REAgent.host.features.support.references import current_faces, link_sub_readback, set_face_base

_JOIN = {"arc": "Arc", "intersection": "Intersection"}


class ThicknessOperations:
    def __init__(self, *, execute_native):
        self.execute_native = execute_native

    def contribute(self):
        return {
            'PartDesign_Thickness': self._create_thickness,
        }

    def _create_thickness(self, params):
        name = params["feature_name"]
        context = [None]
        openings = [None]

        def preflight(document, group, request):
            context[0] = history_context(
                document, group, request, require_solid=True)
            body, _profile, tip = context[0]
            openings[0], _evidence = current_faces(
                body, group, request, tip, request["face_refs"],
                "thickness_opening", "face_refs")

        def mutate(document, group, request, changes):
            body, _profile, tip = context[0]
            feature = document.addObject("PartDesign::Thickness", name)
            if feature.Name != name or feature.TypeId != "PartDesign::Thickness":
                raise RuntimeError("Native Thickness type or identity differs")
            body.addObject(feature)
            set_face_base(feature, tip, openings[0])
            set_number(feature, "Value", request["thickness_mm"])
            set_enum(feature, "Mode", "Skin")
            set_enum(feature, "Join", _JOIN[request["join"]])
            set_bool(feature, "Reversed", request["direction"] == "inward")
            set_bool(feature, "Intersection", False)
            for prop in ("Refine", "SupportTransform"):
                fixed_dressup_false(feature, prop)
            return feature

        def readback(document, group, feature, _state):
            body, _profile, tip = context[0]
            body_shape = shape_readback(
                document, body, feature, name, "PartDesign::Thickness", tip)
            link_sub_readback(feature, "Base", tip, openings[0])
            thickness = number_readback(
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

        return self.execute_native(
            params, command="PartDesign_Thickness", preflight=preflight,
            mutate=mutate, readback=readback)
