"""One closed Sketch section along one whole continuous Sketch path."""

from functools import partial

from REAgent.host.workspace.properties import _metadata
from REAgent.host.features.support.construction import _history_context, _profile_readback, _section_or_path, _set_profile, _shape_readback, _whole_sketch_link

_PIPE_TYPES = {
    "PartDesign_AdditivePipe": "PartDesign::AdditivePipe",
    "PartDesign_SubtractivePipe": "PartDesign::SubtractivePipe",
}
_PIPE_FIXED_ENUMS = {
    "Transformation": "Constant",
    "Mode": "Standard",
    "Transition": "Transformed",
}


def _fixed_enum(feature, prop, value):
    meta = _metadata(feature, prop)
    if meta["codec"] != "string" or value not in meta.get("enum", []):
        raise ValueError("Native Pipe mode is unavailable: " + prop + "=" + value)
    if meta["writable"]:
        setattr(feature, prop, value)
    if getattr(feature, prop) != value:
        raise ValueError("Native Pipe mode cannot be fixed: " + prop + "=" + value)


def _fixed_false(feature, prop):
    meta = _metadata(feature, prop)
    if meta["codec"] != "bool":
        raise ValueError("Native Pipe option is not Boolean: " + prop)
    if meta["writable"]:
        setattr(feature, prop, False)
    if bool(getattr(feature, prop)):
        raise ValueError("Native Pipe option cannot be fixed to false: " + prop)


class PipeOperations:
    def __init__(self, *, execute_native):
        self.execute_native = execute_native

    def _create_pipe(self, params, *, command):
        if command not in _PIPE_TYPES:
            raise ValueError("Unknown native Pipe command")
        additive = command == "PartDesign_AdditivePipe"
        type_id = _PIPE_TYPES[command]
        name = params["feature_name"]
        context = [None]
        inputs = [None]
        path_closed = [None]

        def preflight(document, group, request):
            context[0] = _history_context(
                document, group, request, require_solid=not additive)
            body = context[0][0]
            if request["profile_sketch_name"] == request["path_sketch_name"]:
                raise ValueError("Pipe profile and path must be different Sketches")
            profile, _ = _section_or_path(
                document, body, request["profile_sketch_name"], closed=True)
            path, wire = _section_or_path(
                document, body, request["path_sketch_name"], closed=False, path=True)
            inputs[0] = (profile, path)
            path_closed[0] = bool(wire.isClosed())

        def mutate(document, group, request, changes):
            body = context[0][0]
            profile, path = inputs[0]
            feature = document.addObject(type_id, name)
            if feature.Name != name or feature.TypeId != type_id:
                raise RuntimeError("Native Pipe type or identity differs")
            body.addObject(feature)
            _set_profile(feature, profile)
            meta = _metadata(feature, "Spine")
            if not meta["writable"] or meta["codec"] not in ("link", "link_sub"):
                raise ValueError("Native Pipe Spine is not a writable whole-Sketch link")
            feature.Spine = path
            for prop, value in _PIPE_FIXED_ENUMS.items():
                _fixed_enum(feature, prop, value)
            for prop in ("Refine", "SpineTangent", "AuxiliarySpineTangent"):
                if prop in feature.PropertiesList:
                    _fixed_false(feature, prop)
            if list(feature.Sections) or feature.AuxiliarySpine is not None:
                raise ValueError("Native Pipe unexpectedly has extra sections or auxiliary path")
            return feature

        def readback(document, group, feature, _state):
            body, _profile, tip = context[0]
            profile, path = inputs[0]
            body_shape = _shape_readback(document, body, feature, name, type_id, tip)
            _profile_readback(feature, profile)
            if not _whole_sketch_link(feature.Spine, path) or path not in feature.OutList:
                raise RuntimeError("Native Pipe Spine did not retain the whole path Sketch")
            for prop, value in _PIPE_FIXED_ENUMS.items():
                if getattr(feature, prop) != value:
                    raise RuntimeError("Native Pipe fixed mode changed: " + prop)
            for prop in ("Refine", "SpineTangent", "AuxiliarySpineTangent"):
                if prop in feature.PropertiesList and bool(getattr(feature, prop)):
                    raise RuntimeError("Native Pipe fixed option changed: " + prop)
            if list(feature.Sections) or feature.AuxiliarySpine is not None:
                raise RuntimeError("Native Pipe introduced extra sections or auxiliary path")
            return {"feature": feature.Name, "body": body.Name, "type_id": type_id,
                    "profile": profile.Name, "path": path.Name,
                    "path_closed": path_closed[0], "tip": body.Tip.Name,
                    "fixed_modes": dict(_PIPE_FIXED_ENUMS), "body_shape": body_shape}

        return self.execute_native(
            params, command=command, preflight=preflight,
            mutate=mutate, readback=readback,
        )

    def contribute(self):
        return {
            'PartDesign_AdditivePipe': partial(self._create_pipe, command='PartDesign_AdditivePipe'),
            'PartDesign_SubtractivePipe': partial(self._create_pipe, command='PartDesign_SubtractivePipe'),
        }
