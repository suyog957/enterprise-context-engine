import re

_CORPORATE_TERMS = {
    "corporation": "corp",
    "company": "co",
    "incorporated": "inc",
    "limited": "ltd",
}


def normalize_supplier_name(value: str) -> str:
    normalized = value.casefold().replace("&", " and ")
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
    tokens = [_CORPORATE_TERMS.get(token, token) for token in normalized.split()]
    return " ".join(tokens)


def normalize_identifier(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = re.sub(r"[^a-z0-9]", "", value.casefold())
    return normalized or None
