"""FreeCAD camera poses, stable readback and local restoration."""

import math
import time

import FreeCAD as App
from PySide import QtCore, QtGui

from ..analysis.math3d import _normalized_vector
from ..protocol.contract import VIEW_CAMERA_SPECS, VIEW_PROJECTIONS


def _canonical_view_method(view, direction):
    try:
        specification = VIEW_CAMERA_SPECS[direction]
    except KeyError:
        raise ValueError("Unsupported canonical view direction: %s" % direction)
    method = getattr(view, specification["method"], None)
    if not callable(method):
        raise RuntimeError(
            "FreeCAD view does not expose %s" % specification["method"]
        )
    return method, specification


def _rotation_from_camera_pose(camera_direction, up_direction):
    """Map camera-local right/up/back axes to one deterministic world pose."""

    forward = _normalized_vector(camera_direction)
    up = _normalized_vector(up_direction)
    right = _normalized_vector(
        {
            "x": forward["y"] * up["z"] - forward["z"] * up["y"],
            "y": forward["z"] * up["x"] - forward["x"] * up["z"],
            "z": forward["x"] * up["y"] - forward["y"] * up["x"],
        }
    )
    if max(abs(value) for value in right.values()) <= 1e-12:
        raise ValueError("Camera direction and up direction must not be parallel")
    # Recompute up so numerical drift cannot introduce a skewed camera basis.
    up = _normalized_vector(
        {
            "x": right["y"] * forward["z"] - right["z"] * forward["y"],
            "y": right["z"] * forward["x"] - right["x"] * forward["z"],
            "z": right["x"] * forward["y"] - right["y"] * forward["x"],
        }
    )
    backward = {axis: -forward[axis] for axis in ("x", "y", "z")}
    matrix = (
        (right["x"], up["x"], backward["x"]),
        (right["y"], up["y"], backward["y"]),
        (right["z"], up["z"], backward["z"]),
    )
    m00, m01, m02 = matrix[0]
    m10, m11, m12 = matrix[1]
    m20, m21, m22 = matrix[2]
    trace = m00 + m11 + m22
    if trace > 0.0:
        scale = math.sqrt(trace + 1.0) * 2.0
        qw = 0.25 * scale
        qx = (m21 - m12) / scale
        qy = (m02 - m20) / scale
        qz = (m10 - m01) / scale
    elif m00 > m11 and m00 > m22:
        scale = math.sqrt(1.0 + m00 - m11 - m22) * 2.0
        qw = (m21 - m12) / scale
        qx = 0.25 * scale
        qy = (m01 + m10) / scale
        qz = (m02 + m20) / scale
    elif m11 > m22:
        scale = math.sqrt(1.0 + m11 - m00 - m22) * 2.0
        qw = (m02 - m20) / scale
        qx = (m01 + m10) / scale
        qy = 0.25 * scale
        qz = (m12 + m21) / scale
    else:
        scale = math.sqrt(1.0 + m22 - m00 - m11) * 2.0
        qw = (m10 - m01) / scale
        qx = (m02 + m20) / scale
        qy = (m12 + m21) / scale
        qz = 0.25 * scale
    length = math.sqrt(qx * qx + qy * qy + qz * qz + qw * qw)
    return App.Rotation(qx / length, qy / length, qz / length, qw / length)


def _apply_canonical_view(view, direction, scope):
    scope.assert_render_safe()
    method, specification = _canonical_view_method(view, direction)
    if specification["method"] == "setCameraOrientation":
        method(
            _rotation_from_camera_pose(
                specification["camera_direction"],
                specification["up_direction"],
            )
        )
    else:
        method()
    _refresh_camera(scope)


def _set_orthographic_projection(view):
    setter = getattr(view, "setCameraType", None)
    getter = getattr(view, "getCameraType", None)
    if not callable(setter) or not callable(getter):
        raise RuntimeError(
            "FreeCAD view must expose setCameraType/getCameraType for verified views"
        )
    setter("Orthographic")
    actual = str(getter())
    if actual.lower() != "orthographic":
        raise RuntimeError(
            "FreeCAD view did not enter Orthographic projection: %s" % actual
        )
    return actual


