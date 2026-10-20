"""Role-local geometry observations and selection queries."""

import math
import os

from ..analysis.evidence import _classify_points, _geometry_evidence, _semantic_regions
from ..analysis.measurements import _shape_summary
from ..analysis.predecessor import infer_virtual_predecessor_bounds
from ..analysis.references import _edge_names, _face_names, _semantic_entity_ref_from_detail
from ..analysis.selection import (
    _dressup_edge_queries,
    _dressup_edge_selectors,
    _dressup_face_selectors,
    _feature_semantic_refs,
    _resolve_dressup_selection,
    _resolve_face_selection,
)
from ..analysis.topology import _geometric_relations, _resolve_geometry_selectors
from ..runtime import _document, _state
from ..protocol.contract import DRESSUP_SCOPE
from ..validation.comparison import _residual_regions
from ..validation.structure import result_state
from ..validation.tolerance import _ir_tolerance_contract, _source_tolerance_contract
from .model import (
    _file_sha256,
    _owned_objects,
    _placement_summary,
    _predecessor_shape,
    _result_object,
    _result_shape,
    _session_bodies,
    _source_objects,
    _target_state,
    _workspace,
    _world_shape,
)
from .state import _source_view_revision, _target_view_revision


class ObservationOperations:
    def _with_observation_revision(self, document_name, result):
        """Give every successful role-local observation one top-level revision."""
        if not isinstance(result, dict):
            raise RuntimeError("Native observation did not return a result object")
        revision = result.get("revision")
        if revision is None:
            if _state.INSTANCE_ROLE == "source":
                _document_object, source = _source_objects(document_name)
                revision = _source_view_revision(source)
            else:
                document = _document(document_name)
                group = _workspace(document)
                revision = _target_view_revision(document, group, group)
            result["revision"] = revision
        if not isinstance(revision, str) or not revision:
            raise RuntimeError("Native observation has no string model revision")
        return result


    def _analyze_source(self, params):
        document, source = _source_objects(params.get("document"))
        shape = source.Shape
        summary = _shape_summary(shape)
        tolerance_contract = _source_tolerance_contract(source)
        tolerance = tolerance_contract["linear_tolerance_mm"]

        source_path = getattr(source, "SourceFile", None)
        expected_sha256 = getattr(source, "SourceSHA256", None)
        source_exists = isinstance(source_path, str) and os.path.isfile(source_path)
        current_sha256 = _file_sha256(source_path) if source_exists else None

        return {
            "document": document.Name,
            "source": source.Name,
            "tolerance_mm": tolerance,
            "tolerance_contract": tolerance_contract,
            "input_audit": {
                "source_path": source_path,
                "source_exists": source_exists,
                "expected_sha256": expected_sha256,
                "current_sha256": current_sha256,
                "sha256_matches": bool(
                    source_exists
                    and expected_sha256
                    and current_sha256 == expected_sha256
                ),
                "units": "mm",
                "connected_component_count": summary["solid_count"],
                "manifold_candidate": bool(
                    summary["is_valid"]
                    and summary["is_closed"]
                    and summary["solid_count"] > 0
                ),
                "supported_single_solid": bool(
                    summary["is_valid"]
                    and summary["is_closed"]
                    and summary["solid_count"] == 1
                ),
                "characteristic_length_mm": math.sqrt(
                    summary["bounding_box_mm"]["x_length"] ** 2
                    + summary["bounding_box_mm"]["y_length"] ** 2
                    + summary["bounding_box_mm"]["z_length"] ** 2
                ),
                "placement": _placement_summary(source),
            },
            "shape": summary,
        }


    def _inspect_geometry(self, params):
        if _state.INSTANCE_ROLE == "source":
            if params.get("object") is not None:
                raise ValueError(
                    "Source FaceN/EdgeN are subelements: use entity.query.faces/edges; "
                    "object names are available only for Target owned objects"
                )
            document, source = _source_objects(params.get("document"))
            tolerance_contract = _source_tolerance_contract(source)
            shape = source.Shape
            role = "source"
            owner = None
            target_scope = None
        elif _state.INSTANCE_ROLE == "rebuild":
            (
                document,
                _source,
                rebuild,
                reconstruction_ir,
            ) = _target_state(params.get("document"))
            tolerance_contract = _ir_tolerance_contract(reconstruction_ir)
            tip = _result_object(rebuild)
            requested_name = params.get("object")
            if requested_name is None:
                if tip is None:
                    raise ValueError("Target Rebuild body has no current Tip to inspect")
                inspected = tip
                shape = _predecessor_shape(rebuild)
                target_scope = {
                    "kind": "current_predecessor",
                    "requested_object": None,
                    "shape_object": tip.Name,
                    "selection_body": rebuild.Name,
                    "current_result_tip": tip.Name,
                    "is_current_result": True,
                    "is_current_predecessor": True,
                }
            else:
                inspected = document.getObject(requested_name)
                if inspected is None or inspected not in _owned_objects(rebuild):
                    raise ValueError(
                        "entity.query.object must name an owned Target object"
                    )
                shape = _world_shape(inspected)
                owning_body = next((body for body in _session_bodies(rebuild)
                                    if inspected in body.Group), None)
                current_predecessor = bool(owning_body is not None and owning_body.Tip is inspected)
                target_scope = {
                    "kind": "owned_object",
                    "requested_object": inspected.Name,
                    "shape_object": inspected.Name,
                    "current_result_tip": tip.Name if tip else None,
                    "selection_body": owning_body.Name if owning_body else None,
                    "is_current_result": bool(
                        inspected is rebuild or inspected is tip
                    ),
                    "is_current_predecessor": current_predecessor,
                }
            role = "target"
            owner = {
                "name": inspected.Name,
                "label": inspected.Label,
                "type_id": inspected.TypeId,
            }
        else:
            raise ValueError("Entity observation requires Source or Target role")
        tolerance = tolerance_contract["linear_tolerance_mm"]
        include = params.get("include", [])
        writable_refs = bool(role == "target" and target_scope["is_current_predecessor"]
                             and shape is not None and not shape.isNull()
                             and shape.isValid() and len(shape.Solids) == 1
                             and not any({str(value).lower() for value in obj.State}
                                         & {"touched", "invalid", "error"}
                                         for obj in _owned_objects(rebuild)))
        if "flags" in params.get("fields", []):
            raise ValueError("Quality flags are not exposed by entity observations")
        observation_only = role == "target" and not writable_refs
        if observation_only and (params.get("selectors") or "stable_selection" in include
                or {"semantic_ref", "stable_selector"} & set(params.get("fields", []))):
            raise ValueError("Object observation has no writable current-predecessor selectors/refs")
        if shape is None or shape.isNull():
            return {"document": document.Name, "role": role, "owning_feature": owner,
                    "target_geometry_scope": target_scope, "geometry_available": False,
                    "geometry_unavailable_reason": "object_has_no_non_null_shape",
                    "writable_references_available": False, "available_fields": [],
                    "faces": [], "edges": [],
                    "model_revision": _target_view_revision(document, rebuild, reconstruction_ir)}
        allowed_sections = {"wires", "topology", "stable_selection", "repetition", "relations", "semantic_regions"}
        if not isinstance(include, list) or any(not isinstance(key, str) or key not in allowed_sections for key in include):
            raise ValueError("include must list supported global sections")
        fields = params.get("fields", ["name", "surface_type", "curve_type", "area_mm2", "length_mm", "center_mm", "curve", "surface", "stable_selector", "semantic_ref"])
        if observation_only and "fields" not in params:
            fields = [key for key in fields if key not in ("stable_selector", "semantic_ref")]
        if not isinstance(fields, list) or any(not isinstance(key, str) for key in fields):
            raise ValueError("fields must be a list of entity field names")
        offset, limit = params.get("offset", 0), params.get("limit", 50)
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError("offset must be non-negative and limit must be 1 to 500")
        evidence = _geometry_evidence(shape, tolerance_contract,
            include_relations=bool({"relations", "semantic_regions"} & set(include)),
            include_repetition="repetition" in include)
        if observation_only:
            evidence = self._without_writable_references(evidence)
        all_faces = evidence["faces"]
        all_edges = evidence["edges"]
        faces_supplied = "faces" in params
        edges_supplied = "edges" in params
        if faces_supplied and params.get("faces") is None:
            raise ValueError("faces must be an array when supplied")
        if edges_supplied and params.get("edges") is None:
            raise ValueError("edges must be an array when supplied")
        requested = [] if params.get("faces") == [] else _face_names(params.get("faces"), len(all_faces))
        requested_edges = [] if params.get("edges") == [] else _edge_names(params.get("edges"), len(all_edges))
        resolved_selectors = _resolve_geometry_selectors(
            None if params.get("selectors") == [] else params.get("selectors"), evidence["stable_selection"]
        )
        selector_faces = set(resolved_selectors["face_names"])
        selector_edges = set(resolved_selectors["edge_names"])
        selector_wires = set(resolved_selectors["wire_names"])
        selectors_supplied = bool(params.get("selectors"))
        # Keep omission, an explicit empty list, and an explicit subset
        # distinguishable.  In particular, ``faces=[]`` means that this
        # request did not ask for face records, even when edge selectors are
        # present in the same query.
        selected_names = None
        if faces_supplied:
            selected_names = set(requested or [])
            if params.get("faces") != [] and selectors_supplied:
                selected_names.update(selector_faces)
        elif selectors_supplied:
            selected_names = set(selector_faces)
        selected_edge_names = None
        if edges_supplied:
            selected_edge_names = set(requested_edges or [])
            if params.get("edges") != [] and selectors_supplied:
                selected_edge_names.update(selector_edges)
        elif selectors_supplied:
            selected_edge_names = set(selector_edges)
        face_subset = [item for item in all_faces if selected_names is None or item["name"] in selected_names]
        edge_subset = [item for item in all_edges if selected_edge_names is None or item["name"] in selected_edge_names]
        available_fields = {"owning_feature"} | ({"semantic_ref"} if not observation_only else set())
        for item in all_faces + all_edges:
            available_fields.update(item)
        unknown_fields = set(fields) - available_fields - {"*"}
        if unknown_fields:
            raise ValueError("Unknown entity fields: %s; available: %s" % (sorted(unknown_fields), sorted(available_fields)))
        selected_faces = [
            self._entity_observation_detail(
                face,
                "face",
                evidence["stable_selection"]["model_revision"],
                owner,
                writable_refs,
            )
            for face in face_subset[offset:offset + limit]
        ]
        selected_edges = [
            self._entity_observation_detail(
                edge,
                "edge",
                evidence["stable_selection"]["model_revision"],
                owner,
                writable_refs,
            )
            for edge in edge_subset[offset:offset + limit]
        ]
        relations = _geometric_relations(all_faces, tolerance) if "relations" in include else {}
        if "relations" in include:
            relations.update(evidence["topological_relations"])
        if selected_names is not None:
            relations = {
                relation_name: [
                    relation
                    for relation in values
                    if any(
                        name in selected_names
                        for name in relation.get("faces", [])
                    )
                ]
                for relation_name, values in relations.items()
            }

        result = {
            "document": document.Name,
            "role": role,
            "geometry_available": True,
            "writable_references_available": writable_refs,
            "source": source.Name if role == "source" else None,
            "target_workspace": "REAgentWorkspace" if role == "target" else None,
            "owning_feature": owner,
            "tolerance_mm": tolerance,
            "tolerance_contract": tolerance_contract,
            "model_revision": evidence["stable_selection"]["model_revision"],
            "available_fields": sorted(available_fields),
            "faces": [{key: value for key, value in item.items() if "*" in fields or key in fields} for item in selected_faces],
            "edges": [{key: value for key, value in item.items() if "*" in fields or key in fields} for item in selected_edges],
            "pagination": {
                "faces": {
                    "model_total": len(all_faces),
                    "selected_total": len(face_subset),
                    "returned": len(selected_faces),
                    "offset": offset,
                    "next_offset": (
                        offset + limit
                        if offset + limit < len(face_subset)
                        else None
                    ),
                    "requested": bool(
                        (faces_supplied and params.get("faces") != [])
                        or (not faces_supplied and (not selectors_supplied or selector_faces))
                    ),
                    "selection_mode": (
                        "none"
                        if selected_names is not None and not selected_names
                        else "subset"
                        if selected_names is not None
                        else "all"
                    ),
                },
                "edges": {
                    "model_total": len(all_edges),
                    "selected_total": len(edge_subset),
                    "returned": len(selected_edges),
                    "offset": offset,
                    "next_offset": (
                        offset + limit
                        if offset + limit < len(edge_subset)
                        else None
                    ),
                    "requested": bool(
                        (edges_supplied and params.get("edges") != [])
                        or (not edges_supplied and (not selectors_supplied or selector_edges))
                    ),
                    "selection_mode": (
                        "none"
                        if selected_edge_names is not None and not selected_edge_names
                        else "subset"
                        if selected_edge_names is not None
                        else "all"
                    ),
                },
            },
            "resolved_selectors": resolved_selectors["results"],
        }
        if target_scope is not None:
            result["target_geometry_scope"] = target_scope
        if "wires" in include:
            result["wires"] = [
                wire
                for wire in evidence["wires"]
                if (
                    wire["name"] in selector_wires
                    or (not selector_wires and (selected_names is None or wire["face"] in selected_names))
                )
            ]
        for key in ("topology", "stable_selection", "repetition"):
            if key in include:
                result[key] = evidence[key]
        if "relations" in include:
            result["relations"] = relations
        if "semantic_regions" in include:
            result["semantic_regions"] = _semantic_regions(shape, evidence, tolerance_contract)
        if observation_only:
            result = self._without_writable_references(result)
            result["owning_feature"] = owner
        return result


    @staticmethod
    def _without_writable_references(value):
        if isinstance(value, dict):
            return {key: ObservationOperations._without_writable_references(item)
                    for key, item in value.items() if key not in ("semantic_ref", "stable_selector")}
        if isinstance(value, list):
            return [ObservationOperations._without_writable_references(item) for item in value]
        return value


    @staticmethod
    def _entity_observation_detail(detail, entity_kind, model_revision, owner, writable_refs):
        result = dict(detail)
        result["owning_feature"] = owner
        if owner is not None and writable_refs and entity_kind == "edge":
            result["semantic_ref"] = _semantic_entity_ref_from_detail(
                detail,
                model_revision,
                entity_kind,
                "dressup_edge",
                owner_name=owner["name"],
            )
        else:
            result["semantic_ref"] = None
        return result


    def _resolve_target_selection(self, params):
        """Diagnose explicit current predecessor edge or face inputs read-only."""

        allowed = {
            "body_name",
            "edge_queries",
            "edge_selectors",
            "edge_refs",
            "edge_scope",
            "edge_role",
            "face_selectors",
            "face_refs",
            "face_role",
            "nearest_limit",
        }
        unknown = sorted(set(params) - allowed - {"document"})
        if unknown:
            raise ValueError(
                "Unknown selection resolution parameters: %s"
                % ", ".join(unknown)
            )
        (
            document,
            _source,
            rebuild,
            reconstruction_ir,
        ) = _target_state(params.get("document"))
        body_name = params.get("body_name", rebuild.Name)
        body = document.getObject(body_name)
        if body not in _session_bodies(rebuild):
            raise ValueError("Selection Body must belong to the Target session")
        tip = body.Tip
        shape = _predecessor_shape(body)
        if shape is None or shape.isNull() or tip is None or tip not in body.Group:
            raise ValueError("Selection Body has no owned current Tip to inspect")
        if (not shape.isValid() or len(shape.Solids) != 1
                or any({str(value).lower() for value in obj.State} & {"touched", "invalid", "error"}
                       for obj in _owned_objects(rebuild))):
            raise ValueError("Selection requires a ready valid current predecessor solid")
        face_mode = any(key in params for key in (
            "face_selectors", "face_refs", "face_role"))
        if face_mode:
            if any(key in params for key in (
                    "edge_queries", "edge_selectors", "edge_refs", "edge_scope",
                    "edge_role")):
                raise ValueError("Face and edge selection inputs cannot be combined")
            if not shape.isValid() or len(shape.Solids) != 1:
                raise ValueError("Face selection requires one valid predecessor solid")
            role = params.get("face_role")
            selectors = _dressup_face_selectors(params.get("face_selectors"))
            face_refs = _feature_semantic_refs(
                params.get("face_refs"), "face", role, "face_refs", 64)
            if any(ref["owner"].get("object_name") != tip.Name
                   or ref.get("fallback") is not None for ref in face_refs):
                raise ValueError("Face references require the current Tip and no fallback")
            nearest_limit = params.get("nearest_limit", 3)
            tolerance_contract = _ir_tolerance_contract(reconstruction_ir)
            evidence = _geometry_evidence(shape, tolerance_contract)
            report = _resolve_face_selection(
                evidence, {"face_selectors": selectors, "face_refs": face_refs},
                role, nearest_limit=nearest_limit)
            report["resolved_references"] = [
                _semantic_entity_ref_from_detail(
                    next(face for face in evidence["faces"] if face["name"] == face_name),
                    report["model_revision"], "face", role, owner_name=tip.Name)
                for face_name in report["matched_names"]
            ] if report["all_resolved"] else []
            return {
                "document": document.Name, "role": "target",
                "target_workspace": rebuild.Name,
                "selection_body": body.Name,
                "owning_feature": {"name": tip.Name, "label": tip.Label,
                                   "type_id": tip.TypeId},
                "tolerance_contract": tolerance_contract,
                **report,
            }
        edge_role = params.get("edge_role", "dressup_edge")
        if edge_role not in ("dressup_edge", "pattern_axis"):
            raise ValueError("Unknown edge selection role")
        queries = _dressup_edge_queries(params.get("edge_queries"))
        selectors = _dressup_edge_selectors(params.get("edge_selectors"))
        edge_refs = _feature_semantic_refs(
            params.get("edge_refs"),
            "edge",
            edge_role,
            "edge_refs",
            64,
        )
        edge_scope = params.get("edge_scope")
        if edge_scope is not None and edge_scope != "all":
            raise ValueError("edge_scope must be all")
        if edge_scope is not None and edge_role != "dressup_edge":
            raise ValueError("Pattern axis requires one explicit edge, not all edges")
        if edge_scope == "all" and (queries or selectors or edge_refs):
            raise ValueError(
                "edge_scope cannot be combined with edge queries, selectors, or refs"
            )
        if edge_scope == "all" and len(shape.Edges) > DRESSUP_SCOPE["edge_limit"]:
            raise ValueError("Complete predecessor edge set exceeds the reference limit")
        if not queries and not selectors and not edge_refs and edge_scope is None:
            raise ValueError(
                "Selection resolution requires edge_queries, edge_selectors, "
                "edge_refs, or edge_scope=all"
            )
        nearest_limit = params.get("nearest_limit", 3)
        tolerance_contract = _ir_tolerance_contract(reconstruction_ir)
        evidence = _geometry_evidence(shape, tolerance_contract)
        if any(ref["owner"].get("object_name") != tip.Name for ref in edge_refs):
            raise ValueError("Selection references must name the current Body Tip")
        report = _resolve_dressup_selection(
            evidence,
            {
                "edge_queries": queries,
                "edge_selectors": selectors,
                "edge_refs": edge_refs,
                "edge_scope": edge_scope,
            },
            tolerance_contract,
            nearest_limit=nearest_limit,
        )
        report["edge_role"] = edge_role
        report["resolved_references"] = [
            _semantic_entity_ref_from_detail(
                next(edge for edge in evidence["edges"] if edge["name"] == edge_name),
                evidence["stable_selection"]["model_revision"], "edge", edge_role,
                owner_name=tip.Name,
            )
            for edge_name in report["matched_names"]
        ] if report["all_resolved"] else []
        return {
            "document": document.Name,
            "role": "target",
            "target_workspace": rebuild.Name,
            "selection_body": body.Name,
            "owning_feature": {
                "name": tip.Name,
                "label": tip.Label,
                "type_id": tip.TypeId,
            },
            "tolerance_contract": tolerance_contract,
            **report,
        }


    def _infer_virtual_predecessor_bounds(self, params):
        """Return Source-only predecessor-envelope constraints.

        This is intentionally an observation operation. It never creates or
        modifies a CAD document.
        """

        if _state.INSTANCE_ROLE != "source":
            raise ValueError(
                "Virtual predecessor bounds require the Source instance"
            )
        document, source = _source_objects(params.get("document"))
        tolerance_contract = _source_tolerance_contract(source)
        evidence = _geometry_evidence(source.Shape, tolerance_contract)
        tolerance = tolerance_contract["linear_tolerance_mm"]
        relations = _geometric_relations(evidence["faces"], tolerance)
        relations.update(evidence["topological_relations"])
        analysis = {
            "faces": evidence["faces"],
            "adjacency": evidence["face_adjacency"],
            "relations": relations,
            "topological_relations": evidence["topological_relations"],
            "stable_selection": evidence["stable_selection"],
        }
        result = infer_virtual_predecessor_bounds(
            analysis,
            face_names=_face_names(params.get("faces"), len(evidence["faces"])),
            selectors=params.get("selectors"),
            support_face_names=_face_names(
                params.get("support_faces"), len(evidence["faces"])
            ),
            radius_mm=params.get("radius_mm"),
            tolerance_mm=tolerance,
        )
        return {
            "document": document.Name,
            "source": source.Name,
            "role": "SourceOriginal",
            "tolerance_mm": tolerance,
            "source_model_revision": evidence["stable_selection"]["model_revision"],
            **result,
        }


    def _get_semantic_regions(self, params):
        """Return stable faceset regions for Source or the current Rebuild tip."""

        if _state.INSTANCE_ROLE == "source":
            document, source = _source_objects(params.get("document"))
            tolerance_contract = _source_tolerance_contract(source)
            shape = source.Shape
            role = "SourceOriginal"
        elif _state.INSTANCE_ROLE == "rebuild":
            document = _document(params.get("document"))
            if document.getObject("Source") is not None:
                raise ValueError("Rebuild document must not contain a Source object")
            rebuild = _workspace(document)
            shape = _predecessor_shape(rebuild)
            if shape is None or shape.isNull():
                raise ValueError("Rebuild has no Tip to inspect")
            (
                _document_state,
                _source,
                _rebuild,
                reconstruction_ir,
            ) = _target_state(document.Name)
            tolerance_contract = _ir_tolerance_contract(reconstruction_ir)
            role = "Rebuild"
        else:
            raise ValueError("Semantic regions require Source or Rebuild role")

        evidence = _geometry_evidence(shape, tolerance_contract)
        regions = _semantic_regions(shape, evidence, tolerance_contract)
        requested = params.get("region_ids")
        if requested is not None:
            if not isinstance(requested, list) or any(not isinstance(item, str) for item in requested):
                raise ValueError("region_ids must be a list of strings")
            wanted = set(requested)
            regions = dict(regions)
            regions["regions"] = [
                region for region in regions["regions"] if region["region_id"] in wanted
            ]
            regions["tangent_groups"] = [
                group for group in regions["tangent_groups"] if group["region_id"] in wanted
            ]
            regions["region_count"] = len(regions["regions"])
            regions["tangent_group_count"] = len(regions["tangent_groups"])
        return {
            "document": document.Name,
            "role": role,
            "source": "Source" if role == "SourceOriginal" else None,
            "geometry_scope": "source" if role == "SourceOriginal" else "current_predecessor",
            "tolerance_mm": tolerance_contract["linear_tolerance_mm"],
            **regions,
        }


    def _classify_points(self, params):
        """Classify points as inside, outside, or boundary in one role-local shape."""

        if _state.INSTANCE_ROLE == "source":
            document, source = _source_objects(params.get("document"))
            tolerance_contract = _source_tolerance_contract(source)
            shape = source.Shape
            role = "SourceOriginal"
            evidence = _geometry_evidence(shape, tolerance_contract)
        elif _state.INSTANCE_ROLE == "rebuild":
            (
                document,
                _source,
                rebuild,
                reconstruction_ir,
            ) = _target_state(params.get("document"))
            tolerance_contract = _ir_tolerance_contract(reconstruction_ir)
            shape = _result_shape(rebuild)
            if shape is None or shape.isNull():
                raise ValueError("Rebuild has no Tip to classify")
            role = "Rebuild"
            evidence = _geometry_evidence(shape, tolerance_contract)
        else:
            raise ValueError("Point classification requires Source or Rebuild role")
        tolerance = tolerance_contract["linear_tolerance_mm"]
        return {
            "document": document.Name,
            "role": role,
            "model_revision": evidence["stable_selection"]["model_revision"],
            "geometry_scope": "source" if role == "SourceOriginal" else "body_result",
            "tolerance_mm": tolerance,
            "points": _classify_points(shape, params.get("points"), tolerance),
        }


    def _get_reconstruction_residuals(self, params):
        """Run two explicit directional cuts with per-direction availability."""

        document, source, rebuild, reconstruction_ir = _target_state(
            params.get("document")
        )
        rebuild_shape = _result_shape(rebuild)
        if rebuild_shape is None or rebuild_shape.isNull():
            raise ValueError("Rebuild has no Tip to compare")
        tolerance_contract = _ir_tolerance_contract(reconstruction_ir)
        result = _residual_regions(source.Shape, rebuild_shape, tolerance_contract)
        result.update(
            {
                "document": document.Name,
                "rebuild_model_revision": _target_view_revision(document, rebuild, reconstruction_ir),
                "source_model_revision": result.get("source_model_revision"),
                "tolerance_mm": tolerance_contract["linear_tolerance_mm"],
            }
        )
        return result


    def _get_target_state(self, params):
        document = _document(params.get("document"))
        group = config = _workspace(document)
        binding = next((item for item in _state.SESSION_BINDINGS.values()
                        if item["document"] is document and item["role"] == "rebuild"), None)
        bound_revision = binding["source_revision"] if binding else None
        source_basis = {"bound_revision": bound_revision, "current_file_revision": None,
                        "current_file_revision_basis": "file_sha256", "available": False,
                        "matches_bound": None, "unavailable_reason": None}
        try:
            current = _file_sha256(config.SourceFile)
            source_basis.update(current_file_revision=current, available=True,
                                matches_bound=current == bound_revision if bound_revision else None)
        except (OSError, ValueError, TypeError) as exc:
            source_basis["unavailable_reason"] = str(exc)[:300]
        result = _result_object(group)
        block = _state.AUDIT_BLOCKS.get(document.Name)
        try:
            model_state = result_state(group)
        except Exception as exc:
            model_state = {"state": "invalid_or_unknown", "reason": str(exc)[:300]}
        return {
            "document": document.Name,
            "result": result.Name if result is not None else None,
            "result_body": group.Name,
            "model_state": model_state,
            "revision": _target_view_revision(document, group, config),
            "tip": group.Tip.Name if group.Tip else None,
            "checkpoint_path": document.FileName or None,
            "artifacts": dict(binding["artifacts"]) if binding else None,
            "modified_status": "unavailable",
            "source_path": config.SourceFile or None,
            "source_basis": source_basis,
            "tolerance_contract": _ir_tolerance_contract(config),
            "active_operation": _state.ACTIVE_OPERATION,
            "audit_restoration_block": block[1] if block and block[0] is document else None,
            "undo": list(document.UndoNames), "redo": list(document.RedoNames),
            "native_undo_enabled": bool(document.UndoMode),
            "pending_transaction": bool(document.HasPendingTransaction),
            "objects": [{"name": obj.Name, "label": obj.Label, "type_id": obj.TypeId,
                         "state": list(obj.State)} for obj in _owned_objects(group)],
        }


    def _diagnostic_document_state(self, name):
        document = _document(name)
        group = _workspace(document)
        block = _state.AUDIT_BLOCKS.get(name)
        states = [{"object": obj.Name, "state": list(obj.State)} for obj in _owned_objects(group)
                  if any(str(s).lower() in ("touched", "invalid", "error") for s in obj.State)]
        return {"geometry_source": "cached_shape", "recomputed": False,
                "pending_or_error_objects": states[:16], "object_count": len(states),
                "objects_omitted": max(0, len(states)-16),
                "write_block": block[1] if block and block[0] is document else None}


    def _observe_target_global(self, params):
        document, _source, rebuild, reconstruction_ir = _target_state(
            params.get("document")
        )
        shape = _result_shape(rebuild)
        shape_summary = (
            _shape_summary(shape)
            if shape is not None and not shape.isNull()
            else None
        )
        return {
            "document": document.Name,
            "role": "target",
            "revision": _target_view_revision(document, rebuild, reconstruction_ir),
            "shape": shape_summary,
            "object_count": len(_owned_objects(rebuild)),
            "objects": [
                {
                    "name": obj.Name,
                    "label": obj.Label,
                    "type_id": obj.TypeId,
                }
                for obj in document.Objects
                if obj is not reconstruction_ir
            ],
        }
