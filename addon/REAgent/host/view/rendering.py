"""View rendering, image artifacts and composition; no durable recovery decisions."""

import math
import os

import FreeCAD as App
import Part
from PySide import QtCore, QtGui

from REAgent.host.analysis.measurements import _geometry_bound_box
from REAgent.protocol.observations import DEFAULT_VIEW_DIRECTIONS, VIEW_DIRECTIONS, VIEW_DISPLAY_MODES, VIEW_QUERY_FIELDS, IMAGE_CONTENT_LIMITS, view_direction_label, validate_observation_role
from REAgent.host.view.cameras import VIEW_CAMERA_SPECS
from REAgent.host.file_digest import _file_sha256
from REAgent.host.view.camera import _apply_canonical_view, _camera_metadata, _camera_numeric_state, _camera_state_differences, _canonical_view_method, _direction_error_deg, _projection, _refresh_camera, _record_camera_failure, _set_camera_height, _set_camera_focus, _set_orthographic_projection, _wait_for_camera_stable


def _existing_directory(value):
    if not isinstance(value, str) or not value or not os.path.isabs(value):
        raise ValueError("Bound output directory must be a non-empty absolute path")
    path = os.path.normpath(value)
    if not os.path.isdir(path):
        raise ValueError("Bound output directory is unavailable: %s" % path)
    return path


def _pixel_dimension(value, field):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("%s must be an integer" % field)
    number = value
    if number < 128 or number > 4096:
        raise ValueError("%s must be an integer from 128 to 4096" % field)
    return number


def _view_directions(value):
    if value is None:
        return DEFAULT_VIEW_DIRECTIONS
    if not isinstance(value, list) or not value or len(value) > 8:
        raise ValueError("views must be a non-empty list of at most 8 directions")
    result = []
    for direction in value:
        if direction not in VIEW_DIRECTIONS:
            raise ValueError("Unsupported view direction: %s" % direction)
        if direction not in result:
            result.append(direction)
    return tuple(result)


def _display_mode(value):
    value = "flat_lines" if value is None else value
    if value not in VIEW_DISPLAY_MODES:
        raise ValueError(
            "display_mode must be wireframe, flat_lines, or shaded"
        )
    return value


def _view_precondition(message, code):
    failure = ValueError(message)
    failure.error_class = "precondition"
    failure.error_code = code
    failure.retry_action = "correct_request"
    return failure


def _view_transparency(value):
    if type(value) is not int or not 0 <= value <= 100:
        raise ValueError("transparency must be an integer from 0 to 100")
    return value


    # _camera_metadata will report the actual vectors and a useful error if a
    # host did not settle on the declared standard pose.


# Coin camera fields are single precision. These tolerances describe display
# state round trips, not tolerances for model geometry or exact comparison.


def _render_item_groups(value):
    """Normalize a render item into visible roots and style owners."""
    if isinstance(value, dict):
        if "render" not in value or "style" not in value:
            raise ValueError("Canonical rendering item plan requires render and style")
        render_value = value["render"]
        style_value = value["style"]
    else:
        render_value = style_value = value

    def group(item):
        return tuple(item) if isinstance(item, (list, tuple)) else (item,)

    render_group = group(render_value)
    style_group = group(style_value)
    if not render_group or not style_group:
        raise ValueError("Canonical rendering item plan cannot be empty")
    return render_group, style_group


