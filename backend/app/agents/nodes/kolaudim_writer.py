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
MAX_FACTS_PER_CATEGORY_FOR_WRITER = 5
MAX_CONSISTENCY_ISSUES_FOR_WRITER = 12
MAX_FINDINGS_FOR_WRITER = 8
BUDGET_METADATA_RESERVED_TOKENS = 250

DOCUMENT_TYPE_PRIORITY = {
    "kolaudim_act": 0,
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
    writer_input: dict[str, Any] = {
        "project": state.get("project", {}),
        "job": state.get("job", {}),
        "document_inventory": state.get("document_inventory", {}),
        "extracted_facts": _compact_extracted_facts(state.get("extracted_facts", {})),
        "vkm_obligation_map": _compact_vkm_obligation_map(
            state.get("vkm_obligation_map", {})
        ),
        "verified_findings": _compact_findings(
            state.get("verified_findings", state.get("findings", [])),
        ),
        "consistency_review": _compact_consistency_review(
            state.get("consistency_review", {})
        ),
        "kolaudim_analysis": state.get("kolaudim_analysis", {}),
        "senior_ai_review": _compact_ai_review(state.get("ai_review", {})),
        "instructions": [
            "Përgatit Draft Akt Kolaudimi, jo checklist.",
            "Përdor vetëm faktet dhe evidencën në input.",
            "Nëse mungon një fakt i zakonshëm i aktit njerëzor, vendose te human_completion_items.",
            "Rezervat duhet të lidhen me gjetje, konsistencë ose dokumente të paklasifikuara.",
            "Mos e shpall objektin të kolauduar përfundimisht; drafti kërkon rishikim/nënshkrim njerëzor.",
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
        else:
            documents.pop()
        return True

    vkm_map = writer_input.get("vkm_obligation_map")
    if isinstance(vkm_map, dict) and _remove_last_list_item(vkm_map, "items", 12):
        return True
    consistency_review = writer_input.get("consistency_review")
    if isinstance(consistency_review, dict) and _remove_last_list_item(
        consistency_review,
        "issues",
        6,
    ):
        return True
    if _remove_last_list_item(writer_input, "verified_findings", 4):
        return True

    facts = writer_input.get("extracted_facts")
    categories = facts.get("categories") if isinstance(facts, dict) else None
    if isinstance(categories, dict):
        for items in categories.values():
            if isinstance(items, list) and len(items) > 2:
                items.pop()
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


def _compact_extracted_facts(extracted_facts: object) -> dict[str, Any]:
    if not isinstance(extracted_facts, dict):
        return {}

    categories = extracted_facts.get("categories", {})
    compact_categories: dict[str, list[dict[str, Any]]] = {}
    if isinstance(categories, dict):
        for category, items in categories.items():
            if not isinstance(items, list):
                continue
            compact_items = []
            for item in items[:MAX_FACTS_PER_CATEGORY_FOR_WRITER]:
                if not isinstance(item, dict):
                    continue
                compact_items.append(
                    {
                        "label": item.get("label"),
                        "value": _truncate(item.get("value"), 180),
                        "source_document": item.get("source_document"),
                        "document_type": item.get("document_type"),
                        "confidence": item.get("confidence"),
                    }
                )
            if compact_items:
                compact_categories[str(category)] = compact_items

    return {
        "categories": compact_categories,
        "summary": dict(extracted_facts.get("summary", {}))
        if isinstance(extracted_facts.get("summary"), dict)
        else {},
        "limitations": _string_list(extracted_facts.get("limitations"), limit=4),
    }


def _compact_vkm_obligation_map(vkm_map: object) -> dict[str, Any]:
    if not isinstance(vkm_map, dict):
        return {}

    compact_items = []
    items = vkm_map.get("items", [])
    if isinstance(items, list):
        for item in items:
            if not isinstance(item, dict):
                continue
            compact_items.append(
                {
                    "code": item.get("code"),
                    "title": item.get("title"),
                    "law_reference": item.get("law_reference"),
                    "status": item.get("status"),
                    "present_document_types": _string_list(
                        item.get("present_document_types"),
                        limit=10,
                    ),
                    "missing_document_types": _string_list(
                        item.get("missing_document_types"),
                        limit=12,
                    ),
                    "professional_use": _truncate(item.get("professional_use"), 180),
                }
            )

    return {
        "summary": dict(vkm_map.get("summary", {}))
        if isinstance(vkm_map.get("summary"), dict)
        else {},
        "items": compact_items,
    }


def _compact_findings(findings: list[dict]) -> list[dict[str, Any]]:
    compact_findings = []
    for finding in findings[:MAX_FINDINGS_FOR_WRITER]:
        if not isinstance(finding, dict):
            continue
        evidence = finding.get("evidence", {})
        missing_document_types = []
        if isinstance(evidence, dict):
            missing_document_types = _string_list(
                evidence.get("missing_document_types"),
                limit=12,
            )
        compact_findings.append(
            {
                "severity": finding.get("severity"),
                "title": finding.get("title"),
                "law_reference": finding.get("law_reference"),
                "rule_code": finding.get("rule_code"),
                "missing_document_types": missing_document_types,
                "required_action": _truncate(finding.get("required_action"), 220),
            }
        )
    return compact_findings


def _compact_consistency_review(consistency_review: object) -> dict[str, Any]:
    if not isinstance(consistency_review, dict):
        return {}

    issues = consistency_review.get("issues", [])
    compact_issues = []
    if isinstance(issues, list):
        for issue in issues[:MAX_CONSISTENCY_ISSUES_FOR_WRITER]:
            if not isinstance(issue, dict):
                continue
            compact_issues.append(
                {
                    "code": issue.get("code"),
                    "severity": issue.get("severity"),
                    "title": issue.get("title"),
                    "description": _truncate(issue.get("description"), 220),
                    "source_document": issue.get("source_document"),
                    "evidence": _compact_issue_evidence(issue.get("evidence")),
                }
            )

    return {
        "status": consistency_review.get("status"),
        "summary": dict(consistency_review.get("summary", {}))
        if isinstance(consistency_review.get("summary"), dict)
        else {},
        "issues": compact_issues,
    }


def _document_evidence(
    documents: list[dict[str, Any]],
    *,
    available_tokens: int,
) -> list[dict[str, Any]]:
    if available_tokens < 250:
        return []

    evidence = []
    for document in _prioritized_documents(documents):
        excerpt = _relevant_excerpt(
            str(document.get("text_excerpt") or ""),
            max_chars=_per_document_excerpt_chars(available_tokens),
        )
        candidate = {
            "filename": document.get("original_filename"),
            "parse_status": document.get("parse_status"),
            "document_type": document.get("document_type"),
            "classification_confidence": document.get("classification_confidence"),
            "evidence_excerpt": excerpt,
        }
        if _estimate_tokens(evidence + [candidate]) <= available_tokens:
            evidence.append(candidate)
            continue

        small_candidate = dict(candidate)
        small_candidate["evidence_excerpt"] = excerpt[:240]
        if _estimate_tokens(evidence + [small_candidate]) <= available_tokens:
            evidence.append(small_candidate)
        else:
            break
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
        if len(fallback_lines) < 5:
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

    excerpt = "\n".join(selected_lines or fallback_lines)
    return excerpt[:max_chars]


def _per_document_excerpt_chars(available_tokens: int) -> int:
    if available_tokens <= 2_000:
        return 260
    if available_tokens <= 4_500:
        return 420
    if available_tokens <= 9_000:
        return 620
    return 900


def _compact_ai_review(ai_review: object) -> dict[str, Any]:
    if not isinstance(ai_review, dict):
        return {}
    return {
        "status": ai_review.get("status"),
        "executive_summary": ai_review.get("executive_summary"),
        "recommendation": ai_review.get("recommendation"),
        "human_review_required": ai_review.get("human_review_required"),
        "limitations": ai_review.get("limitations", []),
    }


def _compact_issue_evidence(evidence: object) -> list[str]:
    if isinstance(evidence, list):
        compact = []
        for item in evidence[:4]:
            if isinstance(item, dict):
                value = item.get("value") or item.get("source_document")
                if value:
                    compact.append(_truncate(value, 160))
            else:
                compact.append(_truncate(item, 160))
        return [item for item in compact if item]
    if isinstance(evidence, str):
        return [_truncate(evidence, 180)]
    return []


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
        "title": str(draft.get("title") or "Draft Akt Kolaudimi Teknik").strip(),
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
