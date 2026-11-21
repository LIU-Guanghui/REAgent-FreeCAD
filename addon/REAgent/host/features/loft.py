"""Two ordered, closed Sketch sections for native additive/subtractive Loft."""

from functools import partial

from REAgent.host.workspace.properties import metadata
from REAgent.host.features.construction import distinct_section_planes, history_context, profile_readback, section_or_path, set_profile, shape_readback, whole_sketch_link

_LOFT_TYPES = {
    "PartDesign_AdditiveLoft": "PartDesign::AdditiveLoft",
    "PartDesign_SubtractiveLoft": "PartDesign::SubtractiveLoft",
}


def _fixed_false(feature, prop):
    meta = metadata(feature, prop)
    if meta["codec"] != "bool":
        raise ValueError("Native Loft option is not Boolean: " + prop)
    if meta["writable"]:
        setattr(feature, prop, False)
    if bool(getattr(feature, prop)):
        raise ValueError("Native Loft option cannot be fixed to false: " + prop)


class LoftOperations:
    def __init__(self, *, execute_native):
        self.execute_native = execute_native

    def contribute(self):
        return {
            'PartDesign_AdditiveLoft': partial(self._create_loft, command='PartDesign_AdditiveLoft'),
            'PartDesign_SubtractiveLoft': partial(self._create_loft, command='PartDesign_SubtractiveLoft'),
        }

    def _create_loft(self, params, *, command):
        if command not in _LOFT_TYPES:
            raise ValueError("Unknown native Loft command")
        additive = command == "PartDesign_AdditiveLoft"
        type_id = _LOFT_TYPES[command]
        name = params["feature_name"]
        context = [None]
        sections = [None]

        def preflight(document, group, request):
            context[0] = history_context(
                document, group, request, require_solid=not additive)
            body = context[0][0]
            if request["profile_sketch_name"] == request["section_sketch_name"]:
                raise ValueError("Loft requires two distinct Sketch sections")
            first, _ = section_or_path(
                document, body, request["profile_sketch_name"], closed=True)
            second, _ = section_or_path(
                document, body, request["section_sketch_name"], closed=True)
            distinct_section_planes(first, second)
            sections[0] = (first, second)

        def mutate(document, group, request, changes):
            body = context[0][0]
            first, second = sections[0]
            feature = document.addObject(type_id, name)
            if feature.Name != name or feature.TypeId != type_id:
                raise RuntimeError("Native Loft type or identity differs")
            body.addObject(feature)
            set_profile(feature, first)
            meta = metadata(feature, "Sections")
            if not meta["writable"] or meta["codec"] not in ("link_list", "link_sub_list"):
                raise ValueError("Native Loft Sections is not a writable ordered Sketch list")
            feature.Sections = [second]
            for prop in ("Closed", "Ruled", "Refine"):
                if prop in feature.PropertiesList:
                    _fixed_false(feature, prop)
            return feature

        def readback(document, group, feature, _state):
            body, _profile, tip = context[0]
            first, second = sections[0]
            body_shape = shape_readback(document, body, feature, name, type_id, tip)
            profile_readback(feature, first)
            linked = list(feature.Sections)
            if (len(linked) != 1 or not whole_sketch_link(linked[0], second)
                    or second not in feature.OutList):
                raise RuntimeError("Native Loft section order or dependency changed")
            for prop in ("Closed", "Ruled", "Refine"):
                if prop in feature.PropertiesList and bool(getattr(feature, prop)):
                    raise RuntimeError("Native Loft fixed option changed: " + prop)
            return {"feature": feature.Name, "body": body.Name, "type_id": type_id,
                    "profile": first.Name, "sections": [second.Name],
                    "tip": body.Tip.Name, "body_shape": body_shape}

        return self.execute_native(
            params, command=command, preflight=preflight,
            mutate=mutate, readback=readback,
        )
