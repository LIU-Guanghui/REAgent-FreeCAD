"""Native one-direction tool-shape Linear and Polar Patterns."""

from ..workspace.executor import ExecutorOperations
from ..workspace.properties import (
    _fixed_dressup_false,
    _metadata,
    _number_readback,
    _set_bool,
    _set_enum,
    _set_number,
)
from .common.construction import _shape_readback
from .common.transforms import (
    _set_transform_reference,
    _set_transform_sources,
    _transform_axis,
    _transform_context,
    _transform_reference_readback,
    _transform_sources_readback,
)

_PATTERN_TYPES = {
    "PartDesign_LinearPattern": ("PartDesign::LinearPattern", "Direction"),
    "PartDesign_PolarPattern": ("PartDesign::PolarPattern", "Axis"),
}
_AXIS_PARENT_PROPERTY = "REAgentAxisParent"


def _native_extent_mode(feature, public_mode):
    meta = _metadata(feature, "Mode")
    if not meta["writable"] or meta["codec"] != "string":
        raise ValueError("Native pattern mode is not a writable enumeration")
    options = meta.get("enum", [])
    if public_mode == "overall":
        matches = [value for value in options
                   if "overall" in value.lower() or value.lower() == "extent"]
    else:
        matches = [value for value in options
                   if "offset" in value.lower() or value.lower() == "spacing"]
    if len(matches) != 1:
        raise ValueError("Native pattern mode has no unique overall/offset mapping")
    return matches[0]


def _one_direction(feature, *, write=False):
    if "Direction2" in feature.PropertiesList and feature.Direction2 is not None:
        raise ValueError("Second native pattern direction is linked")
    if "Occurrences2" in feature.PropertiesList:
        meta = _metadata(feature, "Occurrences2")
        if write and meta["writable"] and meta["codec"] == "integer":
            feature.Occurrences2 = 1
        if int(feature.Occurrences2) != 1:
            raise ValueError("Second native pattern direction is active")
    for prop in ("Spacings", "Spacings2"):
        if prop in feature.PropertiesList:
            values = list(getattr(feature, prop))
            if any(float(value) != -1.0 for value in values):
                raise ValueError("Nonuniform native pattern spacing is not supported")


class PatternOperations(ExecutorOperations):
    def _create_pattern(self, params, *, command):
        if command not in _PATTERN_TYPES:
            raise ValueError("Unknown native pattern command")
        type_id, axis_property = _PATTERN_TYPES[command]
        name = params["feature_name"]
        prepared = [None]
        sources = [None]
        axis = [None]
        axis_parent = [None]
        native_mode = [None]
        numeric_property = (
            "Length" if command == "PartDesign_LinearPattern" and params["extent_mode"] == "overall"
            else "Angle" if command == "PartDesign_PolarPattern" and params["extent_mode"] == "overall"
            else "Offset")
        numeric_field = "distance_mm" if command == "PartDesign_LinearPattern" else "angle_deg"

        def preflight(document, group, request):
            prepared[0], sources[0] = _transform_context(document, group, request)
            body, _profile, tip = prepared[0]
            axis[0], axis_parent[0] = _transform_axis(document, body, group, tip, request)
            if (command == "PartDesign_PolarPattern"
                    and request["extent_mode"] == "offset"
                    and request["angle_deg"] * (request["occurrences"] - 1) >= 360):
                raise ValueError("Polar offset copies must not duplicate or wrap past the original")

        def mutate(document, group, request, changes):
            body, _profile, _tip = prepared[0]
            feature = document.addObject(type_id, name)
            if feature.Name != name or feature.TypeId != type_id:
                raise RuntimeError("Native pattern type or identity differs")
            body.addObject(feature)
            _set_enum(feature, "TransformMode", "Features")
            _set_transform_sources(feature, sources[0])
            _set_transform_reference(feature, axis_property, axis[0])
            if axis_parent[0] is not None:
                # The child axis alone does not propagate parent CS motion to Pattern.
                if _AXIS_PARENT_PROPERTY in feature.PropertiesList:
                    raise RuntimeError("Native pattern already has the axis-parent dependency")
                feature.addProperty(
                    "App::PropertyLink", _AXIS_PARENT_PROPERTY, "REAgent",
                    "Parent Coordinate System dependency")
                parent_link = _metadata(feature, _AXIS_PARENT_PROPERTY)
                if not parent_link["writable"] or parent_link["codec"] != "link":
                    raise ValueError("Pattern axis-parent dependency is not a writable Link")
                setattr(feature, _AXIS_PARENT_PROPERTY, axis_parent[0])
            native_mode[0] = _native_extent_mode(feature, request["extent_mode"])
            _set_enum(feature, "Mode", native_mode[0])
            _set_number(feature, numeric_property, request[numeric_field])
            _set_bool(feature, "Reversed", request["reversed"])
            count = _metadata(feature, "Occurrences")
            if not count["writable"] or count["codec"] != "integer":
                raise ValueError("Native pattern Occurrences are not writable integers")
            feature.Occurrences = request["occurrences"]
            _one_direction(feature, write=True)
            if "Refine" in feature.PropertiesList:
                _fixed_dressup_false(feature, "Refine")
            body.Tip = feature
            return feature

        def readback(document, group, feature, _state):
            body, _profile, tip = prepared[0]
            body_shape = _shape_readback(document, body, feature, name, type_id, tip)
            _transform_sources_readback(feature, sources[0], tip)
            _transform_reference_readback(feature, axis_property, axis[0])
            if axis_parent[0] is not None:
                if (_AXIS_PARENT_PROPERTY not in feature.PropertiesList
                        or feature.getTypeIdOfProperty(_AXIS_PARENT_PROPERTY) != "App::PropertyLink"
                        or getattr(feature, _AXIS_PARENT_PROPERTY) is not axis_parent[0]
                        or axis_parent[0] not in feature.OutList
                        or axis_parent[0] not in body.Group
                        or axis[0][0] not in axis_parent[0].OriginFeatures):
                    raise RuntimeError("Pattern axis-parent dependency did not read back")
            elif _AXIS_PARENT_PROPERTY in feature.PropertiesList:
                raise RuntimeError("Pattern has an unexpected axis-parent dependency")
            if feature.TransformMode != "Features" or feature.Mode != native_mode[0]:
                raise RuntimeError("Native pattern mode did not read back")
            if (bool(feature.Reversed) != params["reversed"]
                    or int(feature.Occurrences) != params["occurrences"]):
                raise RuntimeError("Native pattern direction or count did not read back")
            extent = _number_readback(feature, numeric_property, params[numeric_field])
            _one_direction(feature)
            if "Refine" in feature.PropertiesList and bool(feature.Refine):
                raise RuntimeError("Native pattern Refine changed")
            return {
                "feature": name, "body": body.Name, "type_id": feature.TypeId,
                "predecessor": tip.Name,
                "source_features": [source.Name for source in sources[0]],
                "source_count": len(sources[0]), "mode": feature.TransformMode,
                "extent_mode": params["extent_mode"], "native_extent_mode": feature.Mode,
                "extent": extent, "extent_property": numeric_property,
                "occurrences": int(feature.Occurrences),
                "reversed": bool(feature.Reversed),
                "axis_kind": params["axis"]["kind"], "axis_object": axis[0][0].Name,
                "axis_subelements": axis[0][1],
                "axis_parent": axis_parent[0].Name if axis_parent[0] is not None else None,
                "tip": body.Tip.Name, "body_shape": body_shape,
            }

        return self._execute_native_command(
            params, command=command, preflight=preflight,
            mutate=mutate, readback=readback)