def _render_canonical_views(view, items, *args, scope, transparency=0, **kwargs):
    """Render with explicit opacity; retain local cleanup facts for the scope owner."""
    if type(transparency) is not int or not 0 <= transparency <= 100:
        raise ValueError("transparency must be an integer from 0 to 100")
    scope.assert_render_safe()
    operation_error = None
    snapshots = []
    try:
        seen = set()
        styled = set()
        for value in items.values():
            render_group, style_group = _render_item_groups(value)
            styled.update(id(obj) for obj in style_group)
            for obj in (*render_group, *style_group):
                if id(obj) in seen:
                    continue
                seen.add(id(obj))
                state = {
                    key: getattr(obj, key)
                    for key in (
                        "Visibility", "DisplayMode", "DisplayModeBody",
                        "Transparency",
                    )
                    if hasattr(obj, key)
                }
                snapshots.append((obj, state))
                if obj in style_group and "Transparency" in state:
                    obj.Transparency = transparency
            if isinstance(value, dict) and value.get("body") is not None:
                body_view = value["body"]
                expected_mode = value.get("body_display_mode", "Through")
                if not hasattr(body_view, "DisplayModeBody"):
                    raise ValueError(
                        "Canonical rendering Body has no DisplayModeBody"
                    )
                body_view.DisplayModeBody = expected_mode
                if str(body_view.DisplayModeBody) != expected_mode:
                    raise RuntimeError(
                        "Canonical rendering Body did not enter %s mode"
                        % expected_mode
                    )
        metadata = _render_canonical_views_core(view, items, *args, scope=scope, **kwargs)
        metadata.setdefault("transparency", {}).update({
            obj.Object.Name: int(obj.Transparency)
            for obj, state in snapshots
            if id(obj) in styled and "Transparency" in state
        })
        metadata["requested_transparency"] = transparency
        return metadata
    except Exception as exc:
        operation_error = exc
        raise
    finally:
        errors = []
        try:
            scope.assert_render_safe()
        except Exception as exc:
            errors.append(str(exc))
        else:
            for key in ("DisplayModeBody", "DisplayMode", "Transparency", "Visibility"):
                for obj, state in snapshots:
                    if key not in state:
                        continue
                    try:
                        setattr(obj, key, state[key])
                    except Exception as exc:
                        errors.append(str(exc))
        for message in errors:
            scope.cleanup_errors.add("renderer_cleanup: " + message)
        # Final camera/restoration certification belongs to the coordinator.
        # Preserve a real rendering error even if local cleanup also fails.
        if errors and operation_error is None:
            raise RuntimeError("Local renderer cleanup failed: " + "; ".join(errors)[:500])


