import json
from typing import Any

from app.agents.claim_grounding import (
    build_claim_evidence_catalog,
    canonical_evidence_id,
    claim_source_references,
    register_evidence_id,
)
from app.agents.llm import (
    LLMReviewError,
    kolaudim_draft_input_token_budget,
    request_kolaudim_draft,
)
from app.agents.state import AuditGraphState
from app.core.config import settings
from app.files.status import is_parsed_status

APPROX_CHARS_PER_TOKEN = 3
BUDGET_METADATA_RESERVED_TOKENS = 250

CORE_WRITER_FACTS = {
    "object_name",
    "location",
    "investor",
    "owner",
    "contractor",
    "supervisor",
    "designer",
    "kolaudator",
    "construction_permit_number",
    "construction_permit_date",
    "start_date",
    "completion_date",
    "planned_value",
    "final_value",
}

DOCUMENT_TYPE_PRIORITY = {
    "construction_permit": 1,
    "development_permit": 1,
    "contract_and_related_acts": 2,
    "supervisor_contract": 2,
    "construction_permit_conformity_declaration": 3,
    "technical_declaration": 3,
    "start_works_notification": 4,
    "start_works_notification_letter": 4,
    "start_works_minutes": 4,
    "site_handover_act": 4,
    "setting_out_act": 5,
    "structure_setting_out_control_act": 5,
    "foundation_completion_and_level_0_00_control_act": 6,
    "level_0_00_control_act": 6,
    "structural_frame_completion_control_act": 6,
    "facade_and_finishing_completion_control_act": 6,
    "external_system_completion_control_act": 6,
    "hidden_works_minutes": 7,
    "material_quality_certificate": 8,
    "maintenance_project": 9,
    "as_built_project": 9,
    "kolaudim_act": 30,
}

RELEVANT_LINE_TERMS = (
    "objekt",
    "vendndodh",
    "adresa",
    "bashkia",
    "investitor",
    "zhvillues",
    "sipermarres",
    "sipërmarrës",
    "kontraktor",
    "mbikeqyres",
    "mbikëqyrës",
    "kolaudator",
    "projektues",
    "leje",
    "vendim",
    "license",
    "licenc",
    "date",
    "datë",
    "fillim",
    "perfundim",
    "përfundim",
    "siperfaq",
    "sipërfaq",
    "vlera",
    "preventiv",
    "situacion",
    "deklar",
    "konformitet",
    "perputh",
    "përputh",
    "punime",
    "kontroll",
    "material",
    "cilësi",
    "cilesi",
)


def write_kolaudim_draft(state: AuditGraphState) -> AuditGraphState:
    state.setdefault("agent_trace", []).append("kolaudim_writer")
    job = state.get("job", {})
    if job.get("job_type") != "kolaudim_act":
        state["kolaudim_draft"] = {
            "status": "skipped",
            "reason": "not_kolaudim_act",
        }
        return state

    ai_settings = state.get("ai_settings")
    if not settings.ai_senior_review_enabled:
        state["kolaudim_draft"] = {
            "status": "skipped",
            "reason": "ai_generation_disabled",
        }
        state["needs_human_review"] = True
        return state

    if not isinstance(ai_settings, dict) or not ai_settings.get("api_key"):
        state["kolaudim_draft"] = {
            "status": "skipped",
            "reason": "missing_user_ai_settings",
        }
        state["needs_human_review"] = True
        return state

    try:
        writer_input = _build_kolaudim_writer_input(state, ai_settings=ai_settings)
        draft = request_kolaudim_draft(
            writer_input,
            ai_settings=ai_settings,
        )
    except LLMReviewError as exc:
        if state.get("require_ai_review"):
            raise
        state["kolaudim_draft"] = {
            "status": "failed",
            "reason": str(exc)[:500],
            "provider": ai_settings.get("provider"),
            "model": ai_settings.get("model"),
        }
        state["needs_human_review"] = True
        return state

    normalized_draft = _normalize_kolaudim_draft(
        draft,
        evidence_catalog=build_claim_evidence_catalog(state),
    )
    normalized_draft["provider"] = ai_settings.get("provider")
    normalized_draft["model"] = ai_settings.get("model")
    normalized_draft["api_key_hint"] = ai_settings.get("api_key_hint")
    state["kolaudim_draft"] = normalized_draft
    if normalized_draft["human_completion_items"] or normalized_draft["reservations"]:
        state["needs_human_review"] = True
    return state


