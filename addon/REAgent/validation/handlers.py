"""Native Target readback with independently requested diagnostic computations."""

from ..runtime import _VALIDATION_SCHEMA_VERSION, _document, _state
from ..workspace.model import (_file_sha256, _result_shape, _step_source_path, _workspace)
from ..workspace.state import _target_view_revision
from .comparison import _compare_shapes
from .conclusions import _native_validation_conclusions
from .parameters import _parametric_audit
from .structure import _structure_audit, result_state
from .tolerance import _ir_tolerance_contract


class ValidationOperations:
    def _validate_target(self, params):
        document = _document(params["document"])
        group = _workspace(document)
        options = params.get("options", {})
        block = _state.AUDIT_BLOCKS.get(document.Name)
        blocked = bool(block and block[0] is document)
        structure, read_errors = None, {}
        try:
            structure = _structure_audit(group)
            native = result_state(group)
        except (OSError, AttributeError, RuntimeError, TypeError, ValueError) as exc:
            stage = "structure" if structure is None else "native"
            read_errors[stage] = {"reason": str(exc)[:300], "exception_type": type(exc).__name__}
            native = {"state": "unready", "result_geometry": None, **read_errors[stage]}
        target_revision = None
        try:
            target_revision = _target_view_revision(document, group, group)
        except (OSError, AttributeError, RuntimeError, TypeError, ValueError) as exc:
            read_errors["revision"] = {"reason": str(exc)[:300], "exception_type": type(exc).__name__}
        native_provenance = ({"document": document.Name, "target_revision": target_revision}
                             if target_revision is not None else None)
        requests = [*options.get("measurements", [])]
        if options.get("surface_sampling", False):
            requests.append("surface_sampling")
        requests.extend(options.get("boolean_operations", []))
        checks, provenance = {}, {}
        if not read_errors:
            provenance["native"] = native_provenance
        audit_requested = options.get("parameter_audit", False)
        unavailable = ("write_state_unknown" if blocked else
                       "native_structure_read_failed" if structure is None else
                       "native_result_unready" if native["state"] == "unready" else
                       "target_revision_unavailable" if target_revision is None else
                       "native_result_in_progress" if native["state"] == "in_progress" else None)
        if unavailable:
            checks = {name: {"status": "unavailable", "available": False, "reason": unavailable}
                      for name in requests}
        elif requests:
            # Source parsing is deliberately absent from default native readback.
            try:
                import Part
                path = _step_source_path(group.SourceFile)
                source_revision = _file_sha256(path)
                source = Part.read(path)
                if _file_sha256(path) != source_revision:
                    raise ValueError("Source STEP changed during diagnostic read")
                if source.isNull() or not source.isValid() or len(source.Solids) != 1:
                    raise ValueError("Source is not a readable valid single solid")
                checks = _compare_shapes(source, _result_shape(group),
                                         _ir_tolerance_contract(group), options)
                pair_provenance = {**native_provenance, "source_revision": source_revision}
                provenance.update({name: pair_provenance for name in checks})
                try:
                    source_current = _file_sha256(path) == source_revision
                except OSError:
                    source_current = None
                for row in checks.values():
                    row["revisions"] = {"source": source_revision, "target": target_revision}
                    if source_current is not True:
                        row["applicability"] = "stale" if source_current is False else "unknown"
                        row["applicability_reason"] = "source_changed_after_snapshot" if source_current is False else "source_revision_unavailable"
            except (OSError, AttributeError, RuntimeError, TypeError, ValueError) as exc:
                checks = {name: {"status": "unavailable", "available": False,
                                 "reason": str(exc)[:300], "exception_type": type(exc).__name__}
                          for name in requests}
        audit = {"status": "not_requested"}
        if audit_requested:
            if unavailable:
                audit = {"status": "unavailable", "reason": unavailable}
            else:
                audit = _parametric_audit(group)
                after = _target_view_revision(document, group, group)
                if after != target_revision:
                    from ..workspace.recovery import _block
                    raise _block(document, "Parameter audit changed persistent Target revision")
                native = result_state(group)
                provenance["parameter_audit"] = {"document": document.Name, "target_revision": after}
        return {"validation_schema_version": _VALIDATION_SCHEMA_VERSION,
                "status": "completed", "scope": "target", "requested": options,
                "revision": target_revision,
                "native": native, "structure": structure, "checks": checks,
                "read_errors": read_errors,
                "parameter_audit": audit,
                "conclusions": _native_validation_conclusions(
                    structure, audit, requested=audit_requested,
                    structure_unavailable_reason="native_structure_read_failed" if structure is None else None),
                "validation_provenance": provenance,
                "document_state": {"target_writes_blocked": blocked,
                                   "write_block": block[1] if blocked else None}}
