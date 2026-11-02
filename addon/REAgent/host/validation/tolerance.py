"""Tolerance contracts for geometry references and explicit diagnostics."""

import json
import math

_DEFAULT_LINEAR_TOLERANCE_MM = 0.01
_DEFAULT_ANGULAR_TOLERANCE_DEG = 0.1
_MAX_SURFACE_SAMPLES = 160
from REAgent.host.numeric import _positive_finite_number, _positive_number


def _resolve_tolerance_contract(params, source_summary):
    linear_value = params.get(
        "linear_tolerance_mm", _DEFAULT_LINEAR_TOLERANCE_MM
    )
    linear_tolerance = _positive_number(
        linear_value, "linear_tolerance_mm"
    )
    angular_tolerance = _positive_number(
        params.get(
            "angular_tolerance_deg", _DEFAULT_ANGULAR_TOLERANCE_DEG
        ),
        "angular_tolerance_deg",
    )
    if angular_tolerance > 180:
        raise ValueError("angular_tolerance_deg must not exceed 180")

    box = source_summary["bounding_box_mm"]
    characteristic_length = math.sqrt(
        box["x_length"] ** 2
        + box["y_length"] ** 2
        + box["z_length"] ** 2
    )
    source_volume = float(source_summary["volume_mm3"])
    source_area = float(source_summary["area_mm2"])
    numeric_linear = max(1e-7, characteristic_length * 1e-12)
    numeric_volume = max(1e-7, source_volume * 1e-12)
    numeric_area = max(1e-7, source_area * 1e-12)

    area_tolerance = max(
        numeric_area,
        4.0 * math.sqrt(max(source_area, 0.0)) * linear_tolerance
        + 4.0 * math.pi * linear_tolerance * linear_tolerance,
    )
    sampling_deflection = max(
        numeric_linear * 10.0, linear_tolerance / 2.0
    )
    return {
        "units": {"linear": "mm", "angular": "deg"},
        "linear_tolerance_mm": linear_tolerance,
        "angular_tolerance_deg": angular_tolerance,
        "area_tolerance_mm2": area_tolerance,
        "numeric_linear_epsilon_mm": numeric_linear,
        "numeric_volume_epsilon_mm3": numeric_volume,
        "numeric_area_epsilon_mm2": numeric_area,
        "surface_sampling_deflection_mm": sampling_deflection,
        "max_surface_samples_per_direction": _MAX_SURFACE_SAMPLES,
        "origins": {
            "linear_tolerance": (
                "user"
                if "linear_tolerance_mm" in params
                else "default"
            ),
            "angular_tolerance": (
                "user"
                if "angular_tolerance_deg" in params
                else "default"
            ),
            "area_tolerance": "derived_characteristic_area",
            "numeric_epsilon": "derived_scale_aware_floor",
        },
    }


def _parse_tolerance_contract(raw_value):
    try:
        contract = json.loads(raw_value)
    except (TypeError, ValueError):
        raise ValueError("ToleranceContractJSON is invalid")
    if not isinstance(contract, dict):
        raise ValueError("ToleranceContractJSON must be an object")
    required = (
        "linear_tolerance_mm",
        "angular_tolerance_deg",
        "area_tolerance_mm2",
        "numeric_linear_epsilon_mm",
        "numeric_volume_epsilon_mm3",
        "surface_sampling_deflection_mm",
        "max_surface_samples_per_direction",
    )
    for name in required:
        if name not in contract:
            raise ValueError(
                "ToleranceContractJSON is missing property: %s" % name
            )
    for name in required[:-1]:
        _positive_finite_number(contract[name], name)
    maximum = contract["max_surface_samples_per_direction"]
    if (
        isinstance(maximum, bool)
        or not isinstance(maximum, int)
        or maximum < 2
        or maximum > 5000
    ):
        raise ValueError(
            "max_surface_samples_per_direction must be an integer from 2 to 5000"
        )
    return {
        name: contract[name]
        for name in (*required, "units", "numeric_area_epsilon_mm2", "origins")
        if name in contract
    }


def _ir_tolerance_contract(reconstruction_ir):
    raw_value = getattr(reconstruction_ir, "ToleranceContractJSON", None)
    if not raw_value:
        raise ValueError("Workspace is missing ToleranceContractJSON")
    return _parse_tolerance_contract(raw_value)


def _source_tolerance_contract(source):
    raw_value = getattr(source, "ToleranceContractJSON", None)
    if not raw_value:
        raise ValueError("Source is missing ToleranceContractJSON")
    return _parse_tolerance_contract(raw_value)