def _camera_height_field(view):
    getter = getattr(view, "getCameraNode", None)
    if not callable(getter):
        return None
    try:
        node = getter()
    except Exception:
        return None
    field = getattr(node, "height", None)
    if field is None:
        return None
    return field


def _camera_height(view):
    field = _camera_height_field(view)
    if field is None:
        return None
    try:
        value = field.getValue() if callable(getattr(field, "getValue", None)) else field
        value = float(value)
    except (TypeError, ValueError, RuntimeError):
        return None
    return value if math.isfinite(value) and value > 0 else None


class _BoundedViewErrors(list):
    """Bound presentation-recovery details without losing the total count."""

    limit = 8

    def __init__(self):
        super().__init__()
        self.total_count = 0

    def add(self, message):
        self.total_count += 1
        if len(self) < self.limit:
            self.append(str(message)[:300])


def _set_camera_height(view, value):
    field = _camera_height_field(view)
    if field is None:
        raise RuntimeError(
            "FreeCAD view does not expose orthographic camera height"
        )
    try:
        setter = getattr(field, "setValue", None)
        if callable(setter):
            setter(float(value))
        else:
            setattr(field, "value", float(value))
    except Exception as exc:
        raise RuntimeError("Cannot set orthographic camera height: %s" % exc)
    actual = _camera_height(view)
    if actual is None or abs(actual - float(value)) > max(1e-7, abs(value) * 1e-7):
        raise RuntimeError("FreeCAD did not accept the requested camera height")
    return actual


def _rotated_vector(orientation, vector):
    method = getattr(orientation, "multVec", None)
    if not callable(method):
        return None
    try:
        result = method(App.Vector(vector["x"], vector["y"], vector["z"]))
        values = {
            "x": float(result.x),
            "y": float(result.y),
            "z": float(result.z),
        }
    except Exception:
        return None
    length = math.sqrt(sum(component * component for component in values.values()))
    if not math.isfinite(length) or length <= 1e-12:
        return None
    return {axis: values[axis] / length for axis in values}


def _direction_error_deg(actual, expected):
    if actual is None or expected is None:
        return None
    dot = sum(actual[axis] * expected[axis] for axis in ("x", "y", "z"))
    dot = max(-1.0, min(1.0, dot))
    return math.degrees(math.acos(dot))


def _camera_metadata(view, direction, camera_height, width, height, *,
                     state=None, center=None, expected=None):
    state = _camera_numeric_state(view) if state is None else state
    if expected is not None:
        differences = _camera_state_differences(view, expected, actual=state)
        if differences:
            raise RuntimeError("Capture camera changed: " + "; ".join(differences))
    if center is not None:
        _verify_camera_focus(view, center, state=state)
    method, specification = _canonical_view_method(view, direction)
    del method
    camera_type = state["camera_type"]
    if camera_type.lower() != "orthographic":
        raise RuntimeError("Canonical view is not Orthographic: %s" % camera_type)
    actual_height = state["fields"].get("height")
    if actual_height is None or not math.isclose(actual_height, camera_height, rel_tol=1e-7, abs_tol=1e-7):
        raise RuntimeError("Canonical camera scale differs from the requested shared height")
    orientation = App.Rotation(*state["quaternion"])
    actual_direction = _rotated_vector(orientation, {"x": 0.0, "y": 0.0, "z": -1.0})
    actual_up = _rotated_vector(orientation, {"x": 0.0, "y": 1.0, "z": 0.0})
    if actual_direction is None or actual_up is None:
        raise RuntimeError("Canonical camera pose could not be read")
    direction_error = _direction_error_deg(
        actual_direction, specification["camera_direction"]
    )
    up_error = _direction_error_deg(actual_up, specification["up_direction"])
    errors = [error for error in (direction_error, up_error) if error is not None]
    pose_error = max(errors) if errors else 0.0
    if pose_error > 0.01:
        raise RuntimeError(
            "Canonical %s camera pose error is %.6f degrees (actual_direction=%s, "
            "expected_direction=%s, actual_up=%s, expected_up=%s)"
            % (
                direction,
                pose_error,
                actual_direction,
                specification["camera_direction"],
                actual_up,
                specification["up_direction"],
            )
        )
    return {
        "method": specification["method"],
        "camera_type": camera_type,
        "camera_direction": specification["camera_direction"] or actual_direction,
        "up_direction": specification["up_direction"] or actual_up,
        "actual_camera_direction": actual_direction,
        "actual_up_direction": actual_up,
        "orientation": str(orientation),
        "pose_error_deg": float(pose_error),
        "camera_height_mm": actual_height,
        "millimeters_per_pixel": actual_height / float(height),
        "width_px": int(width),
        "height_px": int(height),
        **_camera_framing(view, state=state),
    }


