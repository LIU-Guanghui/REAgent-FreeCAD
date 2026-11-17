"""Role-local geometry observations and selection queries."""

import math
import os

import FreeCAD as App

from REAgent.host.analysis.evidence import classify_points, geometry_evidence, semantic_regions
from REAgent.host.analysis.measurements import summarize_shape
from REAgent.host.analysis.predecessor import infer_virtual_predecessor_bounds
from REAgent.host.analysis.references import existing_entity_names, semantic_entity_ref_from_detail
from REAgent.host.analysis.selection import dressup_edge_queries, dressup_edge_selectors, dressup_face_selectors, feature_semantic_refs, resolve_dressup_selection, resolve_face_selection
from REAgent.host.analysis.topology import geometric_relations, resolve_geometry_selectors
from REAgent.host.numeric import vector_dict
from REAgent.host.process_state import require_document, PROCESS
from REAgent.protocol.features import DRESSUP_SCOPE, EDIT_FEATURE_TYPES
from REAgent.protocol.observations import FEATURE_OBSERVATION_BUDGET_BYTES
from REAgent.json_values import encode_json
from REAgent.host.validation.comparison import residual_regions
from REAgent.host.validation.structure import result_state
from REAgent.host.validation.tolerance import ir_tolerance_contract, source_tolerance_contract
from REAgent.host.file_digest import file_sha256
from REAgent.host.workspace.model import feature_owner, feature_predecessor, owned_objects, placement_summary, predecessor_shape, read_stp_reference, resolve_result_object, result_shape, session_bodies, source_objects, target_state, workspace, copy_world_shape
from REAgent.host.workspace.properties import metadata, sketch_content_state
from REAgent.host.workspace.state import source_geometry_state, source_view_revision, target_view_revision


def _bounded_feature_page(result, container, key, section, offset, total, rows):
    # Leave room for the revision and normal Controller envelope fields.
    budget = FEATURE_OBSERVATION_BUDGET_BYTES - 512
    page = container[key] = {"section": section, "offset": offset, "total": total,
                             "items": [], "next_offset": None}
    for row in rows:
        page["items"].append(row)
        following = offset + len(page["items"])
        page["next_offset"] = following if following < total else None
        if len(encode_json(result).encode("utf-8")) > budget:
            page["items"].pop()
            page["next_offset"] = following - 1
            if not page["items"]:
                raise ValueError("A complete feature page item exceeds the observation budget at offset " + str(offset))
            break


def _sketch_page_row(index, row):
    omitted = ("poles", "knots", "weights", "multiplicities")
    item = {"index": index, **{key: value for key, value in row.items() if key not in omitted}}
    if "poles" in row:
        item["spline_counts"] = {key: len(row[key]) for key in omitted if key in row}
    if isinstance(item.get("expression"), str) and len(item["expression"]) > 200:
        item["expression"] = item["expression"][:200]
        item["expression_truncated"] = True
    return item


def _mesh_section(source, query):
    axis = query["axis"]
    coordinate = query["coordinate_mm"]
    normal = App.Vector(*(int(component == axis) for component in "xyz"))
    tolerance = source_tolerance_contract(source)["linear_tolerance_mm"]
    contours, = source.Mesh.crossSections([(normal * coordinate, normal)], tolerance)
    offset = query.get("offset", 0)
    page = []
    for points in contours[offset:offset + 32]:
        bounds = {}
        for direction in "xyz":
            coordinates = [getattr(point, direction) for point in points]
            lower, upper = min(coordinates), max(coordinates)
            bounds.update({direction + "_min": lower, direction + "_length": upper - lower})
        page.append({"bounding_box_mm": bounds, "point_count": len(points),
                     "closed": (points[0] - points[-1]).Length <= tolerance})
    following = offset + len(page)
    return {"axis": axis, "coordinate_mm": coordinate, "join_tolerance_mm": tolerance,
            "contour_count": len(contours), "offset": offset,
            "contours": page, "next_offset": following if following < len(contours) else None}


