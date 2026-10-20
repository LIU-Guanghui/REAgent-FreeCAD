"""Source/Target view plans within the single observation scope."""

import os

import FreeCAD as App
import FreeCADGui as Gui

from ..runtime import _operation_stage, _state
from ..analysis.measurements import _geometry_bound_box
from ..protocol.contract import INITIAL_VIEW_GROUPS, VIEW_DIRECTIONS
from ..workspace.model import (
    _owned_objects,
    _result_display_plan,
    _result_object,
    _result_shape,
    _source_objects,
    _target_state,
)
from ..workspace.scope import _cleanup_observation_previews
from ..workspace.state import _source_view_revision, _target_view_revision
from .camera import _projection
from .rendering import (
    _compose_view_board,
    _compose_initial_boards,
    _display_mode,
    _existing_directory,
    _focus_box,
    _image_artifact,
    _image_manifest,
    _pixel_dimension,
    _render_canonical_views,
    _section_preview_shape,
    _view_directions,
    _view_precondition,
    _view_transparency,
)


def _view_artifacts(params, document):
    binding = _state.SESSION_BINDINGS.get(params["session_id"])
    if (binding is None or binding["document"] is not document
            or binding["role"] != _state.INSTANCE_ROLE):
        raise ValueError("View output requires the exact bound session Document")
    artifacts = dict(binding["artifacts"])
    _existing_directory(artifacts["output_dir"])
    return artifacts


