"""Filesystem location of the installed Addon."""

from pathlib import Path

ADDON_PATH = str(Path(__file__).resolve().parent)
