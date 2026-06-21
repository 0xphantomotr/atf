import json
from typing import Any

from app.agents.llm import (
    LLMReviewError,
    kolaudim_draft_input_token_budget,
    request_kolaudim_draft,
)
from app.agents.state import AuditGraphState
from app.core.config import settings

APPROX_CHARS_PER_TOKEN = 3
BUDGET_METADATA_RESERVED_TOKENS = 250

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
        draft = request_kolaudim_draft(
            _build_kolaudim_writer_input(state, ai_settings=ai_settings),
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

    normalized_draft = _normalize_kolaudim_draft(draft)
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
    dossier = _compact_professional_dossier(raw_dossier)
    writer_input: dict[str, Any] = {
        "project_fallback_metadata": state.get("project", {}),
        "job": state.get("job", {}),
        "professional_dossier": dossier,
        "legal_basis": _compact_legal_basis(state),
        "section_blueprint": _section_blueprint(state.get("kolaudim_analysis", {})),
        "instructions": [
            "Përgatit një Akt-Kolaudimi tekniko-ekonomik të plotë, jo raport auditimi dhe jo checklist.",
            "Faktet kanonike kanë përparësi ndaj çdo formulimi tjetër në fragmentet e dokumenteve.",
            "Dokumentet me evidence_role=style_reference përdoren vetëm për strukturë dhe stil, kurrë për fakte.",
            "Dokumentet me evidence_role=foreign_project_reference i përkasin një objekti tjetër dhe nuk përdoren për asnjë fakt të këtij akti.",
            "Përshkruaj faktet e provuara nga aktet, procesverbalet dhe dokumentet teknike pa pretenduar inspektim fizik të kryer nga sistemi.",
            "Mos shfaq emra fushash, kode sistemi, status parse, confidence, workflow, gjetje apo lista dokumentesh që mungojnë.",
            "Pasiguritë materiale integroji shkurt në konkluzion; mos krijo seksion checklist ose listë të gjatë rezervash.",
            "Përdor 10 deri në 12 seksionet e blueprint-it, me narrativë të detajuar dhe pa përsëritje.",
            "Titulli publik duhet të jetë 'AKT-KOLAUDIMI TEKNIKO-EKONOMIK'.",
        ],
    }

    remaining_tokens = (
        input_token_budget
        - _estimate_tokens(writer_input)
        - BUDGET_METADATA_RESERVED_TOKENS
    )
    writer_input["document_evidence"] = _document_evidence(
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
    writer_input["budget"] = {
        "model": ai_settings.get("model"),
        "provider": ai_settings.get("provider"),
        "target_input_tokens": input_token_budget,
        "estimated_input_tokens": 0,
        "selected_document_count": len(writer_input["document_evidence"]),
    }
    writer_input["budget"]["estimated_input_tokens"] = _estimate_tokens(writer_input)
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
    if isinstance(dossier, dict):
        if _remove_last_list_item(dossier, "technical_observations", 12):
            return True
        if _remove_last_list_item(dossier, "chronology", 8):
            return True
        if _remove_last_list_item(dossier, "conflicts", 3):
            return True

    return False


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


def _compact_professional_dossier(dossier: object) -> dict[str, Any]:
    if not isinstance(dossier, dict):
        return {}

    canonical = dossier.get("canonical_facts", {})
    compact_facts: dict[str, dict[str, Any]] = {}
    if isinstance(canonical, dict):
        for field, fact in canonical.items():
            if not isinstance(fact, dict):
                continue
            compact_facts[str(field)] = {
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
        "chronology": _limit_dicts(dossier.get("chronology", []), 40),
        "technical_observations": _limit_dicts(
            dossier.get("technical_observations", []),
            60,
        ),
        "conflicts": _limit_dicts(dossier.get("conflicts", []), 12),
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


def _compact_legal_basis(state: AuditGraphState) -> dict[str, Any]:
    references = []
    seen = set()
    for rule in state.get("rules", []):
        if not isinstance(rule, dict):
            continue
        reference = str(rule.get("law_reference") or "").strip()
        if not reference or reference in seen:
            continue
        seen.add(reference)
        references.append(reference)
    return {
        "law_scope": state.get("job", {}).get("law_scope", []),
        "verified_references": references[:20],
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
        parse_penalty = 0 if document.get("parse_status") == "parsed" else 5
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


def _normalize_kolaudim_draft(draft: dict[str, Any]) -> dict[str, Any]:
    sections = draft.get("sections", [])
    if not isinstance(sections, list):
        sections = []

    normalized_sections = []
    for section in sections:
        if not isinstance(section, dict):
            continue
        normalized_sections.append(
            {
                "code": str(section.get("code", "")).strip() or "section",
                "title": str(section.get("title", "")).strip() or "Seksion",
                "body": str(section.get("body", "")).strip(),
                "evidence_notes": _string_list(section.get("evidence_notes")),
            }
        )

    return {
        "status": "drafted",
        "title": "AKT-KOLAUDIMI TEKNIKO-EKONOMIK",
        "executive_summary": str(draft.get("executive_summary") or "").strip(),
        "sections": normalized_sections,
        "reservations": _string_list(draft.get("reservations")),
        "human_completion_items": _string_list(draft.get("human_completion_items")),
        "signature_note": str(draft.get("signature_note") or "").strip(),
        "confidence": _safe_float(draft.get("confidence")),
    }


def _limit_dicts(items: list[dict], limit: int) -> list[dict]:
    return [dict(item) for item in items[:limit] if isinstance(item, dict)]


def _string_list(value: object, *, limit: int | None = None) -> list[str]:
    if not isinstance(value, list):
        return []
    items = [str(item).strip() for item in value if str(item).strip()]
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
