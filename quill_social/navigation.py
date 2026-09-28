"""Validated, account-specific timeline and field presentation preferences."""

from __future__ import annotations


def sanitize_navigation(value: object) -> dict:
    if not isinstance(value, dict):
        return {}
    result = {}
    for account, options in value.items():
        if not isinstance(account, str) or not isinstance(options, dict):
            continue
        clean = {}
        for key in ("order", "hidden", "fields", "unified_sources"):
            if isinstance(options.get(key), list):
                clean[key] = list(dict.fromkeys(x for x in options[key] if isinstance(x, str)))
        labels = options.get("labels", {})
        if isinstance(labels, dict):
            clean["labels"] = {key: text.strip() for key, text in labels.items()
                               if isinstance(key, str) and isinstance(text, str) and text.strip()}
        result[account] = clean
    return result


def ordered_scopes(options: dict, defaults: list | tuple) -> list[str]:
    order = list(dict.fromkeys([x for x in options.get("order", []) if x in defaults] + list(defaults)))
    hidden = options.get("hidden", [])
    # Home is always available as a safe starting point.
    return [x for x in order if x == "home:all" or x not in hidden]