def _render_canonical_views_core(
    view,
    items,
    capture,
    directions,
    width,
    height,
    *,
    scope,
    display_mode=None,
    projection="orthographic",
    camera_metadata=None,
    framing_box=None,
):
    """Render role-local objects with one verified orthographic camera scale.

    ``items`` maps output prefixes (for example ``source``/``rebuild``) to
    temporary ViewObjects.  All objects are framed together, then captured one
    at a time with the same camera height, so a Source/Rebuild pair and the
    canonical front/top/left views are pixel-scale comparable.
    """

    if not isinstance(items, dict) or not items:
        raise ValueError("Canonical rendering requires at least one display item")
    directions = tuple(directions)
    if not directions:
        raise ValueError("Canonical rendering requires at least one direction")
    for direction in directions:
        _canonical_view_method(view, direction)
    requested_display_mode = display_mode
    display_mode = _display_mode(display_mode)
    projection = _projection(projection)
    normalized_items = {}
    render_objects = []
    actual_display_modes = []
    for label, value in items.items():
        if value is None:
            raise ValueError("Canonical rendering item %s is empty" % label)
        render_group, style_group = _render_item_groups(value)
        if any(not hasattr(item, "Visibility") for item in render_group):
            raise ValueError("Canonical rendering item %s has no ViewObject" % label)
        if any(not hasattr(item, "DisplayMode") for item in style_group):
            raise ValueError("Canonical rendering style %s has no ViewObject" % label)
        styled_count = 0
        for index, view_object in enumerate(style_group):
            try:
                _previous, actual = _set_display_mode(
                    view_object,
                    "%s preview %d" % (label, index + 1),
                    display_mode,
                )
                actual_display_modes.append(actual)
                styled_count += 1
            except ValueError:
                if len(style_group) == 1:
                    raise
        if styled_count == 0:
            raise ValueError(
                "Canonical rendering item %s has no object supporting %s"
                % (label, display_mode)
            )
        normalized_items[label] = render_group
        render_objects.extend(render_group)
    unique_actual_modes = sorted(set(actual_display_modes))
    if len(unique_actual_modes) != 1:
        raise RuntimeError(
            "Canonical rendering objects did not use one display mode: %s"
            % unique_actual_modes
        )
    actual_display_mode = unique_actual_modes[0]

    if camera_metadata is None:
        _set_orthographic_projection(view)
        for view_object in render_objects:
            view_object.Visibility = True
        heights = []
        for direction in directions:
            state = None
            try:
                _apply_canonical_view(view, direction, scope)
                view.fitAll()
                state = _wait_for_camera_stable(view, direction, scope, phase="fit")
                heights.append(state["fields"]["height"])
            except Exception as exc:
                _record_camera_failure(exc, direction, "fit", None, state)
                raise
        center = None
        if framing_box is not None:
            center = {axis: (getattr(framing_box, axis.upper() + "Min") +
                             getattr(framing_box, axis.upper() + "Max")) / 2
                      for axis in ("x", "y", "z")}
            corners = [dict(x=x, y=y, z=z)
                       for x in (framing_box.XMin, framing_box.XMax)
                       for y in (framing_box.YMin, framing_box.YMax)
                       for z in (framing_box.ZMin, framing_box.ZMax)]
            for direction in directions:
                spec = VIEW_CAMERA_SPECS[direction]
                forward, up = spec["camera_direction"], spec["up_direction"]
                right = dict(x=forward["y"] * up["z"] - forward["z"] * up["y"],
                             y=forward["z"] * up["x"] - forward["x"] * up["z"],
                             z=forward["x"] * up["y"] - forward["y"] * up["x"])
                for corner in corners:
                    vertical = abs(sum((corner[a] - center[a]) * up[a] for a in center))
                    horizontal = abs(sum((corner[a] - center[a]) * right[a] for a in center))
                    heights.append(2 * max(vertical, horizontal * height / width))
        common_height = _set_camera_height(view, max(heights) * 1.05)
        camera_metadata = {
            "projection": projection,
            "requested_display_mode": requested_display_mode,
            "normalized_display_mode": display_mode,
            "object_display_mode": actual_display_mode,
            "directions": list(directions),
            "scale": {
                "shared": True,
                "camera_height_mm": float(common_height),
                "millimeters_per_pixel": float(common_height) / float(height),
                "margin_factor": 1.05,
                "width_px": int(width),
                "height_px": int(height),
                "focus_mm": center,
            },
            "camera": {},
        }
    else:
        if camera_metadata.get("projection") != projection:
            raise ValueError("Shared canonical camera projection does not match")
        if camera_metadata.get("normalized_display_mode") != display_mode:
            raise ValueError("Shared canonical display mode does not match")
        if camera_metadata.get("object_display_mode") != actual_display_mode:
            raise ValueError("Shared canonical object display mode does not match")
        common_height = camera_metadata.get("scale", {}).get("camera_height_mm")
        center = camera_metadata.get("scale", {}).get("focus_mm")
        if common_height is None:
            raise ValueError("Shared canonical camera has no scale")
        _set_orthographic_projection(view)

    for direction in directions:
        state = None
        phase = "settle_initial"
        try:
            _apply_canonical_view(view, direction, scope)
            settled = _wait_for_camera_stable(view, direction, scope, phase=phase, center=center)
            phase = "stop_animation"
            view.stopAnimating()
            state = _camera_numeric_state(view)
            differences = _camera_state_differences(view, settled, actual=state)
            if differences:
                raise RuntimeError("Camera changed while stopping animation: " + "; ".join(differences))
            phase = "calibrate"
            basis = state
            state = None
            _set_camera_height(view, common_height)
            if center is not None:
                _set_camera_focus(view, center, state=basis)
            phase = "settle_after_calibrate"
            state = _wait_for_camera_stable(view, direction, scope, phase=phase, center=center)
            phase = "validate_camera"
            actual_camera = _camera_metadata(
                view, direction, common_height, width, height, state=state, center=center)
            previous_camera = camera_metadata.setdefault("camera", {}).get(direction)
            if previous_camera is not None and any(
                _direction_error_deg(previous_camera[key], actual_camera[key]) > 0.01
                for key in ("actual_camera_direction", "actual_up_direction")
            ):
                raise RuntimeError("Shared canonical camera pose changed for %s" % direction)
            camera_metadata["camera"][direction] = actual_camera
            settled = state
            for label, group in normalized_items.items():
                phase = "before_capture"
                state = None
                for other in render_objects:
                    other.Visibility = other in group
                _refresh_camera(scope)
                state = _camera_numeric_state(view)
                _camera_metadata(view, direction, common_height, width, height,
                                 state=state, center=center, expected=settled)
                phase = "capture"
                capture(label, direction, view, camera_metadata)
                phase = "after_capture"
                state = None
                state = _camera_numeric_state(view)
                _camera_metadata(view, direction, common_height, width, height,
                                 state=state, center=center, expected=settled)
        except Exception as exc:
            _record_camera_failure(exc, direction, phase, center, state)
            raise
    scope.assert_render_safe()
    for view_object in render_objects:
        view_object.Visibility = False
    return camera_metadata


