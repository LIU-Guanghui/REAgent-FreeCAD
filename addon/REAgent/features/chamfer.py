"""Native equal/two-distance Chamfer on explicit current predecessor edges."""

from ..workspace.executor import ExecutorOperations
from ..protocol.contract import validate_feature_request
from ..workspace.properties import _fixed_dressup_false, _number_readback, _set_bool, _set_enum, _set_number
from .common.construction import _history_context, _shape_readback
from .common.references import _dressup_base_readback, _dressup_edges, _set_dressup_base

_MODES = {"equal_distance": "Equal distance", "two_distances": "Two distances"}


class ChamferOperations(ExecutorOperations):
    def _create_chamfer(self, params):
        name = params["feature_name"]
        context = [None]
        edges = [None]

        def preflight(document, group, request):
            validate_feature_request("PartDesign_Chamfer", request)
            context[0] = _history_context(
                document, group, request, require_solid=True)
            body, _profile, tip = context[0]
            edges[0] = _dressup_edges(body, group, request, tip)

        def mutate(document, group, request, changes):
            body, _profile, tip = context[0]
            feature = document.addObject("PartDesign::Chamfer", name)
            if feature.Name != name or feature.TypeId != "PartDesign::Chamfer":
                raise RuntimeError("Native Chamfer type or identity differs")
            body.addObject(feature)
            _set_dressup_base(feature, tip, edges[0])
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
            body, _profile, tip = context[0]
            body_shape = _shape_readback(
                document, body, feature, name, "PartDesign::Chamfer", tip)
            _dressup_base_readback(feature, tip, edges[0])
            if feature.ChamferType != _MODES[params["mode"]]:
                raise RuntimeError("Native Chamfer mode changed")
            size = _number_readback(feature, "Size", params["size_mm"])
            size2 = None
            expected_flip = params.get("flip_direction", False)
            if params["mode"] == "two_distances":
                size2 = _number_readback(feature, "Size2", params["size2_mm"])
            if bool(feature.FlipDirection) != expected_flip:
                raise RuntimeError("Native Chamfer direction changed")
            for prop in ("UseAllEdges", "Refine", "SupportTransform"):
                if prop in feature.PropertiesList and bool(getattr(feature, prop)):
                    raise RuntimeError("Native Chamfer fixed option changed: " + prop)
            return {"feature": name, "body": body.Name, "type_id": feature.TypeId,
                    "predecessor": tip.Name, "edges": edges[0],
                    "mode": params["mode"], "size_mm": size, "size2_mm": size2,
                    "flip_direction": bool(feature.FlipDirection),
                    "tip": body.Tip.Name, "body_shape": body_shape}

        return self._execute_native_command(
            params, command="PartDesign_Chamfer", preflight=preflight,
            mutate=mutate, readback=readback)