def _camera_framing(view, *, state=None):
    state = _camera_numeric_state(view) if state is None else state
    forward = _rotated_vector(App.Rotation(*state["quaternion"]), dict(x=0., y=0., z=-1.))
    distance = state["fields"]["focalDistance"]
    if forward is None or distance <= 0:
        raise RuntimeError("Camera focus could not be read")
    position = dict(zip(("x", "y", "z"), state["position"]))
    focus = {axis: position[axis] + forward[axis] * distance for axis in position}
    if not all(math.isfinite(value) for value in focus.values()):
        raise RuntimeError("Camera focus contains non-finite values")
    return {"position_mm": position, "focal_distance_mm": distance, "focus_mm": focus}


def _set_camera_focus(view, center, *, state=None):
    state = _camera_numeric_state(view) if state is None else state
    framing = _camera_framing(view, state=state)
    forward = _rotated_vector(App.Rotation(*state["quaternion"]), dict(x=0., y=0., z=-1.))
    if forward is None or any(not math.isfinite(center[axis]) for axis in ("x", "y", "z")):
        raise ValueError("Camera center must be a finite world point")
    position = tuple(center[axis] - forward[axis] * framing["focal_distance_mm"]
                     for axis in ("x", "y", "z"))
    if not all(math.isfinite(value) for value in position):
        raise ValueError("Camera position must be finite")
    view.getCameraNode().position.setValue(*position)


def _verify_camera_focus(view, center, *, state=None):
    if not all(math.isfinite(center[axis]) for axis in ("x", "y", "z")):
        raise ValueError("Camera center must be a finite world point")
    actual = _camera_framing(view, state=state)["focus_mm"]
    if any(abs(actual[axis] - center[axis]) > _CAMERA_FOCUS_TOLERANCE_MM
           for axis in ("x", "y", "z")):
        raise RuntimeError("Camera focus did not read back at the shared center")


def _record_camera_failure(error, direction, phase, center, state):
    """Keep one bounded failure-time snapshot, independent of later restoration."""
    detail = dict(getattr(error, "failure_detail", {}) or {})
    if "camera" in detail:
        return
    camera = dict.fromkeys(("expected_focus_mm", "actual_focus_mm", "position_mm",
                            "focal_distance_mm", "focus_error_mm"))
    camera.update(direction=direction, phase=phase, tolerance_mm=_CAMERA_FOCUS_TOLERANCE_MM)
    try:
        if center is not None:
            camera["expected_focus_mm"] = {
                axis: center[axis] if math.isfinite(center[axis]) else None for axis in ("x", "y", "z")}
        if state is not None:
            camera["position_mm"] = dict(zip(("x", "y", "z"), state["position"]))
            camera["focal_distance_mm"] = state["fields"]["focalDistance"]
            focus = _camera_framing(None, state=state)["focus_mm"]
            camera["actual_focus_mm"] = focus
            if center is not None:
                camera["focus_error_mm"] = {
                    axis: abs(focus[axis] - center[axis])
                    if math.isfinite(focus[axis] - center[axis]) else None for axis in focus}
    except Exception:
        pass  # Diagnostics must not replace the original camera/readback failure.
    error.failure_detail = {**detail, "camera": camera}


def _refresh_camera(scope):
    scope.assert_render_safe()
    QtGui.QApplication.processEvents(QtCore.QEventLoop.ExcludeUserInputEvents | QtCore.QEventLoop.ExcludeSocketNotifiers)
    scope.assert_render_safe()


