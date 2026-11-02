# FreeCAD document state

A Body has an ordered feature history and an active Tip; an earlier object's Shape may differ from the visible Result. Assess geometry, object errors, recompute and visibility separately.

Recheck `PropertyLinkSub` targets after recompute. Investigate reopening when persistence is in question, rather than after every feature. Use [transactions and errors](transactions-errors.md) to assess recovery, and [validation](../workflow/validation.md) for acceptance and saving.
