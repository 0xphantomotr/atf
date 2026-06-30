import re
import unicodedata
from collections import defaultdict
from datetime import datetime
from typing import Any

REGISTER_STAKEHOLDERS = "stakeholders"
REGISTER_PERMITS = "permits_property_licenses"
REGISTER_PARAMETERS = "project_parameters"
REGISTER_CHRONOLOGY = "construction_chronology"
REGISTER_TECHNICAL = "technical_works"
REGISTER_MATERIALS = "materials_and_tests"
REGISTER_ECONOMIC = "contracts_and_economics"
REGISTER_DECLARATIONS = "declarations_and_conclusions"
REGISTER_OTHER = "supporting_evidence"

REGISTER_ORDER = (
    REGISTER_STAKEHOLDERS,
    REGISTER_PERMITS,
    REGISTER_PARAMETERS,
    REGISTER_CHRONOLOGY,
    REGISTER_TECHNICAL,
    REGISTER_MATERIALS,
    REGISTER_ECONOMIC,
    REGISTER_DECLARATIONS,
    REGISTER_OTHER,
)

CONTRACT_DOCUMENT_TYPES = frozenset(
    {
        "contract_and_related_acts",
        "supervisor_contract",
        "contractor_contract",
        "kolaudator_contract",
    }
)
PERMIT_FIELDS = frozenset(
    {
        "construction_permit_number",
        "construction_permit_protocol",
        "construction_permit_date",
        "development_permit_number",
        "development_permit_protocol",
        "development_permit_date",
    }
)
PERMIT_DOCUMENT_TYPES = frozenset({"construction_permit", "development_permit"})
PERMIT_CONTEXT_TERMS = (
    "leje ndertimi",
    "leja e ndertimit",
    "lejes se ndertimit",
    "leje zhvillimi",
    "leja e zhvillimit",
    "lejes se zhvillimit",
    "building permit",
    "construction permit",
    "development permit",
)
RICH_PERMIT_CONTEXT_TERMS = (
    "prot",
    "protokoll",
    "date",
    "dat",
    "vendim",
)

