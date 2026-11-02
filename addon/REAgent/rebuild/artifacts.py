"""Allocate one source-named artifact family without overwriting prior output."""

import os
from pathlib import Path

from REAgent.protocol.artifacts import ARTIFACT_NUMBER_LIMIT, artifact_basename, artifact_filename, artifact_matches
from REAgent.rebuild.errors import PublicError


def absolute_path(value, field, extensions=()):
    if not isinstance(value, str) or not value or not os.path.isabs(value):
        error = PublicError("invalid_request", "public_validation", field + " must be an absolute path")
        error.field_path = field
        raise error
    path = os.path.normpath(value)
    if extensions and Path(path).suffix.lower() not in extensions:
        error = PublicError("invalid_request", "public_validation", field + " has an unsupported extension")
        error.field_path = field
        raise error
    return path


def same_path(left, right):
    return os.path.normcase(os.path.abspath(left)) == os.path.normcase(os.path.abspath(right))


def artifact_paths(source_path, output_dir, number=0):
    stem = Path(source_path).stem
    basename = artifact_basename(stem, number)
    return {"source_stem": stem, "basename": basename, "output_dir": output_dir,
            "model_path": str(Path(output_dir) / artifact_filename(basename, "model")),
            "journal_path": str(Path(output_dir) / artifact_filename(basename, "journal"))}


def claim_artifacts(artifacts, resume_path=None):
    """Return the selected family and its open, exclusively created journal."""
    directory = Path(artifacts["output_dir"])
    directory.mkdir(parents=True, exist_ok=True)
    names = [path.name for path in directory.iterdir()]
    for number in range(ARTIFACT_NUMBER_LIMIT + 1):
        basename = artifact_basename(artifacts["source_stem"], number)
        if any(artifact_matches(name, basename) for name in names):
            continue
        selected = {**artifacts, "basename": basename,
                    "model_path": str(directory / artifact_filename(basename, "model")),
                    "journal_path": str(directory / artifact_filename(basename, "journal"))}
        if resume_path and same_path(selected["model_path"], resume_path):
            continue
        try:
            reservation = Path(selected["journal_path"]).open("x", encoding="utf-8")
        except FileExistsError:
            continue
        return selected, reservation
    raise FileExistsError("Artifact basenames through _999 are occupied in " + str(directory))


def claim_fixed_artifacts(artifacts):
    """Recover a failed initial claim without renaming or adopting other files."""
    directory = Path(artifacts["output_dir"])
    directory.mkdir(parents=True, exist_ok=True)
    if any(artifact_matches(path.name, artifacts["basename"]) for path in directory.iterdir()):
        raise FileExistsError("Unclaimed artifact family is occupied: " + artifacts["basename"])
    return Path(artifacts["journal_path"]).open("x", encoding="utf-8")