def _wait_for_camera_stable(view, direction, scope, timeout_seconds=2.0, *,
                            phase="settle", center=None):
    """Return one complete stable snapshot without rereading fields per check."""

    _method, specification = _canonical_view_method(view, direction)
    deadline = time.monotonic() + float(timeout_seconds)
    previous = None
    stable_samples = 0
    state = None
    try:
        for _sample in range(101):
            if time.monotonic() >= deadline:
                break
            state = None
            _refresh_camera(scope)
            state = _camera_numeric_state(view)
            orientation = App.Rotation(*state["quaternion"])
            actual_direction = _rotated_vector(orientation, dict(x=0., y=0., z=-1.))
            actual_up = _rotated_vector(orientation, dict(x=0., y=1., z=0.))
            if actual_direction is None or actual_up is None:
                raise RuntimeError("Canonical camera pose could not be read")
            stable = previous is not None and not _camera_state_differences(view, previous, actual=state)
            stable_samples = stable_samples + 1 if stable else 0
            previous = state
            errors = (_direction_error_deg(actual_direction, specification["camera_direction"]),
                      _direction_error_deg(actual_up, specification["up_direction"]))
            if stable_samples >= 2 and max(errors) <= 0.01:
                return state
            time.sleep(0.02)
        raise RuntimeError("Canonical %s camera did not settle within %.1f seconds" % (direction, timeout_seconds))
    except Exception as exc:
        _record_camera_failure(exc, direction, phase, center, state)
        raise


def _camera_numeric_state(view):
    node = view.getCameraNode()
    camera_type = view.getCameraType()
    projection_field = {"Orthographic": "height", "Perspective": "heightAngle"}.get(camera_type)
    if projection_field is None:
        raise RuntimeError("Unsupported camera type: %s" % camera_type)
    fields = {
        name: float(getattr(node, name).getValue())
        for name in ("focalDistance", "nearDistance", "farDistance", "aspectRatio", projection_field)
    }
    position = tuple(float(v) for v in node.position.getValue().getValue())
    quaternion = tuple(float(v) for v in node.orientation.getValue().getValue())
    if not all(math.isfinite(v) for v in (*position, *quaternion, *fields.values())):
        raise RuntimeError("Camera state contains non-finite values")
    if math.hypot(*quaternion) == 0:
        raise RuntimeError("Camera state has a zero quaternion")
    if fields["focalDistance"] <= 0 or fields[projection_field] <= 0:
        raise RuntimeError("Camera focal distance and projection size must be positive")
    return {"camera_type": camera_type, "position": position,
            "quaternion": quaternion, "fields": fields,
            "viewport_mapping": int(node.viewportMapping.getValue())}


def _camera_state_differences(view, expected, *, actual=None):
    """Compare physical camera state, including equivalent q/-q rotations."""
    actual = _camera_numeric_state(view) if actual is None else actual
    differences = []
    if actual["camera_type"] != expected["camera_type"]:
        return ["camera_type: expected %s, actual %s" %
                (expected["camera_type"], actual["camera_type"])]
    before, after = expected["quaternion"], actual["quaternion"]
    q = tuple(v / math.sqrt(sum(x*x for x in before)) for v in before)
    r = tuple(v / math.sqrt(sum(x*x for x in after)) for v in after)
    chord = min(math.sqrt(sum((x-y)**2 for x,y in zip(q,r))),
                math.sqrt(sum((x+y)**2 for x,y in zip(q,r))))
    angle = 4.0 * math.asin(min(1.0, chord / 2.0))
    if angle > _CAMERA_ANGLE_TOLERANCE_RAD:
        differences.append("orientation: angle_rad=%.9g expected=%s actual=%s" %
                           (angle, tuple(round(v, 9) for v in before),
                            tuple(round(v, 9) for v in after)))
    for name, target in expected["fields"].items():
        # Coin recomputes clipping planes from the visible scene during redraw.
        # Restore the snapshot once, but do not fight this derived state while
        # draining events (the scene can change across preview cleanup/undo).
        if name in ("nearDistance", "farDistance"):
            continue
        value = actual["fields"][name]
        if not math.isclose(value, target, rel_tol=_CAMERA_REL_TOLERANCE,
                            abs_tol=_CAMERA_ABS_TOLERANCE):
            differences.append("%s: expected %.9g, actual %.9g" % (name, target, value))
    near = actual["fields"]["nearDistance"]
    far = actual["fields"]["farDistance"]
    if far <= near or (actual["camera_type"] == "Perspective" and near <= 0):
        differences.append("clipping: invalid range near=%.9g far=%.9g" % (near, far))
    for axis, target, value in zip("xyz", expected["position"], actual["position"]):
        if not math.isclose(value, target, rel_tol=_CAMERA_REL_TOLERANCE,
                            abs_tol=_CAMERA_ABS_TOLERANCE):
            differences.append("position.%s: expected %.9g, actual %.9g" % (axis, target, value))
    if actual["viewport_mapping"] != expected["viewport_mapping"]:
        differences.append("viewport_mapping: restored value differs")
    return differences


