"""Load the single pure contract resource without importing the GUI adapter."""

from importlib import resources, util
from pathlib import Path


def _load():
    packaged = resources.files("REAgentFreeCAD").joinpath("knowledge_assets", "contract.py")
    if packaged.is_file():
        with resources.as_file(packaged) as path:
            return _module(path)
    return _module(Path(__file__).resolve().parents[2] / "addon" / "REAgent" / "protocol" / "contract.py")


def _module(path):
    spec = util.spec_from_file_location("_reagentfreecad_contract", path)
    module = util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_shared = _load()
for _name in _shared.__all__:
    if not _name.startswith("__"):
        globals()[_name] = getattr(_shared, _name)
