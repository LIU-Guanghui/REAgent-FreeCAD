"""Explicit geometric comparisons and opt-in transient parameter probes."""

from REAgent.host.process_state import _document, PROCESS
from REAgent.protocol.definitions import validation_items
from REAgent.host.file_digest import _file_sha256
from REAgent.host.workspace.model import _comparison_inputs_state, _comparison_shape, _step_source_path, _workspace
from REAgent.host.workspace.state import _target_view_revision
from REAgent.host.validation.comparison import _compare_shapes
from REAgent.host.validation.parameters import _parameter_conclusion, _parametric_audit
from REAgent.host.validation.tolerance import _ir_tolerance_contract


class ValidationOperations:
    def _validate_target(self, params):
        document = _document(params["document"])
        group = _workspace(document)
        options = params["options"]
        requests = validation_items(options)
        audit_requested = options.get("parameter_audit", False)
        block = PROCESS.AUDIT_BLOCKS.get(document.Name)
        blocked = bool(block and block[0] is document)
        checks, provenance, read_errors = {}, {}, {}
        target_revision = None
        try:
            target_revision = _target_view_revision(document, group, group)
        except (OSError, AttributeError, RuntimeError, TypeError, ValueError) as exc:
            read_errors["revision"] = {"reason": str(exc)[:300], "exception_type": type(exc).__name__}
        if requests:
            try:
                if blocked or target_revision is None:
                    raise ValueError("Target write state is unknown" if blocked else "Target revision is unavailable")
                import Part
                target, inputs = _comparison_shape(group)
                if _target_view_revision(document, group, group) != target_revision:
                    raise ValueError("Target changed while copying comparison geometry")
                path = _step_source_path(group.SourceFile)
                source_revision = _file_sha256(path)
                binding = PROCESS.SESSION_BINDINGS.get(params["session_id"])
                if binding is None or binding["document"] is not document or binding["source_revision"] != source_revision:
                    raise ValueError("Source STEP differs from the bound session snapshot")
                source = Part.read(path)
                if _file_sha256(path) != source_revision:
                    raise ValueError("Source STEP changed during comparison read")
                if source.isNull() or not source.isValid() or len(source.Solids) != 1:
                    raise ValueError("Source is not a readable valid single solid")
                checks = _compare_shapes(source, target, _ir_tolerance_contract(group), options)
                applicability, reason = "current", "same_dependency_revisions"
                try:
                    if (_comparison_inputs_state(group) != inputs
                            or _target_view_revision(document, group, group) != target_revision
                            or _file_sha256(path) != source_revision):
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
                audit = _parametric_audit(group)
                after = _target_view_revision(document, group, group)
                if after != target_revision:
                    from REAgent.host.workspace.recovery import _block
                    raise _block(document, "Parameter audit changed persistent Target revision")
                provenance["parameter_audit"] = {"document": document.Name, "target_revision": after}
        return {"status": "completed", "scope": "target", "requested": options,
                "revision": target_revision, "checks": checks, "read_errors": read_errors,
                "parameter_audit": audit,
                "conclusions": {"parameter_editing_verified": _parameter_conclusion(audit)} if audit_requested else {},
                "validation_provenance": provenance,
                "document_state": {"target_writes_blocked": blocked,
                                   "write_block": block[1] if blocked else None}}
