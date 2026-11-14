"""Native equal/two-distance Chamfer on explicit current predecessor edges."""

from REAgent.host.workspace.properties import fixed_dressup_false, number_readback, set_bool, set_enum, set_number
from REAgent.host.features.support.references import edge_dressup_context, edge_dressup_readback, set_dressup_base

_MODES = {"equal_distance": "Equal distance", "two_distances": "Two distances"}


class ChamferOperations:
    def __init__(self, *, execute_native):
        self.execute_native = execute_native

    def contribute(self):
        return {
            'PartDesign_Chamfer': self._create_chamfer,
        }

    def _create_chamfer(self, params):
        name = params["feature_name"]
        context = [None]

        def preflight(document, group, request):
            context[0] = edge_dressup_context(document, group, request)

        def mutate(document, group, request, changes):
            body, tip, edges = context[0]
            feature = document.addObject("PartDesign::Chamfer", name)
            if feature.Name != name or feature.TypeId != "PartDesign::Chamfer":
                raise RuntimeError("Native Chamfer type or identity differs")
            body.addObject(feature)
            set_dressup_base(feature, tip, edges)
            set_enum(feature, "ChamferType", _MODES[request["mode"]])
            set_number(feature, "Size", request["size_mm"])
            if request["mode"] == "two_distances":
                set_number(feature, "Size2", request["size2_mm"])
                set_bool(feature, "FlipDirection", request["flip_direction"])
            else:
                fixed_dressup_false(feature, "FlipDirection")
            for prop in ("UseAllEdges", "Refine", "SupportTransform"):
                fixed_dressup_false(feature, prop)
            return feature

        def readback(document, group, feature, _state):
            common = edge_dressup_readback(document, feature, name, "PartDesign::Chamfer", context[0])
            if feature.ChamferType != _MODES[params["mode"]]:
                raise RuntimeError("Native Chamfer mode changed")
            size = number_readback(feature, "Size", params["size_mm"])
            size2 = None
            expected_flip = params.get("flip_direction", False)
            if params["mode"] == "two_distances":
                size2 = number_readback(feature, "Size2", params["size2_mm"])
            if bool(feature.FlipDirection) != expected_flip:
                raise RuntimeError("Native Chamfer direction changed")
            return {**common, "mode": params["mode"], "size_mm": size, "size2_mm": size2,
                    "flip_direction": bool(feature.FlipDirection)}

        return self.execute_native(
            params, command="PartDesign_Chamfer", preflight=preflight,
            mutate=mutate, readback=readback)
