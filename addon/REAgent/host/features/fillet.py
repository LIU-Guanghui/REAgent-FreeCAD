"""Native constant-radius Fillet on explicit current predecessor edges."""

from REAgent.host.workspace.properties import _fixed_dressup_false, _number_readback, _set_number
from REAgent.host.features.support.construction import _history_context, _shape_readback
from REAgent.host.features.support.references import _dressup_base_readback, _dressup_edges, _set_dressup_base


class FilletOperations:
    def __init__(self, *, execute_native):
        self.execute_native = execute_native

    def _create_fillet(self, params):
        name = params["feature_name"]
        context = [None]
        edges = [None]

        def preflight(document, group, request):
            context[0] = _history_context(
                document, group, request, require_solid=True)
            body, _profile, tip = context[0]
            edges[0] = _dressup_edges(body, group, request, tip)

        def mutate(document, group, request, changes):
            body, _profile, tip = context[0]
            feature = document.addObject("PartDesign::Fillet", name)
            if feature.Name != name or feature.TypeId != "PartDesign::Fillet":
                raise RuntimeError("Native Fillet type or identity differs")
            body.addObject(feature)
            _set_dressup_base(feature, tip, edges[0])
            _set_number(feature, "Radius", request["radius_mm"])
            for prop in ("UseAllEdges", "Refine", "SupportTransform"):
                _fixed_dressup_false(feature, prop)
            return feature

        def readback(document, group, feature, _state):
            body, _profile, tip = context[0]
            body_shape = _shape_readback(
                document, body, feature, name, "PartDesign::Fillet", tip)
            _dressup_base_readback(feature, tip, edges[0])
            radius = _number_readback(feature, "Radius", params["radius_mm"])
            for prop in ("UseAllEdges", "Refine", "SupportTransform"):
                if prop in feature.PropertiesList and bool(getattr(feature, prop)):
                    raise RuntimeError("Native Fillet fixed option changed: " + prop)
            return {"feature": name, "body": body.Name, "type_id": feature.TypeId,
                    "predecessor": tip.Name, "edges": edges[0], "radius_mm": radius,
                    "tip": body.Tip.Name, "body_shape": body_shape}

        return self.execute_native(
            params, command="PartDesign_Fillet", preflight=preflight,
            mutate=mutate, readback=readback)

    def contribute(self):
        return {
            'PartDesign_Fillet': self._create_fillet,
        }
