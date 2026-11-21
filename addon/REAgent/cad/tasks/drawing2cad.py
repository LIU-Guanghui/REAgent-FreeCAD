"""Reconstruct the part shown on a fully dimensioned DXF engineering drawing."""

from .spec import Workflow


def require_reference(record, observations):
    view = observations.get("source_view", {})
    if (view.get("scope") != "model_request_completed"
            or view.get("document") != record["source_document"]
            or view.get("revision") != record["source_revision"]):
        raise ValueError("Receive a view of the bound DXF drawing before modeling")

TASK = Workflow(
    "drawing2cad", "Drawing to CAD",
    "Reconstruct the part from the fully dimensioned engineering drawing and check the model against its callouts.",
    file_label="DXF drawing (callouts as geometry)", file_filter="DXF (*.dxf)", native_source="drawing",
    require_reference=require_reference, delivery_queries=(("view",),), require_dimension_readback=True,
)
