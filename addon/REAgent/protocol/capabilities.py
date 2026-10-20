"""Internal Bridge discovery, separate from public MCP tool contracts."""

import os
import xml.etree.ElementTree as ET

from ..runtime import ADDON_PATH
from .contract import CAPABILITY_SCHEMA_VERSION, OPERATION_SPECS, PRODUCT_ID
from .logs import native_log_source


def _addon_version():
    path = os.path.join(ADDON_PATH, "package.xml")
    try:
        root = ET.parse(path).getroot()
        for child in root:
            if child.tag.rsplit("}", 1)[-1] == "version":
                return str(child.text or "unknown")
    except Exception:
        pass
    return "unknown"


def capability_response(*, view, operation, role, port, registered_handlers):
    if set(registered_handlers) != set(OPERATION_SPECS):
        raise RuntimeError("Bridge handlers differ from the shared operation contract")
    if view == "status":
        return {"schema_version": CAPABILITY_SCHEMA_VERSION, "available": True,
                "product_id": PRODUCT_ID, "role": role, "port": port,
                "product_version": _addon_version(),
                "native_log_source": native_log_source(),
                "operation_contract": {"schema_version": 1, "operations": OPERATION_SPECS}}
    if view == "operation":
        if operation is None:
            raise ValueError("view=operation requires operation")
        spec = OPERATION_SPECS.get(operation)
        return {"available": bool(spec and role in spec["roles"]), "operation": operation, "contract": spec}
    if view == "catalog":
        return {"available": True, "operations": [name for name, spec in OPERATION_SPECS.items() if role in spec["roles"]]}
    raise ValueError("Internal capability view must be status, catalog or operation")
