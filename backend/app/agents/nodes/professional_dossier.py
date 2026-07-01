import re
import unicodedata
from collections import defaultdict
from datetime import datetime
from typing import Any

from app.agents.dossier_consolidation import (
    CONTRACT_DOCUMENT_TYPES,
    PERMIT_FIELDS,
    REGISTER_CHRONOLOGY,
    REGISTER_MATERIALS,
    REGISTER_TECHNICAL,
    canonical_field_name,
    contextual_claim_field_name,
    consolidate_project_registers,
    permit_claim_has_context,
)
from app.agents.state import AuditGraphState
from app.files.status import is_parsed_status

PLACEHOLDER_PATTERN = re.compile(r"(?:\?{2,}|_{3,}|\.{5,}|\bxxx\b|\btodo\b)", re.I)
DATE_PATTERN = re.compile(r"\b([0-3]?\d)[./-]([01]?\d)[./-]((?:19|20)?\d{2})\b")
MONEY_PATTERN = re.compile(
    r"\b(\d{1,3}(?:[.,\s]\d{3})+|\d{4,})\s*(?:lek(?:e|ë)?|all)\b",
    re.I,
)
LICENSE_PATTERN = re.compile(
    r"(?:li[cç](?:enc(?:e|ë)|ens[ëe])?|li[cç]\.?|license)\s*"
    r"(?:profesionale\s*)?(?:nr\.?\s*)?[:.]?\s*"
    r"([A-Z]{1,4}[\s.-]*\d{2,6}(?:/\d+)?)",
    re.I,
)
NIPT_PATTERN = re.compile(r"\bNIPT\s*[:.]?\s*([A-Z]\d{8}[A-Z])\b", re.I)
PROTOCOL_PATTERN = re.compile(
    r"(?:nr\.?\s*)?([A-Za-z0-9]+(?:/[A-Za-z0-9]+)+)\s*prot\.?",
    re.I,
)

CONTROL_ACT_TYPES = {
    "control_act",
    "site_setup_control_act",
    "structure_setting_out_control_act",
    "foundation_completion_and_level_0_00_control_act",
    "level_0_00_control_act",
    "structural_frame_completion_control_act",
    "facade_and_finishing_completion_control_act",
    "external_system_completion_control_act",
}

SOURCE_AUTHORITY = {
    "construction_permit": 1.0,
    "development_permit": 1.0,
    "construction_permit_conformity_declaration": 0.94,
    "contract_and_related_acts": 0.92,
    "supervisor_contract": 0.92,
    "technical_administrative_document_handover_act": 0.92,
    "site_handover_act": 0.9,
    "setting_out_act": 0.9,
    "start_works_minutes": 0.9,
    "start_works_notification": 0.88,
    "start_works_notification_letter": 0.88,
    "start_interruption_extension_completion_minutes": 0.9,
    "completion_minutes": 0.9,
    "technical_declaration": 0.87,
    "daily_site_log": 0.78,
    "project_schedule": 0.74,
    "monthly_situations": 0.84,
    "bill_of_quantities": 0.9,
    "approved_execution_project": 0.9,
    "as_built_project": 0.9,
    "geological_engineering_study": 0.9,
    "seismic_study": 0.9,
    "hidden_works_minutes": 0.88,
    "material_quality_certificate": 0.88,
    "kolaudim_act": 0.1,
    "unknown": 0.45,
}

PHASE_BY_DOCUMENT_TYPE = {
    "site_handover_act": ("site_handover", "Dorëzimi i sheshit të ndërtimit"),
    "start_works_notification": ("works_start", "Njoftimi i fillimit të punimeve"),
    "start_works_notification_letter": ("works_start", "Njoftimi i fillimit të punimeve"),
    "start_works_minutes": ("works_start", "Procesverbali i fillimit të punimeve"),
    "site_setup_control_act": ("site_setup", "Ngritja e kantierit"),
    "setting_out_act": ("setting_out", "Piketimi i objektit"),
    "structure_setting_out_control_act": ("setting_out", "Kontrolli i piketimit"),
    "foundation_completion_and_level_0_00_control_act": (
        "foundations",
        "Përfundimi i themeleve dhe kuota 0.00",
    ),
    "level_0_00_control_act": ("level_0_00", "Kontrolli në kuotën 0.00"),
    "structural_frame_completion_control_act": (
        "structural_frame",
        "Përfundimi i karabinasë",
    ),
    "facade_and_finishing_completion_control_act": (
        "facade_finishes",
        "Përfundimi i fasadave dhe rifiniturave",
    ),
    "external_system_completion_control_act": (
        "external_systems",
        "Përfundimi i sistemeve të jashtme",
    ),
    "completion_minutes": ("works_completion", "Përfundimi i punimeve"),
    "start_interruption_extension_completion_minutes": (
        "contract_timeline",
        "Ecuria kontraktuale e punimeve",
    ),
}

SECTION_BY_DOCUMENT_TYPE = {
    "construction_permit": "legal_and_administrative",
    "development_permit": "legal_and_administrative",
    "construction_permit_conformity_declaration": "legal_and_administrative",
    "contract_and_related_acts": "parties_and_contracts",
    "supervisor_contract": "parties_and_contracts",
    "professional_license": "parties_and_contracts",
    "approved_execution_project": "design_and_parameters",
    "as_built_project": "design_and_parameters",
    "geological_engineering_study": "design_and_parameters",
    "seismic_study": "design_and_parameters",
    "topographic_documentation": "design_and_parameters",
    "daily_site_log": "execution_and_chronology",
    "project_schedule": "execution_and_chronology",
    "site_book": "execution_and_chronology",
    "hidden_works_minutes": "quality_and_hidden_works",
    "material_quality_certificate": "quality_and_hidden_works",
    "bill_of_quantities": "technical_economic",
    "monthly_situations": "technical_economic",
    "technical_declaration": "completion_and_conclusion",
    "maintenance_project": "completion_and_conclusion",
}

LABEL_PATTERNS: tuple[tuple[str, re.Pattern[str], float], ...] = (
    (
        "object_name",
        re.compile(
            r"(?:em[eë]rtimi\s+i\s+objektit|emri\s+i\s+objektit|"
            r"p[eë]r\s+objektin|objektin?|objekti)\s*[:\-]\s*(.+)",
            re.I,
        ),
        0.94,
    ),
    (
        "location",
        re.compile(r"(?:vend(?:n)?dodhja\s+e\s+objektit|adresa)\s*[:\-]\s*(.+)", re.I),
        0.92,
    ),
    (
        "investor",
        re.compile(
            r"(?:investitor(?:i)?|zhvillues(?:i)?|porosit[eë]s(?:i)?)\s*[:\-]\s*(.+)",
            re.I,
        ),
        0.93,
    ),
    (
        "contractor",
        re.compile(
            r"(?:zbatuesi|sip[eë]rmarr[eë]si|subjekti\s+nd[eë]rtues)\s*[:\-]\s*(.+)",
            re.I,
        ),
        0.93,
    ),
    (
        "supervisor",
        re.compile(
            r"(?:mbik[eë]qyr[eë]si(?:\s+i\s+punimeve)?|supervizori)\s*[:\-]\s*(.+)",
            re.I,
        ),
        0.93,
    ),
    (
        "kolaudator",
        re.compile(r"kolaudatori(?:\s+i\s+punimeve)?\s*[:\-]\s*(.+)", re.I),
        0.95,
    ),
    (
        "designer",
        re.compile(r"(?:projektuesi|studio\s+projektuese)\s*[:\-]\s*(.+)", re.I),
        0.88,
    ),
)