def _build_kolaudim_writer_input(
    state: AuditGraphState,
    *,
    ai_settings: dict[str, Any],
) -> dict[str, Any]:
    input_token_budget = kolaudim_draft_input_token_budget(ai_settings)
    raw_dossier = state.get("professional_dossier", {})
    evidence_catalog = build_claim_evidence_catalog(state)
    dossier = _compact_professional_dossier(
        raw_dossier,
        evidence_catalog=evidence_catalog,
    )
    writer_input: dict[str, Any] = {
        "project_fallback_metadata": state.get("project", {}),
        "job": state.get("job", {}),
        "professional_dossier": dossier,
        "specialist_memoranda": _compact_specialist_reviews(
            state.get("specialist_reviews")
        ),
        "legal_basis": _compact_legal_basis(state),
        "section_blueprint": _section_blueprint(state.get("kolaudim_analysis", {})),
        "instructions": [
            "Përgatit një Akt-Kolaudimi tekniko-ekonomik të plotë, jo raport "
            "auditimi dhe jo checklist.",
            "Faktet kanonike dhe regjistrat e konsoliduar kanë përparësi ndaj "
            "çdo formulimi tjetër.",
            "Përdor regjistrat profesionalë për kronologjinë, punimet, materialet, "
            "provat, kontratat dhe vlerat.",
            "Përdor memorandat specialistike vetëm si sintezë; çdo fakt duhet të "
            "mbetet në përputhje me regjistrat dhe faktet kanonike.",
            "Çdo paragraf publik duhet të ketë claim_type dhe evidence_ids. Përdor "
            "vetëm evidence_id që shfaqen në input.",
            "documented_fact kërkon evidencë të drejtpërdrejtë; "
            "professional_inference duhet të mbështetet në evidencë dhe të mos "
            "paraqitet si matje ose inspektim fizik; qualification shpreh kufizim.",
            "Dokumentet me evidence_role=style_reference përdoren vetëm për "
            "strukturë dhe stil, kurrë për fakte.",
            "Dokumentet me evidence_role=foreign_project_reference i përkasin një "
            "objekti tjetër dhe nuk përdoren për asnjë fakt të këtij akti.",
            "Përshkruaj faktet e provuara nga aktet, procesverbalet dhe dokumentet "
            "teknike pa pretenduar inspektim fizik të kryer nga sistemi.",
            "Mos shfaq emra fushash, kode sistemi, status parse, confidence, "
            "workflow, gjetje apo lista dokumentesh që mungojnë.",
            "Pasiguritë materiale integroji shkurt në konkluzion; mos krijo "
            "seksion checklist ose listë të gjatë rezervash.",
            "Përdor 10 deri në 12 seksionet e blueprint-it, me narrativë të "
            "detajuar dhe pa përsëritje.",
            "Titulli publik duhet të jetë 'AKT-KOLAUDIMI TEKNIKO-EKONOMIK'.",
        ],
    }

    remaining_tokens = (
        input_token_budget
        - _estimate_tokens(writer_input)
        - BUDGET_METADATA_RESERVED_TOKENS
    )
    has_persisted_analysis = (
        isinstance(raw_dossier, dict)
        and isinstance(raw_dossier.get("summary"), dict)
        and int(raw_dossier["summary"].get("persisted_analysis_count") or 0) > 0
    )
    writer_input["document_evidence"] = (
        []
        if has_persisted_analysis
        else _document_evidence(
            state.get("documents", []),
            available_tokens=max(0, remaining_tokens),
            document_roles={
                str(record.get("filename")): str(record.get("role"))
                for record in raw_dossier.get("document_records", [])
                if isinstance(record, dict)
            }
            if isinstance(raw_dossier, dict)
            else {},
        )
    )
    return _fit_writer_input_to_budget(
        writer_input,
        input_token_budget=input_token_budget,
        ai_settings=ai_settings,
    )


