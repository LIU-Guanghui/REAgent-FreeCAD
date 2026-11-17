"""Reconstruct editable features using adopted views of a native STL mesh."""

from .spec import Workflow, require_initial_views, require_source_global


def require_reference(record, observations):
    require_source_global(record, observations)
    require_initial_views(record, observations)


TASK = Workflow(
    "stl2cad", "STL to CAD", "Rebuild the STL as one editable native part and report approximate agreement.",
    file_label="STL file", file_filter="STL (*.stl)", native_source="mesh",
    require_reference=require_reference, delivery_queries=(("global",), ("paired_views",)),
)
