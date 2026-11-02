"""Shared artifact names and purposes."""

from REAgent.protocol.observations import VIEW_DIRECTIONS

ARTIFACT_NUMBER_LIMIT = 999

ARTIFACT_SUFFIXES = {
    "model": ("", ".FCStd"),
    "journal": ("_journal", ".json"),
    "journal_tmp": ("_journal_tmp", ".json"),
    "initial_orthographic": ("_initial_orthographic", ".png"),
    "initial_corners": ("_initial_corners", ".png"),
    "paired": ("_paired", ".png"),
}

for _role in ("source", "rebuild"):
    for _direction in VIEW_DIRECTIONS:
        _key = _role + "_" + _direction
        ARTIFACT_SUFFIXES[_key] = ("_" + _key, ".png")

def artifact_basename(stem, number=0):
    if (not isinstance(stem, str) or not stem or stem in (".", "..")
            or any(char in stem for char in ("/", "\\", "\0"))):
        raise ValueError("Artifact basename must be one non-empty filename stem")
    if type(number) is not int or not 0 <= number <= ARTIFACT_NUMBER_LIMIT:
        raise ValueError("Artifact number must be 0 through 999")
    return stem + ("_%03d" % number if number else "")

def artifact_filename(basename, kind, number=0):
    suffix, extension = ARTIFACT_SUFFIXES[kind]
    if number and extension != ".png" and kind != "journal_tmp":
        raise ValueError("Only captures and journal temporary files have local numbers")
    return artifact_basename(artifact_basename(basename) + suffix, number) + extension

def artifact_matches(filename, basename):
    """Match this allocated basename, including local capture/temp numbers."""
    name = filename.lower()
    for kind, (suffix, extension) in ARTIFACT_SUFFIXES.items():
        prefix, extension = (basename + suffix).lower(), extension.lower()
        if name == prefix + extension:
            return True
        if extension == ".png" or kind == "journal_tmp":
            if name.startswith(prefix + "_") and name.endswith(extension):
                number = name[len(prefix) + 1:-len(extension)]
                if len(number) == 3 and number.isascii() and number.isdigit() and number != "000":
                    return True
    return False