METRIC_PATTERNS: tuple[tuple[str, re.Pattern[str], str], ...] = (
    (
        "site_area",
        re.compile(
            r"sip[eë]rfaq(?:ja|e)\s+(?:e\s+)?(?:truallit|tok[eë]s)\s+q[eë]\s+"
            r"zhvillohet[^\d]{0,50}(\d+(?:[.,]\d+)?)\s*m(?:2|²)",
            re.I,
        ),
        "m²",
    ),
    (
        "footprint_area",
        re.compile(
            r"sip[eë]rfaq(?:ja|e)[^\n]{0,50}(?:z[eë]n[eë]|zene)\s+nga\s+"
            r"struktura[^\d]{0,30}(\d+(?:[.,]\d+)?)\s*m(?:2|²)",
            re.I,
        ),
        "m²",
    ),
    (
        "total_construction_area",
        re.compile(
            r"sip[eë]rfaq(?:ja|e)\s+e\s+p[eë]rgjithshme\s+e\s+nd[eë]rtimit"
            r"[^\d]{0,40}(\d+(?:[.,]\d+)?)",
            re.I,
        ),
        "m²",
    ),
    (
        "maximum_height",
        re.compile(r"lart[eë]sia\s+maksimale[^\d]{0,50}(\d+(?:[.,]\d+)?)\s*m\b", re.I),
        "m",
    ),
    (
        "floors_above_ground",
        re.compile(
            r"numri\s+i\s+kateve\s+mbi\s+nivelin\s+e\s+tok[eë]s[^\d]{0,20}(\d+)",
            re.I,
        ),
        "kate",
    ),
    (
        "floors_below_ground",
        re.compile(
            r"numri\s+i\s+kateve\s+n[eë]n\s+nivelin\s+e\s+tok[eë]s[^\d]{0,20}(\d+)",
            re.I,
        ),
        "kate",
    ),
    (
        "soil_bearing_capacity",
        re.compile(r"ngarkesa\s+e\s+lejuar[^\d]{0,40}(\d+(?:[.,]\d+)?)\s*kg/cm2", re.I),
        "kg/cm²",
    ),
    (
        "seismic_intensity",
        re.compile(r"sizmicitet[^\d]{0,30}(\d+(?:[.,]\d+)?)\s*ball", re.I),
        "ballë",
    ),
)

CORE_FIELDS = (
    "object_name",
    "location",
    "investor",
    "contractor",
    "supervisor",
    "kolaudator",
    "construction_permit_number",
    "construction_permit_date",
    "start_date",
    "completion_date",
)
PROJECT_ANCHOR_FIELDS = ("object_name", "location", "investor", "contractor")
PROJECT_CANONICAL_FIELDS = frozenset(
    {
        *CORE_FIELDS,
        "owner",
        "designer",
        "contractor_nipt",
        "investor_nipt",
        "supervisor_license",
        "kolaudator_license",
        "designer_license",
        "development_permit_number",
        "development_permit_protocol",
        "development_permit_date",
        "construction_permit_protocol",
        "property_number",
        "cadastral_zone",
        "site_area",
        "footprint_area",
        "total_construction_area",
        "basement_area",
        "maximum_height",
        "floors_above_ground",
        "floors_below_ground",
        "soil_bearing_capacity",
        "seismic_intensity",
        "planned_value",
        "final_value",
    }
)
PUBLIC_BLOCKING_CONFLICT_FIELDS = frozenset(
    {
        "location",
        "investor",
        "owner",
        "contractor",
        "supervisor",
        "kolaudator",
        "construction_permit_number",
        "construction_permit_protocol",
        "construction_permit_date",
        "development_permit_number",
        "development_permit_protocol",
        "development_permit_date",
        "property_number",
        "cadastral_zone",
    }
)
DOCUMENT_METADATA_FIELDS = frozenset(
    {
        "document_date",
        "document_number",
        "protocol_number",
        "notary_repertory_number",
        "notary_collection_number",
    }
)
WORK_STAGE_FIELDS = frozenset(
    {
        "work_element",
        "phase_name",
        "stage_name",
        "construction_phase",
        "hidden_work_element",
    }
)
CONTRACT_REFERENCE_FIELDS = frozenset(
    {
        "contract_reference",
        "contractor_contract_reference",
        "supervisor_contract_reference",
        "kolaudator_contract_reference",
    }
)


def build_professional_dossier(state: AuditGraphState) -> AuditGraphState:
    state.setdefault("agent_trace", []).append("professional_dossier")
    candidates: dict[str, list[dict[str, Any]]] = defaultdict(list)
    document_records: list[dict[str, Any]] = []
    chronology: list[dict[str, Any]] = []
    technical_observations: list[dict[str, Any]] = []
    style_references: list[dict[str, Any]] = []
    analyses = state.get("document_analyses", [])
    has_persisted_analyses = isinstance(analyses, list) and bool(analyses)

    for document in state.get("documents", []):
        if not isinstance(document, dict):
            continue
        record, new_chronology, observations = _analyse_document(
            document,
            candidates,
            extract_excerpt_facts=not has_persisted_analyses,
        )
        document_records.append(record)
        chronology.extend(new_chronology)
        technical_observations.extend(observations)
        if record["role"] == "style_reference":
            style_references.append(
                {
                    "filename": record["filename"],
                    "document_type": record["document_type"],
                    "allowed_use": "structure_and_professional_style_only",
                }
            )

    persisted_claim_count = _add_persisted_analysis_candidates(
        state.get("document_analyses", []),
        state.get("documents", []),
        candidates,
        document_records,
    )
    if not has_persisted_analyses:
        _add_legacy_fact_candidates(
            state.get("extracted_facts", {}),
            candidates,
            document_records,
        )
    initial_facts, _ = _resolve_canonical_facts(
        candidates,
        canonical_fields=set(PROJECT_ANCHOR_FIELDS),
    )
    project_relations = _assign_project_relations(
        document_records,
        candidates,
        initial_facts,
    )
    canonical_facts, conflicts = _resolve_canonical_facts(
        candidates,
        canonical_fields=PROJECT_CANONICAL_FIELDS,
        conflict_fields=PUBLIC_BLOCKING_CONFLICT_FIELDS,
    )
    user_confirmations = _apply_user_fact_overrides(
        canonical_facts,
        conflicts,
        state.get("user_fact_overrides"),
    )
    scoped_facts = _build_scoped_facts(
        candidates,
        canonical_fields=PROJECT_CANONICAL_FIELDS,
    )
    consolidation = consolidate_project_registers(
        analyses=analyses,
        documents=state.get("documents", []),
        document_records=document_records,
        canonical_facts=canonical_facts,
    )
    registers = consolidation["registers"]
    if has_persisted_analyses:
        chronology = list(registers[REGISTER_CHRONOLOGY])
        technical_observations = list(registers[REGISTER_TECHNICAL]) + list(
            registers[REGISTER_MATERIALS]
        )
    chronology = _deduplicate_chronology(
        [
            event
            for event in chronology
            if project_relations.get(str(event.get("source_document")))
            != "foreign_project_reference"
        ]
    )
    technical_observations = [
        observation
        for observation in technical_observations
        if project_relations.get(str(observation.get("source_document")))
        != "foreign_project_reference"
    ]
    evidence_by_section = _evidence_register(document_records)
    missing_core_fields = [field for field in CORE_FIELDS if field not in canonical_facts]

    state["professional_dossier"] = {
        "canonical_facts": canonical_facts,
        "registers": registers,
        "economic_summary": consolidation["economic_summary"],
        "evidence_coverage": consolidation["evidence_coverage"],
        "integrity_issues": consolidation["integrity_issues"],
        "conflicts": conflicts,
        "scoped_facts": scoped_facts,
        "chronology": chronology,
        "technical_observations": technical_observations[:80],
        "document_records": document_records,
        "evidence_by_section": evidence_by_section,
        "style_references": style_references,
        "user_confirmations": user_confirmations,
        "missing_core_fields": missing_core_fields,
        "summary": {
            "documents_received": len(document_records),
            "documents_analysed": sum(
                1 for record in document_records if record["role"] != "unreadable"
            ),
            "evidence_documents": sum(
                1
                for record in document_records
                if record["role"] in {"authoritative_evidence", "supporting_evidence"}
            ),
            "style_reference_documents": len(style_references),
            "foreign_project_documents": sum(
                1
                for record in document_records
                if record.get("project_relation") == "foreign_project_reference"
            ),
            "canonical_fact_count": len(canonical_facts),
            "persisted_analysis_count": len(state.get("document_analyses", [])),
            "persisted_claim_candidate_count": persisted_claim_count,
            "conflict_count": len(conflicts),
            "scoped_fact_count": sum(len(items) for items in scoped_facts.values()),
            "chronology_event_count": len(chronology),
            "missing_core_field_count": len(missing_core_fields),
            "register_entry_count": sum(len(items) for items in registers.values()),
            "integrity_issue_count": len(consolidation["integrity_issues"]),
        },
        "method": (
            "Nxjerrje sipas llojit të dokumentit, renditje e autoritetit të burimit, "
            "bashkim i vlerave të njëjta dhe ruajtje e konflikteve me citim burimi."
        ),
    }
    return state


