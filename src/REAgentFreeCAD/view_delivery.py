"""Bounded image content from the current, restored Bridge view artifacts."""

import base64
import hashlib
import stat
import struct
import zlib
from pathlib import Path

from mcp.types import ImageContent
from .contract import (
    IMAGE_CONTENT_LIMITS, INITIAL_VIEW_GROUPS, VIEW_BOARD_HEADER_HEIGHT, VIEW_DIRECTIONS,
    artifact_filename, initial_view_layout,
)


class _ImageLimitError(ValueError):
    def __init__(self, limit, actual):
        self.detail = {"limit": limit, "actual": actual,
                       "maximum": IMAGE_CONTENT_LIMITS[limit]}
        super().__init__("View image delivery exceeds " + limit)


def _image_plan(value, role, revisions=None):
    """Match the entire image manifest to its registered role/direction keys."""
    images, views = value["images"], value["views"]
    if not isinstance(images, list) or not images or not isinstance(views, dict):
        raise ValueError("View image manifest is empty or invalid")
    if revisions is None:
        keys = [(role, image["direction"]) for image in images]
    else:
        layout = value["layout"]
        directions = layout["rows"]
        if (layout["columns"] != ["source", "rebuild"]
                or not isinstance(directions, list) or not directions
                or any(not isinstance(item, str) or not item for item in directions)
                or len(directions) != len(set(directions))):
            raise ValueError("Paired view layout is invalid")
        keys = [(item_role, direction) for direction in directions
                for item_role in ("source", "target")]
    by_key = {(image["role"], image["direction"]): image for image in images}
    if len(by_key) != len(images) or set(by_key) != set(keys) or len(keys) != len(set(keys)):
        raise ValueError("View image manifest must contain the complete unique view set")
    plan = []
    for item_role, direction in keys:
        key = direction if revisions is None else (
            ("source" if item_role == "source" else "rebuild") + "_" + direction)
        revision = value["revision"] if revisions is None else revisions[item_role]
        plan.append((by_key[item_role, direction], views[key], item_role, revision, key, direction))
    if set(views) != {item[4] for item in plan}:
        raise ValueError("View paths differ from the complete image manifest")
    return plan


def _artifact_path(item, root, artifacts, capture_id, capture_number):
    artifact, registered, role, revision, _key, direction = item
    path = Path(artifact["path"])
    if role == "paired":
        kind = "paired"
    elif direction in INITIAL_VIEW_GROUPS:
        kind = "initial_" + direction
    else:
        kind = ("source" if role == "source" else "rebuild") + "_" + direction
    expected = Path(artifacts["output_dir"]) / artifact_filename(artifacts["basename"], kind, capture_number)
    if path != expected:
        raise ValueError("Image path differs from the bound artifact identity and capture number")
    if artifact["capture_id"] != capture_id or artifact["capture_number"] != capture_number:
        raise ValueError("Image manifest differs from the current capture identity")
    if not path.is_absolute() or path.is_symlink() or path.resolve(strict=True).parent != root:
        raise ValueError("Image must be a generated file in this view output directory")
    if path != Path(registered) or artifact["evidence_status"] != "generated":
        raise ValueError("Image does not belong to the current view manifest")
    if artifact["role"] != role or artifact["revision"] != revision:
        raise ValueError("Image role or revision differs from the current observation")
    return path


def _prepare_images(plan, root, evidence_id, artifacts, capture_id, capture_number):
    """Prepare a whole representation, with no partial content on failure."""
    if len(plan) > IMAGE_CONTENT_LIMITS["max_images"]:
        raise _ImageLimitError("max_images", len(plan))
    content, rows, total = [], [], 0
    for index, (artifact, registered, role, revision, key, direction) in enumerate(plan):
        path = _artifact_path((artifact, registered, role, revision, key, direction),
                              root, artifacts, capture_id, capture_number)
        size, width, height = artifact["size_bytes"], artifact["width_px"], artifact["height_px"]
        if any(type(number) is not int or number <= 0 for number in (size, width, height)):
            raise ValueError("View image size and dimensions must be positive integers")
        if not path.is_file() or path.stat().st_size != size:
            raise ValueError("View image file size differs from capture")
        for limit, actual in (("max_image_bytes", size), ("max_total_bytes", total + size),
                              ("max_image_pixels", width * height)):
            if actual > IMAGE_CONTENT_LIMITS[limit]:
                raise _ImageLimitError(limit, actual)
        with path.open("rb") as stream:
            data = stream.read(size + 1)
        if len(data) != size or hashlib.sha256(data).hexdigest() != artifact["sha256"]:
            raise ValueError("View image changed after capture")
        if (len(data) < 45 or data[:16] != b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
                or zlib.crc32(data[12:29]) != struct.unpack(">I", data[29:33])[0]
                or data[-12:] != b"\x00\x00\x00\x00IEND\xaeB`\x82"):
            raise ValueError("Generated image is not a complete PNG")
        if struct.unpack(">II", data[16:24]) != (width, height):
            raise ValueError("View image dimensions differ from capture")
        content.append(ImageContent(type="image", data=base64.b64encode(data).decode("ascii"),
                                    mimeType="image/png"))
        total += size
        rows.append({"image_id": evidence_id + ":image:" + str(index),
                     "content_index": index + 1, "artifact_key": key,
                     "role": role, "direction": direction, "size_bytes": size,
                     "width_px": width, "height_px": height})
    return {"status": "prepared", "image_count": len(rows), "total_bytes": total,
            "images": rows, "scope": "observation_capture", "inspection": "not_attested"}, content