class ObservationOperations:
    def with_observation_revision(self, document_name, result):
        """Give every successful role-local observation one top-level revision."""
        if not isinstance(result, dict):
            raise RuntimeError("Native observation did not return a result object")
        revision = result.get("revision")
        if revision is None:
            if PROCESS.INSTANCE_ROLE == "source":
                _document_object, source = source_objects(document_name)
                revision = source_view_revision(source)
            else:
                document = require_document(document_name)
                group = workspace(document)
                revision = target_view_revision(document, group)
            result["revision"] = revision
        if not isinstance(revision, str) or not revision:
            raise RuntimeError("Native observation has no string model revision")
        return result


    def analyze_source(self, params):
        document, source = source_objects(params.get("document"))
        if source.TypeId == "Mesh::Feature":
            result = {"document": document.Name, "source": source.Name, "units": "mm",
                      "mesh": source_geometry_state(source)}
            if "mesh_section" in params:
                result["mesh_section"] = _mesh_section(source, params["mesh_section"])
            return result
        summary = summarize_shape(source.Shape)
        tolerance_contract = source_tolerance_contract(source)
        tolerance = tolerance_contract["linear_tolerance_mm"]

        source_path = getattr(source, "SourceFile", None)
        expected_sha256 = getattr(source, "SourceSHA256", None)
        source_exists = isinstance(source_path, str) and os.path.isfile(source_path)
        current_sha256 = file_sha256(source_path) if source_exists else None

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
                "placement": placement_summary(source),
            },
            "shape": summary,
        }


    def observe_feature(self, params):
        document, group = target_state(params["document"])
        obj = document.getObject(params["object"])
        if obj is None or obj.TypeId not in EDIT_FEATURE_TYPES:
            raise ValueError("Feature observation requires an editable native Target object")
        body = feature_owner(group, obj)
        prior = feature_predecessor(body, obj)
        from REAgent.host.features.edit_parameters import observable_properties
        properties = {}
        for prop in observable_properties(obj):
            row = metadata(obj, prop, body_edit=obj is body)
            properties[prop] = {key: row[key] for key in
                                ("value", "enum", "writable", "unavailable_reason") if key in row}
        value = {"object": obj.Name, "type_id": obj.TypeId, "body": body.Name,
                 "label": obj.Label, "predecessor": prior.Name if prior else None,
                 "tip": body.Tip.Name if body.Tip else None, "properties": properties,
                 "dependencies": [item.Name for item in obj.OutList[:64]],
                 "consumers": [item.Name for item in obj.InList[:64]]}
        if "sketch_page" in params and obj.TypeId != "Sketcher::SketchObject":
            raise ValueError("Sketch element page requires a Sketch")
        if obj.TypeId == "Sketcher::SketchObject":
            content = sketch_content_state(obj)
            value["sketch"] = {"geometry_count": obj.GeometryCount, "constraint_count": obj.ConstraintCount,
                               "external_geometry": content["external_geometry"][:16],
                               "external_link_count": len(content["external_geometry"]),
                               "external_index_count": len(content["external_indices"]),
                               "external_indices": [row["index"] for row in content["external_indices"][:32]]}
            if "sketch_page" in params:
                query = params["sketch_page"]
                section, offset, limit = query["section"], query.get("offset", 0), query.get("limit", 12)
                rows = content[section]
                _bounded_feature_page(value, value["sketch"], "page", section, offset, len(rows),
                    (_sketch_page_row(i, rows[i]) for i in range(offset, min(offset + limit, len(rows)))))
        if "vertex_page" in params:
            if obj is body or not obj.TypeId.startswith("PartDesign::"):
                raise ValueError("Vertex pages require a solid history feature in its owned Body")
            shape = copy_world_shape(obj)
            if shape is None or shape.isNull() or not shape.isValid() or len(shape.Solids) != 1:
                raise ValueError("Observed feature has no valid single solid for vertex references")
            if any(str(state).lower() in ("touched", "invalid", "error") for state in obj.State):
                raise ValueError("Recompute the feature before observing writable vertex references")
            query = params["vertex_page"]
            offset, limit = query.get("offset", 0), query.get("limit", 32)
            vertices = shape.Vertexes
            _bounded_feature_page(value, value, "vertex_page", "vertices", offset, len(vertices),
                ({"index": i + 1, "name": "Vertex" + str(i + 1), "point_mm": vector_dict(vertices[i].Point)}
                 for i in range(offset, min(offset + limit, len(vertices)))))
        if len(encode_json(value).encode("utf-8")) > FEATURE_OBSERVATION_BUDGET_BYTES - 512:
            raise ValueError("Feature metadata exceeds the observation budget")
        return value

    def inspect_geometry(self, params):
        if PROCESS.INSTANCE_ROLE == "source":
            if params.get("object") is not None or params.get("edit_object") is not None:
                raise ValueError(
                    "Source FaceN/EdgeN are subelements: use entity.query.faces/edges; "
                    "object names are available only for Target owned objects"
                )
            document, source = source_objects(params.get("document"))
            tolerance_contract = source_tolerance_contract(source)
            shape = source.Shape
            role = "source"
            owner = None
            target_scope = None
        elif PROCESS.INSTANCE_ROLE == "target":
            document, target_body = target_state(params.get("document"))
            tolerance_contract = ir_tolerance_contract(target_body)
            tip = resolve_result_object(target_body)
            requested_name = params.get("object")
            edit_object = params.get("edit_object")
            if edit_object is not None:
                feature = document.getObject(edit_object)
                if feature is None or feature.TypeId not in EDIT_FEATURE_TYPES:
                    raise ValueError("Edit geometry requires an owned editable feature")
                body = feature_owner(target_body, feature)
                inspected = feature_predecessor(body, feature)
                if inspected is None or (requested_name is not None and requested_name != inspected.Name):
                    raise ValueError("Edit geometry must reference this feature's actual predecessor")
                shape = copy_world_shape(inspected)
                target_scope = {"kind": "edit_predecessor", "edit_object": feature.Name,
                                "requested_object": requested_name, "shape_object": inspected.Name,
                                "selection_body": body.Name, "current_result_tip": tip.Name if tip else None,
                                "is_current_result": inspected is tip,
                                "is_current_predecessor": False}
            elif requested_name is None:
                if tip is None:
                    raise ValueError("Target has no current Tip for entity geometry; use Target global observation for workspace evidence before the first feature")
                inspected = tip
                shape = predecessor_shape(target_body)
                target_scope = {
                    "kind": "current_predecessor",
                    "requested_object": None,
                    "shape_object": tip.Name,
                    "selection_body": target_body.Name,
                    "current_result_tip": tip.Name,
                    "is_current_result": True,
                    "is_current_predecessor": True,
                }
            else:
                inspected = document.getObject(requested_name)
                if inspected is None or inspected not in owned_objects(target_body):
                    raise ValueError(
                        "entity.query.object must name an owned Target object"
                    )
                shape = copy_world_shape(inspected)
                owning_body = next((body for body in session_bodies(target_body)
                                    if inspected in body.Group), None)
                current_predecessor = bool(owning_body is not None and owning_body.Tip is inspected)
                target_scope = {
                    "kind": "owned_object",
                    "requested_object": inspected.Name,
                    "shape_object": inspected.Name,
                    "current_result_tip": tip.Name if tip else None,
                    "selection_body": owning_body.Name if owning_body else None,
                    "is_current_result": bool(
                        inspected is target_body or inspected is tip
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
        writable_refs = bool(role == "target" and (target_scope["is_current_predecessor"]
                                                  or target_scope["kind"] == "edit_predecessor")
                             and shape is not None and not shape.isNull()
                             and shape.isValid() and len(shape.Solids) == 1
                             and not any({str(value).lower() for value in obj.State}
                                         & {"touched", "invalid", "error"}
                                         for obj in owned_objects(target_body)))
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
                    "model_revision": target_view_revision(document, target_body)}
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
        evidence = geometry_evidence(shape, tolerance_contract,
            include_relations=bool({"relations", "semantic_regions"} & set(include)),
            include_repetition="repetition" in include)
        if observation_only:
            evidence = self._without_writable_references(evidence)
        all_faces = evidence["faces"]
        all_edges = evidence["edges"]
        resolved_selectors = resolve_geometry_selectors(
            params.get("selectors"), evidence["stable_selection"]
        )
        selector_wires = set(resolved_selectors["wire_names"])
        default_all = not {"faces", "edges", "selectors"}.intersection(params)

        def selected(key, entities, matches):
            requested = params.get(key)
            if default_all or requested == "all":
                return None
            return set(existing_entity_names(requested, entities) or ()).union(matches)

        selected_names = selected("faces", all_faces, resolved_selectors["face_names"])
        selected_edge_names = selected("edges", all_edges, resolved_selectors["edge_names"])
        face_subset = [item for item in all_faces if selected_names is None or item["name"] in selected_names]
        edge_subset = [item for item in all_edges if selected_edge_names is None or item["name"] in selected_edge_names]
        available_fields = {"owning_feature"} | ({"semantic_ref"} if not observation_only else set())
        for item in all_faces + all_edges:
            available_fields.update(item)
        unknown_fields = set(fields) - available_fields - {"*"}
        if unknown_fields:
            raise ValueError("Unknown entity fields: %s; available: %s. Read agent_observe entity capabilities or use fields=['*']; nested surface/curve values are not top-level fields" % (sorted(unknown_fields), sorted(available_fields)))
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
        relations = geometric_relations(all_faces, tolerance) if "relations" in include else {}
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
            "target_workspace": target_body.Name if role == "target" else None,
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
                    "requested": selected_names is None or bool(selected_names),
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
                    "requested": selected_edge_names is None or bool(selected_edge_names),
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
            result["semantic_regions"] = semantic_regions(shape, evidence, tolerance_contract)
        if observation_only:
            result = self._without_writable_references(result)
            result["owning_feature"] = owner
        return result

    def resolve_target_selection(self, params):
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
        document, target_body = target_state(params.get("document"))
        body_name = params.get("body_name", target_body.Name)
        body = document.getObject(body_name)
        if body not in session_bodies(target_body):
            raise ValueError("Selection Body must belong to the Target session")
        tip = body.Tip
        shape = predecessor_shape(body)
        if shape is None or shape.isNull() or tip is None or tip not in body.Group:
            raise ValueError("Selection Body has no owned current Tip to inspect")
        if (not shape.isValid() or len(shape.Solids) != 1
                or any({str(value).lower() for value in obj.State} & {"touched", "invalid", "error"}
                       for obj in owned_objects(target_body))):
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
            selectors = dressup_face_selectors(params.get("face_selectors"))
            face_refs = feature_semantic_refs(
                params.get("face_refs"), "face", role, "face_refs", 64)
            if any(ref["owner"].get("object_name") != tip.Name
                   or ref.get("fallback") is not None for ref in face_refs):
                raise ValueError("Face references require the current Tip and no fallback")
            nearest_limit = params.get("nearest_limit", 3)
            tolerance_contract = ir_tolerance_contract(target_body)
            evidence = geometry_evidence(shape, tolerance_contract)
            report = resolve_face_selection(
                evidence, {"face_selectors": selectors, "face_refs": face_refs},
                role, nearest_limit=nearest_limit)
            report["resolved_references"] = [
                semantic_entity_ref_from_detail(
                    next(face for face in evidence["faces"] if face["name"] == face_name),
                    report["model_revision"], "face", role, owner_name=tip.Name)
                for face_name in report["matched_names"]
            ] if report["all_resolved"] else []
            return {
                "document": document.Name, "role": "target",
                "target_workspace": target_body.Name,
                "selection_body": body.Name,
                "owning_feature": {"name": tip.Name, "label": tip.Label,
                                   "type_id": tip.TypeId},
                "tolerance_contract": tolerance_contract,
                **report,
            }
        edge_role = params.get("edge_role", "dressup_edge")
        if edge_role not in ("dressup_edge", "pattern_axis"):
            raise ValueError("Unknown edge selection role")
        queries = dressup_edge_queries(params.get("edge_queries"))
        selectors = dressup_edge_selectors(params.get("edge_selectors"))
        edge_refs = feature_semantic_refs(
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
        tolerance_contract = ir_tolerance_contract(target_body)
        evidence = geometry_evidence(shape, tolerance_contract)
        if any(ref["owner"].get("object_name") != tip.Name for ref in edge_refs):
            raise ValueError("Selection references must name the current Body Tip")
        report = resolve_dressup_selection(
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
            semantic_entity_ref_from_detail(
                next(edge for edge in evidence["edges"] if edge["name"] == edge_name),
                evidence["stable_selection"]["model_revision"], "edge", edge_role,
                owner_name=tip.Name,
            )
            for edge_name in report["matched_names"]
        ] if report["all_resolved"] else []
        return {
            "document": document.Name,
            "role": "target",
            "target_workspace": target_body.Name,
            "selection_body": body.Name,
            "owning_feature": {
                "name": tip.Name,
                "label": tip.Label,
                "type_id": tip.TypeId,
            },
            "tolerance_contract": tolerance_contract,
            **report,
        }


    def infer_virtual_predecessor_bounds(self, params):
        """Return Source-only predecessor-envelope constraints.

        This is intentionally an observation operation. It never creates or
        modifies a CAD document.
        """

        if PROCESS.INSTANCE_ROLE != "source":
            raise ValueError(
                "Virtual predecessor bounds require the Source instance"
            )
        document, source = source_objects(params.get("document"))
        tolerance_contract = source_tolerance_contract(source)
        evidence = geometry_evidence(source.Shape, tolerance_contract)
        tolerance = tolerance_contract["linear_tolerance_mm"]
        relations = geometric_relations(evidence["faces"], tolerance)
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
            face_names=existing_entity_names(params.get("faces"), evidence["faces"]),
            selectors=params.get("selectors"),
            support_face_names=existing_entity_names(
                params.get("support_faces"), evidence["faces"]
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


    def get_semantic_regions(self, params):
        """Return stable faceset regions for Source or the current Target Tip."""

        if PROCESS.INSTANCE_ROLE == "source":
            document, source = source_objects(params.get("document"))
            tolerance_contract = source_tolerance_contract(source)
            shape = source.Shape
            role = "SourceOriginal"
        elif PROCESS.INSTANCE_ROLE == "target":
            document = require_document(params.get("document"))
            if document.getObject("Source") is not None:
                raise ValueError("Target document must not contain a Source object")
            target_body = workspace(document)
            shape = predecessor_shape(target_body)
            if shape is None or shape.isNull():
                raise ValueError("Target Body has no Tip to inspect")
            tolerance_contract = ir_tolerance_contract(target_body)
            role = "target"
        else:
            raise ValueError("Semantic regions require Source or Target role")

        evidence = geometry_evidence(shape, tolerance_contract)
        regions = semantic_regions(shape, evidence, tolerance_contract)
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


    def classify_points(self, params):
        """Classify points as inside, outside, or boundary in one role-local shape."""

        if PROCESS.INSTANCE_ROLE == "source":
            document, source = source_objects(params.get("document"))
            tolerance_contract = source_tolerance_contract(source)
            shape = source.Shape
            role = "SourceOriginal"
            evidence = geometry_evidence(shape, tolerance_contract)
        elif PROCESS.INSTANCE_ROLE == "target":
            document, target_body = target_state(params.get("document"))
            tolerance_contract = ir_tolerance_contract(target_body)
            shape = result_shape(target_body)
            if shape is None or shape.isNull():
                raise ValueError("Target Body has no Tip to classify")
            role = "target"
            evidence = geometry_evidence(shape, tolerance_contract)
        else:
            raise ValueError("Point classification requires Source or Target role")
        tolerance = tolerance_contract["linear_tolerance_mm"]
        return {
            "document": document.Name,
            "role": role,
            "model_revision": evidence["stable_selection"]["model_revision"],
            "geometry_scope": "source" if role == "SourceOriginal" else "body_result",
            "tolerance_mm": tolerance,
            "points": classify_points(shape, params.get("points"), tolerance),
        }


    def get_reconstruction_residuals(self, params):
        """Run two explicit directional cuts with per-direction availability."""

        document, target_body = target_state(params.get("document"))
        source = read_stp_reference(document, target_body, params["session_id"])
        target_shape = result_shape(target_body)
        if target_shape is None or target_shape.isNull():
            raise ValueError("Target Body has no Tip to compare")
        tolerance_contract = ir_tolerance_contract(target_body)
        result = residual_regions(source.Shape, target_shape, tolerance_contract)
        result.update(
            {
                "document": document.Name,
                "revision": target_view_revision(document, target_body),
                "source_model_revision": result.get("source_model_revision"),
                "tolerance_mm": tolerance_contract["linear_tolerance_mm"],
            }
        )
        return result


    def get_target_state(self, params):
        document = require_document(params.get("document"))
        group = workspace(document)
        binding = next((item for item in PROCESS.SESSION_BINDINGS.values()
                        if item["document"] is document and item["role"] == "target"), None)
        bound_revision = binding["source_revision"] if binding else None
        source_basis = {"bound_revision": bound_revision, "current_file_revision": None,
                        "current_file_revision_basis": "file_sha256", "available": False,
                        "matches_bound": None, "unavailable_reason": None}
        source_path = getattr(group, "SourceFile", None) or None
        if source_path:
            try:
                current = file_sha256(source_path)
                source_basis.update(current_file_revision=current, available=True,
                                    matches_bound=current == bound_revision if bound_revision else None)
            except (OSError, ValueError, TypeError) as exc:
                source_basis["unavailable_reason"] = str(exc)[:300]
        result = resolve_result_object(group)
        block = PROCESS.AUDIT_BLOCKS.get(document.Name)
        try:
            model_state = result_state(group)
        except Exception as exc:
            model_state = {"state": "invalid_or_unknown", "reason": str(exc)[:300]}
        return {
            "document": document.Name,
            "result": result.Name if result is not None else None,
            "result_body": group.Name,
            "model_state": model_state,
            "revision": target_view_revision(document, group),
            "tip": group.Tip.Name if group.Tip else None,
            "checkpoint_path": document.FileName or None,
            "artifacts": dict(binding["artifacts"]) if binding else None,
            "modified_status": "unavailable",
            "source_path": source_path,
            "source_basis": source_basis,
            "tolerance_contract": ir_tolerance_contract(group),
            "active_operation": PROCESS.ACTIVE_OPERATION,
            "audit_restoration_block": block[1] if block and block[0] is document else None,
            "undo": list(document.UndoNames), "redo": list(document.RedoNames),
            "native_undo_enabled": bool(document.UndoMode),
            "pending_transaction": bool(document.HasPendingTransaction),
            "objects": [{"name": obj.Name, "label": obj.Label, "type_id": obj.TypeId,
                         "state": list(obj.State)} for obj in owned_objects(group)],
        }


    def diagnostic_document_state(self, name):
        document = require_document(name)
        group = workspace(document)
        block = PROCESS.AUDIT_BLOCKS.get(name)
        states = [{"object": obj.Name, "state": list(obj.State)} for obj in owned_objects(group)
                  if any(str(s).lower() in ("touched", "invalid", "error") for s in obj.State)]
        return {"geometry_source": "cached_shape", "recomputed": False,
                "pending_or_error_objects": states[:16], "object_count": len(states),
                "objects_omitted": max(0, len(states)-16),
                "write_block": block[1] if block and block[0] is document else None}


    def observe_target_global(self, params):
        document, target_body = target_state(params.get("document"))
        shape = result_shape(target_body)
        objects = owned_objects(target_body)
        shape_summary = (
            summarize_shape(shape)
            if shape is not None and not shape.isNull()
            else None
        )
        return {
            "document": document.Name,
            "role": "target",
            "target_workspace": target_body.Name,
            "revision": target_view_revision(document, target_body),
            "shape": shape_summary,
            "object_count": len(objects),
            "objects": [
                {
                    "name": obj.Name,
                    "label": obj.Label,
                    "type_id": obj.TypeId,
                }
                for obj in objects
            ],
        }

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
            result["semantic_ref"] = semantic_entity_ref_from_detail(
                detail,
                model_revision,
                entity_kind,
                "dressup_edge",
                owner_name=owner["name"],
            )
        else:
            result["semantic_ref"] = None
        return result
