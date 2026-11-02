"""Native tool-Body command in the bound Target session."""

from REAgent.host.workspace.model import _session_bodies
from REAgent.host.features.support.construction import create_body


class BodyOperations:
    def __init__(self, *, execute_native):
        self.execute_native = execute_native

    def _create_body(self, params):
        name = params["body_name"]

        def preflight(document, group, request):
            if name == group.Name or document.getObject(name) is not None:
                raise ValueError("Tool Body name must be unique and distinct from the main Body")
            _session_bodies(group)

        def mutate(document, group, request, changes):
            body = create_body(document, name)
            group.REAgentBodyNames = [*group.REAgentBodyNames, body.Name]
            return body

        def readback(document, group, body, state):
            if (document.getObject(name) is not body or body not in _session_bodies(group)
                    or body.Tip is not None or body.TypeId != "PartDesign::Body"):
                raise RuntimeError("Tool Body creation or registration did not read back")
            if "AllowCompound" in body.PropertiesList and bool(body.AllowCompound):
                raise RuntimeError("Tool Body unexpectedly permits compound results")
            if state["state"] != "in_progress":
                raise RuntimeError("A new empty tool Body must remain in progress")
            return {"body": body.Name, "type_id": body.TypeId, "tip": None,
                    "registered": True}

        return self.execute_native(
            params, command="PartDesign_Body", preflight=preflight,
            mutate=mutate, readback=readback,
        )

    def contribute(self):
        return {
            'PartDesign_Body': self._create_body,
        }
