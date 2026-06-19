from app.agents.llm import LLMReviewError, request_senior_review
from app.agents.state import AuditGraphState
from app.core.config import settings


def senior_review(state: AuditGraphState) -> AuditGraphState:
    state.setdefault("agent_trace", []).append("senior_reviewer")

    if not settings.ai_senior_review_enabled:
        state["ai_review"] = {
            "status": "skipped",
            "reason": "ai_senior_review_disabled",
            "model": settings.openai_model,
        }
        return state

    if not settings.openai_api_key:
        state["ai_review"] = {
            "status": "skipped",
            "reason": "missing_openai_api_key",
            "model": settings.openai_model,
        }
        return state

    try:
        review = request_senior_review(_build_review_input(state))
    except LLMReviewError as exc:
        state["ai_review"] = {
            "status": "failed",
            "reason": str(exc)[:500],
            "model": settings.openai_model,
        }
        state["needs_human_review"] = True
        return state

    normalized_review = _normalize_ai_review(review)
    state["ai_review"] = normalized_review
    if normalized_review.get("human_review_required"):
        state["needs_human_review"] = True
    return state


def _build_review_input(state: AuditGraphState) -> dict:
    unknown_documents = [
        document
        for document in state.get("documents", [])
        if document.get("parse_status") == "parsed"
        and document.get("document_type") == "unknown"
    ]
    unsupported_documents = [
        document
        for document in state.get("documents", [])
        if document.get("parse_status") not in {"parsed", "pending"}
    ]

    return {
        "review_scope": {
            "phase": "langgraph_phase_2",
            "allowed_action": "review_existing_deterministic_findings_only",
            "must_not_create_final_findings": True,
        },
        "project": state.get("project", {}),
        "job": state.get("job", {}),
        "document_inventory": state.get("document_inventory", {}),
        "law_context": state.get("law_context", {}),
        "completeness_summary": state.get("completeness_summary", {}),
        "rules": _limit_dicts(state.get("rules", []), 30),
        "verified_findings": _limit_dicts(
            state.get("verified_findings", state.get("findings", [])),
            20,
        ),
        "unknown_documents": _limit_dicts(unknown_documents, 25),
        "unsupported_documents": _limit_dicts(unsupported_documents, 25),
        "instructions": [
            "Rishiko vetëm gjetjet deterministike të dhëna.",
            "Mos shto gjetje përfundimtare të reja.",
            "Nëse një dokument i paklasifikuar mund të mbulojë një mungesë, kërko verifikim njerëzor.",
            "Arsyetimi duhet të jetë i shkurtër, teknik dhe në shqip.",
        ],
    }


def _normalize_ai_review(review: dict) -> dict:
    finding_reviews = review.get("finding_reviews", [])
    if not isinstance(finding_reviews, list):
        finding_reviews = []

    normalized_finding_reviews = [
        item for item in finding_reviews if isinstance(item, dict)
    ]
    human_review_required = bool(review.get("human_review_required", False))
    if any(
        item.get("decision") in {"needs_human_review", "insufficient_evidence"}
        for item in normalized_finding_reviews
    ):
        human_review_required = True

    unknown_document_notes = review.get("unknown_document_notes", [])
    if not isinstance(unknown_document_notes, list):
        unknown_document_notes = []

    limitations = review.get("limitations", [])
    if not isinstance(limitations, list):
        limitations = []

    return {
        "status": "reviewed",
        "model": settings.openai_model,
        "executive_summary": str(review.get("executive_summary", "")).strip(),
        "recommendation": str(review.get("recommendation", "")).strip(),
        "finding_reviews": normalized_finding_reviews,
        "unknown_document_notes": [
            str(note).strip() for note in unknown_document_notes if str(note).strip()
        ],
        "human_review_required": human_review_required,
        "confidence": _safe_float(review.get("confidence")),
        "limitations": [
            str(limitation).strip()
            for limitation in limitations
            if str(limitation).strip()
        ],
    }


def _limit_dicts(items: list[dict], limit: int) -> list[dict]:
    return [dict(item) for item in items[:limit] if isinstance(item, dict)]


def _safe_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
