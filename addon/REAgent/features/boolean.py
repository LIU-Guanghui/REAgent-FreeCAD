"""Native PartDesign Boolean between explicitly registered tool Bodies."""

from ..analysis.measurements import _shape_summary_consistent
from ..validation.structure import _single_solid_summary
from ..workspace.executor import ExecutorOperations
from ..workspace.model import _session_bodies, _validate_boolean_tools, _world_shape
from ..workspace.properties import _metadata, _same_placement, _set_bool, _set_enum
from .common.construction import _history_context, _shape_readback


class BooleanOperations(ExecutorOperations):
    def _create_boolean(self, params):
        name = params["feature_name"]
        context = [None]
        tools = [None]
        tool_facts = [None]

        def preflight(document, group, request):
            context[0] = _history_context(
                document, group, request, require_solid=True, sketch=False)
            body = context[0][0]
            names = request["tool_body_names"]
            if request["mode"] == "Common" and len(names) != 1:
                raise ValueError("Boolean Common requires exactly one tool Body")
            if len(names) != len(set(names)):
                raise ValueError("Boolean tool Body names must be distinct")
            registered = _session_bodies(group)
            resolved = [document.getObject(item) for item in names]
            if any(item not in registered or item is body or item is group
                   for item in resolved):
                raise ValueError("Boolean tools must be distinct registered non-main Bodies")
            facts = []
            for tool in resolved:
                if tool.Tip is None or tool.Tip not in tool.Group:
                    raise ValueError("Boolean tool has no valid native Tip: " + tool.Name)
                _single_solid_summary(tool.Shape, "Boolean tool " + tool.Name)
                world_shape = _world_shape(tool)
                shape_summary = _single_solid_summary(world_shape, "Boolean world tool " + tool.Name)
                facts.append({"tool": tool, "members": list(tool.Group),
                              "tip": tool.Tip,
                              "global_placement": tool.getGlobalPlacement(),
                              "shape_summary": shape_summary})
            tools[0], tool_facts[0] = resolved, facts

        def mutate(document, group, request, changes):
            body = context[0][0]
            feature = document.addObject("PartDesign::Boolean", name)
            if feature.Name != name or feature.TypeId != "PartDesign::Boolean":
                raise RuntimeError("Native Boolean type or identity differs")
            body.addObject(feature)
            _set_enum(feature, "Type", request["mode"])
            if not hasattr(feature, "setObjects"):
                raise ValueError("Native Boolean tool setter is unavailable")
            feature.setObjects(tools[0])
            for fact in tool_facts[0]:
                tool = fact["tool"]
                wanted_global = fact["global_placement"]
                if not _same_placement(tool.getGlobalPlacement(), wanted_global):
                    placement = _metadata(tool, "Placement")
                    if not placement["writable"] or placement["codec"] != "placement":
                        raise ValueError("Boolean tool Placement cannot be converted: " + tool.Name)
                    new_parent = tool.getGlobalPlacement().multiply(tool.Placement.inverse())
                    tool.Placement = new_parent.inverse().multiply(wanted_global)
                if not _same_placement(tool.getGlobalPlacement(), wanted_global):
                    raise RuntimeError("Boolean tool world Placement changed: " + tool.Name)
            _validate_boolean_tools(group, feature, tools[0])
            if "Refine" in feature.PropertiesList:
                _set_bool(feature, "Refine", False)
            return feature

        def readback(document, group, feature, _state):
            body, _sketch, tip = context[0]
            body_shape = _shape_readback(document, body, feature, name,
                                         "PartDesign::Boolean", tip)
            if feature.Type != params["mode"] or list(feature.Group) != tools[0]:
                raise RuntimeError("Native Boolean mode or ordered tool links differ")
            _validate_boolean_tools(group, feature, list(feature.Group))
            if any(tool not in feature.OutList for tool in tools[0]):
                raise RuntimeError("Native Boolean lost a tool dependency")
            for fact in tool_facts[0]:
                tool = fact["tool"]
                if (tool not in _session_bodies(group) or list(tool.Group) != fact["members"]
                        or tool.Tip is not fact["tip"]
                        or not _same_placement(tool.getGlobalPlacement(), fact["global_placement"])):
                    raise RuntimeError("Boolean tool Body history or Placement changed: " + tool.Name)
                world_shape = _world_shape(tool)
                shape_summary = _single_solid_summary(world_shape, "Boolean world tool " + tool.Name)
                if not _shape_summary_consistent(fact["shape_summary"], shape_summary):
                    raise RuntimeError("Boolean tool world Shape summary changed: " + tool.Name)
            if "Refine" in feature.PropertiesList and bool(feature.Refine):
                raise RuntimeError("Native Boolean Refine mode changed")
            return {"feature": feature.Name, "body": body.Name,
                    "type_id": feature.TypeId, "tip": body.Tip.Name,
                    "mode": feature.Type, "tools": [tool.Name for tool in tools[0]],
                    "tool_count": len(tools[0]), "body_shape": body_shape}

        return self._execute_native_command(
            params, command="PartDesign_Boolean", preflight=preflight,
            mutate=mutate, readback=readback,
        )
