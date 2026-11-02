"""One registered final PNG group; bounded integrity reads never access CAD."""

import hashlib
import struct
import zlib
from pathlib import Path

from REAgent.rebuild.results import PreparedImage
from REAgent.values import to_json_value
from REAgent.protocol.observations import IMAGE_CONTENT_LIMITS, VIEW_DIRECTIONS, initial_view_layout, paired_view_layout, single_view_layout, view_direction_label
from REAgent.protocol.artifacts import artifact_filename


class _ImageLimitError(ValueError):
    def __init__(self, limit, actual):
        self.detail = {"limit": limit, "actual": actual, "maximum": IMAGE_CONTENT_LIMITS[limit]}
        super().__init__("View image delivery exceeds " + limit)


def _artifact_path(artifact, key, artifacts, capture_id, capture_number, role, revision):
    path = Path(artifact["path"])
    root = Path(artifacts["output_dir"]).resolve(strict=True)
    expected = Path(artifacts["output_dir"]) / artifact_filename(artifacts["basename"], key, capture_number)
    if (path != expected or not path.is_absolute() or path.is_symlink() or path.resolve(strict=True).parent != root
            or artifact["capture_id"] != capture_id or artifact["capture_number"] != capture_number
            or artifact["role"] != role or artifact["revision"] != revision or artifact["evidence_status"] != "generated"):
        raise ValueError("Image differs from the bound capture identity, role, revision or directory")
    return path