def _focus_box(value):
    if value is None:
        return None, None
    fields = ("x_min", "x_max", "y_min", "y_max", "z_min", "z_max")
    if not isinstance(value, dict) or set(value) != set(fields):
        raise ValueError("focus_box_mm must contain x/y/z min/max")
    parsed = {}
    for field in fields:
        number = value[field]
        if isinstance(number, bool):
            raise ValueError("focus_box_mm values must be finite numbers")
        try:
            number = float(number)
        except (TypeError, ValueError):
            raise ValueError("focus_box_mm values must be finite numbers")
        if not math.isfinite(number):
            raise ValueError("focus_box_mm values must be finite numbers")
        parsed[field] = number
    for axis in ("x", "y", "z"):
        if parsed[axis + "_max"] <= parsed[axis + "_min"]:
            raise ValueError("focus_box_mm maximums must exceed minimums")
    shape = Part.makeBox(
        parsed["x_max"] - parsed["x_min"],
        parsed["y_max"] - parsed["y_min"],
        parsed["z_max"] - parsed["z_min"],
        App.Vector(parsed["x_min"], parsed["y_min"], parsed["z_min"]),
    )
    return parsed, shape


_FREECAD_DISPLAY_MODES = {
    "wireframe": "Wireframe",
    "flat_lines": "Flat Lines",
    "shaded": "Shaded",
}


def _set_display_mode(view_object, label, requested):
    """Set and read back object DisplayMode; this does not certify PNG pixel style."""

    if view_object is None:
        raise ValueError("%s has no GUI view object" % label)
    requested = _display_mode(requested)
    host_mode = _FREECAD_DISPLAY_MODES[requested]
    display_modes = list(view_object.listDisplayModes())
    if host_mode not in display_modes:
        raise ValueError("%s does not support %s display mode" % (label, host_mode))
    previous = str(view_object.DisplayMode)
    view_object.DisplayMode = host_mode
    actual = str(view_object.DisplayMode)
    if actual != host_mode:
        raise RuntimeError(
            "%s did not enter requested display mode %s; actual=%s"
            % (label, host_mode, actual)
        )
    return previous, actual


