from typing import Any

from app.agents.llm import LLMReviewError, request_kolaudim_draft
from app.agents.state import AuditGraphState
from app.core.config import settings

MAX_DOCUMENTS_FOR_WRITER = 60
MAX_EXCERPT_CHARS = 900


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
            _build_kolaudim_writer_input(state),
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


def _build_kolaudim_writer_input(state: AuditGraphState) -> dict[str, Any]:
    return {
        "project": state.get("project", {}),
        "job": state.get("job", {}),
        "document_inventory": state.get("document_inventory", {}),
        "document_evidence": _document_evidence(state.get("documents", [])),
        "extracted_facts": state.get("extracted_facts", {}),
        "vkm_obligation_map": state.get("vkm_obligation_map", {}),
        "verified_findings": _limit_dicts(
            state.get("verified_findings", state.get("findings", [])),
            30,
        ),
        "consistency_review": state.get("consistency_review", {}),
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


def _document_evidence(documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    evidence = []
    for document in documents[:MAX_DOCUMENTS_FOR_WRITER]:
        text_excerpt = str(document.get("text_excerpt") or "")
        evidence.append(
            {
                "filename": document.get("original_filename"),
                "parse_status": document.get("parse_status"),
                "document_type": document.get("document_type"),
                "classification_confidence": document.get("classification_confidence"),
                "text_excerpt": text_excerpt[:MAX_EXCERPT_CHARS],
            }
        )
    return evidence


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


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _safe_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, min(1.0, number))
