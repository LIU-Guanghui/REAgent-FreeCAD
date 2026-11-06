"""Controller-side observation and revision requirements for in-place editing."""

from .execution import validate_vertex_evidence
from REAgent.rebuild.log_summary import execution_summary


def _edit_evidence(args, record):
    facts = [record["evidence_facts"][item] for item in args["evidence"]]
    facts = [fact for fact in facts if fact["role"] == "target" and fact["revision"] == args["expected_revision"]]
    observed = [fact for fact in facts if fact["kind"] == "feature" and fact["object"] == args["object_name"]
                and fact["type_id"] == args["type_id"] and fact["body"] == args["body_name"]]
    if not observed:
        raise ValueError("Observe this exact Target object, Body and native TypeId at the current revision")
    changes = args["changes"]
    selecting = any(key in changes for key in ("edge_refs", "face_refs")) or any(
        isinstance(value, dict) and value.get("kind") in ("predecessor_face", "predecessor_edge")
        for value in changes.values())
    if selecting and not any(fact.get("edit_object") == args["object_name"]
                             and fact.get("predecessor") == observed[0]["predecessor"] for fact in facts):
        raise ValueError("Observe writable entity references on this edit object's actual predecessor first")

    indices = {section: {index for fact in observed for index in fact.get("sketch_indices", {}).get(section, ())}
               for section in ("geometry", "constraints")}
    for operation in changes.get("operations", ()):
        op = operation["op"]
        if "index" in operation:
            section = "constraints" if "constraint" in op else "geometry"
            if operation["index"] not in indices[section]:
                raise ValueError("Sketch native index requires a current " + section + " page for this object")
        if op == "add_constraint":
            for field in operation["constraint"].values():
                if not isinstance(field, dict):
                    continue
                ref = field.get("geometry", field)
                if ref.get("kind") == "existing" and ref["index"] not in indices["geometry"]:
                    raise ValueError("Constraint reference requires a current Sketch geometry page")


def run(name, args, record, started, ctx):
    _edit_evidence(args, record)
    validate_vertex_evidence(args["changes"].get("reference"), args, record)
    value = ctx.call(record, name, **{key: item for key, item in args.items() if key != "session_id"})
    observed = value["readback"]
    summary = {**execution_summary(value), "object": observed["feature"], "type_id": observed["type_id"],
               "changes": observed["changes"], "affected_bodies": observed["affected_bodies"],
               "affected_object_count": observed["affected_object_count"]}
    if "sketch" in observed:
        summary["sketch"] = observed["sketch"]
    if "companion_changes" in observed:
        summary["companion_changes"] = observed["companion_changes"]
        summary["companion_change_count"] = observed["companion_change_count"]
    event = "feature.unchanged" if value.get("committed") is False else "feature.committed"
    ctx.emit(record, event, name, summary, started)
    return value


def contribute():
    return {"EditFeature": run}
