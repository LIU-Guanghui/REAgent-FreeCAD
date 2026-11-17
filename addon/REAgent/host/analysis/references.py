"""Core kernel-neutral geometry-reference schema and resolver."""

_SEMANTIC_ENTITY_KINDS = {"face", "edge"}
_SEMANTIC_AMBIGUITY_POLICIES = {"reject"}


def normalize_semantic_entity_ref(
    value,
    expected_kind=None,
    expected_owner_role=None,
    expected_intended_role=None,
    prefix="semantic_entity_ref",
):
    if not isinstance(value, dict):
        raise ValueError(prefix + " must be an object")
    allowed = {
        "entity_kind",
        "intended_role",
        "owner",
        "signatures",
        "native_hint",
        "fallback",
        "ambiguity",
        "last_resolution",
    }
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ValueError(prefix + " has unknown fields: " + ", ".join(unknown))

    entity_kind = value.get("entity_kind")
    if entity_kind not in _SEMANTIC_ENTITY_KINDS:
        raise ValueError(prefix + ".entity_kind must be face or edge")
    if expected_kind is not None and entity_kind != expected_kind:
        raise ValueError(prefix + ".entity_kind must be " + expected_kind)

    intended_role = value.get("intended_role")
    if not isinstance(intended_role, str) or not intended_role:
        raise ValueError(prefix + ".intended_role must be non-empty")
    if (
        expected_intended_role is not None
        and intended_role != expected_intended_role
    ):
        raise ValueError(
            prefix + ".intended_role must be " + expected_intended_role
        )

    owner = value.get("owner")
    if not isinstance(owner, dict) or not {"role", "revision"} <= set(owner) or set(owner) - {"role", "revision", "object_name"}:
        raise ValueError(prefix + ".owner must contain role and revision, with optional object_name")
    owner_role = owner.get("role")
    owner_revision = owner.get("revision")
    if not isinstance(owner_role, str) or not owner_role:
        raise ValueError(prefix + ".owner.role must be non-empty")
    if expected_owner_role is not None and owner_role != expected_owner_role:
        raise ValueError(
            prefix + ".owner.role must be " + expected_owner_role
        )
    if not isinstance(owner_revision, str) or not owner_revision:
        raise ValueError(prefix + ".owner.revision must be non-empty")
    owner_name = owner.get("object_name")
    if owner_name is not None and (not isinstance(owner_name, str) or not owner_name):
        raise ValueError(prefix + ".owner.object_name must be non-empty")

    signatures = value.get("signatures")
    if (
        not isinstance(signatures, dict)
        or set(signatures) != {"geometry", "topology"}
        or not all(
            isinstance(signatures.get(name), str) and signatures.get(name)
            for name in ("geometry", "topology")
        )
    ):
        raise ValueError(
            prefix + ".signatures must contain geometry and topology digests"
        )

    native_hint = value.get("native_hint")
    if native_hint is not None:
        if (
            not isinstance(native_hint, dict)
            or set(native_hint) != {"adapter", "subelement"}
            or native_hint.get("adapter") != "freecad"
            or not isinstance(native_hint.get("subelement"), str)
            or not native_hint.get("subelement")
        ):
            raise ValueError(
                prefix
                + ".native_hint must contain adapter=freecad and subelement"
            )

    if value.get("fallback") is not None:
        raise ValueError(prefix + ".fallback must be null; re-observe current geometry")

    ambiguity = value.get("ambiguity", "reject")
    if ambiguity not in _SEMANTIC_AMBIGUITY_POLICIES:
        raise ValueError(prefix + ".ambiguity must be reject")

    last_resolution = value.get("last_resolution")
    if last_resolution is not None and not isinstance(last_resolution, dict):
        raise ValueError(prefix + ".last_resolution must be an object")

    normalized = {
        "entity_kind": entity_kind,
        "intended_role": intended_role,
        "owner": {"role": owner_role, "revision": owner_revision,
                  **({"object_name": owner_name} if owner_name is not None else {})},
        "signatures": {
            "geometry": signatures["geometry"],
            "topology": signatures["topology"],
        },
        "native_hint": (
            None
            if native_hint is None
            else {
                "adapter": "freecad",
                "subelement": native_hint["subelement"],
            }
        ),
        "fallback": None,
        "ambiguity": ambiguity,
    }
    if last_resolution is not None:
        normalized["last_resolution"] = dict(last_resolution)
    return normalized


def semantic_entity_ref_from_detail(
    detail,
    model_revision,
    entity_kind,
    intended_role,
    owner_name=None,
):
    return normalize_semantic_entity_ref(
        {
            "entity_kind": entity_kind,
            "intended_role": intended_role,
            "owner": {"role": "target", "revision": model_revision,
                      **({"object_name": owner_name} if owner_name is not None else {})},
            "signatures": {
                "geometry": detail["geometry_signature"],
                "topology": detail["topology_signature"],
            },
            "native_hint": {
                "adapter": "freecad",
                "subelement": detail["name"],
            },
            "fallback": None,
            "ambiguity": "reject",
            "last_resolution": {
                "status": "resolved",
                "matched_by": "created_from_current_geometry",
                "subelement": detail["name"],
                "revision": model_revision,
            },
        },
        expected_kind=entity_kind,
        expected_owner_role="target",
        expected_intended_role=intended_role,
    )


def existing_entity_names(requested, entities):
    """Resolve explicit, schema-validated handles against the observed shape."""
    if requested is None:
        return None
    available = {entity["name"] for entity in entities}
    names = list(dict.fromkeys(requested))
    for name in names:
        if name not in available:
            raise KeyError(f"{name} is missing from the observed geometry")
    return names
