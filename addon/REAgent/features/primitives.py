"""Sixteen bounded native PartDesign additive/subtractive primitives."""

import math

import FreeCAD as App

from ..protocol.contract import PRIMITIVE_COMMANDS
from ..validation.structure import _single_solid_summary, result_state
from ..workspace.executor import ExecutorOperations
from ..workspace.model import _placement_summary, _session_bodies, _world_shape
from ..workspace.properties import _metadata, _native_number, _same_placement


def _read_number(obj, name, expected, *, integer=False):
    actual = _native_number(getattr(obj, name))
    if integer:
        if actual != expected:
            raise RuntimeError("Native primitive integer property did not read back: " + name)
    elif (not math.isfinite(actual) or
          abs(actual - expected) > max(1e-9, abs(expected) * 1e-12)):
        raise RuntimeError("Native primitive numeric property did not read back: " + name)
    return int(actual) if integer else actual


def _validate_family(request, family):
    if family == "Cone" and request["radius1_mm"] == request["radius2_mm"] == 0:
        raise ValueError("Cone radii cannot both be zero")
    if family == "Torus" and request["radius1_mm"] <= request["radius2_mm"]:
        raise ValueError("Torus requires Radius1 > Radius2 > 0")
    if family == "Wedge":
        x0, x1 = request["xmin_mm"], request["xmax_mm"]
        y0, y1 = request["ymin_mm"], request["ymax_mm"]
        z0, z1 = request["zmin_mm"], request["zmax_mm"]
        x20, x21 = request["x2min_mm"], request["x2max_mm"]
        z20, z21 = request["z2min_mm"], request["z2max_mm"]
        if not (x0 < x1 and y0 < y1 and z0 < z1 and x20 <= x21 and z20 <= z21):
            raise ValueError("Wedge requires positive front spans and ordered back coordinates")
        if x20 == x21 and z20 == z21:
            raise ValueError("Wedge point back face is not supported")


class PrimitiveOperations(ExecutorOperations):
    def _create_primitive(self, params, *, command):

        spec = PRIMITIVE_COMMANDS[command]
        name, body_name = params["feature_name"], params["body_name"]
        prior_tip = [None]
        requested_placement = [None]

        def preflight(document, group, request):
            body = document.getObject(body_name)
            if body not in _session_bodies(group):
                raise ValueError("Primitive Body is not owned by this session")
            if document.getObject(name) is not None:
                raise ValueError("Primitive feature name already exists: " + name)
            result_state(group)  # Require a known, structurally sound starting state.
            _validate_family(request, spec["family"])
            if spec["oriented"]:
                axis = request["axis"]
                if math.sqrt(sum(component * component for component in axis)) < 1e-12:
                    raise ValueError("Primitive rotation axis must be nonzero")
                rotation = App.Rotation(App.Vector(*axis), request["angle_deg"])
            else:
                rotation = App.Rotation()
            requested_placement[0] = App.Placement(
                App.Vector(*request[spec["position_field"]]), rotation)
            prior_tip[0] = body.Tip
            shape = getattr(body, "Shape", None)
            prior = None
            if body.Tip is not None and body.Tip.TypeId not in (
                    "Sketcher::SketchObject", "Part::DatumPlane", "Part::DatumLine",
                    "Part::DatumPoint", "Part::LocalCoordinateSystem"):
                prior = _single_solid_summary(shape, "Receiving Body")
            elif shape is not None and not shape.isNull() and len(shape.Solids) != 0:
                raise ValueError("Receiving Body has a solid without a solid Tip")
            if not spec["additive"] and prior is None:
                raise ValueError("Subtractive primitive requires a prior valid single solid")

        def mutate(document, group, request, changes):
            body = document.getObject(body_name)
            feature = document.addObject(spec["type_id"], name)
            if feature.Name != name or feature.TypeId != spec["type_id"]:
                raise RuntimeError("Native primitive identity or TypeId differs from the contract")
            body.addObject(feature)
            for field, native, rule in spec["inputs"]:
                meta = _metadata(feature, native)
                if not meta["writable"] or meta["codec"] not in (
                        ("integer",) if rule == "polygon" else ("quantity", "number")):
                    raise ValueError("Native primitive dimension is not writable: " + native)
                setattr(feature, native, request[field])
            for native, value in spec["fixed"]:
                meta = _metadata(feature, native)
                if not meta["writable"] or meta["codec"] not in ("quantity", "number"):
                    raise ValueError("Native primitive fixed mode is not writable: " + native)
                setattr(feature, native, value)
            if "MapMode" in feature.PropertiesList:
                mode = _metadata(feature, "MapMode")
                if mode["codec"] != "string" or "Deactivated" not in mode.get("enum", []):
                    raise ValueError("Native primitive free placement is unavailable")
                if mode["value"] != "Deactivated":
                    if not mode["writable"]:
                        raise ValueError("Native primitive MapMode cannot be deactivated")
                    feature.MapMode = "Deactivated"
            placement = _metadata(feature, "Placement")
            if not placement["writable"] or placement["codec"] != "placement":
                raise ValueError("Native primitive Placement is not writable")
            feature.Placement = requested_placement[0]
            return feature

        def readback(document, group, feature, _state):
            body = document.getObject(body_name)
            if (document.getObject(name) is not feature
                    or feature.TypeId != spec["type_id"]
                    or feature not in body.Group or body.Tip is not feature
                    or prior_tip[0] is feature):
                raise RuntimeError("Native primitive identity, ownership or Tip did not read back")
            properties = {}
            for field, native, rule in spec["inputs"]:
                properties[native] = _read_number(
                    feature, native, params[field], integer=(rule == "polygon"))
            for native, expected in spec["fixed"]:
                properties[native] = _read_number(feature, native, expected)
            if not _same_placement(feature.Placement, requested_placement[0]):
                raise RuntimeError("Native primitive Placement did not read back")
            if not _same_placement(body.Placement, App.Placement()):
                if (not hasattr(body, "getGlobalPlacement")
                        or not hasattr(feature, "getGlobalPlacement")
                        or not _same_placement(
                            feature.getGlobalPlacement(),
                            body.getGlobalPlacement().multiply(requested_placement[0]))):
                    raise RuntimeError("Nonidentity Body-local primitive placement is unconfirmed")
            if "MapMode" in feature.PropertiesList and feature.MapMode != "Deactivated":
                raise RuntimeError("Native primitive unexpectedly acquired an attachment")
            for support_name in ("AttachmentSupport", "Support"):
                if support_name in feature.PropertiesList and getattr(feature, support_name):
                    raise RuntimeError("Native primitive unexpectedly acquired support")
            feature_shape = _single_solid_summary(feature.Shape, "Primitive feature")
            body_shape = _single_solid_summary(_world_shape(body), "Receiving Body")
            if abs(feature_shape["volume_mm3"] - body_shape["volume_mm3"]) > max(
                    1e-9, body_shape["volume_mm3"] * 1e-12):
                raise RuntimeError("Native primitive Tip and Body volume summaries disagree")
            return {"feature": feature.Name, "body": body.Name,
                    "type_id": feature.TypeId, "tip": body.Tip.Name,
                    "properties": properties, "placement": _placement_summary(feature),
                    "body_shape": body_shape}

        return self._execute_native_command(
            params, command=command, preflight=preflight,
            mutate=mutate, readback=readback,
        )
