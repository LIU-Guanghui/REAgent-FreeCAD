"""Explicit host family composition and frozen Bridge handler coverage."""

from dataclasses import dataclass
from types import MappingProxyType
from REAgent.protocol.operations import OPERATION_SPECS
from .operations import AdapterOperations
from .workspace.executor import ExecutorOperations
from .workspace.sessions import SessionOperations
from .workspace.persistence import PersistenceOperations
from .workspace.queries import ObservationOperations
from .workspace.scope import ObservationScopeOperations
from .view.queries import ViewObservationOperations
from .validation.handlers import ValidationOperations
from .logs import DiagnosticOperations
from .features.body import BodyOperations
from .features.boolean import BooleanOperations
from .features.chamfer import ChamferOperations
from .features.datum import DatumOperations
from .features.draft import DraftOperations
from .features.extrude import ExtrudeOperations
from .features.fillet import FilletOperations
from .features.hole import HoleOperations
from .features.loft import LoftOperations
from .features.mirror import MirrorOperations
from .features.pattern import PatternOperations
from .features.pipe import PipeOperations
from .features.primitives import PrimitiveOperations
from .features.revolve import RevolveOperations
from .features.sketch import SketchOperations
from .features.thickness import ThicknessOperations


@dataclass(frozen=True)
class HostOperations:
    handlers: object
    diagnostic_state: object


def freeze_handlers(contributions, specifications):
    entries = {}
    for contribution in contributions:
        for name, handler in contribution.items():
            if name in entries or name not in specifications or not callable(handler):
                raise ValueError("Duplicate, unknown or incomplete host operation: " + name)
            entries[name] = handler
    if set(entries) != set(specifications):
        raise ValueError("Host operation handlers differ from the Bridge contract")
    return MappingProxyType(entries)


def assemble_operations(role, port):
    executor = ExecutorOperations()
    queries = ObservationOperations()
    table = None
    adapter = AdapterOperations(role, port, queries=queries, views=ViewObservationOperations(),
        scope=ObservationScopeOperations(), validation=ValidationOperations(), handlers=lambda: table)
    families = (adapter, SessionOperations(), PersistenceOperations(), DiagnosticOperations(), executor,
        BodyOperations(execute_native=executor._execute_native_command),
        BooleanOperations(execute_native=executor._execute_native_command),
        ChamferOperations(execute_native=executor._execute_native_command),
        DatumOperations(execute_native=executor._execute_native_command),
        DraftOperations(execute_native=executor._execute_native_command),
        ExtrudeOperations(execute_native=executor._execute_native_command),
        FilletOperations(execute_native=executor._execute_native_command),
        HoleOperations(execute_native=executor._execute_native_command),
        LoftOperations(execute_native=executor._execute_native_command),
        MirrorOperations(execute_native=executor._execute_native_command),
        PatternOperations(execute_native=executor._execute_native_command),
        PipeOperations(execute_native=executor._execute_native_command),
        PrimitiveOperations(execute_native=executor._execute_native_command),
        RevolveOperations(execute_native=executor._execute_native_command),
        SketchOperations(execute_native=executor._execute_native_command),
        ThicknessOperations(execute_native=executor._execute_native_command),
    )
    table = freeze_handlers((family.contribute() for family in families), OPERATION_SPECS)
    return HostOperations(table, queries._diagnostic_document_state)