FIELD_ALIASES = {
    "project_name": "object_name",
    "project_title": "object_name",
    "object": "object_name",
    "objekt": "object_name",
    "objekti": "object_name",
    "emri_objektit": "object_name",
    "emri_i_objektit": "object_name",
    "emertimi_objektit": "object_name",
    "emertimi_i_objektit": "object_name",
    "object_location": "location",
    "project_location": "location",
    "vendndodhja": "location",
    "vendndodhja_e_objektit": "location",
    "developer": "investor",
    "client": "investor",
    "porositesi": "investor",
    "zhvilluesi": "investor",
    "investitori": "investor",
    "builder": "contractor",
    "construction_company": "contractor",
    "implementing_company": "contractor",
    "sipermarresi": "contractor",
    "sipermarresi_i_punimeve": "contractor",
    "subjekti_ndertues": "contractor",
    "zbatuesi": "contractor",
    "supervision_engineer": "supervisor",
    "supervising_engineer": "supervisor",
    "mbikeqyresi": "supervisor",
    "mbikqyresi": "supervisor",
    "supervizori": "supervisor",
    "design_company": "designer",
    "projektuesi": "designer",
    "auditor": "kolaudator",
    "technical_auditor": "kolaudator",
    "kolaudatori": "kolaudator",
    "investor_name": "investor",
    "investor_name_text": "investor",
    "investor_text": "investor",
    "emri_investitorit": "investor",
    "contractor_name": "contractor",
    "contractor_name_text": "contractor",
    "contractor_text": "contractor",
    "emri_sipermarresit": "contractor",
    "emri_sipermarresit_text": "contractor",
    "supervisor_name": "supervisor",
    "supervisor_name_text": "supervisor",
    "supervisor_text": "supervisor",
    "emri_mbikqyresit": "supervisor",
    "emri_mbikeqyresit": "supervisor",
    "emri_mbikqyresit_text": "supervisor",
    "emri_mbikeqyresit_text": "supervisor",
    "kolaudator_name": "kolaudator",
    "kolaudator_name_text": "kolaudator",
    "kolaudator_text": "kolaudator",
    "emri_kolaudatorit": "kolaudator",
    "emri_kolaudatorit_text": "kolaudator",
    "designer_name": "designer",
    "designer_name_text": "designer",
    "designer_text": "designer",
    "emri_projektuesit": "designer",
    "emri_projektuesit_text": "designer",
    "construction_permit": "construction_permit_number",
    "permit_number": "construction_permit_number",
    "permit_protocol": "construction_permit_protocol",
    "permit_date": "construction_permit_date",
    "building_permit": "construction_permit_number",
    "building_permit_number": "construction_permit_number",
    "building_permit_protocol": "construction_permit_protocol",
    "building_permit_date": "construction_permit_date",
    "leje_ndertimi": "construction_permit_number",
    "leja_ndertimit": "construction_permit_number",
    "leje_ndertimit": "construction_permit_number",
    "leje_ndertimi_date": "construction_permit_date",
    "date_leje_ndertimi": "construction_permit_date",
    "data_lejes_ndertimit": "construction_permit_date",
    "data_e_lejes_se_ndertimit": "construction_permit_date",
    "construction_start_date": "start_date",
    "works_start_date": "start_date",
    "construction_completion_date": "completion_date",
    "works_completion_date": "completion_date",
    "contract_value": "planned_value",
    "approved_value": "planned_value",
    "executed_value": "final_value",
    "final_contract_value": "final_value",
    "date_of_document": "document_date",
    "document_date": "document_date",
    "data_dokumentit": "document_date",
    "data_e_dokumentit": "document_date",
    "document_number": "document_number",
    "nr_dokumentit": "document_number",
    "numri_dokumentit": "document_number",
    "element_name": "work_element",
    "element": "work_element",
    "elementi": "work_element",
    "emri_elementit": "work_element",
    "emertimi_elementit": "work_element",
    "kontrata_sipemarrjes": "contractor_contract_reference",
    "kontrata_sipermarrjes": "contractor_contract_reference",
    "kontrata_e_sipermarrjes": "contractor_contract_reference",
    "kontrate_sipermarrje": "contractor_contract_reference",
    "kontrate_sipermarrjes": "contractor_contract_reference",
    "kontrata_mbikqyresit": "supervisor_contract_reference",
    "kontrata_mbikqyresin": "supervisor_contract_reference",
    "kontrata_mbikeqyresit": "supervisor_contract_reference",
    "kontrata_kolaudatorit": "kolaudator_contract_reference",
    "kontrata_kolaudatorin": "kolaudator_contract_reference",
    "kontrate_kolaudatorit": "kolaudator_contract_reference",
    "kontrate_kolaudatorin": "kolaudator_contract_reference",
}

STAKEHOLDER_FIELDS = {
    "investor",
    "owner",
    "contractor",
    "supervisor",
    "designer",
    "architect",
    "structural_engineer",
    "geotechnical_engineer",
    "electrical_engineer",
    "mechanical_engineer",
    "surveyor",
    "kolaudator",
}
PERMIT_FIELD_TERMS = (
    "permit",
    "property",
    "cadastral",
    "protocol",
    "license",
    "ownership",
    "parcel",
)
PARAMETER_FIELD_TERMS = (
    "area",
    "height",
    "floor",
    "volume",
    "capacity",
    "intensity",
    "coefficient",
    "dimension",
    "structure_type",
    "foundation_type",
    "seismic",
    "geological",
)
CHRONOLOGY_FIELD_TERMS = (
    "date",
    "deadline",
    "duration",
    "start",
    "completion",
    "interruption",
    "extension",
    "handover",
)
ECONOMIC_FIELD_TERMS = (
    "value",
    "amount",
    "cost",
    "price",
    "contract",
    "situation",
    "quantity",
    "invoice",
)
MATERIAL_FIELD_TERMS = (
    "material",
    "concrete",
    "steel",
    "certificate",
    "laboratory",
    "test",
    "quality",
    "sample",
)


def canonical_field_name(value: object) -> str:
    normalized = _normalize_key(value)
    return FIELD_ALIASES.get(normalized, normalized)