def _validate_view_query(query, role):
    """Validate and normalize the complete kind=view query contract."""

    if not isinstance(query, dict):
        raise ValueError("view query must be an object")
    unknown = sorted(set(query) - set(VIEW_QUERY_FIELDS))
    if unknown:
        raise ValueError(
            "Unknown view query parameters: %s; allowed fields: %s"
            % (", ".join(unknown), ", ".join(VIEW_QUERY_FIELDS))
        )
    paired = query.get("paired", False)
    if not isinstance(paired, bool):
        raise ValueError("paired must be a boolean")
    include_section = query.get("include_section", False)
    if not isinstance(include_section, bool):
        raise ValueError("include_section must be a boolean")
    validate_observation_role("view", query, role)
    if include_section and not paired:
        raise ValueError("include_section requires paired=true")
    if "section_axis" in query and not include_section:
        raise ValueError("section_axis requires include_section=true")
    section_axis = query.get("section_axis", "y")
    if section_axis not in ("x", "y", "z"):
        raise ValueError("section_axis must be x, y, or z")
    focus_metadata, _focus_shape = _focus_box(query.get("focus_box_mm"))
    normalized = {
        "views": list(_view_directions(query.get("views"))),
        "width": _pixel_dimension(query.get("width", 640), "width"),
        "height": _pixel_dimension(query.get("height", 480), "height"),
        "output_long_edge_px": query.get("output_long_edge_px", 1536),
        "transparency": _view_transparency(query.get("transparency", 0)),
        "projection": _projection(query.get("projection")),
        "focus_box_mm": focus_metadata,
        "paired": paired,
        "include_section": include_section,
    }
    if "display_mode" in query:
        normalized["display_mode"] = _display_mode(query["display_mode"])
    if include_section:
        normalized["section_axis"] = section_axis
    return normalized


def _image_has_visible_content(image):
    width = min(256, int(image.width()))
    height = min(256, int(image.height()))
    sample = image.scaled(
        width,
        height,
        QtCore.Qt.IgnoreAspectRatio,
        QtCore.Qt.SmoothTransformation,
    )
    for y_value in range(height):
        for x_value in range(width):
            color = QtGui.QColor(sample.pixel(x_value, y_value))
            if color.alpha() > 0 and max(
                abs(255 - color.red()),
                abs(255 - color.green()),
                abs(255 - color.blue()),
            ) > 3:
                return True
    return False


def _image_artifact(path, *, layout, capture_id, capture_number, role, revision):
    size = os.path.getsize(path)
    if not 0 < size <= IMAGE_CONTENT_LIMITS["max_image_bytes"]:
        raise ValueError("Encoded PNG exceeds single-image byte capacity or is empty")
    reader = QtGui.QImageReader(path)
    dimensions = reader.size()
    if (dimensions.width(), dimensions.height()) != (layout["width_px"], layout["height_px"]):
        raise ValueError("Final PNG dimensions differ from the planned canvas")
    return {"path": os.path.abspath(path), "capture_id": capture_id, "capture_number": capture_number,
            "sha256": _file_sha256(path), "size_bytes": size,
            "width_px": layout["width_px"], "height_px": layout["height_px"],
            "evidence_status": "generated", "role": role, "revision": revision}


