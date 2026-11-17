"""Infer the part shape from one or more user-selected reference images."""

from .spec import Workflow, require_input_view

TASK = Workflow(
    "image2cad", "Image to CAD", "Model the part shown by these reference images as one editable solid.",
    file_label="Reference images", file_filter="All files (*)", image_inputs=True,
    require_reference=require_input_view, delivery_queries=(("view",),),
)