def _restore_camera_state(view, state, *, errors=None):
    """Restore Coin fields without starting navigation animation; verify target."""
    failures = []
    if state is not None:
        try:
            # Stop pending navigation before replacing its camera. Direct Coin
            # field writes do not change the user's animation preference.
            view.stopAnimating()
            view.setCamera(state["camera"])
            expected = state["numeric"]
            if view.getCameraType() != expected["camera_type"]:
                raise RuntimeError("Camera type differs after setCamera")
            node = view.getCameraNode()
            node.orientation.setValue(_coin.SbRotation(*expected["quaternion"]))
            node.position.setValue(*expected["position"])
            for name, value in expected["fields"].items():
                getattr(node, name).setValue(value)
            node.viewportMapping.setValue(expected["viewport_mapping"])
            deadline = time.monotonic() + 2.0
            stable_samples = 0
            differences = []
            while True:
                QtGui.QApplication.processEvents(
                    QtCore.QEventLoop.ExcludeUserInputEvents | QtCore.QEventLoop.ExcludeSocketNotifiers)
                differences = _camera_state_differences(view, expected)
                stable_samples = 0 if differences else stable_samples + 1
                if stable_samples >= 3:
                    break
                if time.monotonic() >= deadline:
                    failures.extend(differences or ["camera: target did not remain stable"])
                    failures.append("camera: restoration target timeout after 2s")
                    break
                time.sleep(0.02)
        except Exception as exc:
            failures.append("camera_restore: " + str(exc)[:200])
    if errors is not None:
        if hasattr(errors, "add"):
            for failure in failures:
                errors.add(failure)
        else:
            errors.extend(failures)
    elif failures:
        raise RuntimeError("View state restoration failed: " + "; ".join(failures))
    return failures


def _capture_complete_camera_state(view):
    """Capture immutable numeric fields as well as the native camera string."""
    try:
        if _coin is None:
            raise RuntimeError("Coin camera bindings are unavailable")
        camera = view.getCamera()
        if not camera:
            raise RuntimeError("FreeCAD returned an empty camera")
        return {"camera": camera, "numeric": _camera_numeric_state(view)}
    except Exception as exc:
        raise RuntimeError("Cannot capture the complete view camera state: %s" % exc) from exc


def _projection(value):
    value = "orthographic" if value is None else value
    if value not in VIEW_PROJECTIONS:
        raise ValueError(
            "projection must be orthographic; other projections are not "
            "implemented for verified views"
        )
    return value


_CAMERA_ANGLE_TOLERANCE_RAD = 1e-6

_CAMERA_REL_TOLERANCE = 2e-6

_CAMERA_ABS_TOLERANCE = 1e-6

_CAMERA_FOCUS_TOLERANCE_MM = 0.01

try:
    # FreeCAD returns Coin camera nodes from getCameraNode().  Importing the
    # wrapper here is required before reading SoOrthographicCamera fields.
    from pivy import coin as _coin  # noqa: F401
except Exception:
    _coin = None