class _CaptureOutput:
    """Stream native frames into one final canvas at a time; encode each only once."""

    def __init__(self, scope, artifacts, layouts, revisions, *, section=None):
        if not 0 < len(layouts) <= IMAGE_CONTENT_LIMITS["max_images"]:
            raise ValueError("Complete image group exceeds image-count capacity")
        self.scope, self.layouts, self.revisions = scope, layouts, revisions
        self.section = section
        self.paths = scope.reserve_artifacts(artifacts, list(layouts))
        self.frame_path = scope.temporary_image_path(artifacts["output_dir"])
        self.lookup = {(cell["role"], cell["direction"]): (key, cell)
                       for key, layout in layouts.items() for cell in layout["cells"]}
        if len(self.lookup) != sum(len(layout["cells"]) for layout in layouts.values()):
            raise ValueError("Capture layout has duplicate cells")
        self.key = self.canvas = None
        self.seen, self.outputs, self.frames = set(), {}, []
        self.total_bytes = 0

    def _close_canvas(self):
        if self.canvas is None:
            return
        layout = self.layouts[self.key]
        expected = {(cell["role"], cell["direction"]) for cell in layout["cells"]}
        if not expected <= self.seen:
            raise ValueError("Cannot publish an incomplete canvas")
        path = self.paths[self.key]
        if not self.canvas.save(path, "PNG"):
            raise RuntimeError("Cannot encode final view PNG")
        self.canvas = None
        role = "paired" if len(self.revisions) > 1 else next(iter(self.revisions))
        revision = dict(self.revisions) if role == "paired" else self.revisions[role]
        manifest = _image_artifact(path, layout=layout, capture_id=self.scope.capture_id,
            capture_number=self.scope.capture_number, role=role, revision=revision)
        self.total_bytes += manifest["size_bytes"]
        if self.total_bytes > IMAGE_CONTENT_LIMITS["max_total_bytes"]:
            raise ValueError("Encoded complete PNG group exceeds byte capacity")
        self.outputs[self.key] = {"path": path, "layout": layout, "manifest": manifest}

    def capture(self, role, direction, view, camera):
        output_direction = "section" if self.section is not None and self.section.get("capturing") else direction
        identity = (role, output_direction)
        if identity in self.seen:
            raise ValueError("Native frame was captured twice")
        key, cell = self.lookup[identity]
        layout = self.layouts[key]
        if key != self.key:
            self._close_canvas()
            if key in self.outputs:
                raise ValueError("Capture order revisited an encoded canvas")
            self.key = key
            self.canvas = QtGui.QImage(layout["width_px"], layout["height_px"], QtGui.QImage.Format_ARGB32)
            if self.canvas.isNull():
                raise RuntimeError("Cannot allocate final view canvas")
            if self.canvas.bytesPerLine() * self.canvas.height() > 4 * IMAGE_CONTENT_LIMITS["max_image_pixels"]:
                raise ValueError("Final canvas exceeds raw buffer capacity")
            self.canvas.fill(QtGui.QColor("white"))
        width, height = layout["sampling_width_px"], layout["sampling_height_px"]
        # Native PNG stays on the owned temporary path. Check header dimensions
        # before decoding; its raw pixel budget is independent of final PNG bytes.
        # A failed native save must not reuse the previous direction's pixels.
        with open(self.frame_path, "wb"):
            pass
        if view.saveImage(self.frame_path, width, height, "White") is False:
            raise RuntimeError("Native frame save failed")
        reader = QtGui.QImageReader(self.frame_path)
        dimensions = reader.size()
        if (dimensions.width(), dimensions.height()) != (width, height):
            raise RuntimeError("Native frame dimensions changed")
        image = reader.read()
        if (image.isNull() or image.depth() > 32
                or image.bytesPerLine() * image.height() > 4 * IMAGE_CONTENT_LIMITS["max_image_pixels"]
                or not _image_has_visible_content(image)):
            raise RuntimeError("Native view frame is unreadable, blank or outside the raw allocation budget")
        visible = [obj.Name for obj in self.scope.document.Objects
                   if getattr(getattr(obj, "ViewObject", None), "Visibility", False)]
        if output_direction == "section":
            expected_name = self.section["preview_names"][role]
            if visible != [expected_name]:
                raise RuntimeError("Section capture contains unexpected visible objects")
        painter = QtGui.QPainter(self.canvas)
        try:
            painter.setRenderHint(QtGui.QPainter.SmoothPixmapTransform)
            painter.drawImage(QtCore.QRect(*cell["image_rect_px"]), image)
            font = painter.font()
            font.setPixelSize(14)
            painter.setFont(font)
            label = view_direction_label(output_direction)
            if output_direction == "section":
                label = "%s min half-cutaway @ %.6g mm" % (self.section["axis"].upper(), self.section["coordinate_mm"])
            label += " · " + ("Source" if role == "source" else "Target")
            painter.setPen(QtGui.QColor("black"))
            label = QtGui.QFontMetrics(font).elidedText(label, QtCore.Qt.ElideRight, cell["label_rect_px"][2])
            painter.drawText(*cell["label_rect_px"], int(QtCore.Qt.AlignCenter), label)
        finally:
            painter.end()
        del image
        final_scale = {"millimeters_per_pixel_x": camera["scale"]["millimeters_per_pixel"] / cell["scale_x"],
                       "millimeters_per_pixel_y": camera["scale"]["millimeters_per_pixel"] / cell["scale_y"]}
        self.frames.append({"role": role, "direction": output_direction, "output_key": key,
            "capture_id": self.scope.capture_id, "capture_number": self.scope.capture_number,
            "camera": dict(camera["camera"][direction]), "projection": camera["projection"],
            "native_scale": dict(camera["scale"]), "final_scale": final_scale,
            "sampling_width_px": width, "sampling_height_px": height,
            "image_rect_px": list(cell["image_rect_px"]),
            "requested_display_mode": camera["requested_display_mode"],
            "normalized_display_mode": camera["normalized_display_mode"],
            "object_display_mode": camera["object_display_mode"],
            "visible_object_count": len(visible)})
        if output_direction == "section":
            self.frames[-1]["transparency"] = int(self.scope.document.getObject(expected_name).ViewObject.Transparency)
        self.seen.add(identity)

    def finish(self):
        self._close_canvas()
        if self.seen != set(self.lookup) or set(self.outputs) != set(self.layouts):
            raise ValueError("Complete capture is missing cells or final PNGs")
        return {"outputs": self.outputs, "images": self.frames}