def read_capture_image(path, artifact):
    """Bounded integrity read of one retained capture; independent of cleaned singles."""
    size, width, height = artifact["size_bytes"], artifact["width_px"], artifact["height_px"]
    if any(type(number) is not int or number <= 0 for number in (size, width, height)):
        raise ValueError("View image size and dimensions must be positive integers")
    if not path.is_file() or path.stat().st_size != size:
        raise ValueError("View image file size differs from capture")
    for limit, actual in (("max_image_bytes", size),
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
    return data


def _capture_plan(value, role):
    width, height, limit = value["width"], value["height"], value["output_long_edge_px"]
    directions = value["view_directions"]
    if "initial_views" in value:
        initial = value["initial_views"]
        if (role != "source" or directions != list(VIEW_DIRECTIONS) or initial["directions"] != directions
                or initial["direction_count"] != 14 or initial["complete"] is not True
                or initial["capture_id"] != value["capture_id"]
                or value["projection"] != "orthographic" or value["normalized_display_mode"] != "flat_lines"
                or not isinstance(value["scale"].get("focus_mm"), dict)):
            raise ValueError("Initial capture is not the complete canonical Source set")
        return {"initial_" + key: layout for key, layout in initial_view_layout(width, height, limit).items()}
    if (not isinstance(directions, list) or not directions or len(set(directions)) != len(directions)
            or any(d not in (*VIEW_DIRECTIONS, "section") for d in directions)):
        raise ValueError("Capture directions are invalid")
    if "paired_revisions" in value:
        revisions = value["paired_revisions"]
        if (role != "target" or not isinstance(revisions["source_revision"], str)
                or not revisions["source_revision"] or revisions["target_revision"] != value["revision"]
                or len(directions) > 9 or ("section" in directions and directions[-1] != "section")):
            raise ValueError("Paired capture revisions or directions are invalid")
        return {"paired": paired_view_layout(width, height, directions, limit)}
    if len(directions) > 8 or "section" in directions:
        raise ValueError("Single-role capture directions are invalid")
    prefix = "source" if role == "source" else "rebuild"
    return {prefix + "_" + d: single_view_layout(width, height, role, d, limit) for d in directions}


def image_registration(value, artifacts, evidence_id, role):
    if value.get("evidence_status") != "generated" or value.get("recovery", {}).get("restoration_confirmed") is not True:
        raise ValueError("View generation and restoration must be confirmed")
    layouts = _capture_plan(value, role)
    if set(value["outputs"]) != set(layouts) or len(layouts) > IMAGE_CONTENT_LIMITS["max_images"]:
        raise ValueError("Final output group is incomplete or exceeds image capacity")
    paired = "paired_revisions" in value
    revisions = ({"source": value["paired_revisions"]["source_revision"], "target": value["revision"]}
                 if paired else {role: value["revision"]})
    images = value["images"]
    by_cell = {(row["role"], row["direction"]): row for row in images}
    expected_cells = {(cell["role"], cell["direction"]): (key, cell) for key, layout in layouts.items() for cell in layout["cells"]}
    if len(by_cell) != len(images) or set(by_cell) != set(expected_cells):
        raise ValueError("Native sampling metadata is not the complete unique cell set")
    for identity, (key, cell) in expected_cells.items():
        frame = by_cell[identity]
        direction = "iso_xm_ym_zp" if identity[1] == "section" else identity[1]
        camera = value["camera"][direction]
        if (frame["capture_id"] != value["capture_id"] or frame["capture_number"] != value["capture_number"]
                or frame["output_key"] != key or frame["image_rect_px"] != cell["image_rect_px"]
                or frame["sampling_width_px"] != value["width"] or frame["sampling_height_px"] != value["height"]
                or frame["camera"] != camera or frame["native_scale"] != value["scale"]
                or frame["projection"] != "orthographic" or value["scale"].get("shared") is not True
                or frame["normalized_display_mode"] != value["normalized_display_mode"]
                or frame["object_display_mode"] != value["object_display_mode"]
                or frame["final_scale"] != {"millimeters_per_pixel_x": value["scale"]["millimeters_per_pixel"] / cell["scale_x"],
                                           "millimeters_per_pixel_y": value["scale"]["millimeters_per_pixel"] / cell["scale_y"]}):
            raise ValueError("Capture cell, camera or final pixel scale differs from its layout")
    plan = []
    for key, layout in layouts.items():
        output = value["outputs"][key]
        artifact = output["manifest"]
        output_role = "paired" if paired else role
        revision = revisions if paired else value["revision"]
        if (output["layout"] != layout or output["path"] != artifact["path"]
                or (artifact["width_px"], artifact["height_px"]) != (layout["width_px"], layout["height_px"])):
            raise ValueError("Final PNG dimensions or cell mapping differ from the capture")
        _artifact_path(artifact, key, artifacts, value["capture_id"], value["capture_number"], output_role, revision)
        plan.append({"artifact_key": key, "role": output_role, "artifact": artifact, "layout": layout,
                     "image_id": evidence_id + ":image:" + str(len(plan))})
    return to_json_value({"evidence_id": evidence_id, "role": role,
        "artifacts": {key: artifacts[key] for key in ("output_dir", "basename")}, "plan": plan,
        "scale": value["scale"], "camera": value["camera"], "frames": images,
        **{key: value[key] for key in ("revision", "paired_revisions", "capture_id", "capture_number", "initial_views") if key in value}})


def registration_previews(registration):
    entries = []
    for row in registration["plan"]:
        key = row["artifact_key"]
        label = {"initial_orthographic": "Initial orthogonal views", "initial_corners": "Initial corner views", "paired": "Comparison views"}.get(key)
        if label is None:
            label = view_direction_label(row["layout"]["cells"][0]["direction"])
        label += " · " + {"source": "Source", "target": "Target", "paired": "Source / Target"}[row["role"]]
        entries.append({**row, "label": label, "evidence_id": registration["evidence_id"], "artifacts": dict(registration["artifacts"])})
    return to_json_value(entries)


def read_preview_image(entry):
    artifact = entry["artifact"]
    path = _artifact_path(artifact, entry["artifact_key"], entry["artifacts"], artifact["capture_id"],
                          artifact["capture_number"], entry["role"], artifact["revision"])
    return read_capture_image(path, artifact)


def preview_entries(images, value, artifacts):
    delivery = value.get("image_delivery") or {}
    if delivery.get("status") != "prepared":
        return []
    entries = value["image_previews"]
    if ([entry["image_id"] for entry in entries] != [item.image_id for item in images]
            or any(entry["artifacts"] != {key: artifacts[key] for key in ("output_dir", "basename")} for entry in entries)):
        raise ValueError("Preview descriptions differ from the registered complete group")
    return to_json_value(entries)


def read_registered_images(registration, artifact_key=None):
    plan = registration["plan"]
    if artifact_key is not None and artifact_key not in [row["artifact_key"] for row in plan]:
        raise ValueError("Image key is not in the registered complete group")
    if not 0 < len(plan) <= IMAGE_CONTENT_LIMITS["max_images"]:
        raise _ImageLimitError("max_images", len(plan))
    content, rows, total = [], [], 0
    for row in plan:
        artifact = row["artifact"]
        total += artifact["size_bytes"]
        if total > IMAGE_CONTENT_LIMITS["max_total_bytes"]:
            raise _ImageLimitError("max_total_bytes", total)
        data = read_preview_image({**row, "artifacts": registration["artifacts"]})
        content.append(PreparedImage(data=data, artifact_key=row["artifact_key"], image_id=row["image_id"],
                                     width_px=artifact["width_px"], height_px=artifact["height_px"]))
        rows.append({key: row[key] for key in ("image_id", "artifact_key", "role")})
        rows[-1].update(image_index=len(rows) - 1, size_bytes=artifact["size_bytes"],
                        width_px=artifact["width_px"], height_px=artifact["height_px"])
    report = {"status": "prepared", "image_count": len(content), "total_bytes": total,
              "images": rows, "scope": "observation_capture", "inspection": "not_attested",
              "capture_id": registration["capture_id"], "capture_number": registration["capture_number"],
              "layout": {row["artifact_key"]: row["layout"] for row in plan},
              "native_scale": registration["scale"],
              "direction_count": (registration.get("initial_views") or {}).get("direction_count")}
    return report, tuple(content), registration_previews(registration)


def prepare_view_content(value, artifacts, evidence_id, role):
    try:
        registration = image_registration(value, artifacts, evidence_id, role)
        report, content, previews = read_registered_images(registration)
        return report, content, registration, previews
    except (OSError, KeyError, AttributeError, TypeError, ValueError, struct.error) as exc:
        return {"status": "unavailable", "image_count": 0, "scope": "observation_capture", "reason": str(exc)[:300],
                "exception_type": type(exc).__name__, "inspection": "not_attested",
                **({"limit": exc.detail} if isinstance(exc, _ImageLimitError) else {})}, (), None, ()


def cleanup_unpublished_outputs(value, artifacts):
    previous = value.get("image_cleanup") or {}
    rows = list(previous.get("files", []))
    for key, output in value.get("outputs", {}).items():
        artifact = output["manifest"]
        row = {"path": artifact["path"], "status": "retained"}
        try:
            path = _artifact_path(artifact, key, artifacts, value["capture_id"], value["capture_number"],
                                  artifact["role"], artifact["revision"])
            before = path.lstat()
            read_capture_image(path, artifact)
            after = path.lstat()
            if any(getattr(before, field) != getattr(after, field) for field in
                   ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")):
                raise ValueError("Unpublished PNG changed during cleanup")
            path.unlink()
            row["status"] = "deleted"
        except FileNotFoundError:
            row["status"] = "missing"
        except (OSError, KeyError, AttributeError, TypeError, ValueError) as exc:
            row.update(reason=str(exc)[:200], exception_type=type(exc).__name__)
        rows.append(row)
    return {"status": "partial" if any(row["status"] == "retained" for row in rows) else "completed",
            "candidate_count": len(rows), "deleted_count": sum(row["status"] == "deleted" for row in rows),
            "retained_count": sum(row["status"] == "retained" for row in rows), "files": rows}
