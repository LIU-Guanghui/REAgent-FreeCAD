"""View rendering, image artifacts and composition; no durable recovery decisions."""

import math
import os

import FreeCAD as App
import Part
from PySide import QtCore, QtGui

from ..analysis.measurements import _geometry_bound_box
from ..protocol.contract import (
    DEFAULT_VIEW_DIRECTIONS, INITIAL_VIEW_GROUPS, VIEW_BOARD_HEADER_HEIGHT,
    VIEW_CAMERA_SPECS, VIEW_DIRECTIONS, VIEW_DISPLAY_MODES, VIEW_QUERY_FIELDS,
    initial_view_layout, validate_observation_role,
)
from ..runtime import _file_sha256
from .camera import (
    _apply_canonical_view,
    _camera_metadata,
    _camera_numeric_state,
    _camera_state_differences,
    _canonical_view_method,
    _direction_error_deg,
    _projection,
    _refresh_camera,
    _record_camera_failure,
    _set_camera_height,
    _set_camera_focus,
    _set_orthographic_projection,
    _wait_for_camera_stable,
)


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
        metadata["transparency"] = {
            obj.Object.Name: int(obj.Transparency)
            for obj, state in snapshots
            if id(obj) in styled and "Transparency" in state
        }
        metadata["requested_transparency"] = transparency
        for camera_item in metadata["camera"].values():
            camera_item["transparency"] = metadata["transparency"]
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
    image_paths,
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
                path = image_paths.get("%s_%s" % (label, direction))
                if path is None:
                    raise ValueError("Missing output path for %s/%s" % (label, direction))
                state = _camera_numeric_state(view)
                _camera_metadata(view, direction, common_height, width, height,
                                 state=state, center=center, expected=settled)
                phase = "capture"
                view.saveImage(path, width, height, "White")
                phase = "after_capture"
                state = None
                state = _camera_numeric_state(view)
                _camera_metadata(view, direction, common_height, width, height,
                                 state=state, center=center, expected=settled)
                if not os.path.isfile(path):
                    raise RuntimeError("FreeCAD did not create canonical view image: %s" % path)
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


def _image_manifest(
    path,
    role,
    revision,
    direction,
    camera,
    projection,
    object_display_mode,
    requested_display_mode,
    normalized_display_mode,
    scale,
    expected_width,
    expected_height,
    *,
    capture_id,
    capture_number,
):
    if not os.path.isfile(path) or os.path.getsize(path) <= 0:
        raise RuntimeError("FreeCAD did not create a non-empty view image: %s" % path)
    image = QtGui.QImage(path)
    if image.isNull():
        raise RuntimeError("Cannot load rendered view image: %s" % path)
    width = int(image.width())
    height = int(image.height())
    if width != int(expected_width) or height != int(expected_height):
        raise RuntimeError(
            "Rendered image dimensions do not match the request: %sx%s"
            % (width, height)
        )
    if not _image_has_visible_content(image):
        raise RuntimeError("Rendered view image is blank: %s" % path)
    return {
        "path": os.path.abspath(path),
        "capture_id": capture_id, "capture_number": capture_number,
        "sha256": _file_sha256(path),
        "size_bytes": int(os.path.getsize(path)),
        "width_px": width,
        "height_px": height,
        "role": role,
        "revision": revision,
        # Capture confirms that a file was generated; it does not record that
        # an LLM or user has opened or inspected the image.
        "evidence_status": "generated",
        "revision_basis": (
            "source_step_sha256" if role == "source" else "target_state_hash"
        ),
        "direction": direction,
        "camera": dict(camera),
        "actual_camera_direction": camera.get("actual_camera_direction"),
        "actual_up_direction": camera.get("actual_up_direction"),
        "pose_error_deg": camera.get("pose_error_deg"),
        "projection": projection,
        "object_display_mode": object_display_mode,
        "requested_display_mode": requested_display_mode,
        "normalized_display_mode": normalized_display_mode,
        "scale": dict(scale),
    }


