"""Explicit geometric comparisons and opt-in transient parameter probes."""

from REAgent.host.process_state import require_document, PROCESS
from REAgent.protocol.definitions import validation_items
from REAgent.host.file_digest import file_sha256
from REAgent.host.workspace.model import comparison_inputs_state, comparison_shape, stp_source_path, workspace
from REAgent.host.workspace.state import target_view_revision
from REAgent.host.validation.comparison import compare_shapes
from REAgent.host.validation.parameters import parameter_conclusion, parametric_audit
from REAgent.host.validation.tolerance import ir_tolerance_contract


class ValidationOperations:
    def validate_target(self, params):
        document = require_document(params["document"])
        group = workspace(document)
        options = params["options"]
        requests = validation_items(options)
        audit_requested = options.get("parameter_audit", False)
        block = PROCESS.AUDIT_BLOCKS.get(document.Name)
        blocked = bool(block and block[0] is document)
        checks, provenance, read_errors = {}, {}, {}
        target_revision = None
        try:
            target_revision = target_view_revision(document, group)
        except (OSError, AttributeError, RuntimeError, TypeError, ValueError) as exc:
            read_errors["revision"] = {"reason": str(exc)[:300], "exception_type": type(exc).__name__}
        if requests:
            try:
                if blocked or target_revision is None:
                    raise ValueError("Target write state is unknown" if blocked else "Target revision is unavailable")
                import Part
                target, inputs = comparison_shape(group)
                if target_view_revision(document, group) != target_revision:
                    raise ValueError("Target changed while copying comparison geometry")
                path = stp_source_path(group.SourceFile)
                source_revision = file_sha256(path)
                binding = PROCESS.SESSION_BINDINGS.get(params["session_id"])
                if binding is None or binding["document"] is not document or binding["source_revision"] != source_revision:
                    raise ValueError("Source STP differs from the bound session snapshot")
                source = Part.read(path)
                if file_sha256(path) != source_revision:
                    raise ValueError("Source STP changed during comparison read")
                if source.isNull() or not source.isValid() or len(source.Solids) != 1:
                    raise ValueError("Source is not a readable valid single solid")
                checks = compare_shapes(source, target, ir_tolerance_contract(group), options)
                applicability, reason = "current", "same_dependency_revisions"
                try:
                    if (comparison_inputs_state(group) != inputs
                            or target_view_revision(document, group) != target_revision
                            or file_sha256(path) != source_revision):
                        applicability, reason = "stale", "comparison_inputs_changed"
                except (OSError, AttributeError, RuntimeError, TypeError, ValueError) as exc:
                    applicability, reason = "unknown", str(exc)[:300]
                for name, row in checks.items():
                    row.update(revisions={"source": source_revision, "target": target_revision},
                               applicability=applicability, applicability_reason=reason)
                    if applicability == "current":
                        provenance[name] = {"document": document.Name, "target_revision": target_revision,
                                            "source_revision": source_revision}
            except (OSError, AttributeError, RuntimeError, TypeError, ValueError) as exc:
                checks = {name: {"status": "unavailable", "available": False,
                                 "reason": str(exc)[:300], "exception_type": type(exc).__name__}
                          for name in requests}
        audit = {"status": "not_requested"}
        if audit_requested:
            if blocked or target_revision is None:
                audit = {"status": "unavailable", "reason": "write_state_unknown" if blocked else "target_revision_unavailable"}
            else:
                audit = parametric_audit(group)
                after = target_view_revision(document, group)
                if after != target_revision:
                    from REAgent.host.workspace.recovery import block_document_writes
                    raise block_document_writes(document, "Parameter audit changed persistent Target revision")
                provenance["parameter_audit"] = {"document": document.Name, "target_revision": after}
        return {"status": "completed", "scope": "target", "requested": options,
                "revision": target_revision, "checks": checks, "read_errors": read_errors,
                "parameter_audit": audit,
                "conclusions": {"parameter_editing_verified": parameter_conclusion(audit)} if audit_requested else {},
                "validation_provenance": provenance,
                "document_state": {"target_writes_blocked": blocked,
                                   "write_block": block[1] if blocked else None}}
