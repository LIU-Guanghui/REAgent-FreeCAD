# Geometric comparison and delivery

## Assess the reconstruction

Judge the reconstruction by Source/Target geometric agreement. `agent_validate` computes only selected comparisons or an explicitly requested parameter audit; select at least one item. Use `agent_get_state` to inspect document and execution state. Unavailable or stale geometry is not a comparison result; do not infer agreement from it.

Before accepting ordinary reconstruction, inspect comparable Source/Target views and measure task-required dimensions. Explain agreements, discrepancies and remaining questions. Apply the [observation rules](observation.md) to view failures and restoration.

Request measurements, approximate distances or directional Booleans only to resolve a question; unrequested diagnostics are not missing acceptance evidence. Report requested computations that fail or remain unavailable.

Topology counts, segmentation and analytic/B-Spline representation differences alone do not reject a reconstruction. Volume, area, mass-center and bounding-box differences are descriptive, not acceptance scores. Trimmed bounds can be non-tight; use targeted dimensions. Finite sampling and Booleans do not prove unconditional equivalence.

Parameter audit is opt-in: it temporarily changes native parameters and requires confirmed restoration.

## Save a checkpoint

The user requests `agent_save` to confirm an FCStd write; it does not rerun comparison or refresh historical validation. A structurally known unfinished workspace can be saved and reported as unfinished.

Keep the absolute FCStd checkpoint path distinct from its derived journal path. File existence or journal text does not prove the latest save succeeded. Execution, restoration, acceptance and saving remain separate facts.
