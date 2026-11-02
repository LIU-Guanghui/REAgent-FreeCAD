"""General schema construction and strict value validation."""

import math

def _schema(properties=None, required=()):
    return {
        "type": "object",
        "properties": properties or {},
        "required": list(required),
        "additionalProperties": False,
    }

def _string(description, enum=None):
    result = {"type": "string", "description": description}
    if enum:
        result["enum"] = list(enum)
    return result

def _number(description):
    return {"type": "number", "description": description}

def _boolean(description):
    return {"type": "boolean", "description": description}

def _integer(description, minimum=None, maximum=None):
    result = {"type": "integer", "description": description}
    if minimum is not None:
        result["minimum"] = int(minimum)
    if maximum is not None:
        result["maximum"] = int(maximum)
    return result

def _object(description):
    return {"type": "object", "description": description}

def _array(description, items=None, minimum=None, maximum=None):
    result = {"type": "array", "description": description}
    if items is not None:
        result["items"] = items
    if minimum is not None:
        result["minItems"] = minimum
    if maximum is not None:
        result["maxItems"] = maximum
    return result

def _nullable(spec):
    result = dict(spec)
    result["type"] = [spec["type"], "null"]
    return result

def _check_request_fields(value, schema, field):
    """Check fields and scalar enums before dispatching to geometry handlers."""

    if not isinstance(value, dict):
        raise ValueError("%s must be an object" % field)
    properties = schema["properties"]
    allowed = sorted(properties)
    unknown = sorted(set(value) - set(properties))
    missing = sorted(set(schema.get("required", [])) - set(value))
    if unknown or missing:
        raise ValueError(
            "%s: unsupported fields %s; missing fields %s; allowed fields %s"
            % (field, unknown, missing, allowed)
        )
    for name, item in value.items():
        choices = properties[name].get("enum")
        if choices is not None and item not in choices:
            raise ValueError("%s.%s=%r; allowed values: %s" % (field, name, item, choices))

def _branch(key, value, properties, required=None):
    return _schema({key: {"type": "string", "enum": [value]}, **properties},
                   (key, *(properties if required is None else required)))

def _conditional_form(schema, choices, required=(), excluded=()):
    """Constrain this same request object; common requirements stay in its parent."""
    properties = {key: value for key, value in schema["properties"].items()
                  if key not in excluded}
    for key, value in choices.items():
        properties[key] = {**properties[key], "enum": [value]}
    return _schema(properties, (*choices, *required))

class RequestValidationError(ValueError):
    def __init__(self, issues):
        self.issue_count = len(issues)
        self.issues = issues[:16]
        self.issues_omitted = max(0, self.issue_count - len(self.issues))
        super().__init__("Input does not match the published contract")

def validate_schema_definition(schema):
    """Reject keywords this interpreter cannot enforce, including nested schemas."""
    known = {"type", "properties", "required", "additionalProperties", "items", "oneOf", "enum",
             "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "minItems", "maxItems",
             "minLength", "maxLength", "pattern", "description", "default", "title", "uniqueItems"}
    unknown = set(schema) - known
    if unknown:
        raise ValueError("Unsupported schema keywords: " + ", ".join(sorted(unknown)))
    for child in schema.get("properties", {}).values():
        validate_schema_definition(child)
    for child in schema.get("oneOf", []):
        validate_schema_definition(child)
    if "items" in schema:
        validate_schema_definition(schema["items"])