def _image_artifact(path, *, capture_id, capture_number, role=None, revision=None):
    if not os.path.isfile(path) or os.path.getsize(path) <= 0:
        raise RuntimeError("Image artifact is missing or empty: %s" % path)
    image = QtGui.QImage(path)
    if image.isNull() or not _image_has_visible_content(image):
        raise RuntimeError("Image artifact is unreadable or blank: %s" % path)
    result = {
        "path": os.path.abspath(path),
        "capture_id": capture_id, "capture_number": capture_number,
        "sha256": _file_sha256(path),
        "size_bytes": int(os.path.getsize(path)),
        "width_px": int(image.width()),
        "height_px": int(image.height()),
        "evidence_status": "generated",
    }
    if role is not None:
        result["role"] = role
    if revision is not None:
        result["revision"] = revision
    return result


def _section_preview_shape(shape, axis):
    """Return the lower half of a Shape for a deterministic cutaway preview."""
    bound = _geometry_bound_box(shape)
    lengths = {
        "x": float(bound.XLength),
        "y": float(bound.YLength),
        "z": float(bound.ZLength),
    }
    minimums = {
        "x": float(bound.XMin),
        "y": float(bound.YMin),
        "z": float(bound.ZMin),
    }
    characteristic = max(lengths.values())
    if characteristic <= 1e-9:
        raise ValueError("Cannot section a zero-size Shape")
    margin = max(characteristic * 0.02, 0.1)
    box_lengths = {
        name: length + 2.0 * margin for name, length in lengths.items()
    }
    box_lengths[axis] = lengths[axis] / 2.0 + margin
    origin = App.Vector(
        minimums["x"] - margin,
        minimums["y"] - margin,
        minimums["z"] - margin,
    )
    tool = Part.makeBox(
        box_lengths["x"], box_lengths["y"], box_lengths["z"], origin
    )
    section = shape.common(tool)
    if section.isNull():
        raise ValueError("Section preview produced a null Shape")
    return section, {
        "axis": axis,
        "coordinate_mm": minimums[axis] + lengths[axis] / 2.0,
        "kept_side": "min",
        "kind": "half_cutaway",
    }


def _compose_view_board(image_paths, board_path, rows, columns, width, height):
    """Compose a compact board with each direction's Source/Target pair adjacent."""

    header_height = VIEW_BOARD_HEADER_HEIGHT
    board = QtGui.QImage(
        width * len(columns),
        (height + header_height) * len(rows),
        QtGui.QImage.Format_ARGB32,
    )
    board.fill(QtGui.QColor("white"))
    painter = QtGui.QPainter(board)
    try:
        for row_index, row in enumerate(rows):
            for column_index, column in enumerate(columns):
                x_value = column_index * width
                y_value = row_index * (height + header_height)
                label = "%s  |  %s" % (row.capitalize(), column.capitalize())
                painter.setPen(QtGui.QColor("black"))
                painter.drawText(
                    x_value,
                    y_value,
                    width,
                    header_height,
                    int(QtCore.Qt.AlignCenter),
                    label,
                )
                image = QtGui.QImage(image_paths["%s_%s" % (column, row)])
                if image.isNull():
                    raise RuntimeError("Cannot load rendered view for board")
                painter.drawImage(x_value, y_value + header_height, image)
    finally:
        painter.end()
    if not board.save(board_path, "PNG"):
        raise RuntimeError("Cannot save reconstruction view board")


def _compose_initial_boards(image_paths, board_paths, width, height):
    layouts = initial_view_layout(width, height)
    for key in INITIAL_VIEW_GROUPS:
        layout = layouts[key]
        board = QtGui.QImage(layout["width_px"], layout["height_px"], QtGui.QImage.Format_ARGB32)
        if board.isNull():
            raise RuntimeError("Cannot allocate initial view board: " + key)
        board.fill(QtGui.QColor("white"))
        painter = QtGui.QPainter(board)
        try:
            for cell in layout["cells"]:
                direction = cell["direction"]
                painter.setPen(QtGui.QColor("black"))
                painter.drawText(*cell["label_rect_px"], int(QtCore.Qt.AlignCenter), direction)
                frame = QtGui.QImage(image_paths[direction])
                if frame.isNull() or frame.width() != width or frame.height() != height:
                    raise RuntimeError("Initial view frame is missing or has wrong dimensions: " + direction)
                x, y, _w, _h = cell["image_rect_px"]
                painter.drawImage(x, y, frame)
                del frame
        finally:
            painter.end()
        if not board.save(board_paths[key], "PNG"):
            raise RuntimeError("Cannot save initial view board: " + key)
        del board
    return layouts
