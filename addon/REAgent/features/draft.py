"""Native Draft on exact current Target faces and an explicit neutral plane."""

from ..workspace.executor import ExecutorOperations
from ..workspace.properties import _fixed_dressup_false, _metadata, _number_readback, _set_bool, _set_number
from .common.construction import _history_context, _shape_readback
from .common.references import _current_faces, _link_sub_readback, _role_object, _set_face_base


class DraftOperations(ExecutorOperations):
    def _create_draft(self, params):
        name = params["feature_name"]
        context = [None]
        selected = [None]
        neutral = [None]

        def preflight(document, group, request):
            context[0] = _history_context(
                document, group, request, require_solid=True)
            body, _profile, tip = context[0]
            selected[0], evidence = _current_faces(
                body, group, request, tip, request["face_refs"],
                "draft_target", "face_refs")
            neutral[0] = _neutral_plane(document, body, group, tip, request, evidence)

        def mutate(document, group, request, changes):
            body, _profile, tip = context[0]
            feature = document.addObject("PartDesign::Draft", name)
            if feature.Name != name or feature.TypeId != "PartDesign::Draft":
                raise RuntimeError("Native Draft type or identity differs")
            body.addObject(feature)
            _set_face_base(feature, tip, selected[0])
            _set_neutral_plane(feature, *neutral[0])
            _set_number(feature, "Angle", request["angle_deg"])
            _set_bool(feature, "Reversed", request["reversed"])
            for prop in ("Refine", "SupportTransform"):
                _fixed_dressup_false(feature, prop)
            if feature.PullDirection is not None:
                raise RuntimeError("Native Draft has a custom PullDirection")
            return feature

        def readback(document, group, feature, _state):
            body, _profile, tip = context[0]
            body_shape = _shape_readback(
                document, body, feature, name, "PartDesign::Draft", tip)
            _link_sub_readback(feature, "Base", tip, selected[0])
            _link_sub_readback(feature, "NeutralPlane", *neutral[0])
            angle = _number_readback(feature, "Angle", params["angle_deg"])
            if bool(feature.Reversed) != params["reversed"]:
                raise RuntimeError("Native Draft direction changed")
            if feature.PullDirection is not None:
                raise RuntimeError("Native Draft PullDirection changed")
            for prop in ("Refine", "SupportTransform"):
                if prop in feature.PropertiesList and bool(getattr(feature, prop)):
                    raise RuntimeError("Native Draft fixed option changed: " + prop)
            return {"feature": name, "body": body.Name, "type_id": feature.TypeId,
                    "predecessor": tip.Name, "faces": selected[0],
                    "neutral_plane": {"object": neutral[0][0].Name,
                                      "subelements": neutral[0][1]},
                    "angle_deg": angle, "reversed": bool(feature.Reversed),
                    "tip": body.Tip.Name, "body_shape": body_shape}

        return self._execute_native_command(
            params, command="PartDesign_Draft", preflight=preflight,
            mutate=mutate, readback=readback)


def _neutral_plane(document, body, group, tip, request, evidence):
    choice = request["neutral_plane"]
    if choice["kind"] == "predecessor_face":
        names, _ = _current_faces(
            body, group, request, tip, [choice["face_ref"]],
            "draft_neutral", "neutral_plane.face_ref", evidence=evidence)
        detail = next(face for face in evidence["faces"] if face["name"] == names[0])
        if detail["surface_type"] != "Plane":
            raise ValueError("Draft neutral predecessor face must be planar")
        return tip, names
    if choice["kind"] == "origin_plane":
        origin = body.Origin
        if origin is None:
            raise ValueError("Draft Body has no native Origin")
        plane = _role_object(origin, choice["role"], "App::Plane", document)
        return plane, [""]
    raise ValueError("Unsupported Draft neutral plane kind")


def _set_neutral_plane(feature, object_ref, names):
    meta = _metadata(feature, "NeutralPlane")
    if not meta["writable"] or meta["codec"] != "link_sub":
        raise ValueError("Native Draft NeutralPlane is not a writable LinkSub")
    feature.NeutralPlane = (object_ref, names)
