import re


PLACE_REPLACEMENTS = {
    "Mallakaster": "Mallakastër",
    "Tirane": "Tiranë",
    "Vlore": "Vlorë",
    "Korce": "Korçë",
}


def format_public_value(value: object, field_name: str | None = None) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        return ""
    text = _format_units(text)
    if field_name in {None, "location", "object_name"}:
        text = _restore_common_albanian_display(text)
    return text


def format_public_text(value: object) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        return ""
    text = _format_units(text)
    return _restore_common_albanian_display(text)


def _format_units(text: str) -> str:
    text = re.sub(
        r"\b(\d+(?:[.,]\d+)?)\s*\(\s*(\d+(?:[.,]\d+)?)\s*m\s*(?:2|²)?\s*podrum\s*\)",
        lambda match: (
            f"{_decimal(match.group(1))} m², nga të cilat "
            f"{_decimal(match.group(2))} m² podrum"
        ),
        text,
        flags=re.I,
    )
    text = re.sub(
        r"\b(\d+(?:[.,]\d+)?)\s*m\s*(?:2|²)\b",
        lambda match: f"{_decimal(match.group(1))} m²",
        text,
        flags=re.I,
    )
    text = re.sub(r"\bkg/cm\s*(?:2|²)\b", "kg/cm²", text, flags=re.I)
    return text


def _restore_common_albanian_display(text: str) -> str:
    for plain, restored in PLACE_REPLACEMENTS.items():
        text = re.sub(rf"\b{re.escape(plain)}\b", restored, text)
    return text


def _decimal(value: str) -> str:
    return value.replace(",", ".")