def validate_input_schema(value, schema, *, path="request", partial=False):
    """Strict finite JSON validation; no coercion, inserted defaults or CAD I/O."""
    import re
    issues = []
    def fail(loc, reason, **details):
        issues.append({"path": loc[:500], "message": reason, **details,
                       **({"path_characters_omitted": len(loc)-500} if len(loc)>500 else {})})
    def visit(item, spec, loc, skip_required=False):
        if "oneOf" in spec:
            branches = spec["oneOf"]
            # Branch selection is diagnostic only: shortcuts must never weaken
            # oneOf exclusivity or skip sibling constraints.
            visit(item, {k: v for k, v in spec.items() if k != "oneOf"}, loc, skip_required)
            successful = 0
            for branch in branches:
                before = len(issues)
                visit(item, branch, loc)
                successful += len(issues) == before
                del issues[before:]
            if successful == 1:
                return
            if successful > 1:
                fail(loc, "Matches more than one supported form"); return
            if isinstance(item, dict):
                nested = dict.fromkeys(key for b in branches for key, prop in b.get("properties", {}).items()
                                       if prop.get("properties") and isinstance(item.get(key), dict))
                for key in nested:
                    fields = dict.fromkeys(field for b in branches
                        for field, prop in b.get("properties", {}).get(key, {}).get("properties", {}).items()
                        if prop.get("enum"))
                    for field in fields:
                        matching = [b for b in branches if item[key].get(field) in b.get("properties", {})
                                    .get(key, {}).get("properties", {}).get(field, {}).get("enum", [])]
                        if len(matching) == 1:
                            visit(item, matching[0], loc); return
                # Complete discriminator narrowing before field-presence diagnostics.
                fields = dict.fromkeys(key for b in branches for key, prop in b.get("properties", {}).items()
                                       if prop.get("enum"))
                for key in fields:
                    tagged = [b for b in branches if b.get("properties", {}).get(key, {}).get("enum")]
                    if len(tagged) == len(branches):
                        allowed = [v for b in tagged for v in b["properties"][key]["enum"]]
                        if item.get(key) not in allowed:
                            values = list(dict.fromkeys(allowed))
                            fail(loc + "." + key, "Unsupported or missing discriminator",
                                 allowed_values=values[:16], allowed_values_omitted=max(0,len(values)-16)); return
                        matches_tag = [b for b in tagged if item[key] in b["properties"][key]["enum"]]
                        branches = matches_tag
                if len(branches) == 1:
                    visit(item, branches[0], loc); return
                explicit = [b for b in branches if any(k in item and all(k not in other.get("properties", {})
                            for other in branches if other is not b) for k in b.get("properties", {}))]
                if len(explicit) == 1:
                    visit(item, explicit[0], loc); return
                required_match = [b for b in branches if b.get("properties")
                                  and set(b.get("required", [])) <= set(item)]
                if len(required_match) == 1:
                    visit(item, required_match[0], loc); return
                object_forms = [b for b in branches if b.get("type") in (None, "object")]
                if len(object_forms) == 1:
                    visit(item, object_forms[0], loc); return
            fail(loc, "Must match exactly one supported form",
                 required_forms=[b.get("required", []) for b in branches][:8])
            return
        typ = spec.get("type")
        types = [typ] if isinstance(typ, str) else typ
        matches = {"null": item is None, "object": isinstance(item, dict), "array": isinstance(item, list),
                   "string": isinstance(item, str), "boolean": isinstance(item, bool),
                   "integer": isinstance(item, int) and not isinstance(item, bool),
                   "number": isinstance(item, (float, int)) and not isinstance(item, bool)}
        if types and not any(matches.get(t, False) for t in types):
            fail(loc, "expected " + repr(types)); return
        if "enum" in spec and item not in spec["enum"]:
            fail(loc, "Unsupported value", allowed_values=spec["enum"][:16],
                 allowed_values_omitted=max(0,len(spec["enum"])-16)); return
        if isinstance(item, dict):
            props = spec.get("properties", {})
            if spec.get("additionalProperties") is False:
                for key in sorted(set(item) - set(props)):
                    fail(loc + "." + key, "unknown field")
            if not skip_required:
                for key in spec.get("required", []):
                    if key not in item: fail(loc + "." + key, "required")
            for key in props.keys() & item.keys(): visit(item[key], props[key], loc + "." + key)
        elif isinstance(item, list):
            if len(item) < spec.get("minItems", 0) or len(item) > spec.get("maxItems", len(item)):
                fail(loc, "array length outside supported range")
            if spec.get("uniqueItems") and any(child in item[:index] for index, child in enumerate(item)):
                fail(loc, "array items must be unique")
            for i, child in enumerate(item):
                if "items" in spec: visit(child, spec["items"], loc + "[%d]" % i)
        elif isinstance(item, str):
            if len(item) < spec.get("minLength", 0) or len(item) > spec.get("maxLength", len(item)):
                fail(loc, "string length outside supported range")
            if "pattern" in spec and not re.search(spec["pattern"], item): fail(loc, "invalid format")
        elif isinstance(item, (int, float)) and not isinstance(item, bool):
            if isinstance(item, float) and not math.isfinite(item): fail(loc, "must be finite")
            for key, invalid in (("minimum", item < spec.get("minimum", item)),
                                 ("maximum", item > spec.get("maximum", item)),
                                 ("exclusiveMinimum", "exclusiveMinimum" in spec and item <= spec["exclusiveMinimum"]),
                                 ("exclusiveMaximum", "exclusiveMaximum" in spec and item >= spec["exclusiveMaximum"])):
                if invalid: fail(loc, "violates " + key)
    visit(value, schema, path, partial)
    if issues: raise RequestValidationError(issues)
