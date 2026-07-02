import re
import unicodedata
from typing import Any

from app.agents.claim_grounding import register_evidence_id

MATERIAL_SECTION_CODE = "quality_and_hidden_works"
MATERIAL_SECTION_TITLE = "Materialet, armatura dhe kontrolli i cilësisë"

PUBLIC_NOISE_FIELDS = {
    "numeric_values",
    "numerical_values",
    "numerical_values_list",
    "number_list",
    "dimension_values",
}
REINFORCEMENT_FIELD_TERMS = (
    "armatur",
    "iron",
    "mass",
    "rebar",
    "reinforcement",
    "steel_bar",
    "weight",
)
MASS_FIELD_TERMS = ("mass", "weight", "pesha", "sasia_e_hekurit")
MASS_PATTERN = re.compile(
    r"(?<!\d)(\d{1,7}(?:[.,]\d{1,3})?)\s*(?:kg|kilogram(?:e|ë)?)\b",
    re.I,
)
CONTEXTLESS_NUMBER_PATTERN = re.compile(r"\d+(?:[.,]\d+)?")

MATERIAL_BOILERPLATE_PATTERNS = (
    re.compile(
        r"\bmaterialet\s+e\s+perdorura.{0,180}\b(?:jane|rezultojne)\s+"
        r"(?:te\s+)?deklaruara?\s+ne\s+dosje\b",
        re.I,
    ),
    re.compile(
        r"\bprovat?\s+laboratorike.{0,180}\b(?:kerkojne|mbeten|duhet)\b"
        r".{0,100}\bverifikim",
        re.I,
    ),
    re.compile(r"\bnuk\s+perbejne\s+prova?\s+laboratorike\b", re.I),
    re.compile(
        r"\bnuk\s+(?:u\s+)?(?:identifikuan|paraqiten|gjenden).{0,180}"
        r"\b(?:raporte?\s+laboratorike|certifikata?\s+(?:te\s+)?celikut|"
        r"rezultate?\s+(?:te\s+)?provave?)\b",
        re.I,
    ),
)

ELEMENT_TERMS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("trarët", ("trar", "beam")),
    ("muret", ("mur", "wall")),
    ("kolonat", ("kolon", "column")),
    ("soletat", ("solet", "slab")),
    ("themelet", ("themel", "plint", "foundation")),
)


def build_section_evidence(dossier: object) -> dict[str, Any]:
    if not isinstance(dossier, dict):
        return {}
    registers = dossier.get("registers")
    if not isinstance(registers, dict):
        return {}

    quantities: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for register in ("materials_and_tests", "technical_works"):
        entries = registers.get(register)
        if not isinstance(entries, list):
            continue
        for index, entry in enumerate(entries):
            if not isinstance(entry, dict) or not is_public_register_entry(entry):
                continue
            field = _normalize_key(entry.get("field_name"))
            value = public_register_value(entry)
            match = MASS_PATTERN.search(value)
            if not match or not _is_reinforcement_quantity(field, entry):
                continue
            source_documents = _string_list(entry.get("source_documents"), limit=5)
            key = (_normalize_text(match.group(0)), "|".join(source_documents))
            if key in seen:
                continue
            seen.add(key)
            quantities.append(
                {
                    "field_name": field,
                    "source_value": match.group(0),
                    "display_value": _display_mass(match.group(1)),
                    "elements": _elements_from_sources(source_documents),
                    "source_documents": source_documents,
                    "evidence_id": register_evidence_id(register, index),
                }
            )

    if not quantities:
        return {}
    quantities.sort(key=_quantity_sort_key)
    evidence_ids = list(
        dict.fromkeys(str(item["evidence_id"]) for item in quantities)
    )
    statement = _reinforcement_statement(quantities)
    return {
        "materials_reinforcement": {
            "section_code": MATERIAL_SECTION_CODE,
            "section_title": MATERIAL_SECTION_TITLE,
            "statement": statement,
            "claim_type": "documented_fact",
            "conclusion_level": "proven",
            "evidence_ids": evidence_ids,
            "quantities": quantities,
            "interpretation_limit": (
                "Përshkruaji vetëm si specifikime dhe sasi projektuese të "
                "dokumentuara; mos i paraqit si matje faktike në objekt ose si "
                "rezultat prove laboratorike."
            ),
        }
    }


def is_public_register_entry(entry: object) -> bool:
    if not isinstance(entry, dict):
        return False
    field = _normalize_key(entry.get("field_name"))
    if not field or field in PUBLIC_NOISE_FIELDS:
        return False
    value = public_register_value(entry)
    if not value:
        return False
    if _is_reinforcement_field(field) and _looks_fused_or_unreadable(value):
        return False
    return True


