"""Native constant-radius Fillet on explicit current predecessor edges."""

from REAgent.host.workspace.properties import fixed_dressup_false, number_readback, set_number
from REAgent.host.features.references import edge_dressup_context, edge_dressup_readback, set_dressup_base


class FilletOperations:
    def __init__(self, *, execute_native):
        self.execute_native = execute_native

    def contribute(self):
        return {
            'PartDesign_Fillet': self._create_fillet,
        }

    def _create_fillet(self, params):
        name = params["feature_name"]
        context = [None]

        def preflight(document, group, request):
            context[0] = edge_dressup_context(document, group, request)

        def mutate(document, group, request, changes):
            body, tip, edges = context[0]
            feature = document.addObject("PartDesign::Fillet", name)
            if feature.Name != name or feature.TypeId != "PartDesign::Fillet":
                raise RuntimeError("Native Fillet type or identity differs")
            body.addObject(feature)
            set_dressup_base(feature, tip, edges)
            set_number(feature, "Radius", request["radius_mm"])
            for prop in ("UseAllEdges", "Refine", "SupportTransform"):
                fixed_dressup_false(feature, prop)
            return feature

        def readback(document, group, feature, _state):
            common = edge_dressup_readback(document, feature, name, "PartDesign::Fillet", context[0])
            radius = number_readback(feature, "Radius", params["radius_mm"])
            return {**common, "radius_mm": radius}

        return self.execute_native(
            params, command="PartDesign_Fillet", preflight=preflight,
            mutate=mutate, readback=readback)