def _section_preview_shapes(source, target, axis):
    """One world half-space, including both operands' entire retained side."""
    shapes = {"source": source, "target": target}
    bounds = {role: _geometry_bound_box(shape) for role, shape in shapes.items()}
    axes = ("x", "y", "z")
    minimum = {a: min(float(getattr(box, a.upper() + "Min")) for box in bounds.values()) for a in axes}
    maximum = {a: max(float(getattr(box, a.upper() + "Max")) for box in bounds.values()) for a in axes}
    coordinate = (float(getattr(bounds["source"], axis.upper() + "Min")) +
                  float(getattr(bounds["source"], axis.upper() + "Max"))) / 2
    characteristic = max(maximum[a] - minimum[a] for a in axes)
    margin = max(characteristic * 0.02, 0.1)
    origin = {a: minimum[a] - margin for a in axes}
    lengths = {a: (coordinate if a == axis else maximum[a] + margin) - origin[a] for a in axes}
    if not all(math.isfinite(v) for v in (*origin.values(), *lengths.values(), coordinate, margin)) or any(v <= 0 for v in lengths.values()):
        raise ValueError("Shared section box is non-finite or degenerate")
    tool = Part.makeBox(lengths["x"], lengths["y"], lengths["z"], App.Vector(*(origin[a] for a in axes)))
    metadata = {"axis": axis, "coordinate_mm": coordinate, "kept_side": "min", "kind": "half_cutaway",
                "union_min_mm": minimum, "union_max_mm": maximum, "margin_mm": margin, "results": {}}
    result = {}
    for role, shape in shapes.items():
        row = metadata["results"][role] = {"status": "unknown"}
        try:
            if float(getattr(bounds[role], axis.upper() + "Min")) > coordinate:
                row.update(status="empty", reason="bound_entirely_in_discarded_half_space")
            else:
                section = shape.common(tool)
                if section is None or section.isNull():
                    row.update(status="unavailable", reason="null_result_validity_unconfirmed")
                else:
                    volume = float(section.Volume)
                    valid = bool(section.isValid())
                    solids = len(section.Solids)
                    row.update(shape_type=str(section.ShapeType), is_valid=valid, solid_count=solids,
                               volume_mm3=volume if math.isfinite(volume) else None)
                    if valid and not section.Faces and not section.Edges and not solids:
                        row.update(status="empty", reason="valid_empty_result")
                    elif valid and solids and math.isfinite(volume) and volume > 0:
                        row["status"] = "available"
                        result[role] = section
                    else:
                        row.update(status="unavailable", reason="invalid_degenerate_or_non_solid_result")
        except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
            row.update(status="failed", reason=str(exc)[:300], exception_type=type(exc).__name__)
    if len(result) != 2:
        failure = _view_precondition("Section has no comparable renderable solids; inspect per-role results", "section_unavailable")
        failure.failure_detail = {"section": metadata}
        raise failure
    return result, metadata
