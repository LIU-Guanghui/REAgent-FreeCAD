"""Host geometry numeric and file helpers, without process state dependencies."""

import math


def finite_number(value, field):
    if isinstance(value, bool):
        raise ValueError("%s must be a number" % field)
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError("%s must be a number" % field)
    if not math.isfinite(number) or abs(number) > 1000000:
        raise ValueError("%s must be finite and between -1000000 and 1000000" % field)
    return number


def positive_number(value, field):
    if isinstance(value, bool):
        raise ValueError("%s must be a number" % field)
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError("%s must be a number" % field)
    if not math.isfinite(number) or number <= 0 or number > 1000000:
        raise ValueError("%s must be greater than 0 and at most 1000000" % field)
    return number

def positive_finite_number(value, field):
    if isinstance(value, bool):
        raise ValueError("%s must be a number" % field)
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError("%s must be a number" % field)
    if not math.isfinite(number) or number <= 0:
        raise ValueError("%s must be a positive finite number" % field)
    return number

def vector_dict(vector):
    return {
        "x": float(vector.x),
        "y": float(vector.y),
        "z": float(vector.z),
    }
