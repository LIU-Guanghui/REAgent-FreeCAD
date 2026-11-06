"""Native equal/two-distance Chamfer on explicit current predecessor edges."""

from REAgent.host.workspace.properties import _fixed_dressup_false, _number_readback, _set_bool, _set_enum, _set_number
from REAgent.host.features.support.references import _edge_dressup_context, _edge_dressup_readback, _set_dressup_base

_MODES = {"equal_distance": "Equal distance", "two_distances": "Two distances"}


class ChamferOperations:
    def __init__(self, *, execute_native):
        self.execute_native = execute_native

    def _create_chamfer(self, params):
        name = params["feature_name"]
        context = [None]

        def preflight(document, group, request):
            context[0] = _edge_dressup_context(document, group, request)

        def mutate(document, group, request, changes):
            body, tip, edges = context[0]
            feature = document.addObject("PartDesign::Chamfer", name)
            if feature.Name != name or feature.TypeId != "PartDesign::Chamfer":
                raise RuntimeError("Native Chamfer type or identity differs")
            body.addObject(feature)
            _set_dressup_base(feature, tip, edges)
            _set_enum(feature, "ChamferType", _MODES[request["mode"]])
            _set_number(feature, "Size", request["size_mm"])
            if request["mode"] == "two_distances":
                _set_number(feature, "Size2", request["size2_mm"])
                _set_bool(feature, "FlipDirection", request["flip_direction"])
            else:
                _fixed_dressup_false(feature, "FlipDirection")
            for prop in ("UseAllEdges", "Refine", "SupportTransform"):
                _fixed_dressup_false(feature, prop)
            return feature

        def readback(document, group, feature, _state):
            common = _edge_dressup_readback(document, feature, name, "PartDesign::Chamfer", context[0])
            if feature.ChamferType != _MODES[params["mode"]]:
                raise RuntimeError("Native Chamfer mode changed")
            size = _number_readback(feature, "Size", params["size_mm"])
            size2 = None
            expected_flip = params.get("flip_direction", False)
            if params["mode"] == "two_distances":
                size2 = _number_readback(feature, "Size2", params["size2_mm"])
            if bool(feature.FlipDirection) != expected_flip:
                raise RuntimeError("Native Chamfer direction changed")
            return {**common, "mode": params["mode"], "size_mm": size, "size2_mm": size2,
                    "flip_direction": bool(feature.FlipDirection)}

        return self.execute_native(
            params, command="PartDesign_Chamfer", preflight=preflight,
            mutate=mutate, readback=readback)

    def contribute(self):
        return {
            'PartDesign_Chamfer': self._create_chamfer,
        }
