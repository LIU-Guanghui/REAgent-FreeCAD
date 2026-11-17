"""Prepare, read and crop user-chosen reference images."""

import hashlib
import math
from pathlib import Path

from REAgent.ui import QtCore, QtGui


_MAX_REFERENCE_IMAGES = 6
_MAX_FILE_BYTES = 64 * 1024 * 1024
_MAX_IMAGE_PIXELS = 32_000_000


def _read_reference(path):
    with path.open("rb") as stream:
        data = stream.read(_MAX_FILE_BYTES + 1)
    if len(data) > _MAX_FILE_BYTES:
        raise ValueError("Reference files must be at most 64 MiB")
    return data


def _image_reader(data):
    device = QtCore.QBuffer()
    device.setData(QtCore.QByteArray(data))
    device.open(QtCore.QIODevice.ReadOnly)
    reader = QtGui.QImageReader(device)
    reader.setDecideFormatFromContent(True)
    reader.setAutoTransform(True)
    if not reader.canRead():
        raise ValueError(reader.errorString())
    size = reader.size()
    if size.isValid() and size.width() * size.height() > _MAX_IMAGE_PIXELS:
        raise ValueError("Choose a reference image of at most 32 megapixels")
    return device, reader


def prepare_inputs(paths):
    if not 1 <= len(paths) <= _MAX_REFERENCE_IMAGES:
        raise ValueError(f"Select from 1 to {_MAX_REFERENCE_IMAGES} reference images")
    inputs = []
    for index, value in enumerate(paths, 1):
        path = Path(value)
        if not path.is_absolute():
            raise ValueError("Choose absolute image file paths")
        try:
            data = _read_reference(path)
            _image_reader(data)
            inputs.append({"index": index, "filename": path.name, "path": str(path),
                           "revision": hashlib.sha256(data).hexdigest()})
        except (OSError, RuntimeError, ValueError) as exc:
            raise ValueError(f"Cannot use {path.name}. {exc}") from exc
    return inputs


def _encode_png(image, long_edge):
    if max(image.width(), image.height()) > long_edge:
        image = image.scaled(long_edge, long_edge, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
    device = QtCore.QBuffer()
    device.open(QtCore.QIODevice.WriteOnly)
    if not image.save(device, "PNG"):
        raise ValueError("Cannot encode the selected reference view")
    return bytes(device.data()), image.width(), image.height()


def read_input(item):
    data = _read_reference(Path(item["path"]))
    if hashlib.sha256(data).hexdigest() != item["revision"]:
        raise ValueError("The reference file has changed. Start a new task to use this revision.")
    device, reader = _image_reader(data)
    image = reader.read()
    if image.isNull():
        raise ValueError(reader.errorString())
    return image


def render_input(item, *, region, long_edge):
    image = read_input(item)
    if region:
        x0, y0, x1, y1 = region
        left, top = math.floor(x0 * image.width()), math.floor(y0 * image.height())
        right, bottom = math.ceil(x1 * image.width()), math.ceil(y1 * image.height())
        image = image.copy(left, top, right - left, bottom - top)
    return _encode_png(image, long_edge)
