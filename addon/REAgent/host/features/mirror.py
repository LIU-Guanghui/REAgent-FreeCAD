"""Native tool-shape mirror with caller-ordered sources and an explicit plane."""

from REAgent.host.workspace.properties import fixed_dressup_false, set_enum
from REAgent.host.features.construction import shape_readback
from REAgent.host.features.transforms import set_transform_reference, set_transform_sources, transform_context, transform_plane, transform_reference_readback, transform_sources_readback


class MirrorOperations:
    def __init__(self, *, execute_native):
        self.execute_native = execute_native

    def contribute(self):
        return {
            'PartDesign_Mirrored': self._create_mirror,
        }

    def _create_mirror(self, params):
        name = params["feature_name"]
        prepared = [None]
        sources = [None]
        plane = [None]

        def preflight(document, group, request):
            prepared[0], sources[0] = transform_context(document, group, request)
            body, _profile, tip = prepared[0]
            plane[0] = transform_plane(document, body, group, tip, request)

        def mutate(document, group, request, changes):
            body, _profile, _tip = prepared[0]
            feature = document.addObject("PartDesign::Mirrored", name)
            if feature.Name != name or feature.TypeId != "PartDesign::Mirrored":
                raise RuntimeError("Native Mirrored type or identity differs")
            body.addObject(feature)
            set_enum(feature, "TransformMode", "Features")
            set_transform_sources(feature, sources[0])
            set_transform_reference(feature, "MirrorPlane", plane[0])
            if "Refine" in feature.PropertiesList:
                fixed_dressup_false(feature, "Refine")
            body.Tip = feature
            return feature

        def readback(document, group, feature, _state):
            body, _profile, tip = prepared[0]
            body_shape = shape_readback(
                document, body, feature, name, "PartDesign::Mirrored", tip)
            transform_sources_readback(feature, sources[0], tip)
            transform_reference_readback(feature, "MirrorPlane", plane[0])
            if feature.TransformMode != "Features":
                raise RuntimeError("Native Mirrored transform mode changed")
            if "Refine" in feature.PropertiesList and bool(feature.Refine):
                raise RuntimeError("Native Mirrored Refine changed")
            return {
                "feature": name, "body": body.Name, "type_id": feature.TypeId,
                "predecessor": tip.Name,
                "source_features": [source.Name for source in sources[0]],
                "source_count": len(sources[0]), "mode": feature.TransformMode,
                "plane_kind": params["mirror_plane"]["kind"],
                "plane_object": plane[0][0].Name, "plane_subelements": plane[0][1],
                "tip": body.Tip.Name, "body_shape": body_shape,
            }

        return self.execute_native(
            params, command="PartDesign_Mirrored", preflight=preflight,
            mutate=mutate, readback=readback)