def _fit_writer_input_to_budget(
    writer_input: dict[str, Any],
    *,
    input_token_budget: int,
    ai_settings: dict[str, Any],
) -> dict[str, Any]:
    for _ in range(250):
        _set_budget_metadata(
            writer_input,
            input_token_budget=input_token_budget,
            ai_settings=ai_settings,
        )
        if _estimate_tokens(writer_input) <= input_token_budget:
            return writer_input
        if _shrink_writer_input(writer_input):
            continue
        break

    _set_budget_metadata(
        writer_input,
        input_token_budget=input_token_budget,
        ai_settings=ai_settings,
    )
    return writer_input


def _set_budget_metadata(
    writer_input: dict[str, Any],
    *,
    input_token_budget: int,
    ai_settings: dict[str, Any],
) -> None:
    writer_input["allowed_evidence_ids"] = _writer_input_evidence_ids(writer_input)
    writer_input["budget"] = {
        "model": ai_settings.get("model"),
        "provider": ai_settings.get("provider"),
        "target_input_tokens": input_token_budget,
        "estimated_input_tokens": 0,
        "selected_document_count": len(writer_input["document_evidence"]),
    }
    writer_input["budget"]["estimated_input_tokens"] = _estimate_tokens(writer_input)


def _shrink_writer_input(writer_input: dict[str, Any]) -> bool:
    documents = writer_input.get("document_evidence")
    if isinstance(documents, list) and documents:
        largest = max(
            documents,
            key=lambda document: len(str(document.get("evidence_excerpt") or "")),
        )
        excerpt = str(largest.get("evidence_excerpt") or "")
        if len(excerpt) > 260:
            largest["evidence_excerpt"] = excerpt[: max(260, len(excerpt) // 2)]
        elif excerpt:
            largest["evidence_excerpt"] = ""
        else:
            documents.remove(largest)
        return True

    dossier = writer_input.get("professional_dossier")
    specialist_memoranda = writer_input.get("specialist_memoranda")
    if isinstance(specialist_memoranda, dict):
        if _shrink_specialist_memoranda(specialist_memoranda):
            return True
    if isinstance(dossier, dict):
        if _shrink_canonical_facts(dossier):
            return True
        if _shrink_dossier_registers(dossier):
            return True
        if _remove_last_list_item(dossier, "technical_observations", 12):
            return True
        if _remove_last_list_item(dossier, "chronology", 8):
            return True
        if _remove_last_list_item(dossier, "conflicts", 3):
            return True

    return False


def _shrink_specialist_memoranda(value: dict[str, Any]) -> bool:
    memoranda = value.get("memoranda")
    if not isinstance(memoranda, list):
        return False

    statement_keys = (
        ("writer_guidance", 0),
        ("qualifications", 0),
        ("technical_assessments", 1),
        ("established_facts", 1),
    )
    for key, minimum in statement_keys:
        candidates = [
            memo
            for memo in memoranda
            if isinstance(memo, dict)
            and isinstance(memo.get(key), list)
            and len(memo[key]) > minimum
        ]
        if candidates:
            largest = max(candidates, key=lambda memo: len(memo[key]))
            largest[key].pop()
            return True

    statements = [
        statement
        for memo in memoranda
        if isinstance(memo, dict)
        for key, _ in statement_keys
        for statement in memo.get(key, [])
        if isinstance(statement, dict)
    ]
    source_candidates = [
        statement
        for statement in statements
        if isinstance(statement.get("source_references"), list)
        and statement["source_references"]
    ]
    if source_candidates:
        largest_sources = max(
            source_candidates,
            key=lambda statement: len(statement["source_references"]),
        )
        largest_sources["source_references"].pop()
        return True

    text_candidates = [
        statement
        for statement in statements
        if len(str(statement.get("statement") or "")) > 240
    ]
    if text_candidates:
        longest = max(
            text_candidates,
            key=lambda statement: len(str(statement.get("statement") or "")),
        )
        text = str(longest["statement"])
        longest["statement"] = _truncate(text, max(240, len(text) // 2))
        return True

    for key in ("technical_assessments", "established_facts"):
        candidates = [
            memo
            for memo in memoranda
            if isinstance(memo, dict)
            and isinstance(memo.get(key), list)
            and memo[key]
        ]
        if candidates:
            largest = max(candidates, key=lambda memo: len(memo[key]))
            largest[key].pop()
            return True
    return False


def _shrink_canonical_facts(dossier: dict[str, Any]) -> bool:
    facts = dossier.get("canonical_facts")
    if not isinstance(facts, dict):
        return False

    non_core_fields = [field for field in facts if field not in CORE_WRITER_FACTS]
    if non_core_fields:
        largest_field = max(non_core_fields, key=lambda field: _estimate_tokens(facts[field]))
        del facts[largest_field]
        return True

    for detail_key in ("alternatives", "evidence", "source_documents"):
        candidates = [
            fact
            for fact in facts.values()
            if isinstance(fact, dict)
            and isinstance(fact.get(detail_key), list)
            and fact[detail_key]
        ]
        if candidates:
            largest = max(candidates, key=lambda fact: len(fact[detail_key]))
            largest[detail_key].pop()
            return True
    return False


def _shrink_dossier_registers(dossier: dict[str, Any]) -> bool:
    registers = dossier.get("registers")
    if not isinstance(registers, dict):
        return False
    minimums = {
        "supporting_evidence": 0,
        "declarations_and_conclusions": 1,
        "materials_and_tests": 3,
        "technical_works": 4,
        "construction_chronology": 4,
        "contracts_and_economics": 3,
        "project_parameters": 3,
        "permits_property_licenses": 3,
        "stakeholders": 4,
    }
    candidates = [
        (name, entries)
        for name, entries in registers.items()
        if isinstance(entries, list) and len(entries) > minimums.get(name, 3)
    ]
    if not candidates:
        return False
    _, largest = max(candidates, key=lambda item: len(item[1]))
    largest.pop()
    return True


def _remove_last_list_item(
    container: dict[str, Any],
    key: str,
    minimum_items: int,
) -> bool:
    items = container.get(key)
    if isinstance(items, list) and len(items) > minimum_items:
        items.pop()
        return True
    return False


def _compact_professional_dossier(
    dossier: object,
    *,
    evidence_catalog: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if not isinstance(dossier, dict):
        return {}

    canonical = dossier.get("canonical_facts", {})
    compact_facts: dict[str, dict[str, Any]] = {}
    if isinstance(canonical, dict):
        for field, fact in canonical.items():
            if not isinstance(fact, dict):
                continue
            evidence_id = canonical_evidence_id(str(field))
            catalog_item = (evidence_catalog or {}).get(evidence_id, {})
            compact_facts[str(field)] = {
                "evidence_id": evidence_id,
                "supporting_evidence_ids": _string_list(
                    catalog_item.get("supporting_evidence_ids"),
                    limit=8,
                ),
                "value": fact.get("value"),
                "confidence_level": fact.get("confidence_level"),
                "source_documents": _string_list(
                    fact.get("source_documents"),
                    limit=6,
                ),
                "evidence": [
                    {
                        "source_document": item.get("source_document"),
                        "snippet": _truncate(item.get("snippet"), 220),
                    }
                    for item in fact.get("evidence", [])[:2]
                    if isinstance(item, dict)
                ],
                "alternatives": [
                    {
                        "value": item.get("value"),
                        "source_documents": _string_list(
                            item.get("source_documents"),
                            limit=3,
                        ),
                    }
                    for item in fact.get("alternatives", [])[:2]
                    if isinstance(item, dict)
                ],
            }

    return {
        "canonical_facts": compact_facts,
        "registers": _compact_registers(dossier.get("registers")),
        "economic_summary": dict(dossier.get("economic_summary", {}))
        if isinstance(dossier.get("economic_summary"), dict)
        else {},
        "evidence_coverage": _compact_evidence_coverage(
            dossier.get("evidence_coverage")
        ),
        "integrity_issues": _numbered_items(
            dossier.get("integrity_issues", []),
            prefix="integrity",
            limit=10,
        ),
        "chronology": _limit_dicts(dossier.get("chronology", []), 40),
        "technical_observations": _limit_dicts(
            dossier.get("technical_observations", []),
            60,
        ),
        "conflicts": _numbered_items(
            dossier.get("conflicts", []),
            prefix="conflict",
            limit=12,
        ),
        "document_records": [
            dict(record)
            for record in dossier.get("document_records", [])
            if isinstance(record, dict)
            and record.get("role")
            not in {"foreign_project_reference", "style_reference", "unreadable"}
        ][:120],
        "evidence_by_section": dict(dossier.get("evidence_by_section", {}))
        if isinstance(dossier.get("evidence_by_section"), dict)
        else {},
        "excluded_reference_summary": {
            "style_reference_count": len(dossier.get("style_references", [])),
            "foreign_project_document_count": int(
                dossier.get("summary", {}).get("foreign_project_documents") or 0
            )
            if isinstance(dossier.get("summary"), dict)
            else 0,
            "instruction": (
                "Këto dokumente janë analizuar dhe përjashtuar nga burimet faktike; "
                "struktura profesionale e lejuar është përfshirë në blueprint."
            ),
        },
        "missing_core_fields": _string_list(
            dossier.get("missing_core_fields"),
            limit=20,
        ),
        "summary": dict(dossier.get("summary", {}))
        if isinstance(dossier.get("summary"), dict)
        else {},
    }


def _compact_specialist_reviews(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    memoranda = value.get("memoranda")
    if not isinstance(memoranda, list):
        memoranda = []
    return {
        "status": value.get("status"),
        "summary": dict(value.get("summary", {}))
        if isinstance(value.get("summary"), dict)
        else {},
        "memoranda": [
            {
                "code": memo.get("code"),
                "title": memo.get("title"),
                "status": memo.get("status"),
                "confidence": memo.get("confidence"),
                "established_facts": _compact_specialist_statements(
                    memo.get("established_facts")
                ),
                "technical_assessments": _compact_specialist_statements(
                    memo.get("technical_assessments")
                ),
                "qualifications": _compact_specialist_statements(
                    memo.get("qualifications")
                ),
                "writer_guidance": _compact_specialist_statements(
                    memo.get("writer_guidance")
                ),
            }
            for memo in memoranda[:6]
            if isinstance(memo, dict)
        ],
    }


def _compact_specialist_statements(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [
        {
            "statement": _truncate(item.get("statement"), 600),
            "evidence_ids": _string_list(item.get("evidence_ids"), limit=6),
            "source_references": [
                {
                    "source_document": source.get("source_document"),
                    "file_version_id": source.get("file_version_id"),
                    "chunk_ids": _string_list(source.get("chunk_ids"), limit=3),
                }
                for source in item.get("source_references", [])[:3]
                if isinstance(source, dict)
            ],
        }
        for item in value[:5]
        if isinstance(item, dict) and item.get("statement")
    ]


def _compact_registers(value: object) -> dict[str, list[dict[str, Any]]]:
    if not isinstance(value, dict):
        return {}
    limits = {
        "stakeholders": 40,
        "permits_property_licenses": 60,
        "project_parameters": 80,
        "construction_chronology": 120,
        "technical_works": 140,
        "materials_and_tests": 120,
        "contracts_and_economics": 80,
        "declarations_and_conclusions": 60,
        "supporting_evidence": 40,
    }
    compact: dict[str, list[dict[str, Any]]] = {}
    for register, entries in value.items():
        if not isinstance(entries, list):
            continue
        register_name = str(register)
        compact[register_name] = [
            _compact_register_entry(
                entry,
                evidence_id=register_evidence_id(register_name, index),
            )
            for index, entry in enumerate(entries[: limits.get(register_name, 40)])
            if isinstance(entry, dict)
        ]
    return compact


def _compact_register_entry(
    entry: dict[str, Any],
    *,
    evidence_id: str,
) -> dict[str, Any]:
    return {
        "evidence_id": evidence_id,
        "field_name": entry.get("field_name"),
        "value": entry.get("value"),
        "normalized_value": entry.get("normalized_value"),
        "confidence_level": entry.get("confidence_level"),
        "source_documents": _string_list(entry.get("source_documents"), limit=6),
        "sources": [
            {
                "source_document": source.get("source_document"),
                "document_type": source.get("document_type"),
                "file_version_id": source.get("file_version_id"),
                "chunk_references": [
                    {
                        "chunk_id": reference.get("chunk_id"),
                        "chunk_index": reference.get("chunk_index"),
                        "page_start": reference.get("page_start"),
                        "page_end": reference.get("page_end"),
                        "coordinates": reference.get("coordinates"),
                        "excerpt": _truncate(reference.get("excerpt"), 180),
                    }
                    for reference in source.get("chunk_references", [])[:2]
                    if isinstance(reference, dict)
                ],
            }
            for source in entry.get("sources", [])[:3]
            if isinstance(source, dict)
        ],
    }


def _compact_evidence_coverage(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    by_register = value.get("by_register")
    return {
        "eligible_document_count": value.get("eligible_document_count"),
        "analyzed_document_count": value.get("analyzed_document_count"),
        "unanalyzed_document_count": value.get("unanalyzed_document_count"),
        "analysis_coverage_ratio": value.get("analysis_coverage_ratio"),
        "by_register": {
            str(register): {
                "entry_count": details.get("entry_count"),
                "source_document_count": details.get("source_document_count"),
                "source_chunk_count": details.get("source_chunk_count"),
                "fields": _string_list(details.get("fields"), limit=40),
            }
            for register, details in by_register.items()
            if isinstance(details, dict)
        }
        if isinstance(by_register, dict)
        else {},
    }


def _compact_legal_basis(state: AuditGraphState) -> dict[str, Any]:
    references = []
    evidence_ids = []
    seen = set()
    for rule in state.get("rules", []):
        if not isinstance(rule, dict):
            continue
        rule_code = str(rule.get("rule_code") or "").strip()
        reference = str(rule.get("law_reference") or "").strip()
        key = rule_code, reference
        if not any(key) or key in seen:
            continue
        seen.add(key)
        evidence_ids.append(f"law:{len(seen) - 1}")
        if reference:
            references.append(reference)
    return {
        "law_scope": state.get("job", {}).get("law_scope", []),
        "verified_references": references[:20],
        "evidence_ids": evidence_ids[:20],
        "instruction": (
            "Përdor vetëm referencat e dhëna. Mos shpik numra nenesh ose akte të tjera."
        ),
    }


def _section_blueprint(analysis: object) -> list[dict[str, Any]]:
    if not isinstance(analysis, dict):
        return []
    return _limit_dicts(analysis.get("sections", []), 16)


def _document_evidence(
    documents: list[dict[str, Any]],
    *,
    available_tokens: int,
    document_roles: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    if available_tokens < 250:
        return []

    document_roles = document_roles or {}
    prioritized = [
        document
        for document in _prioritized_documents(documents)
        if document_roles.get(str(document.get("original_filename")))
        not in {"foreign_project_reference", "style_reference", "unreadable"}
    ]
    evidence = [
        {
            "filename": document.get("original_filename"),
            "parse_status": document.get("parse_status"),
            "document_type": document.get("document_type"),
            "classification_confidence": document.get("classification_confidence"),
            "evidence_role": document_roles.get(
                str(document.get("original_filename")),
                "supporting_evidence",
            ),
            "evidence_excerpt": "",
        }
        for document in prioritized
    ]
    while evidence and _estimate_tokens(evidence) > available_tokens:
        evidence.pop()
    if not evidence:
        return []

    included_names = {str(item.get("filename")) for item in evidence}
    included_documents = [
        document
        for document in prioritized
        if str(document.get("original_filename")) in included_names
    ]
    remaining_tokens = max(0, available_tokens - _estimate_tokens(evidence))
    total_chars = remaining_tokens * APPROX_CHARS_PER_TOKEN
    per_document_chars = _per_document_excerpt_chars(
        available_tokens,
        document_count=len(included_documents),
        total_chars=total_chars,
    )

    evidence_by_name = {str(item.get("filename")): item for item in evidence}
    for document in included_documents:
        filename = str(document.get("original_filename"))
        candidate = evidence_by_name[filename]
        candidate["evidence_excerpt"] = _relevant_excerpt(
            str(document.get("text_excerpt") or ""),
            max_chars=per_document_chars,
        )

    while _estimate_tokens(evidence) > available_tokens:
        largest = max(
            evidence,
            key=lambda item: len(str(item.get("evidence_excerpt") or "")),
        )
        excerpt = str(largest.get("evidence_excerpt") or "")
        if not excerpt:
            evidence.pop()
        elif len(excerpt) > 240:
            largest["evidence_excerpt"] = excerpt[: max(240, len(excerpt) - 240)]
        else:
            largest["evidence_excerpt"] = ""
    return evidence


def _prioritized_documents(documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def sort_key(document: dict[str, Any]) -> tuple[int, int, float, str]:
        document_type = str(document.get("document_type") or "unknown")
        parse_penalty = 0 if is_parsed_status(document.get("parse_status")) else 5
        priority = DOCUMENT_TYPE_PRIORITY.get(document_type, 20)
        confidence = document.get("classification_confidence")
        confidence_value = float(confidence) if isinstance(confidence, int | float) else 0.0
        return (
            parse_penalty,
            priority,
            -confidence_value,
            str(document.get("original_filename") or ""),
        )

    return sorted(
        [document for document in documents if isinstance(document, dict)],
        key=sort_key,
    )


def _relevant_excerpt(text: str, *, max_chars: int) -> str:
    if not text or max_chars <= 0:
        return ""

    selected_lines: list[str] = []
    fallback_lines: list[str] = []
    seen = set()
    for raw_line in text.splitlines():
        line = " ".join(raw_line.split())
        if len(line) < 8:
            continue
        normalized = line.lower()
        if len(fallback_lines) < 8:
            fallback_lines.append(line)
        if not any(term in normalized for term in RELEVANT_LINE_TERMS):
            continue
        key = normalized[:140]
        if key in seen:
            continue
        seen.add(key)
        selected_lines.append(line)
        if len("\n".join(selected_lines)) >= max_chars:
            break

    excerpt_lines = fallback_lines + [
        line for line in selected_lines if line not in fallback_lines
    ]
    excerpt = "\n".join(excerpt_lines)
    return excerpt[:max_chars]


def _per_document_excerpt_chars(
    available_tokens: int,
    *,
    document_count: int,
    total_chars: int,
) -> int:
    if not document_count:
        return 0
    fair_share = max(240, total_chars // document_count)
    if available_tokens <= 2_000:
        return min(320, fair_share)
    if available_tokens <= 9_000:
        return min(900, fair_share)
    return min(4_500, fair_share)


def _normalize_kolaudim_draft(
    draft: dict[str, Any],
    *,
    evidence_catalog: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    sections = draft.get("sections", [])
    if not isinstance(sections, list):
        sections = []

    normalized_sections = []
    claim_ledger: list[dict[str, Any]] = []
    summary = _normalize_grounded_paragraph(
        draft.get("executive_summary"),
        claim_id="executive_summary:0",
        section_code="executive_summary",
        evidence_catalog=evidence_catalog,
    )
    if summary is not None:
        claim_ledger.append(summary)

    for section_index, section in enumerate(sections):
        if not isinstance(section, dict):
            continue
        code = str(section.get("code", "")).strip() or "section"
        paragraphs = []
        raw_paragraphs = section.get("paragraphs", [])
        if not isinstance(raw_paragraphs, list):
            raw_paragraphs = []
        for paragraph_index, paragraph in enumerate(raw_paragraphs):
            normalized = _normalize_grounded_paragraph(
                paragraph,
                claim_id=f"{code}:{section_index}:{paragraph_index}",
                section_code=code,
                evidence_catalog=evidence_catalog,
            )
            if normalized is None:
                continue
            paragraphs.append(normalized["statement"])
            claim_ledger.append(normalized)
        normalized_sections.append(
            {
                "code": code,
                "title": str(section.get("title", "")).strip() or "Seksion",
                "body": "\n\n".join(paragraphs),
            }
        )

    return {
        "status": "drafted",
        "title": "AKT-KOLAUDIMI TEKNIKO-EKONOMIK",
        "executive_summary": summary["statement"] if summary is not None else "",
        "sections": normalized_sections,
        "claim_ledger": claim_ledger,
        "reservations": _string_list(draft.get("reservations")),
        "human_completion_items": _string_list(draft.get("human_completion_items")),
        "signature_note": str(draft.get("signature_note") or "").strip(),
        "confidence": _safe_float(draft.get("confidence")),
    }


def _normalize_grounded_paragraph(
    value: object,
    *,
    claim_id: str,
    section_code: str,
    evidence_catalog: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    statement = " ".join(str(value.get("text") or "").split())
    if not statement:
        return None
    claim_type = str(value.get("claim_type") or "").strip()
    evidence_ids = list(
        dict.fromkeys(_string_list(value.get("evidence_ids"), limit=12))
    )
    return {
        "claim_id": claim_id,
        "section_code": section_code,
        "statement": statement,
        "claim_type": claim_type,
        "evidence_ids": evidence_ids,
        "confidence": _safe_float(value.get("confidence")),
        "source_references": claim_source_references(
            evidence_ids,
            evidence_catalog,
        ),
    }


def _writer_input_evidence_ids(writer_input: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    dossier = writer_input.get("professional_dossier", {})
    if isinstance(dossier, dict):
        for fact in dossier.get("canonical_facts", {}).values():
            if not isinstance(fact, dict):
                continue
            ids.extend(_string_list([fact.get("evidence_id")]))
            ids.extend(_string_list(fact.get("supporting_evidence_ids")))
        for entries in dossier.get("registers", {}).values():
            if not isinstance(entries, list):
                continue
            ids.extend(
                str(entry.get("evidence_id"))
                for entry in entries
                if isinstance(entry, dict) and entry.get("evidence_id")
            )
        for key in ("conflicts", "integrity_issues"):
            ids.extend(
                str(item.get("evidence_id"))
                for item in dossier.get(key, [])
                if isinstance(item, dict) and item.get("evidence_id")
            )
    legal_basis = writer_input.get("legal_basis", {})
    if isinstance(legal_basis, dict):
        ids.extend(_string_list(legal_basis.get("evidence_ids")))
    specialist = writer_input.get("specialist_memoranda", {})
    if isinstance(specialist, dict):
        for memorandum in specialist.get("memoranda", []):
            if not isinstance(memorandum, dict):
                continue
            for key in (
                "established_facts",
                "technical_assessments",
                "qualifications",
                "writer_guidance",
            ):
                for statement in memorandum.get(key, []):
                    if isinstance(statement, dict):
                        ids.extend(_string_list(statement.get("evidence_ids")))
    return list(dict.fromkeys(ids))


def _numbered_items(
    value: object,
    *,
    prefix: str,
    limit: int,
) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [
        {"evidence_id": f"{prefix}:{index}", **dict(item)}
        for index, item in enumerate(value[:limit])
        if isinstance(item, dict)
    ]


def _limit_dicts(items: list[dict], limit: int) -> list[dict]:
    return [dict(item) for item in items[:limit] if isinstance(item, dict)]


def _string_list(value: object, *, limit: int | None = None) -> list[str]:
    if not isinstance(value, list):
        return []
    items = [
        str(item).strip()
        for item in value
        if item is not None and str(item).strip()
    ]
    if limit is not None:
        return items[:limit]
    return items


def _truncate(value: object, max_chars: int) -> str:
    text = str(value or "").strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def _estimate_tokens(value: object) -> int:
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return max(1, len(text) // APPROX_CHARS_PER_TOKEN)


def _safe_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, min(1.0, number))