def _initial_board_plan(value, role, root, artifacts):
    if role != "source" or "paired_revisions" in value or "board_manifest" in value:
        raise ValueError("Initial view boards require a Source-only capture")
    initial = value["initial_views"]
    capture_id = value["capture_id"]
    if (initial["directions"] != list(VIEW_DIRECTIONS)
            or initial["direction_count"] != len(VIEW_DIRECTIONS)
            or initial["complete"] is not True
            or not isinstance(capture_id, str) or not capture_id
            or initial["capture_id"] != capture_id):
        raise ValueError("Initial view set must cover all fourteen directions in one capture")
    expected = initial_view_layout(value["width"], value["height"])
    if set(value["boards"]) != set(INITIAL_VIEW_GROUPS):
        raise ValueError("Initial view board set is incomplete")
    images = _image_plan(value, role)
    if [row[5] for row in images] != list(VIEW_DIRECTIONS):
        raise ValueError("Initial single-image manifest is incomplete or out of order")
    scale = value["scale"]
    if (scale.get("shared") is not True or not isinstance(scale.get("focus_mm"), dict)
            or value["projection"] != "orthographic" or value["normalized_display_mode"] != "flat_lines"):
        raise ValueError("Initial views have no shared canonical framing")
    for artifact, registered, item_role, revision, _key, direction in images:
        path = Path(registered)
        if (not path.is_absolute() or path.is_symlink() or path.resolve(strict=True).parent != root
                or path != Path(artifact["path"])
                or path.name != artifact_filename(artifacts["basename"], "source_" + direction, value["capture_number"])
                or artifact["capture_id"] != capture_id or artifact["capture_number"] != value["capture_number"]
                or artifact["role"] != item_role or artifact["revision"] != revision
                or artifact["evidence_status"] != "generated"
                or artifact["width_px"] != value["width"] or artifact["height_px"] != value["height"]
                or artifact["scale"] != scale or artifact["projection"] != "orthographic"):
            raise ValueError("Initial single-image registration differs from the capture")
        camera = artifact["camera"]
        if camera != value["camera"][direction] or camera["camera_height_mm"] != scale["camera_height_mm"]:
            raise ValueError("Initial camera metadata differs from the shared capture")
    plan = []
    for key in INITIAL_VIEW_GROUPS:
        board = value["boards"][key]
        artifact = board["manifest"]
        if (board["layout"] != expected[key] or artifact["width_px"] != expected[key]["width_px"]
                or artifact["height_px"] != expected[key]["height_px"]
                or artifact["capture_id"] != capture_id or artifact["capture_number"] != value["capture_number"]
                or Path(board["path"]).name != artifact_filename(
                    artifacts["basename"], "initial_" + key, value["capture_number"])):
            raise ValueError("Initial board dimensions or cell mapping differ from the capture")
        plan.append((artifact, board["path"], role, value["revision"], key, key))
    return plan


def prepare_view_content(value, artifacts, evidence_id, role):
    """Prepare capture-time pixels; never attest receipt, inspection or later file state."""
    try:
        if (value.get("evidence_status") != "generated"
                or value.get("recovery", {}).get("restoration_confirmed") is not True):
            raise ValueError("View generation and restoration must be confirmed")
        capture_id, capture_number = value["capture_id"], value["capture_number"]
        if not isinstance(capture_id, str) or not capture_id:
            raise ValueError("View capture identity is missing")
        root = Path(artifacts["output_dir"])
        if not root.is_absolute():
            raise ValueError("View output directory must be absolute")
        root = root.resolve(strict=True)
        board = value.get("board_manifest")
        if "initial_views" in value:
            report, content = _prepare_images(_initial_board_plan(value, role, root, artifacts), root, evidence_id,
                                             artifacts, capture_id, capture_number)
            report.update(representation="initial_boards", direction_count=len(VIEW_DIRECTIONS),
                          capture_id=value["capture_id"], layout={key: item["layout"] for key, item in value["boards"].items()})
        elif board is not None:
            if role != "target":
                raise ValueError("Paired image content requires a Target observation")
            revisions = {"source": value["paired_revisions"]["source_revision"],
                         "target": value["paired_revisions"]["target_revision"]}
            if (any(not isinstance(item, str) or not item for item in revisions.values())
                    or revisions["target"] != value["revision"]):
                raise ValueError("Paired image revision differs from the observation")
            plan = [(board, value["board"], "paired", revisions, "board", "board")]
            try:
                report, content = _prepare_images(plan, root, evidence_id, artifacts, capture_id, capture_number)
                report["representation"] = "board"
            except _ImageLimitError as exc:
                # An unselected over-budget board is not read or certified.
                # Integrity/read errors never select an alternative representation.
                report, content = _prepare_images(_image_plan(value, role, revisions), root, evidence_id, artifacts, capture_id, capture_number)
                report.update(representation="paired_images", board_limit=exc.detail)
        else:
            if "paired_revisions" in value:
                raise ValueError("Paired observation is missing its board manifest")
            report, content = _prepare_images(_image_plan(value, role), root, evidence_id, artifacts, capture_id, capture_number)
            report["representation"] = "role_images"
        report.update(capture_id=capture_id, capture_number=capture_number)
        return report, content
    except (OSError, KeyError, AttributeError, TypeError, ValueError, struct.error) as exc:
        return {"status": "unavailable", "image_count": 0, "scope": "observation_capture",
                "reason": str(exc)[:300], "exception_type": type(exc).__name__,
                **({"limit": exc.detail} if isinstance(exc, _ImageLimitError) else {}),
                "inspection": "not_attested"}, []


