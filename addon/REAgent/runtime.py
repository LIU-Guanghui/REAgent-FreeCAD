"""FreeCAD runtime dependencies, process state, constants, and shared helpers."""

import hashlib
import math
import os
import re

import FreeCAD as App


def _file_sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as source_file:
        while True:
            chunk = source_file.read(1024 * 1024)
            if not chunk:
                return digest.hexdigest()
            digest.update(chunk)


class _ProcessState:
    def __init__(self):
        self.INSTANCE_ROLE = None
        self.ACTIVE_OPERATION = None
        self.AUDIT_BLOCKS = {}
        # Session/document binding is process-local authorization evidence. It
        # is deliberately not persisted in FCStd or used as a model revision.
        # Its separate source_revision field pins the observed STEP basis,
        # not document ownership or geometric equivalence.
        self.SESSION_BINDINGS = {}


_state = _ProcessState()


def _operation_stage(stage, *, mutating=False):
    if _state.ACTIVE_OPERATION is not None:
        _state.ACTIVE_OPERATION["stage"] = stage
        if mutating:
            _state.ACTIVE_OPERATION["write_started"] = True


def _stamp_failure(exc):
    active = _state.ACTIVE_OPERATION or {}
    if not hasattr(exc, "operation_stage"):
        exc.operation_stage = active.get("stage", "bridge_preflight")
    if not hasattr(exc, "write_started"):
        exc.write_started = active.get("write_started", False)
    if not hasattr(exc, "request_side_effect"):
        exc.request_side_effect = active.get("side_effect")
    return exc


HOST = "127.0.0.1"
ADDON_PATH = os.path.dirname(os.path.abspath(__file__))
MAX_REQUEST_BYTES = 64 * 1024
POLL_INTERVAL_MS = 25
_NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,99}$")
_TOLERANCE_SCHEMA_VERSION = 1
_VALIDATION_SCHEMA_VERSION = 1
_DEFAULT_LINEAR_TOLERANCE_MM = 0.01
_DEFAULT_ANGULAR_TOLERANCE_DEG = 0.1
_MAX_SURFACE_SAMPLES = 160


def _finite_number(value, field):
    if isinstance(value, bool):
        raise ValueError("%s must be a number" % field)
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError("%s must be a number" % field)
    if not math.isfinite(number) or abs(number) > 1000000:
        raise ValueError("%s must be finite and between -1000000 and 1000000" % field)
    return number


def _valid_name(value, field):
    if not isinstance(value, str) or not _NAME_PATTERN.match(value):
        raise ValueError(
            "%s must start with a letter and contain only letters, numbers, "
            "or underscores (maximum 100 characters)" % field
        )
    return value


def _positive_number(value, field):
    if isinstance(value, bool):
        raise ValueError("%s must be a number" % field)
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError("%s must be a number" % field)
    if not math.isfinite(number) or number <= 0 or number > 1000000:
        raise ValueError("%s must be greater than 0 and at most 1000000" % field)
    return number


def _positive_finite_number(value, field):
    if isinstance(value, bool):
        raise ValueError("%s must be a number" % field)
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError("%s must be a number" % field)
    if not math.isfinite(number) or number <= 0:
        raise ValueError("%s must be a positive finite number" % field)
    return number


def _vector_dict(vector):
    return {
        "x": float(vector.x),
        "y": float(vector.y),
        "z": float(vector.z),
    }


def _snake_case(name):
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def _add_read_only_string(obj, name, value, group):
    obj.addProperty("App::PropertyString", name, group)
    setattr(obj, name, value)
    obj.setEditorMode(name, 1)


def _document(name):
    if not isinstance(name, str) or not name:
        raise ValueError("document must be a non-empty string")
    document = App.listDocuments().get(name)
    if document is None:
        raise KeyError("Document not found: %s" % name)
    return document
