import re

SECRET_REPLACEMENT = "[REDACTED_SECRET]"

_SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bgsk_[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bAIza[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\b[0-9]{8,}:[A-Za-z0-9_-]{24,}\b"),
    re.compile(
        r"(?i)\b(?:api[_ -]?key|token|secret|celes|çeles|çelës)\b"
        r"\s*(?:=|:|eshte|është)?\s*[A-Za-z0-9_.-]{20,}"
    ),
)


def contains_likely_secret(value: str) -> bool:
    return any(pattern.search(value) is not None for pattern in _SECRET_PATTERNS)


def redact_likely_secrets(value: str) -> str:
    redacted = value
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub(SECRET_REPLACEMENT, redacted)
    return redacted