def _apply_user_fact_overrides(
    canonical_facts: dict[str, dict[str, Any]],
    conflicts: list[dict[str, Any]],
    raw_overrides: object,
) -> list[dict[str, str]]:
    if not isinstance(raw_overrides, dict):
        return []
    confirmations: list[dict[str, str]] = []
    conflicts_by_field = {
        str(conflict.get("field") or ""): conflict
        for conflict in conflicts
        if isinstance(conflict, dict)
    }
    for raw_field, raw_value in raw_overrides.items():
        field = canonical_field_name(raw_field)
        value = " ".join(str(raw_value or "").split()).strip(" ;")
        if field not in PROJECT_CANONICAL_FIELDS or not value:
            continue
        previous = canonical_facts.get(field)
        alternatives: list[dict[str, Any]] = []
        if isinstance(previous, dict) and str(previous.get("value") or "").strip():
            previous_value = str(previous["value"])
            if not _values_equivalent(field, previous_value, value):
                alternatives.append(
                    {
                        "value": previous_value,
                        "score": previous.get("confidence"),
                        "source_documents": list(previous.get("source_documents") or []),
                    }
                )
            alternatives.extend(
                item
                for item in previous.get("alternatives", [])
                if isinstance(item, dict)
                and not _values_equivalent(field, str(item.get("value") or ""), value)
            )
        canonical_facts[field] = {
            "value": value,
            "confidence": 1.0,
            "confidence_level": "user_confirmed",
            "source_documents": ["Konfirmuar nga përdoruesi"],
            "source_document_types": ["user_confirmation"],
            "evidence": [],
            "corroborating_source_count": 1,
            "alternatives": alternatives[:3],
            "user_confirmed": True,
        }
        conflict = conflicts_by_field.get(field)
        if conflict is not None:
            conflict["selected_value"] = value
            conflict["selected_score"] = 1.0
            conflict["alternatives"] = alternatives[:3]
            conflict["resolution"] = "user_confirmed"
        elif alternatives and field in PUBLIC_BLOCKING_CONFLICT_FIELDS:
            conflict = {
                "field": field,
                "selected_value": value,
                "selected_score": 1.0,
                "alternatives": alternatives[:3],
                "resolution": "user_confirmed",
            }
            conflicts.append(conflict)
            conflicts_by_field[field] = conflict
        confirmations.append({"field": field, "value": value})
    return confirmations