def public_register_value(entry: object) -> str:
    if not isinstance(entry, dict):
        return ""
    field = _normalize_key(entry.get("field_name"))
    original = _clean_text(entry.get("value"))
    normalized = _clean_text(entry.get("normalized_value"))
    if _is_reinforcement_field(field) and normalized and not _looks_fused_or_unreadable(
        normalized
    ):
        return normalized
    return original


def is_material_boilerplate(value: object) -> bool:
    normalized = _normalize_text(value)
    return any(pattern.search(normalized) for pattern in MATERIAL_BOILERPLATE_PATTERNS)


def is_contextless_numeric_statement(value: object) -> bool:
    text = _clean_text(value)
    numbers = CONTEXTLESS_NUMBER_PATTERN.findall(text)
    if len(numbers) < 6:
        return False
    normalized = _normalize_text(text)
    has_unit_or_label = bool(
        re.search(
            r"\b(?:cm|kg|kn|m2|m3|mm|diameter|gjatesi|gjeresi|lartesi|masa|"
            r"sasia|siperfaqe|vellim)\b",
            normalized,
        )
    )
    return not has_unit_or_label


def _is_reinforcement_quantity(field: str, entry: dict[str, Any]) -> bool:
    if not any(term in field for term in MASS_FIELD_TERMS):
        return False
    context = " ".join(
        [
            field,
            _clean_text(entry.get("value")),
            _clean_text(entry.get("normalized_value")),
            " ".join(_string_list(entry.get("source_documents"), limit=5)),
        ]
    )
    normalized = _normalize_text(context)
    return any(term in normalized for term in ("armatur", "hekur", "rebar", "steel"))


def _is_reinforcement_field(field: str) -> bool:
    return any(term in field for term in REINFORCEMENT_FIELD_TERMS)


def _looks_fused_or_unreadable(value: str) -> bool:
    compact = re.sub(r"\s+", "", value)
    if len(compact) < 24:
        return False
    digit_ratio = sum(character.isdigit() for character in compact) / len(compact)
    long_digit_run = bool(re.search(r"\d{7,}", compact))
    has_readable_units = bool(re.search(r"\b(?:cm|kg|kn|m|mm)\b", value, re.I))
    if has_readable_units and value.count(",") >= 2:
        return long_digit_run
    return digit_ratio >= 0.48 or long_digit_run


def _elements_from_sources(source_documents: list[str]) -> list[str]:
    normalized = _normalize_text(" ".join(source_documents))
    return [
        label
        for label, terms in ELEMENT_TERMS
        if any(term in normalized for term in terms)
    ]


def _reinforcement_statement(quantities: list[dict[str, Any]]) -> str:
    elements = list(
        dict.fromkeys(
            element
            for quantity in quantities
            for element in quantity.get("elements", [])
            if element
        )
    )
    element_text = f" për {_join_albanian(elements)}" if elements else ""
    values = _join_albanian(
        [str(quantity["display_value"]) for quantity in quantities]
    )
    return (
        f"Dosja përmban tabela dhe specifikime armature{element_text}, "
        f"përfshirë sasi të dokumentuara prej {values}. Këto dokumente "
        "provojnë ekzistencën e specifikimeve dhe sasive projektuese."
    )


def _display_mass(raw_number: str) -> str:
    normalized = raw_number.replace(" ", "").replace(",", ".")
    try:
        number = float(normalized)
    except ValueError:
        return f"{raw_number} kg"
    if number.is_integer():
        return f"{int(number):,} kg"
    return f"{number:,.3f}".rstrip("0").rstrip(".") + " kg"


def _quantity_sort_key(item: dict[str, Any]) -> tuple[float, str]:
    value = str(item.get("display_value") or "").replace(",", "")
    match = re.search(r"\d+(?:\.\d+)?", value)
    amount = float(match.group(0)) if match else 0.0
    return (-amount, str(item.get("evidence_id") or ""))


def _join_albanian(items: list[str]) -> str:
    values = [item for item in items if item]
    if len(values) <= 1:
        return values[0] if values else ""
    return f"{', '.join(values[:-1])} dhe {values[-1]}"


def _normalize_key(value: object) -> str:
    normalized = _normalize_text(value)
    return re.sub(r"[^a-z0-9]+", "_", normalized).strip("_")


def _normalize_text(value: object) -> str:
    text = unicodedata.normalize("NFKD", _clean_text(value))
    return "".join(character for character in text if not unicodedata.combining(character)).lower()


def _clean_text(value: object) -> str:
    return " ".join(str(value or "").split())


def _string_list(value: object, *, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    return [_clean_text(item) for item in value if _clean_text(item)][:limit]
