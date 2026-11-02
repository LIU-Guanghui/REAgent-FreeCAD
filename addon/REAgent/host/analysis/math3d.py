"""Low-level core numeric, vector, and topology-name helpers."""

import math


def _normalized_vector(vector):
    length = _vector_length(vector)
    if length == 0:
        return {"x": 0.0, "y": 0.0, "z": 0.0}
    return {
        "x": vector["x"] / length,
        "y": vector["y"] / length,
        "z": vector["z"] / length,
    }
def _vector_difference(left, right):
    return {
        "x": left["x"] - right["x"],
        "y": left["y"] - right["y"],
        "z": left["z"] - right["z"],
    }


def _vector_midpoint(left, right):
    return {
        "x": (left["x"] + right["x"]) / 2.0,
        "y": (left["y"] + right["y"]) / 2.0,
        "z": (left["z"] + right["z"]) / 2.0,
    }
def _dot(left, right):
    return (
        left["x"] * right["x"]
        + left["y"] * right["y"]
        + left["z"] * right["z"]
    )


def _cross_vector(left, right):
    return {
        "x": left["y"] * right["z"] - left["z"] * right["y"],
        "y": left["z"] * right["x"] - left["x"] * right["z"],
        "z": left["x"] * right["y"] - left["y"] * right["x"],
    }


def _point_distance(left, right):
    return math.sqrt(
        (left["x"] - right["x"]) ** 2
        + (left["y"] - right["y"]) ** 2
        + (left["z"] - right["z"]) ** 2
    )


def _vectors_parallel(left, right, angular_tolerance=1e-7):
    if left is None or right is None:
        return False
    left_length = _vector_length(left)
    right_length = _vector_length(right)
    if left_length == 0 or right_length == 0:
        return False
    return (
        _cross_length(left, right) / (left_length * right_length)
        <= angular_tolerance
    )


def _vector_length(vector):
    return math.sqrt(
        vector["x"] ** 2 + vector["y"] ** 2 + vector["z"] ** 2
    )


def _cross_length(left, right):
    x_value = left["y"] * right["z"] - left["z"] * right["y"]
    y_value = left["z"] * right["x"] - left["x"] * right["z"]
    z_value = left["x"] * right["y"] - left["y"] * right["x"]
    return math.sqrt(x_value**2 + y_value**2 + z_value**2)


def _plane_distance(left_position, right_position, axis):
    difference = {
        "x": right_position["x"] - left_position["x"],
        "y": right_position["y"] - left_position["y"],
        "z": right_position["z"] - left_position["z"],
    }
    axis_length = _vector_length(axis)
    if axis_length == 0:
        return float("inf")
    return abs(
        difference["x"] * axis["x"]
        + difference["y"] * axis["y"]
        + difference["z"] * axis["z"]
    ) / axis_length


def _parallel_line_distance(left_center, right_center, axis):
    difference = {
        "x": right_center["x"] - left_center["x"],
        "y": right_center["y"] - left_center["y"],
        "z": right_center["z"] - left_center["z"],
    }
    axis_length = _vector_length(axis)
    if axis_length == 0:
        return float("inf")
    return _cross_length(difference, axis) / axis_length
