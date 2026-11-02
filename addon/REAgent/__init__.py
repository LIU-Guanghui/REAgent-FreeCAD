"""Pure Addon resource location; importing starts no host or application resource."""

from pathlib import Path

ADDON_PATH = str(Path(__file__).resolve().parent)
