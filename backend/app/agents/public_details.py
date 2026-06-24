import re
from typing import Any

from app.agents.claim_grounding import register_evidence_id
from app.agents.dossier_consolidation import STAKEHOLDER_FIELDS, canonical_field_name

REGISTER_PRIORITY = {
    "permits_property_licenses": 1,
    "contracts_and_economics": 2,
    "construction_chronology": 3,
    "project_parameters": 4,
    "materials_and_tests": 5,
    "technical_works": 6,
}

GROUP_LIMITS = {
    "permit_property": 5,
    "contracts_economic": 5,
    "chronology": 5,
    "technical_parameters": 5,
    "materials_tests": 5,
}

EXACT_FIELD_PRIORITY = {
    "construction_permit_number": ("permit_property", 1),
    "construction_permit_protocol": ("permit_property", 1),
    "construction_permit_date": ("permit_property", 1),
    "development_permit_number": ("permit_property", 2),
    "development_permit_protocol": ("permit_property", 2),
    "development_permit_date": ("permit_property", 2),
    "property_number": ("permit_property", 2),
    "cadastral_zone": ("permit_property", 2),
    "parcel_number": ("permit_property", 3),
    "ownership_certificate": ("permit_property", 3),
    "contractor_contract_reference": ("contracts_economic", 1),
    "supervisor_contract_reference": ("contracts_economic", 1),
    "kolaudator_contract_reference": ("contracts_economic", 1),
    "planned_value": ("contracts_economic", 2),
    "final_value": ("contracts_economic", 2),
    "contract_value": ("contracts_economic", 2),
    "contract_date": ("contracts_economic", 3),
    "contract_deadline": ("contracts_economic", 3),
    "start_date": ("chronology", 1),
    "completion_date": ("chronology", 1),
    "site_handover_date": ("chronology", 2),
    "setting_out_date": ("chronology", 2),
    "foundation_completion_date": ("chronology", 2),
    "structural_frame_completion_date": ("chronology", 2),
    "facade_completion_date": ("chronology", 2),
    "external_system_completion_date": ("chronology", 2),
    "total_construction_area": ("technical_parameters", 1),
    "construction_area": ("technical_parameters", 1),
    "building_area": ("technical_parameters", 1),
    "basement_area": ("technical_parameters", 2),
    "floor_count": ("technical_parameters", 2),
    "structure_type": ("technical_parameters", 2),
    "foundation_type": ("technical_parameters", 2),
    "bearing_capacity": ("technical_parameters", 2),
    "seismic_category": ("technical_parameters", 3),
    "concrete_class": ("materials_tests", 1),
    "steel_grade": ("materials_tests", 1),
    "protective_layer": ("materials_tests", 2),
    "material_certificate": ("materials_tests", 2),
    "concrete_test_result": ("materials_tests", 2),
    "steel_test_result": ("materials_tests", 2),
}

TERM_FIELD_PRIORITY = (
    ("permit_property", 2, ("permit", "protocol", "cadastral", "property", "parcel")),
    ("contracts_economic", 2, ("contract", "value", "amount", "cost", "price")),
    ("chronology", 3, ("date", "deadline", "duration", "start", "completion")),
    (
        "technical_parameters",
        3,
        ("area", "floor", "height", "foundation", "geological", "seismic"),
    ),
    (
        "materials_tests",
        3,
        ("concrete", "steel", "material", "certificate", "test", "quality", "layer"),
    ),
)

NON_BLOCKING_FIELD_TERMS = (
    "legal_representative",
    "representative",
    "administrator",
    "authorized_person",
)


def select_required_public_details(
    dossier: object,
    *,
    max_items: int = 24,
) -> list[dict[str, Any]]:
    if not isinstance(dossier, dict):
        return []
    registers = dossier.get("registers")
    if not isinstance(registers, dict):
        return []

    candidates: list[dict[str, Any]] = []
    for register_name, entries in registers.items():
        if not isinstance(entries, list):
            continue
        register = str(register_name)
        for index, entry in enumerate(entries):
            if not isinstance(entry, dict):
                continue
            value = _clean_text(entry.get("value"))
            field = canonical_field_name(_clean_text(entry.get("field_name")))
            if not field or not value:
                continue
            if field in STAKEHOLDER_FIELDS:
                continue
            if _is_non_blocking_role_detail(field):
                continue
            group, priority = _field_group_priority(register, field)
            if not group:
                continue
            candidates.append(
                {
                    "evidence_id": register_evidence_id(register, index),
                    "group": group,
                    "register": register,
                    "field_name": field,
                    "value": value,
                    "priority": priority,
                    "must_include": priority <= 2,
                    "confidence_level": entry.get("confidence_level"),
                    "source_documents": _string_list(
                        entry.get("source_documents"),
                        limit=4,
                    ),
                }
            )

    candidates.sort(
        key=lambda item: (
            int(item["priority"]),
            REGISTER_PRIORITY.get(str(item["register"]), 99),
            str(item["field_name"]),
            -len(str(item["value"])),
            str(item["value"]),
        )
    )
    selected: list[dict[str, Any]] = []
    group_counts = {group: 0 for group in GROUP_LIMITS}
    seen_values: set[tuple[str, str]] = set()
    for item in candidates:
        group = str(item["group"])
        if group_counts.get(group, 0) >= GROUP_LIMITS.get(group, 3):
            continue
        key = (str(item["field_name"]), _normalize_value(item["value"]))
        if key in seen_values:
            continue
        if any(_is_same_field_duplicate(item, existing) for existing in selected):
            continue
        seen_values.add(key)
        group_counts[group] = group_counts.get(group, 0) + 1
        selected.append(item)
        if len(selected) >= max_items:
            break
    return selected


def _field_group_priority(register: str, field: str) -> tuple[str, int]:
    if field in EXACT_FIELD_PRIORITY:
        return EXACT_FIELD_PRIORITY[field]
    normalized = field.lower()
    for group, priority, terms in TERM_FIELD_PRIORITY:
        if any(term in normalized for term in terms):
            return group, priority
    if register == "permits_property_licenses":
        return "permit_property", 4
    if register == "contracts_and_economics":
        return "contracts_economic", 4
    if register == "construction_chronology":
        return "chronology", 4
    if register == "project_parameters":
        return "technical_parameters", 4
    if register == "materials_and_tests":
        return "materials_tests", 4
    return "", 99


def _is_non_blocking_role_detail(field: str) -> bool:
    return any(term in field for term in NON_BLOCKING_FIELD_TERMS)


def _is_same_field_duplicate(
    left: dict[str, Any],
    right: dict[str, Any],
) -> bool:
    if str(left.get("field_name")) != str(right.get("field_name")):
        return False
    left_value = _normalize_value(left.get("value"))
    right_value = _normalize_value(right.get("value"))
    if not left_value or not right_value:
        return False
    if left_value in right_value or right_value in left_value:
        return True
    left_numbers = set(_number_tokens(left_value))
    right_numbers = set(_number_tokens(right_value))
    return len(left_numbers) >= 2 and (
        left_numbers.issubset(right_numbers) or right_numbers.issubset(left_numbers)
    )


def _clean_text(value: object) -> str:
    return " ".join(str(value or "").split())


def _string_list(value: object, *, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()][:limit]


def _normalize_value(value: object) -> str:
    return "".join(str(value or "").lower().split())


def _number_tokens(value: str) -> list[str]:
    return re.findall(r"\d+", value)