class ViewObservationOperations:
    def _get_reconstruction_views(self, params, scope):
        if not getattr(App, "GuiUp", False):
            raise ValueError("get_reconstruction_views requires FreeCAD GUI mode")
        document, source, rebuild, _reconstruction_ir = _target_state(
            params.get("document")
        )
        artifacts = _view_artifacts(params, document)
        width = _pixel_dimension(params.get("width", 640), "width")
        height = _pixel_dimension(params.get("height", 480), "height")
        _display_mode(params.get("display_mode"))
        projection = _projection(params.get("projection"))
        include_section = params.get("include_section", False)
        if not isinstance(include_section, bool):
            raise ValueError("include_section must be a boolean")
        section_axis = params.get("section_axis", "y")
        if section_axis not in ("x", "y", "z"):
            raise ValueError("section_axis must be x, y, or z")
        rows = _view_directions(params.get("views"))
        focus_metadata, focus_shape = _focus_box(params.get("focus_box_mm"))
        initial_revisions = {
            "source": _source_view_revision(source),
            "target": _target_view_revision(
                document, rebuild, _reconstruction_ir
            ),
        }
        result_object = _result_object(rebuild)
        if result_object is None:
            raise _view_precondition(
                "Target has no Result to render; finish a native result or request Source-only views.",
                "view_result_unavailable")
        effective_tip = (
            result_object.Tip
            if result_object.TypeId == "PartDesign::Body"
            else result_object
        )
        if effective_tip is None:
            raise _view_precondition("Target Result has no Tip to render; finish a native result or request Source-only views.",
                                     "view_tip_unavailable")
        rebuild_display_plan = _result_display_plan(rebuild)
        rebuild_display_views = list(rebuild_display_plan["render"])
        rebuild_style_views = list(rebuild_display_plan["style"])
        rebuild_view = rebuild_style_views[0] if rebuild_style_views else None
        rebuild_display_modes = (
            list(rebuild_view.listDisplayModes())
            if rebuild_view is not None
            else []
        )
        if not rebuild_display_modes:
            raise _view_precondition("Target Result has no GUI style object for views.", "view_style_unavailable")
        if source.Shape is None or source.Shape.isNull():
            raise _view_precondition("Source snapshot has no geometry for views.", "view_result_unavailable")
        rows = rows + (("section",) if include_section else ())
        columns = ("source", "rebuild")
        image_kinds = [column + "_" + row for row in rows for column in columns]
        paths = scope.reserve_artifacts(artifacts, [*image_kinds, "paired"])
        image_paths = {kind: paths[kind] for kind in image_kinds}
        board_path = paths["paired"]
        view = None
        source_preview = None
        source_section_preview = None
        rebuild_focus_preview = None
        rebuild_section_preview = None
        section_metadata = None
        result = None
        operation_error = None
        operation_phase = "setup"
        try:
            App.setActiveDocument(document.Name)
            gui_document = Gui.activeDocument()
            if gui_document is None:
                raise RuntimeError("Target GUI document is unavailable for reconstruction views")
            view = gui_document.activeView()
            source_preview = scope.create_preview("REAgentSourcePreview")
            source_preview.Label = "Temporary Isolated Source Preview"
            source_preview.Shape = source.Shape.copy()
            if getattr(source_preview, "ViewObject", None) is None:
                raise _view_precondition("Source preview has no GUI object for views.", "view_style_unavailable")
            source_preview.ViewObject.ShapeColor = (0.75, 0.80, 0.90)
            source_preview.ViewObject.LineColor = (0.20, 0.25, 0.35)
            source_preview.ViewObject.Transparency = 0
            if focus_shape is not None:
                source_focus = source.Shape.common(focus_shape)
                rebuild_focus = _result_shape(rebuild).common(focus_shape)
                if source_focus.isNull() or rebuild_focus.isNull():
                    raise ValueError("focus_box_mm does not intersect both shapes")
                source_preview.Shape = source_focus
                rebuild_focus_preview = scope.create_preview("REAgentRebuildFocusPreview")
                rebuild_focus_preview.Label = "Temporary Local Rebuild Preview"
                rebuild_focus_preview.Shape = rebuild_focus
                rebuild_focus_preview.ViewObject.ShapeColor = (0.25, 0.65, 0.95)
                rebuild_focus_preview.ViewObject.LineColor = (0.10, 0.25, 0.40)
            if include_section:
                source_section, section_metadata = _section_preview_shape(
                    source.Shape, section_axis
                )
                rebuild_section, _ignored = _section_preview_shape(
                    _result_shape(rebuild), section_axis
                )
                source_section_preview = scope.create_preview("REAgentSourceSectionPreview")
                source_section_preview.Label = "Temporary Source Section Preview"
                source_section_preview.Shape = source_section
                source_section_preview.ViewObject.ShapeColor = (0.75, 0.80, 0.90)
                source_section_preview.ViewObject.LineColor = (0.20, 0.25, 0.35)
                rebuild_section_preview = scope.create_preview("REAgentRebuildSectionPreview")
                rebuild_section_preview.Label = "Temporary Rebuild Section Preview"
                rebuild_section_preview.Shape = rebuild_section
                rebuild_section_preview.ViewObject.ShapeColor = (0.85, 0.80, 0.55)
                rebuild_section_preview.ViewObject.LineColor = (0.25, 0.20, 0.10)
            for obj in document.Objects:
                if hasattr(obj, "ViewObject"):
                    obj.ViewObject.Visibility = False

            operation_phase = "render"
            canonical_rows = tuple(row for row in rows if row != "section")
            rebuild_main_views = (
                rebuild_focus_preview.ViewObject
                if rebuild_focus_preview is not None
                else rebuild_display_plan
            )
            main_items = {
                "source": source_preview.ViewObject,
                "rebuild": rebuild_main_views,
            }
            canonical_paths = {
                "%s_%s" % (column, row): image_paths["%s_%s" % (column, row)]
                for column in columns
                for row in canonical_rows
            }
            camera_metadata = _render_canonical_views(
                view,
                main_items,
                canonical_paths,
                canonical_rows,
                width,
                height,
                scope=scope,
                transparency=_view_transparency(params.get("transparency", 0)),
                display_mode=params.get("display_mode"),
                projection=projection,
            )
            if include_section:
                section_items = {
                    "source": source_section_preview.ViewObject,
                    "rebuild": rebuild_section_preview.ViewObject,
                }
                section_paths = {
                    "source_iso_xm_ym_zp": image_paths["source_section"],
                    "rebuild_iso_xm_ym_zp": image_paths["rebuild_section"],
                }
                _render_canonical_views(
                    view,
                    section_items,
                    section_paths,
                    ("iso_xm_ym_zp",),
                    width,
                    height,
                    scope=scope,
                    display_mode=params.get("display_mode"),
                    projection=projection,
                    camera_metadata=camera_metadata,
                    transparency=_view_transparency(params.get("transparency", 0)),
                )

            operation_phase = "output"
            for path in image_paths.values():
                if not os.path.isfile(path):
                    raise RuntimeError("FreeCAD did not create view image: %s" % path)
            _compose_view_board(
                image_paths, board_path, rows, columns, width, height
            )
            images = []
            for row in rows:
                camera_direction = (
                    "iso_xm_ym_zp" if row == "section" else row
                )
                for column in columns:
                    evidence_role = "source" if column == "source" else "target"
                    images.append(
                        _image_manifest(
                            image_paths["%s_%s" % (column, row)],
                            evidence_role,
                            initial_revisions[evidence_role],
                            row,
                            camera_metadata["camera"][camera_direction],
                            camera_metadata["projection"],
                            camera_metadata["object_display_mode"],
                            camera_metadata["requested_display_mode"],
                            camera_metadata["normalized_display_mode"],
                            camera_metadata["scale"],
                            width,
                            height,
                            capture_id=scope.capture_id, capture_number=scope.capture_number,
                        )
                    )
            result = {
                "document": document.Name,
                "capture_id": scope.capture_id, "capture_number": scope.capture_number,
                "width": width,
                "height": height,
                "views": image_paths,
                "images": images,
                "board": board_path,
                "board_manifest": _image_artifact(
                    board_path,
                    role="paired",
                    revision=dict(initial_revisions),
                    capture_id=scope.capture_id, capture_number=scope.capture_number,
                ),
                "evidence_status": "generated",
                "layout": {
                    "rows": list(rows),
                    "columns": list(columns),
                    "pairing": "same_direction_adjacent",
                    "cell_order": "row_major",
                },
                "section": section_metadata,
                "focus_box_mm": focus_metadata,
                "requested_display_mode": camera_metadata["requested_display_mode"],
                "normalized_display_mode": camera_metadata["normalized_display_mode"],
                "object_display_mode": camera_metadata["object_display_mode"],
                "projection": camera_metadata["projection"],
                "camera": camera_metadata["camera"],
                "scale": camera_metadata["scale"],
                "revision": initial_revisions["target"],
                "paired_revisions": {
                    "source_revision": initial_revisions["source"],
                    "source_revision_basis": "step_sha256",
                    "source_sha256": source.SourceSHA256,
                    "target_revision": initial_revisions["target"],
                    "rebuild_object_count": len(_owned_objects(rebuild)),
                },
                "projection_layout": (
                    "first_angle"
                    if all(item in canonical_rows for item in ("front", "top", "left"))
                    else "requested"
                ),
                "rebuild_rendered_from": {
                    "name": effective_tip.Name,
                    "type_id": effective_tip.TypeId,
                    "body_display_mode": rebuild_display_plan[
                        "body_display_mode"
                    ],
                    "display_modes": rebuild_display_modes,
                    "display_objects": [
                        {"name": getattr(getattr(item, "Object", None), "Name", None),
                         "type_id": getattr(getattr(item, "Object", None), "TypeId", None)}
                        for item in rebuild_display_views
                    ],
                    "style_objects": [
                        {"name": getattr(getattr(item, "Object", None), "Name", None),
                         "type_id": getattr(getattr(item, "Object", None), "TypeId", None)}
                        for item in rebuild_style_views
                    ],
                },
            }
        except Exception as exc:
            operation_error = exc
        finally:
            _cleanup_observation_previews(document, scope, (
                source_preview, source_section_preview, rebuild_focus_preview, rebuild_section_preview))
        if operation_error is not None:
            operation_error.view_phase = operation_phase
            raise operation_error
        return result


    def _get_initial_views(self, params, scope):
        return self._get_observation_views({**params, "_initial_views": True,
                                           "projection": "orthographic", "display_mode": "flat_lines",
                                           "transparency": 0}, scope)

    def _get_observation_views(self, params, scope):
        if not getattr(App, "GuiUp", False):
            raise ValueError("get_observation_views requires FreeCAD GUI mode")
        role = _state.INSTANCE_ROLE
        initial_views = params.get("_initial_views", False)
        source = None
        rebuild = None
        reconstruction_ir = None
        if role == "source":
            document, source = _source_objects(params.get("document"))
            shape = source.Shape
            initial_revision = _source_view_revision(source)
            evidence_role = "source"
        elif role == "rebuild":
            document, _source, rebuild, reconstruction_ir = _target_state(
                params.get("document")
            )
            result_object = _result_object(rebuild)
            if result_object is None:
                raise _view_precondition(
                    "Target has no Result to render; finish a native result or request Source-only views.",
                    "view_result_unavailable")
            effective_tip = result_object.Tip if result_object.TypeId == "PartDesign::Body" else result_object
            if effective_tip is None:
                raise _view_precondition("Target Result has no Tip to render; finish a native result or request Source-only views.",
                                         "view_tip_unavailable")
            shape = _result_shape(rebuild)
            display_plan = _result_display_plan(rebuild)
            if not display_plan["style"] or not display_plan["render"]:
                raise _view_precondition("Target Result has no GUI objects for views.", "view_style_unavailable")
            initial_revision = _target_view_revision(
                document, rebuild, reconstruction_ir
            )
            evidence_role = "target"
        else:
            raise ValueError("Observation views require source or rebuild role")
        if role == "source" and getattr(source, "ViewObject", None) is None:
            raise _view_precondition("Source has no GUI object for views.", "view_style_unavailable")

        artifacts = _view_artifacts(params, document)
        width = _pixel_dimension(params.get("width", 640), "width")
        height = _pixel_dimension(params.get("height", 480), "height")
        _display_mode(params.get("display_mode"))
        projection = _projection(params.get("projection"))
        directions = VIEW_DIRECTIONS if initial_views else _view_directions(params.get("views"))
        focus_metadata, focus_shape = _focus_box(params.get("focus_box_mm"))
        if focus_shape is not None:
            shape = shape.common(focus_shape)
            if shape.isNull():
                raise ValueError("focus_box_mm does not intersect the role shape")
        board_kinds = ["initial_" + key for key in INITIAL_VIEW_GROUPS] if initial_views else []
        paths = scope.reserve_artifacts(artifacts, [*(role + "_" + direction for direction in directions), *board_kinds])
        image_paths = {direction: paths[role + "_" + direction] for direction in directions}
        board_paths = {key: paths["initial_" + key] for key in INITIAL_VIEW_GROUPS} if initial_views else {}
        view = None
        preview = None
        temporary_preview = False
        result = None
        operation_error = None
        operation_phase = "setup"
        _operation_stage("observation", mutating=True)
        try:
            App.setActiveDocument(document.Name)
            gui_document = Gui.activeDocument()
            if gui_document is None:
                raise RuntimeError("GUI document is unavailable for observation views")
            view = gui_document.activeView()
            scope.assert_render_safe()
            for obj in document.Objects:
                if hasattr(obj, "ViewObject"):
                    obj.ViewObject.Visibility = False
            if focus_shape is not None:
                preview = scope.create_preview("REAgentObservationPreview")
                temporary_preview = True
                preview.Shape = shape.copy()
            else:
                preview = source if role == "source" else result_object
            if role == "source":
                display_item = preview.ViewObject
                display_render_views = [preview.ViewObject]
                display_style_views = [preview.ViewObject]
            elif temporary_preview:
                display_item = preview.ViewObject
                display_render_views = [preview.ViewObject]
                display_style_views = [preview.ViewObject]
            else:
                display_item = display_plan
                display_render_views = list(display_plan["render"])
                display_style_views = list(display_plan["style"])
            operation_phase = "render"
            camera_metadata = _render_canonical_views(
                view,
                {"observation": display_item},
                # Role-local previews share the same explicit opacity contract.
                {"observation_%s" % direction: image_paths[direction] for direction in directions},
                directions,
                width,
                height,
                scope=scope,
                transparency=_view_transparency(params.get("transparency", 0)),
                display_mode=params.get("display_mode"),
                projection=projection,
                framing_box=_geometry_bound_box(shape) if initial_views else None,
            )
            operation_phase = "output"
            images = [
                _image_manifest(
                    image_paths[direction],
                    evidence_role,
                    initial_revision,
                    direction,
                    camera_metadata["camera"][direction],
                    camera_metadata["projection"],
                    camera_metadata["object_display_mode"],
                    camera_metadata["requested_display_mode"],
                    camera_metadata["normalized_display_mode"],
                    camera_metadata["scale"],
                    width,
                    height,
                    capture_id=scope.capture_id, capture_number=scope.capture_number,
                )
                for direction in directions
            ]
            result = {
                "document": document.Name,
                "capture_id": scope.capture_id, "capture_number": scope.capture_number,
                "role": evidence_role,
                "owner": "SourceOriginal" if role == "source" else "Rebuild",
                "width": width,
                "height": height,
                "views": image_paths,
                "images": images,
                "evidence_status": "generated",
                "focus_box_mm": focus_metadata,
                "requested_display_mode": camera_metadata["requested_display_mode"],
                "normalized_display_mode": camera_metadata["normalized_display_mode"],
                "object_display_mode": camera_metadata["object_display_mode"],
                "projection": camera_metadata["projection"],
                "camera": camera_metadata["camera"],
                "scale": camera_metadata["scale"],
                "display_objects": [
                    {"name": getattr(getattr(item, "Object", None), "Name", None),
                     "type_id": getattr(getattr(item, "Object", None), "TypeId", None)}
                    for item in display_render_views
                ],
                "style_objects": [
                    {"name": getattr(getattr(item, "Object", None), "Name", None),
                     "type_id": getattr(getattr(item, "Object", None), "TypeId", None)}
                    for item in display_style_views
                ],
                "revision": initial_revision,
                "projection_layout": (
                    "initial_boards" if initial_views else (
                        "first_angle"
                        if all(item in directions for item in ("front", "top", "left"))
                        else "requested"
                    )
                ),
            }
            if initial_views:
                layouts = _compose_initial_boards(image_paths, board_paths, width, height)
                result.update(
                    initial_views={"directions": list(directions), "direction_count": len(directions),
                                   "complete": True, "capture_id": scope.capture_id},
                    boards={key: {"path": path, "layout": layouts[key],
                                  "manifest": _image_artifact(path, role="source", revision=initial_revision,
                                                              capture_id=scope.capture_id, capture_number=scope.capture_number)}
                            for key, path in board_paths.items()},
                )
            if role == "rebuild":
                result["result_object"] = {
                    "name": result_object.Name,
                    "type_id": result_object.TypeId,
                }
                result["rendered_from"] = result_object.Name
                result["body_display_mode"] = display_plan[
                    "body_display_mode"
                ]
        except Exception as exc:
            operation_error = exc
        finally:
            _cleanup_observation_previews(document, scope, (preview,) if temporary_preview else ())
        if operation_error is not None:
            operation_error.view_phase = operation_phase
            raise operation_error
        return result