def _unchanged_image(path, artifact):
    """Check capture bytes and file identity immediately before optional cleanup."""
    size = artifact["size_bytes"]
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or path.is_symlink():
        raise ValueError("Cleanup requires a regular capture file")
    if type(size) is not int or size <= 0 or before.st_size != size:
        raise ValueError("Capture file size changed before cleanup")
    with path.open("rb") as stream:
        data = stream.read(size + 1)
    after = path.lstat()
    if (any(getattr(before, key) != getattr(after, key)
            for key in ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns"))
            or len(data) != size or hashlib.sha256(data).hexdigest() != artifact["sha256"]):
        raise ValueError("Capture file changed before cleanup")


def _board_single_plan(value, artifacts):
    root = Path(artifacts["output_dir"]).resolve(strict=True)
    capture_id, capture_number = value["capture_id"], value["capture_number"]
    if value["image_delivery"]["representation"] == "initial_boards":
        boards = _initial_board_plan(value, "source", root, artifacts)
        singles = _image_plan(value, "source")
    else:
        revisions = {"source": value["paired_revisions"]["source_revision"],
                     "target": value["paired_revisions"]["target_revision"]}
        singles = _image_plan(value, "target", revisions)
        board = value["board_manifest"]
        if (board["width_px"] != 2 * value["width"]
                or board["height_px"] != len(value["layout"]["rows"]) * (value["height"] + VIEW_BOARD_HEADER_HEIGHT)):
            raise ValueError("Board does not cover the complete single-image grid")
        boards = [(board, value["board"], "paired", revisions, "board", "board")]
    kept = set()
    for item in boards:
        path = _artifact_path(item, root, artifacts, capture_id, capture_number)
        _unchanged_image(path, item[0])
        kept.add(path)
    plan = []
    for item in singles:
        artifact = item[0]
        path = _artifact_path(item, root, artifacts, capture_id, capture_number)
        if (path in kept or artifact["width_px"] != value["width"]
                or artifact["height_px"] != value["height"]):
            raise ValueError("Single image is not a full-size intermediate of this board")
        plan.append((artifact, item[4], path))
    if len({path for _artifact, _key, path in plan}) != len(plan):
        raise ValueError("Cleanup manifest contains duplicate paths")
    return plan


def cleanup_view_singles(value, artifacts):
    """Remove only this capture's unchanged singles after a board response is built."""
    delivery = value.get("image_delivery", {})
    if (delivery.get("status") != "prepared"
            or delivery.get("representation") not in ("initial_boards", "board")):
        return None
    report = {"status": "skipped", "deleted_count": 0}
    try:
        plan = _board_single_plan(value, artifacts)
    except (OSError, KeyError, TypeError, ValueError) as exc:
        report.update(reason="capture_not_verified", exception_type=type(exc).__name__, message=str(exc)[:300])
        return report
    report.update(candidate_count=len(plan), missing_count=0, retained_count=0, files=[])
    removed = set()
    for artifact, key, path in plan:
        row = {"path": str(path), "status": "retained"}
        try:
            _unchanged_image(path, artifact)
            path.unlink()
            row["status"] = "deleted"
        except FileNotFoundError:
            row["status"] = "missing"
        except (OSError, KeyError, TypeError, ValueError) as exc:
            row.update(exception_type=type(exc).__name__, message=str(exc)[:300])
        report[row["status"] + "_count"] += 1
        artifact["file_status"] = row["status"]
        if row["status"] != "retained":
            removed.add(key)
        report["files"].append(row)
    # The earlier capture event may reference the old map; keep that fact intact.
    value["views"] = {key: path for key, path in value["views"].items() if key not in removed}
    report["status"] = "partial" if report["retained_count"] else "completed"
    return report
