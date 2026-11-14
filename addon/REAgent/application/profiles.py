"""Pure named-profile parsing, semantic comparison and detached draft values."""

import json

from REAgent.agent import settings

_EMPTY_PROFILES = '{"selected":"","items":[]}'
_CONFIG_FIELDS = ("provider", "base_url", "model", "context_tokens", "output_tokens", "reasoning_effort")
_CONTEXT_TOKENS = 1048576


def default_profiles():
    return {name: {"provider": name, **defaults, "model": "", "context_tokens": _CONTEXT_TOKENS,
                   "reasoning_effort": "default"} for name, defaults in settings.provider_defaults().items()}


def profile_efforts(provider, model):
    return settings.reasoning_efforts(provider, model)


def profile_connection(provider, base_url):
    return settings.connection_details(provider, base_url)


def idle_context(config):
    return {"status": "not_started", **(settings.context_budget(config) if config is not None else {})}


def config_values(row):
    try:
        budget = row["output_tokens"]
        if (isinstance(budget, bool) or isinstance(row["context_tokens"], bool)
                or type(budget) not in (str, int) or type(row["context_tokens"]) not in (str, int)):
            raise ValueError()
        return {**{name: row[name] for name in ("provider", "base_url", "model", "reasoning_effort")},
                "context_tokens": int(row["context_tokens"]),
                "output_tokens": int(budget)}
    except (ValueError, TypeError, KeyError, OverflowError):
        raise ValueError("Enter a provider, model, whole-number context capacity and reasoning effort.") from None

def validate_config(values):
    return settings.validate_config(values)

def semantic_inputs(row):
    values = config_values(row)
    connection = profile_connection(values["provider"], values["base_url"])
    return (connection["credential_binding"], values["model"],
            values["context_tokens"], values["reasoning_effort"], values["output_tokens"])

def read_profiles(value):
    try:
        data = json.loads(value or _EMPTY_PROFILES)
        if not isinstance(data, dict) or set(data) != {"selected", "items"} or not isinstance(data["selected"], str):
            raise ValueError()
        rows = data["items"]
        if not isinstance(rows, list) or len(rows) > 100:
            raise ValueError()
        profiles = {}
        providers = settings.provider_defaults()
        for row in rows:
            if not isinstance(row, dict) or set(row) - {*_CONFIG_FIELDS, "name", "protected_key"}:
                raise ValueError()
            if (any(not isinstance(row[field], str) for field in ("provider", "base_url", "model", "reasoning_effort"))
                    or row["provider"] not in providers or type(row["context_tokens"]) is not int
                    or row["context_tokens"] <= 0 or row["output_tokens"] is not None
                    and (type(row["output_tokens"]) is not int or row["output_tokens"] <= 0)):
                raise ValueError()
            name = row.get("name")
            if not isinstance(name, str) or not name.strip() or len(name) > 100 or name in profiles:
                raise ValueError()
            if not isinstance(row.get("protected_key", ""), str):
                raise ValueError()
            profiles[name] = {field: row[field] for field in _CONFIG_FIELDS}
            if profiles[name]["output_tokens"] is None:
                profiles[name]["output_tokens"] = providers[row["provider"]]["output_tokens"]
            if row.get("protected_key"):
                profiles[name]["protected_key"] = row["protected_key"]
        if profiles and data["selected"] not in profiles:
            raise ValueError()
        return profiles, data["selected"]
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
        raise ValueError("Saved model profiles are invalid; the stored value has not been overwritten.") from None