def consolidate_project_registers(
    *,
    analyses: object,
    documents: object,
    document_records: list[dict[str, Any]],
    canonical_facts: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    if not isinstance(analyses, list):
        analyses = []
    if not isinstance(documents, list):
        documents = []

    documents_by_version = {
        str(document.get("version_id") or ""): document
        for document in documents
        if isinstance(document, dict) and document.get("version_id")
    }
    records_by_version = {
        str(record.get("file_version_id") or ""): record
        for record in document_records
        if record.get("file_version_id")
    }
    records_by_filename = {
        str(record.get("filename") or ""): record
        for record in document_records
    }
    grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
    analyzed_versions: set[str] = set()

    for analysis in analyses:
        if not isinstance(analysis, dict):
            continue
        version_id = str(analysis.get("file_version_id") or "")
        document = documents_by_version.get(version_id)
        if not isinstance(document, dict):
            continue
        filename = str(document.get("original_filename") or "")
        record = records_by_version.get(version_id) or records_by_filename.get(filename)
        if not _record_is_usable(record):
            continue
        if analysis.get("file_sha256") and document.get("sha256_hash"):
            if str(analysis["file_sha256"]) != str(document["sha256_hash"]):
                continue
        analyzed_versions.add(version_id)
        claims = analysis.get("claims")
        if not isinstance(claims, list):
            continue
        for claim in claims:
            _merge_claim_into_registers(
                grouped,
                claim=claim,
                analysis=analysis,
                document=document,
                record=record,
            )

    registers = {register: [] for register in REGISTER_ORDER}
    for entry in grouped.values():
        _finalize_entry(entry)
        registers[entry.pop("register")].append(entry)
    _add_missing_canonical_entries(registers, canonical_facts)
    for register_name, entries in registers.items():
        entries.sort(key=lambda item: _entry_sort_key(register_name, item))

    eligible_versions = {
        str(document.get("version_id") or "")
        for document in documents
        if isinstance(document, dict)
        and document.get("version_id")
        and _record_is_usable(
            records_by_version.get(str(document.get("version_id") or ""))
            or records_by_filename.get(str(document.get("original_filename") or ""))
        )
    }
    coverage = _evidence_coverage(
        registers,
        analyzed_versions=analyzed_versions,
        eligible_versions=eligible_versions,
    )
    integrity_issues = _integrity_issues(canonical_facts)
    if coverage["unanalyzed_document_count"]:
        integrity_issues.append(
            {
                "code": "DOSSIER-ANALYSIS-COVERAGE",
                "severity": "major",
                "description": (
                    "Një ose më shumë dokumente të lexueshme nuk kanë analizë të "
                    "përfunduar për versionin aktual."
                ),
                "unanalyzed_document_count": coverage["unanalyzed_document_count"],
            }
        )
    return {
        "registers": registers,
        "economic_summary": _economic_summary(canonical_facts, registers),
        "evidence_coverage": coverage,
        "integrity_issues": integrity_issues,
    }


def _merge_claim_into_registers(
    grouped: dict[tuple[str, str, str], dict[str, Any]],
    *,
    claim: object,
    analysis: dict[str, Any],
    document: dict[str, Any],
    record: dict[str, Any],
) -> None:
    if not isinstance(claim, dict):
        return
    evidence = [
        item
        for item in claim.get("evidence", [])
        if isinstance(item, dict) and item.get("excerpt_verified") is True
    ]
    if not evidence:
        return
    field = canonical_field_name(claim.get("field_name"))
    category = _normalize_key(claim.get("category")) or "other"
    value = _clean_value(claim.get("original_value"))
    normalized_value = _clean_value(claim.get("normalized_value"))
    if not field or not value:
        return
    if not _claim_allowed_for_document(
        field,
        category=category,
        document_type=str(document.get("document_type") or ""),
        value=value,
        evidence_text=" ".join(
            _clean_value(item.get("supporting_excerpt")) for item in evidence[:6]
        ),
    ):
        return

    source = {
        "claim_id": claim.get("claim_id"),
        "extraction_method": claim.get("extraction_method"),
        "analysis_run_id": analysis.get("analysis_run_id"),
        "file_version_id": analysis.get("file_version_id"),
        "file_sha256": analysis.get("file_sha256"),
        "source_document": document.get("original_filename"),
        "document_type": document.get("document_type"),
        "chunk_references": [
            {
                "chunk_id": item.get("chunk_id"),
                "chunk_index": item.get("chunk_index"),
                "page_start": item.get("page_start"),
                "page_end": item.get("page_end"),
                "coordinates": dict(item.get("coordinates") or {}),
                "excerpt": _clean_value(item.get("supporting_excerpt"))[:300],
            }
            for item in evidence[:6]
        ],
    }
    for register in _register_names(category, field):
        key = (register, field, _normalize_value(normalized_value or value))
        existing = grouped.get(key)
        if existing is None:
            grouped[key] = {
                "register": register,
                "category": category,
                "field_name": field,
                "value": value,
                "normalized_value": normalized_value or None,
                "extraction_confidence": _confidence(claim.get("confidence")),
                "source_authority": _confidence(record.get("authority_score")),
                "classification_confidence": _confidence(
                    document.get("classification_confidence")
                ),
                "sources": [source],
            }
            continue
        existing["extraction_confidence"] = max(
            existing["extraction_confidence"],
            _confidence(claim.get("confidence")),
        )
        existing["source_authority"] = max(
            existing["source_authority"],
            _confidence(record.get("authority_score")),
        )
        existing["classification_confidence"] = max(
            existing["classification_confidence"],
            _confidence(document.get("classification_confidence")),
        )
        if not any(
            item.get("file_version_id") == source["file_version_id"]
            for item in existing["sources"]
        ):
            existing["sources"].append(source)


def _finalize_entry(entry: dict[str, Any]) -> None:
    source_documents = sorted(
        {
            str(source.get("source_document") or "")
            for source in entry["sources"]
            if source.get("source_document")
        }
    )
    source_versions = {
        str(source.get("file_version_id") or source.get("source_document") or "")
        for source in entry["sources"]
        if source.get("file_version_id") or source.get("source_document")
    }
    corroboration = min(0.15, max(0, len(source_versions) - 1) * 0.05)
    score = min(
        0.99,
        entry["source_authority"] * 0.57
        + entry["extraction_confidence"] * 0.25
        + entry["classification_confidence"] * 0.1
        + corroboration,
    )
    entry["confidence"] = round(score, 3)
    entry["confidence_level"] = _confidence_level(score)
    entry["source_documents"] = source_documents
    entry["corroborating_source_count"] = len(source_versions)
    del entry["source_authority"]
    del entry["classification_confidence"]


def _add_missing_canonical_entries(
    registers: dict[str, list[dict[str, Any]]],
    canonical_facts: dict[str, dict[str, Any]],
) -> None:
    existing_fields = {
        entry["field_name"] for entries in registers.values() for entry in entries
    }
    for raw_field, fact in canonical_facts.items():
        if not isinstance(fact, dict):
            continue
        field = canonical_field_name(raw_field)
        if not field or field in existing_fields:
            continue
        value = _clean_value(fact.get("value"))
        if not value:
            continue
        register = _register_names("other", field)[0]
        registers[register].append(
            {
                "category": "canonical_fact",
                "field_name": field,
                "value": value,
                "normalized_value": None,
                "extraction_confidence": _confidence(fact.get("confidence")),
                "confidence": _confidence(fact.get("confidence")),
                "confidence_level": fact.get("confidence_level") or "medium",
                "source_documents": list(fact.get("source_documents") or []),
                "corroborating_source_count": int(
                    fact.get("corroborating_source_count") or 0
                ),
                "sources": [
                    {
                        "source_document": item.get("source_document"),
                        "chunk_references": [
                            {
                                "chunk_id": item.get("source_chunk_id"),
                                "chunk_index": item.get("source_chunk_index"),
                                "excerpt": item.get("snippet"),
                            }
                        ],
                    }
                    for item in fact.get("evidence", [])
                    if isinstance(item, dict)
                ],
            }
        )
        existing_fields.add(field)


def _register_names(category: str, field: str) -> list[str]:
    registers: list[str] = []
    if category == "party" or field in STAKEHOLDER_FIELDS or field.endswith("_nipt"):
        registers.append(REGISTER_STAKEHOLDERS)
    if category in {"permit", "property"} or _contains(field, PERMIT_FIELD_TERMS):
        registers.append(REGISTER_PERMITS)
    if category in {"material", "test"} or _contains(field, MATERIAL_FIELD_TERMS):
        registers.append(REGISTER_MATERIALS)
    if category in {"contract", "economic"} or _contains(field, ECONOMIC_FIELD_TERMS):
        registers.append(REGISTER_ECONOMIC)
    if category == "chronology" or _contains(field, CHRONOLOGY_FIELD_TERMS):
        registers.append(REGISTER_CHRONOLOGY)
    if category in {"work_phase", "control_act"}:
        registers.extend((REGISTER_TECHNICAL, REGISTER_CHRONOLOGY))
    if category == "technical":
        if _contains(field, PARAMETER_FIELD_TERMS):
            registers.append(REGISTER_PARAMETERS)
        else:
            registers.append(REGISTER_TECHNICAL)
    if category in {"declaration", "reservation", "conclusion"}:
        registers.append(REGISTER_DECLARATIONS)
    if category == "identity" or _contains(field, PARAMETER_FIELD_TERMS):
        registers.append(REGISTER_PARAMETERS)
    if not registers:
        registers.append(REGISTER_OTHER)
    return list(dict.fromkeys(registers))


def _claim_allowed_for_document(
    field: str,
    *,
    category: str,
    document_type: str,
    value: str,
    evidence_text: str,
) -> bool:
    if document_type in CONTRACT_DOCUMENT_TYPES:
        if field in PERMIT_FIELDS or category in {"permit", "property"}:
            return False
    if field in PERMIT_FIELDS and not permit_claim_has_context(
        field,
        value=value,
        evidence_text=evidence_text,
        document_type=document_type,
    ):
        return False
    return True


def permit_claim_has_context(
    field: str,
    *,
    value: str,
    evidence_text: str,
    document_type: str,
) -> bool:
    if field not in PERMIT_FIELDS:
        return True
    if permit_value_is_bare_reference(value):
        return _has_rich_permit_context(value, evidence_text)
    if document_type in PERMIT_DOCUMENT_TYPES:
        return True
    context = _normalize_value(f"{value} {evidence_text}")
    if any(term in context for term in PERMIT_CONTEXT_TERMS):
        return True
    return False


def permit_value_is_bare_reference(value: str) -> bool:
    compact = re.sub(r"[^a-z0-9]+", "", _normalize_value(value))
    return bool(re.fullmatch(r"(?:nr)?\d{1,8}", compact))


def _has_rich_permit_context(value: str, evidence_text: str) -> bool:
    context = _normalize_value(f"{value} {evidence_text}")
    if not any(term in context for term in PERMIT_CONTEXT_TERMS):
        return False
    if not any(term in context for term in RICH_PERMIT_CONTEXT_TERMS):
        return False
    return len(re.findall(r"\d+", context)) >= 2


def _economic_summary(
    canonical_facts: dict[str, dict[str, Any]],
    registers: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    planned = _canonical_value(canonical_facts, "planned_value")
    final = _canonical_value(canonical_facts, "final_value")
    planned_amount = _money_amount(planned)
    final_amount = _money_amount(final)
    difference = None
    difference_percent = None
    if planned_amount is not None and final_amount is not None:
        difference = round(final_amount - planned_amount, 2)
        if planned_amount:
            difference_percent = round((difference / planned_amount) * 100, 2)
    return {
        "planned_value": planned or None,
        "final_value": final or None,
        "difference": difference,
        "difference_percent": difference_percent,
        "entry_count": len(registers[REGISTER_ECONOMIC]),
        "calculation_status": (
            "calculated" if difference is not None else "insufficient_values"
        ),
    }


def _evidence_coverage(
    registers: dict[str, list[dict[str, Any]]],
    *,
    analyzed_versions: set[str],
    eligible_versions: set[str],
) -> dict[str, Any]:
    by_register = {}
    for register, entries in registers.items():
        source_documents = sorted(
            {
                document
                for entry in entries
                for document in entry.get("source_documents", [])
                if document
            }
        )
        chunk_ids = {
            reference.get("chunk_id")
            for entry in entries
            for source in entry.get("sources", [])
            for reference in source.get("chunk_references", [])
            if reference.get("chunk_id")
        }
        by_register[register] = {
            "entry_count": len(entries),
            "source_document_count": len(source_documents),
            "source_documents": source_documents,
            "source_chunk_count": len(chunk_ids),
            "fields": sorted({entry["field_name"] for entry in entries}),
        }
    eligible_count = len(eligible_versions)
    analyzed_count = len(analyzed_versions & eligible_versions)
    return {
        "eligible_document_count": eligible_count,
        "analyzed_document_count": analyzed_count,
        "unanalyzed_document_count": max(0, eligible_count - analyzed_count),
        "analysis_coverage_ratio": (
            round(analyzed_count / eligible_count, 3) if eligible_count else 0.0
        ),
        "by_register": by_register,
    }


def _integrity_issues(
    canonical_facts: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    start = _parse_date(_canonical_value(canonical_facts, "start_date"))
    completion = _parse_date(_canonical_value(canonical_facts, "completion_date"))
    if start is not None and completion is not None and start > completion:
        issues.append(
            {
                "code": "DOSSIER-CHRONOLOGY-ORDER",
                "severity": "major",
                "description": "Data e fillimit rezulton pas datës së përfundimit.",
                "start_date": start.strftime("%d.%m.%Y"),
                "completion_date": completion.strftime("%d.%m.%Y"),
            }
        )
    return issues


def _entry_sort_key(register: str, entry: dict[str, Any]) -> tuple[Any, ...]:
    if register == REGISTER_CHRONOLOGY:
        parsed = _parse_date(str(entry.get("normalized_value") or entry.get("value") or ""))
        date_key = parsed or datetime.max
        return (date_key, str(entry.get("field_name")), str(entry.get("value")))
    return (str(entry.get("field_name")), str(entry.get("value")))


def _record_is_usable(record: object) -> bool:
    return isinstance(record, dict) and record.get("role") not in {
        "foreign_project_reference",
        "style_reference",
        "unreadable",
    }


def _contains(value: str, terms: tuple[str, ...]) -> bool:
    return any(term in value for term in terms)


def _canonical_value(canonical_facts: dict[str, dict[str, Any]], field: str) -> str:
    fact = canonical_facts.get(field)
    return _clean_value(fact.get("value")) if isinstance(fact, dict) else ""


def _money_amount(value: str) -> float | None:
    if not value:
        return None
    numeric = re.sub(r"[^0-9,.-]", "", value)
    if not numeric:
        return None
    if numeric.count(".") > 1 and "," not in numeric:
        numeric = numeric.replace(".", "")
    elif "," in numeric and "." in numeric:
        if numeric.rfind(",") > numeric.rfind("."):
            numeric = numeric.replace(".", "").replace(",", ".")
        else:
            numeric = numeric.replace(",", "")
    elif "," in numeric:
        parts = numeric.split(",")
        numeric = "".join(parts) if len(parts[-1]) == 3 else numeric.replace(",", ".")
    elif "." in numeric and len(numeric.rsplit(".", 1)[-1]) == 3:
        numeric = numeric.replace(".", "")
    try:
        return float(numeric)
    except ValueError:
        return None


def _parse_date(value: str) -> datetime | None:
    for pattern in ("%d.%m.%Y", "%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value.strip(), pattern)
        except ValueError:
            continue
    match = re.search(r"\b\d{1,2}[./-]\d{1,2}[./-]\d{4}\b", value)
    if not match or match.group(0) == value.strip():
        return None
    return _parse_date(match.group(0))


def _normalize_key(value: object) -> str:
    if not isinstance(value, str):
        return ""
    normalized = unicodedata.normalize("NFKD", value)
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    normalized = re.sub(r"[^a-zA-Z0-9]+", "_", normalized.lower())
    return normalized.strip("_")[:128]


def _normalize_value(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    return " ".join(normalized.casefold().split())


def _clean_value(value: object) -> str:
    return " ".join(value.split()) if isinstance(value, str) else ""


def _confidence(value: object) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return 0.0
    return max(0.0, min(1.0, float(value)))


def _confidence_level(score: float) -> str:
    if score >= 0.85:
        return "high"
    if score >= 0.65:
        return "medium"
    return "low"
