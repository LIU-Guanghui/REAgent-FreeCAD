"""Internal Bridge discovery, using the shared native operation contract."""

import os
import xml.etree.ElementTree as ET

from REAgent import ADDON_PATH
from REAgent.protocol.operations import OPERATION_SPECS
from REAgent.protocol.definitions import PRODUCT_ID
from REAgent.host.logs import native_log_source


def _addon_metadata():
    path = os.path.join(ADDON_PATH, "package.xml")
    try:
        root = ET.parse(path).getroot()
        version = next((child.text for child in root if child.tag.rsplit("}", 1)[-1] == "version"), None)
        if not version or not version.strip():
            raise ValueError("package.xml has no nonempty version")
        return {"product_version": version.strip()}
    except (OSError, ET.ParseError, ValueError) as exc:
        return {"product_version": "unknown", "version_error": {
            "error_class": "package_read" if isinstance(exc, OSError) else "package_metadata",
            "exception_type": type(exc).__name__, "message": str(exc)[:200]}}


def capability_response(*, view, operation, role, port, registered_handlers):
    if set(registered_handlers) != set(OPERATION_SPECS):
        raise RuntimeError("Bridge handlers differ from the shared operation contract")
    if view == "status":
        return {"available": True,
                "product_id": PRODUCT_ID, "role": role, "port": port,
                **_addon_metadata(),
                "native_log_source": native_log_source(),
                "operation_contract": {"operations": OPERATION_SPECS}}
    if view == "operation":
        if operation is None:
            raise ValueError("view=operation requires operation")
        spec = OPERATION_SPECS.get(operation)
        return {"available": bool(spec and role in spec["roles"]), "operation": operation, "contract": spec}
    if view == "catalog":
        return {"available": True, "operations": [name for name, spec in OPERATION_SPECS.items() if role in spec["roles"]]}
    raise ValueError("Internal capability view must be status, catalog or operation")