def _analyse_document(
    document: dict[str, Any],
    candidates: dict[str, list[dict[str, Any]]],
    *,
    extract_excerpt_facts: bool = True,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    filename = str(document.get("original_filename") or "Dokument pa emër")
    document_type = str(document.get("document_type") or "unknown")
    parse_status = str(document.get("parse_status") or "unknown")
    text = str(document.get("text_excerpt") or "").strip()
    style_reference = _is_style_reference(filename, document_type, text)
    role = _document_role(parse_status, document_type, style_reference)
    lines = _lines(text)
    before_counts = {field: len(items) for field, items in candidates.items()}

    if extract_excerpt_facts and is_parsed_status(parse_status) and text:
        for line in lines:
            _extract_labeled_values(line, document, candidates, style_reference)
            _extract_permit_values(line, document, candidates, style_reference)
            _extract_economic_values(line, document, candidates, style_reference)
        _extract_location(text, document, candidates, style_reference)
        _extract_role_details(lines, document, candidates, style_reference)
        _extract_technical_metrics(text, document, candidates, style_reference)
        _extract_property_values(text, document, candidates, style_reference)
        _extract_timeline_values(lines, document, candidates, style_reference)

    if is_parsed_status(parse_status) and text:
        if not extract_excerpt_facts:
            _extract_role_details(lines, document, candidates, style_reference)
        _extract_contract_role_values(
            text,
            lines,
            document,
            candidates,
            style_reference,
        )

    extracted_fields = sorted(
        field
        for field, items in candidates.items()
        if len(items) > before_counts.get(field, 0)
    )
    chronology = (
        _document_chronology(document, lines, style_reference)
        if extract_excerpt_facts
        else []
    )
    observations = (
        _technical_observations(document, lines, style_reference)
        if extract_excerpt_facts
        else []
    )
    section = SECTION_BY_DOCUMENT_TYPE.get(document_type)
    if document_type in CONTROL_ACT_TYPES or document_type in PHASE_BY_DOCUMENT_TYPE:
        section = "execution_and_chronology"

    return (
        {
            "file_version_id": document.get("version_id"),
            "file_sha256": document.get("sha256_hash"),
            "filename": filename,
            "document_type": document_type,
            "parse_status": parse_status,
            "classification_confidence": _safe_float(
                document.get("classification_confidence")
            ),
            "role": role,
            "authority_score": round(_source_authority(document_type, style_reference), 3),
            "professional_section": section or "supporting_documentation",
            "extracted_fields": extracted_fields,
            "text_available": bool(text),
        },
        chronology,
        observations,
    )


def _extract_labeled_values(
    line: str,
    document: dict[str, Any],
    candidates: dict[str, list[dict[str, Any]]],
    style_reference: bool,
) -> None:
    for field, pattern, confidence in LABEL_PATTERNS:
        match = pattern.search(line)
        if not match:
            continue
        value = _clean_field_value(field, match.group(1))
        _add_candidate(
            candidates,
            field,
            value,
            document,
            line,
            confidence,
            style_reference,
        )


def _extract_location(
    text: str,
    document: dict[str, Any],
    candidates: dict[str, list[dict[str, Any]]],
    style_reference: bool,
) -> None:
    patterns = (
        re.compile(r"\b(Fshati\s+[^,.;\n]{2,60},\s*Bashkia\s+[^,.;\n]{2,60})", re.I),
        re.compile(r"\b(Lagj(?:ja|ia)\s+[^,.;\n]{2,60},\s*Bashkia\s+[^,.;\n]{2,60})", re.I),
    )
    for pattern in patterns:
        for match in pattern.finditer(text):
            _add_candidate(
                candidates,
                "location",
                _clean_field_value("location", match.group(1)),
                document,
                match.group(0),
                0.88,
                style_reference,
            )


def _extract_role_details(
    lines: list[str],
    document: dict[str, Any],
    candidates: dict[str, list[dict[str, Any]]],
    style_reference: bool,
) -> None:
    role_terms = {
        "contractor": ("zbatues", "sipermarres", "subjekti ndertues"),
        "supervisor": ("mbikeqyres", "mbikqyres", "supervizor"),
        "kolaudator": ("kolaudator",),
        "designer": ("projektues", "arkitekt", "konstruktor"),
    }
    for index, line in enumerate(lines):
        normalized = _normalize(line)
        for role, terms in role_terms.items():
            if not any(term in normalized for term in terms):
                continue
            window = " ".join(lines[max(0, index - 1) : index + 3])
            license_match = _license_for_role(role, window)
            if license_match:
                _add_candidate(
                    candidates,
                    f"{role}_license",
                    _normalize_license(license_match.group(1)),
                    document,
                    window,
                    0.94,
                    style_reference,
                )
            if role == "contractor":
                nipt_match = NIPT_PATTERN.search(window)
                if nipt_match:
                    _add_candidate(
                        candidates,
                        "contractor_nipt",
                        nipt_match.group(1).upper(),
                        document,
                        window,
                        0.96,
                        style_reference,
                    )
            break


def _extract_contract_role_values(
    text: str,
    lines: list[str],
    document: dict[str, Any],
    candidates: dict[str, list[dict[str, Any]]],
    style_reference: bool,
) -> None:
    role = _contract_role_from_document(document, text)
    if not role:
        return

    filename = str(document.get("original_filename") or "")
    party = _party_from_filename(filename) or _party_from_contract_lines(lines, role)
    if party:
        _add_candidate(
            candidates,
            role,
            party,
            document,
            filename,
            0.91,
            style_reference,
        )

    reference = _contract_reference_from_text(text)
    if reference:
        _add_candidate(
            candidates,
            f"{role}_contract_reference",
            reference,
            document,
            reference,
            0.92,
            style_reference,
        )


def _contract_role_from_document(document: dict[str, Any], text: str) -> str | None:
    filename = _normalize(str(document.get("original_filename") or ""))
    document_type = str(document.get("document_type") or "")
    if "kolaudator" in filename:
        return "kolaudator"
    if "mbikeqyres" in filename or "mbikqyres" in filename or "supervizor" in filename:
        return "supervisor"
    if "sipermarres" in filename or "sipemarr" in filename or "zbatues" in filename:
        return "contractor"
    if document_type == "supervisor_contract":
        return "supervisor"
    if document_type not in {"contract_and_related_acts", "supervisor_contract", "unknown"}:
        return None

    first_page = _normalize(text[:2_500])
    if "kontrat" not in first_page:
        return None
    if "kolaudator" in first_page:
        return "kolaudator"
    if "mbikeqyres" in first_page or "mbikqyres" in first_page or "supervizor" in first_page:
        return "supervisor"
    if "sipermarres" in first_page or "sipemarr" in first_page or "zbatues" in first_page:
        return "contractor"
    return None


def _party_from_filename(filename: str) -> str | None:
    stem = re.sub(r"\.[A-Za-z0-9]{2,5}$", "", filename.replace("_", " ")).strip()
    stem = re.sub(r"^\s*\d+(?:\.\d+)*\s*", "", stem)
    match = re.search(
        r"\bme\s+((?:z\.?|znj\.?)?\s*[A-ZÇË][A-Za-zÇËËçë.'\s-]{2,80})",
        stem,
        re.I,
    )
    if not match:
        return None
    return _clean_party_name(match.group(1))


def _party_from_contract_lines(lines: list[str], role: str) -> str | None:
    terms = {
        "contractor": r"(?:sip[eë]rmarr[eë]s(?:i|in)?|zbatues(?:i|in)?)",
        "supervisor": r"(?:mbik[eë]qyr[eë]s(?:i|in)?|supervizor(?:i|in)?)",
        "kolaudator": r"(?:kolaudator(?:i|in)?)",
    }.get(role)
    if not terms:
        return None
    pattern = re.compile(
        terms
        + r"(?:\s+i\s+punimeve)?\s*(?:[:\-]|me\s+)?\s*"
        + r"((?:z\.?|znj\.?|ing\.?|ark\.?)?\s*"
        + r"[A-ZÇË][^,;\n]{2,90}(?:sh\.?\s*p\.?\s*k\.?)?)",
        re.I,
    )
    for line in lines[:80]:
        match = pattern.search(line)
        if match:
            party = _clean_party_name(match.group(1))
            if party:
                return party
    return None


def _contract_reference_from_text(text: str) -> str | None:
    compact = " ".join(text.split())[:5_000]
    match = re.search(
        r"(?:nr\.?\s*)?(?:rep\.?|repertori)\s*[:.]?\s*(\d{1,8})"
        r".{0,80}?(?:nr\.?\s*)?(?:kol\.?|koleksioni)\s*[:.]?\s*(\d{1,8})"
        r".{0,80}?(?:dat[eë]|date)\s*[:.]?\s*"
        r"([0-3]?\d[./-][01]?\d[./-](?:19|20)?\d{2})",
        compact,
        re.I,
    )
    if match:
        rep, kol, date = match.groups()
        dates = _extract_dates(date)
        normalized_date = dates[0] if dates else date
        return f"Nr. Rep. {rep}, Nr. Kol. {kol}, datë {normalized_date}"

    match = re.search(
        r"(?:nr\.?\s*)?(\d{1,8})\s*(?:rep\.?|repertori)"
        r".{0,80}?(?:nr\.?\s*)?(\d{1,8})\s*(?:kol\.?|koleksioni)"
        r".{0,80}?(?:dat[eë]|date)\s*[:.]?\s*"
        r"([0-3]?\d[./-][01]?\d[./-](?:19|20)?\d{2})",
        compact,
        re.I,
    )
    if match:
        rep, kol, date = match.groups()
        dates = _extract_dates(date)
        normalized_date = dates[0] if dates else date
        return f"Nr. Rep. {rep}, Nr. Kol. {kol}, datë {normalized_date}"

    match = re.search(
        r"(?:nr\.?\s*)?(\d{1,8})\s*(?:rep\.?|repertori)"
        r".{0,80}?(?:nr\.?\s*)?(\d{1,8})\s*(?:kol\.?|koleksioni)",
        compact,
        re.I,
    )
    if match:
        rep, kol = match.groups()
        return f"Nr. Rep. {rep}, Nr. Kol. {kol}"
    return None


def _clean_party_name(value: str) -> str | None:
    value = " ".join(value.replace("_", " ").split())
    value = re.sub(r"\b(?:docx?|pdf|kontrat[ëe]?)\b.*$", "", value, flags=re.I)
    value = re.sub(r"^(?:me\s+)", "", value, flags=re.I).strip(" ,.;:-")
    value = re.sub(r"^(z|znj|ing|ark)\.\s*", lambda m: f"{m.group(1)}. ", value, flags=re.I)
    value = value.strip('"“” ,.;:-')
    if len(value) < 3 or PLACEHOLDER_PATTERN.search(value):
        return None
    return value[:120]


def _extract_permit_values(
    line: str,
    document: dict[str, Any],
    candidates: dict[str, list[dict[str, Any]]],
    style_reference: bool,
) -> None:
    normalized = _normalize(line)
    if "leje ndertimi" not in normalized and "leja e ndertimit" not in normalized:
        if "leje zhvill" not in normalized and "leja e zhvill" not in normalized:
            return

    prefix = "development_permit" if "zhvill" in normalized else "construction_permit"
    decision_match = re.search(
        r"vendim\s*(?:nr\.?\s*)?[:.]?\s*([0-9]+(?:[/.-][A-Za-z0-9]+)*)",
        line,
        re.I,
    )
    if not decision_match:
        decision_match = re.search(
            r"leje(?:s|n|\s+e)?\s+(?:s[eë]\s+)?(?:nd[eë]rtimi(?:t)?|zhvillimi(?:t)?)\s*"
            r"(?:nr\.?\s*)?[:.]?\s*([0-9]+(?:[/.-][A-Za-z0-9]+)*)",
            line,
            re.I,
        )
    if decision_match:
        _add_candidate(
            candidates,
            f"{prefix}_number",
            f"Nr. {decision_match.group(1)}",
            document,
            line,
            0.97,
            style_reference,
        )
    protocol_match = PROTOCOL_PATTERN.search(line)
    if protocol_match:
        _add_candidate(
            candidates,
            f"{prefix}_protocol",
            f"Nr. Prot. {protocol_match.group(1)}",
            document,
            line,
            0.97,
            style_reference,
        )
    dates = _extract_dates(line)
    if dates:
        _add_candidate(
            candidates,
            f"{prefix}_date",
            dates[-1],
            document,
            line,
            0.95,
            style_reference,
        )


def _extract_economic_values(
    line: str,
    document: dict[str, Any],
    candidates: dict[str, list[dict[str, Any]]],
    style_reference: bool,
) -> None:
    normalized = _normalize(line)
    money = MONEY_PATTERN.search(line)
    if not money or "vler" not in normalized:
        return
    value = _normalize_money(money.group(1))
    if "preventiv" in normalized or "kontrakt" in normalized:
        field = "planned_value"
    elif "situacion" in normalized or "perfundimtar" in normalized:
        field = "final_value"
    elif "vlera e objektit" in normalized:
        field = "planned_value"
    else:
        return
    _add_candidate(
        candidates,
        field,
        f"{value} lekë",
        document,
        line,
        0.9,
        style_reference,
    )


def _extract_technical_metrics(
    text: str,
    document: dict[str, Any],
    candidates: dict[str, list[dict[str, Any]]],
    style_reference: bool,
) -> None:
    for field, pattern, unit in METRIC_PATTERNS:
        for match in pattern.finditer(text):
            value = match.group(1).replace(",", ".")
            _add_candidate(
                candidates,
                field,
                f"{value} {unit}",
                document,
                match.group(0),
                0.9,
                style_reference,
            )
    for line in _lines(text):
        normalized = _normalize(line)
        if "siperfaqja e tokes qe zhvillohet" in normalized:
            _add_table_metric(
                candidates,
                "site_area",
                line,
                document,
                style_reference,
            )
        elif (
            "siperfaqja e objektit te ri" in normalized
            or "zene nga struktura" in normalized
        ):
            _add_table_metric(
                candidates,
                "footprint_area",
                line,
                document,
                style_reference,
            )
        elif "siperfaqja e pergjithshme e ndertimit" in normalized:
            _add_table_metric(
                candidates,
                "total_construction_area",
                line,
                document,
                style_reference,
            )
            basement = re.search(r"(\d+(?:[.,]\d+)?)\s*m(?:2|²)?\s*podrum", line, re.I)
            if basement:
                _add_candidate(
                    candidates,
                    "basement_area",
                    f"{basement.group(1).replace(',', '.')} m²",
                    document,
                    line,
                    0.92,
                    style_reference,
                )


def _extract_property_values(
    text: str,
    document: dict[str, Any],
    candidates: dict[str, list[dict[str, Any]]],
    style_reference: bool,
) -> None:
    patterns = (
        (
            "property_number",
            re.compile(r"pasuria\s+(?:me\s+)?nr\.?\s*[:.]?\s*([A-Za-z0-9/-]+)", re.I),
            "Pasuria nr. {}",
        ),
        (
            "property_number",
            re.compile(r"nr\.?\s*pasuris[eë]\s*:\s*([A-Za-z0-9/-]+)", re.I),
            "Pasuria nr. {}",
        ),
        (
            "cadastral_zone",
            re.compile(
                r"zon(?:a|en)\s+kadastrale\s+(?:me\s+)?nr\.?\s*[:.]?\s*(\d+)",
                re.I,
            ),
            "Zona kadastrale nr. {}",
        ),
        (
            "cadastral_zone",
            re.compile(r"nr\.?\s*z\.?\s*k\.?\s*:\s*(\d+)", re.I),
            "Zona kadastrale nr. {}",
        ),
    )
    for field, pattern, template in patterns:
        for match in pattern.finditer(text):
            _add_candidate(
                candidates,
                field,
                template.format(match.group(1)),
                document,
                match.group(0),
                0.92,
                style_reference,
            )


def _extract_timeline_values(
    lines: list[str],
    document: dict[str, Any],
    candidates: dict[str, list[dict[str, Any]]],
    style_reference: bool,
) -> None:
    for line in lines:
        normalized = _normalize(line)
        dates = _extract_dates(line)
        if not dates:
            continue
        if ("fillim" in normalized or "fillu" in normalized) and "punim" in normalized:
            field = "start_date"
        elif "perfund" in normalized and "punim" in normalized:
            field = "completion_date"
        else:
            continue
        _add_candidate(
            candidates,
            field,
            dates[0],
            document,
            line,
            0.92,
            style_reference,
        )


def _document_chronology(
    document: dict[str, Any],
    lines: list[str],
    style_reference: bool,
) -> list[dict[str, Any]]:
    document_type = str(document.get("document_type") or "unknown")
    phase = PHASE_BY_DOCUMENT_TYPE.get(document_type)
    if not phase or style_reference:
        return []

    dated_lines = []
    for line in lines:
        normalized = _normalize(line)
        if "leje" in normalized or "prot" in normalized or "kontrate" in normalized:
            continue
        dates = _extract_dates(line)
        if dates:
            dated_lines.append((line, dates))
    if not dated_lines:
        return [
            {
                "phase": phase[0],
                "title": phase[1],
                "date": None,
                "source_document": document.get("original_filename"),
                "document_type": document_type,
            }
        ]

    phase_terms = {
        "works_start": ("fillim", "fillo", "nis"),
        "works_completion": ("perfund",),
        "foundations": ("theme", "0.00"),
        "structural_frame": ("karabina", "struktur"),
        "facade_finishes": ("fasad", "rifinitur"),
        "external_systems": ("sistem", "rrjet", "jasht"),
        "setting_out": ("piket",),
        "site_setup": ("kantier",),
    }.get(phase[0], ())
    preferred = [
        item
        for item in dated_lines
        if any(term in _normalize(item[0]) for term in phase_terms)
    ]
    line, dates = (preferred or dated_lines)[0]
    return [
        {
            "phase": phase[0],
            "title": phase[1],
            "date": dates[0],
            "source_document": document.get("original_filename"),
            "document_type": document_type,
            "evidence": line[:260],
        }
    ]


def _technical_observations(
    document: dict[str, Any],
    lines: list[str],
    style_reference: bool,
) -> list[dict[str, Any]]:
    if style_reference:
        return []
    terms = (
        "punime mask",
        "prova laborator",
        "marka e betonit",
        "certifikat",
        "konform",
        "kuota 0.00",
        "themele",
        "karabina",
        "fasad",
        "rifinitur",
        "rrjet",
        "matje",
        "defekt",
    )
    observations = []
    for line in lines:
        normalized = _normalize(line)
        if not any(term in normalized for term in terms):
            continue
        observations.append(
            {
                "statement": line[:360],
                "source_document": document.get("original_filename"),
                "document_type": document.get("document_type"),
            }
        )
        if len(observations) >= 5:
            break
    return observations


def _add_legacy_fact_candidates(
    extracted_facts: object,
    candidates: dict[str, list[dict[str, Any]]],
    document_records: list[dict[str, Any]],
) -> None:
    if not isinstance(extracted_facts, dict):
        return
    categories = extracted_facts.get("categories", {})
    if not isinstance(categories, dict):
        return
    records_by_name = {record["filename"]: record for record in document_records}
    allowed_fields = {
        "object_name",
        "location",
        "investor",
        "contractor",
        "supervisor",
        "kolaudator",
        "designer",
    }
    for field, items in categories.items():
        if field not in allowed_fields or not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            filename = str(item.get("source_document") or "")
            record = records_by_name.get(filename, {})
            if record.get("role") == "style_reference":
                continue
            value = _clean_field_value(str(field), str(item.get("value") or ""))
            if not value:
                continue
            candidates[str(field)].append(
                {
                    "value": value,
                    "source_document": filename,
                    "document_type": item.get("document_type") or "unknown",
                    "authority": float(record.get("authority_score") or 0.45),
                    "classification_confidence": 0.6,
                    "extraction_confidence": 0.6,
                    "evidence": str(item.get("snippet") or value)[:300],
                    "style_reference": False,
                }
            )


def _add_persisted_analysis_candidates(
    analyses: object,
    documents: object,
    candidates: dict[str, list[dict[str, Any]]],
    document_records: list[dict[str, Any]],
) -> int:
    if not isinstance(analyses, list) or not isinstance(documents, list):
        return 0
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
    added = 0

    for analysis in analyses:
        if not isinstance(analysis, dict):
            continue
        document = documents_by_version.get(str(analysis.get("file_version_id") or ""))
        if not isinstance(document, dict):
            continue
        filename = str(document.get("original_filename") or "")
        record = records_by_version.get(
            str(analysis.get("file_version_id") or "")
        ) or records_by_filename.get(filename)
        if record is None or record.get("role") in {"style_reference", "unreadable"}:
            continue
        claims = analysis.get("claims")
        if not isinstance(claims, list):
            continue

        for claim in claims:
            if not isinstance(claim, dict):
                continue
            verified_evidence = [
                item
                for item in claim.get("evidence", [])
                if isinstance(item, dict) and item.get("excerpt_verified") is True
            ]
            if not verified_evidence:
                continue
            value = str(claim.get("original_value") or "").strip()
            if not value or PLACEHOLDER_PATTERN.search(value):
                continue
            value = " ".join(value.split()).strip(" ;,.")
            if len(value) < 2 or len(value) > 280:
                continue
            evidence = verified_evidence[0]
            snippet = str(evidence.get("supporting_excerpt") or value)
            field = contextual_claim_field_name(
                claim.get("field_name"),
                evidence_text=snippet,
                document_type=str(document.get("document_type") or ""),
            )
            if not field:
                continue
            if not _field_allowed_for_document(
                field,
                document,
                value=value,
                evidence=snippet,
            ):
                continue
            if any(
                item.get("source_document") == filename
                and _normalize_fact_value(field, str(item.get("value") or ""))
                == _normalize_fact_value(field, value)
                for item in candidates[field]
            ):
                continue

            candidates[field].append(
                {
                    "value": value,
                    "source_document": filename,
                    "document_type": document.get("document_type") or "unknown",
                    "authority": float(record.get("authority_score") or 0.45),
                    "classification_confidence": _safe_float(
                        document.get("classification_confidence")
                    )
                    or 0.55,
                    "extraction_confidence": _safe_float(claim.get("confidence")) or 0.5,
                    "evidence": snippet[:300],
                    "source_chunk_id": evidence.get("chunk_id"),
                    "source_chunk_index": evidence.get("chunk_index"),
                    "source_file_version_id": analysis.get("file_version_id"),
                    "analysis_run_id": analysis.get("analysis_run_id"),
                    "style_reference": False,
                }
            )
            if field not in record["extracted_fields"]:
                record["extracted_fields"].append(field)
                record["extracted_fields"].sort()
            added += 1
    return added


def _resolve_canonical_facts(
    candidates: dict[str, list[dict[str, Any]]],
    *,
    canonical_fields: frozenset[str] | set[str] | None = None,
    conflict_fields: frozenset[str] | set[str] | None = None,
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    canonical: dict[str, dict[str, Any]] = {}
    conflicts: list[dict[str, Any]] = []

    for field, field_candidates in sorted(candidates.items()):
        if canonical_fields is not None and field not in canonical_fields:
            continue
        usable = [
            item
            for item in field_candidates
            if not item.get("style_reference")
            and item.get("project_relation") != "foreign_project_reference"
        ]
        if not usable:
            continue
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for candidate in usable:
            key = _normalize_fact_value(field, str(candidate.get("value") or ""))
            if key:
                grouped[key].append(candidate)
        if not grouped:
            continue

        ranked = sorted(
            (_rank_group(field, items) for items in grouped.values()),
            key=lambda item: (item["score"], item["source_count"]),
            reverse=True,
        )
        winner = ranked[0]
        alternatives = [
            {
                "value": item["value"],
                "score": item["score"],
                "source_documents": item["source_documents"],
            }
            for item in ranked[1:4]
        ]
        canonical[field] = {
            "value": winner["value"],
            "confidence": winner["score"],
            "confidence_level": _confidence_level(winner["score"]),
            "source_documents": winner["source_documents"],
            "source_document_types": winner["source_document_types"],
            "evidence": winner["evidence"],
            "corroborating_source_count": winner["source_count"],
            "alternatives": alternatives,
        }
        if alternatives and (conflict_fields is None or field in conflict_fields):
            conflicts.append(
                {
                    "field": field,
                    "selected_value": winner["value"],
                    "selected_score": winner["score"],
                    "alternatives": alternatives,
                    "resolution": "highest_source_authority_and_corroboration",
                }
            )

    return canonical, conflicts


def _build_scoped_facts(
    candidates: dict[str, list[dict[str, Any]]],
    *,
    canonical_fields: frozenset[str] | set[str],
) -> dict[str, list[dict[str, Any]]]:
    scoped: dict[str, list[dict[str, Any]]] = {
        "document_metadata": [],
        "work_stages": [],
        "contract_references": [],
        "supporting_facts": [],
    }
    seen: set[tuple[str, str, str, str]] = set()
    for field, field_candidates in sorted(candidates.items()):
        if field in canonical_fields:
            continue
        scope = _fact_scope(field)
        for candidate in field_candidates:
            if candidate.get("style_reference"):
                continue
            if candidate.get("project_relation") == "foreign_project_reference":
                continue
            value = str(candidate.get("value") or "").strip()
            source_document = str(candidate.get("source_document") or "").strip()
            if not value or not source_document:
                continue
            key = (scope, field, _normalize_fact_value(field, value), source_document)
            if key in seen:
                continue
            seen.add(key)
            scoped[scope].append(
                {
                    "field_name": field,
                    "value": value,
                    "source_document": source_document,
                    "document_type": candidate.get("document_type") or "unknown",
                    "confidence": round(
                        float(candidate.get("extraction_confidence") or 0.0),
                        3,
                    ),
                    "evidence": str(candidate.get("evidence") or "")[:260],
                }
            )
    return {
        scope: items[:80 if scope == "supporting_facts" else 60]
        for scope, items in scoped.items()
        if items
    }


def _fact_scope(field: str) -> str:
    if field in DOCUMENT_METADATA_FIELDS or field.endswith("_document_date"):
        return "document_metadata"
    if field in WORK_STAGE_FIELDS or "element" in field or "phase" in field:
        return "work_stages"
    if field in CONTRACT_REFERENCE_FIELDS or "contract_reference" in field:
        return "contract_references"
    return "supporting_facts"


def _assign_project_relations(
    document_records: list[dict[str, Any]],
    candidates: dict[str, list[dict[str, Any]]],
    initial_facts: dict[str, dict[str, Any]],
) -> dict[str, str]:
    selected = {
        field: str(initial_facts.get(field, {}).get("value") or "")
        for field in PROJECT_ANCHOR_FIELDS
    }
    anchors_by_document: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for field in PROJECT_ANCHOR_FIELDS:
        for candidate in candidates.get(field, []):
            filename = str(candidate.get("source_document") or "")
            value = str(candidate.get("value") or "")
            if filename and value and not candidate.get("style_reference"):
                anchors_by_document[filename].append((field, value))

    relations: dict[str, str] = {}
    for record in document_records:
        filename = str(record.get("filename") or "")
        if record.get("role") == "style_reference":
            relation = "style_reference"
        elif record.get("role") == "unreadable":
            relation = "unreadable"
        elif _foreign_reference_filename(filename):
            relation = "foreign_project_reference"
            record["role"] = "foreign_project_reference"
            record["authority_score"] = 0.1
        else:
            matches = 0
            conflicts = 0
            for field, value in anchors_by_document.get(filename, []):
                selected_value = selected.get(field, "")
                if not selected_value:
                    continue
                if _values_equivalent(field, value, selected_value):
                    matches += 1
                else:
                    conflicts += 1
            if matches >= 2 or (matches >= 1 and conflicts == 0):
                relation = "target_project"
            elif conflicts >= 2:
                relation = "foreign_project_reference"
                record["role"] = "foreign_project_reference"
                record["authority_score"] = 0.1
            else:
                relation = "unlinked_supporting"
        record["project_relation"] = relation
        relations[filename] = relation

    for field_candidates in candidates.values():
        for candidate in field_candidates:
            candidate["project_relation"] = relations.get(
                str(candidate.get("source_document") or ""),
                "unlinked_supporting",
            )
    return relations


def _values_equivalent(field: str, left: str, right: str) -> bool:
    normalized_left = _normalize_fact_value(field, left)
    normalized_right = _normalize_fact_value(field, right)
    if not normalized_left or not normalized_right:
        return False
    if normalized_left == normalized_right:
        return True
    shorter, longer = sorted((normalized_left, normalized_right), key=len)
    return len(shorter) >= 8 and shorter in longer


def _rank_group(field: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    representative = max(
        items,
        key=lambda item: (
            float(item.get("authority") or 0),
            float(item.get("extraction_confidence") or 0),
        ),
    )
    sources = sorted(
        {
            str(item.get("source_document") or "")
            for item in items
            if item.get("source_document")
        }
    )
    source_types = sorted({str(item.get("document_type") or "unknown") for item in items})
    authority = max(float(item.get("authority") or 0.45) for item in items)
    extraction = max(float(item.get("extraction_confidence") or 0.6) for item in items)
    classification = max(
        float(item.get("classification_confidence") or 0.5) for item in items
    )
    corroboration = min(0.15, max(0, len(sources) - 1) * 0.05)
    score = min(
        0.99,
        authority * 0.62 + extraction * 0.23 + classification * 0.1 + corroboration,
    )
    if (
        field
        in {
            "construction_permit_number",
            "construction_permit_protocol",
            "construction_permit_date",
        }
        and authority >= 0.9
    ):
        score = min(0.99, score + 0.04)
    return {
        "value": representative["value"],
        "score": round(score, 3),
        "source_count": len(sources),
        "source_documents": sources,
        "source_document_types": source_types,
        "evidence": [
            {
                "source_document": item.get("source_document"),
                "snippet": str(item.get("evidence") or "")[:260],
                "analysis_run_id": item.get("analysis_run_id"),
                "source_file_version_id": item.get("source_file_version_id"),
                "source_chunk_id": item.get("source_chunk_id"),
                "source_chunk_index": item.get("source_chunk_index"),
            }
            for item in sorted(
                items,
                key=lambda item: float(item.get("authority") or 0),
                reverse=True,
            )[:3]
        ],
    }


def _add_candidate(
    candidates: dict[str, list[dict[str, Any]]],
    field: str,
    value: str | None,
    document: dict[str, Any],
    evidence: str,
    extraction_confidence: float,
    style_reference: bool,
) -> None:
    if not value or PLACEHOLDER_PATTERN.search(value):
        return
    value = " ".join(value.split()).strip(" ;,.")
    if len(value) < 2 or len(value) > 280:
        return
    filename = str(document.get("original_filename") or "")
    if any(
        item.get("source_document") == filename
        and _normalize_fact_value(field, str(item.get("value") or ""))
        == _normalize_fact_value(field, value)
        for item in candidates[field]
    ):
        return
    document_type = str(document.get("document_type") or "unknown")
    candidates[field].append(
        {
            "value": value,
            "source_document": filename,
            "document_type": document_type,
            "authority": _source_authority(document_type, style_reference),
            "classification_confidence": _safe_float(
                document.get("classification_confidence")
            )
            or 0.55,
            "extraction_confidence": extraction_confidence,
            "evidence": " ".join(evidence.split())[:300],
            "source_file_version_id": document.get("version_id"),
            "style_reference": style_reference,
        }
    )


def _clean_field_value(field: str, value: str) -> str | None:
    value = value.strip().strip('"“”')
    if field == "object_name":
        value = re.sub(
            r"^(?:emri|em[eë]rtimi)\s+i\s+objektit\s*:\s*",
            "",
            value,
            flags=re.I,
        )
    stop_patterns = {
        "object_name": (
            r",?\s*(?:me\s+adres[eë]|n[eë]\s+Fshat|Fshati\s+|me\s+Leje|"
            r"Investitori\s*:|q[eë]\s+ndodhet).*$"
        ),
        "location": r",?\s*(?:me\s+Leje|me\s+Vendim|Investitori\s*:).*$",
        "investor": (
            r",?\s*(?:i\s+dat[eë]lindjes|me\s+NID|me\s+nr\.?\s+personal|"
            r"k[eë]tu\s+e).*$"
        ),
        "contractor": (
            r",?\s*(?:me\s+Li[cç]|me\s+NIPT|p[eë]rfaq[eë]suar|me\s+seli).*$"
        ),
        "supervisor": (
            r",?\s*(?:me\s+profesion|i\s+pajisur|me\s+Li[cç]|"
            r"me\s+nr\.?\s+Li[cç]).*$"
        ),
        "kolaudator": (
            r",?\s*(?:me\s+profesion|i\s+pajisur|me\s+Li[cç]|"
            r"me\s+nr\.?\s+Li[cç]|Identifikuar).*$"
        ),
        "designer": r",?\s*(?:me\s+Li[cç]|Li[cç]\s*:).*$",
    }
    pattern = stop_patterns.get(field)
    if pattern:
        value = re.sub(pattern, "", value, flags=re.I).strip()
    value = value.split("|", 1)[0].strip()
    value = re.sub(r",?\s+me\s*$", "", value, flags=re.I)
    value = value.strip('"“” ;,.')
    if field == "designer":
        normalized = _normalize(value)
        narrative_terms = (
            "zerat e punimeve",
            "punimeve jane",
            "realizuar sipas",
            "ndryshimeve te miratuara",
            "organet perkatese",
        )
        if len(value) > 100 or any(term in normalized for term in narrative_terms):
            return None
    if not value or PLACEHOLDER_PATTERN.search(value):
        return None
    return value[:220]


def _is_style_reference(filename: str, document_type: str, text: str) -> bool:
    if document_type == "kolaudim_act":
        return True
    normalized_name = _normalize(filename)
    return (
        "akt kolaudimi" in normalized_name
        and (filename.lower().startswith("x.") or bool(PLACEHOLDER_PATTERN.search(text)))
    )


def _foreign_reference_filename(filename: str) -> bool:
    basename = filename.replace("\\", "/").rsplit("/", 1)[-1]
    normalized = _normalize(basename)
    return bool(re.match(r"^x(?:\.|[ _-])", basename, re.I)) or normalized.startswith(
        "x a "
    )


def _document_role(parse_status: str, document_type: str, style_reference: bool) -> str:
    if not is_parsed_status(parse_status):
        return "unreadable"
    if style_reference:
        return "style_reference"
    if _source_authority(document_type, False) >= 0.88:
        return "authoritative_evidence"
    return "supporting_evidence"


def _source_authority(document_type: str, style_reference: bool) -> float:
    if style_reference:
        return 0.1
    if document_type in CONTROL_ACT_TYPES:
        return 0.93
    return SOURCE_AUTHORITY.get(document_type, 0.62)


def _evidence_register(document_records: list[dict[str, Any]]) -> dict[str, list[str]]:
    register: dict[str, list[str]] = defaultdict(list)
    for record in document_records:
        if record["role"] in {
            "style_reference",
            "unreadable",
            "foreign_project_reference",
        }:
            continue
        register[str(record["professional_section"])].append(str(record["filename"]))
    return {
        section: sorted(set(filenames))
        for section, filenames in sorted(register.items())
    }


def _deduplicate_chronology(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    result = []
    for event in events:
        key = (event.get("phase"), event.get("date"), event.get("source_document"))
        if key in seen:
            continue
        seen.add(key)
        result.append(event)
    return sorted(
        result,
        key=lambda item: (_date_sort_key(item.get("date")), str(item.get("phase"))),
    )


def _extract_dates(value: str) -> list[str]:
    dates = []
    for match in DATE_PATTERN.finditer(value):
        day, month, year = match.groups()
        if len(year) == 2:
            year = "20" + year
        try:
            parsed = datetime(int(year), int(month), int(day))
        except ValueError:
            continue
        dates.append(parsed.strftime("%d.%m.%Y"))
    return dates


def _date_sort_key(value: object) -> tuple[int, int, int]:
    try:
        parsed = datetime.strptime(str(value), "%d.%m.%Y")
    except (TypeError, ValueError):
        return (9999, 12, 31)
    return (parsed.year, parsed.month, parsed.day)


def _normalize_fact_value(field: str, value: str) -> str:
    if field.endswith("_date") or field in {"start_date", "completion_date"}:
        dates = _extract_dates(value)
        if dates:
            return dates[0]
    normalized = _normalize(value)
    normalized = re.sub(
        r"\b(z|znj|ing|inxh|ark|shoqeria|shpk|sh p k|nr|prot)\b",
        " ",
        normalized,
    )
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
    return " ".join(normalized.split())


def _field_allowed_for_document(
    field: str,
    document: dict[str, Any],
    *,
    value: str = "",
    evidence: str = "",
) -> bool:
    document_type = str(document.get("document_type") or "")
    if document_type in CONTRACT_DOCUMENT_TYPES and field in PERMIT_FIELDS:
        return False
    if field in PERMIT_FIELDS and not permit_claim_has_context(
        field,
        value=value,
        evidence_text=evidence,
        document_type=document_type,
    ):
        return False
    return True


def _normalize(value: str) -> str:
    value = unicodedata.normalize("NFKD", value)
    value = "".join(char for char in value if not unicodedata.combining(char))
    return " ".join(value.lower().split())


def _normalize_license(value: str) -> str:
    return re.sub(r"\s+", "", value.upper()).replace(".", "-")


def _license_for_role(role: str, text: str) -> re.Match[str] | None:
    matches = list(LICENSE_PATTERN.finditer(text))
    if not matches:
        return None
    prefixes = {
        "contractor": ("NZ",),
        "supervisor": ("MK",),
        "kolaudator": ("MK",),
        "designer": ("A", "N"),
    }.get(role, ())
    for match in matches:
        value = _normalize_license(match.group(1))
        if any(value.startswith(prefix) for prefix in prefixes):
            return match
    return None


def _normalize_money(value: str) -> str:
    digits = re.sub(r"\D", "", value)
    if not digits:
        return value
    return f"{int(digits):,}".replace(",", ".")


def _add_table_metric(
    candidates: dict[str, list[dict[str, Any]]],
    field: str,
    line: str,
    document: dict[str, Any],
    style_reference: bool,
) -> None:
    numbers = re.findall(r"\b(\d+(?:[.,]\d+)?)\b", line)
    if not numbers:
        return
    value = numbers[0].replace(",", ".")
    _add_candidate(
        candidates,
        field,
        f"{value} m²",
        document,
        line,
        0.93,
        style_reference,
    )


def _lines(text: str) -> list[str]:
    return [
        " ".join(raw_line.split())
        for raw_line in text.splitlines()
        if len(" ".join(raw_line.split())) >= 3
    ][:1_500]


def _confidence_level(score: float) -> str:
    if score >= 0.82:
        return "high"
    if score >= 0.68:
        return "medium"
    return "low"


def _safe_float(value: object) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
