from app.agents.llm import LLMReviewError, request_senior_review
from app.agents.state import AuditGraphState
from app.core.config import settings


def senior_review(state: AuditGraphState) -> AuditGraphState:
    state.setdefault("agent_trace", []).append("senior_reviewer")
    ai_settings = state.get("ai_settings")

    specialist_reviews = state.get("specialist_reviews", {})
    specialist_status = (
        specialist_reviews.get("status")
        if isinstance(specialist_reviews, dict)
        else None
    )
    if state.get("job", {}).get("job_type") == "kolaudim_act" and specialist_status in {
        "reviewed",
        "partially_reviewed",
        "invalid_model_output",
        "insufficient_evidence",
        "failed",
    }:
        state["ai_review"] = {
            "status": "skipped",
            "reason": "replaced_by_specialist_review_stage",
        }
        return state

    if not settings.ai_senior_review_enabled:
        state["ai_review"] = {
            "status": "skipped",
            "reason": "ai_senior_review_disabled",
        }
        if state.get("require_ai_review"):
            state["needs_human_review"] = True
        return state

    if not isinstance(ai_settings, dict) or not ai_settings.get("api_key"):
        state["ai_review"] = {
            "status": "skipped",
            "reason": "missing_user_ai_settings",
        }
        if state.get("require_ai_review"):
            state["needs_human_review"] = True
        return state

    try:
        review = request_senior_review(_build_review_input(state), ai_settings=ai_settings)
    except LLMReviewError as exc:
        state["ai_review"] = {
            "status": "failed",
            "reason": str(exc)[:500],
            "provider": ai_settings.get("provider"),
            "model": ai_settings.get("model"),
        }
        state["needs_human_review"] = True
        return state

    normalized_review = _normalize_ai_review(review)
    normalized_review["provider"] = ai_settings.get("provider")
    normalized_review["model"] = ai_settings.get("model")
    normalized_review["api_key_hint"] = ai_settings.get("api_key_hint")
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
            "phase": "professional_kolaudim_phase_1",
            "allowed_action": "review_existing_findings_and_professional_analysis_only",
            "must_not_create_final_findings": True,
        },
        "project": state.get("project", {}),
        "job": state.get("job", {}),
        "document_inventory": state.get("document_inventory", {}),
        "law_context": state.get("law_context", {}),
        "professional_analysis": _professional_review_context(state),
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
            "Rishiko edhe përmbledhjet e analizës profesionale pa krijuar fakte të reja.",
            "Mos shto gjetje përfundimtare të reja.",
            "Nëse një dokument i paklasifikuar mund të mbulojë një mungesë, "
            "kërko verifikim njerëzor.",
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


def _professional_review_context(state: AuditGraphState) -> dict:
    extracted_facts = state.get("extracted_facts", {})
    vkm_obligations = state.get("vkm_obligation_map", {})
    consistency_review = state.get("consistency_review", {})
    kolaudim_analysis = state.get("kolaudim_analysis", {})
    specialist_reviews = state.get("specialist_reviews", {})
    return {
        "fact_summary": (
            dict(extracted_facts.get("summary", {}))
            if isinstance(extracted_facts, dict)
            else {}
        ),
        "vkm_obligation_summary": (
            dict(vkm_obligations.get("summary", {}))
            if isinstance(vkm_obligations, dict)
            else {}
        ),
        "consistency_summary": (
            dict(consistency_review.get("summary", {}))
            if isinstance(consistency_review, dict)
            else {}
        ),
        "kolaudim_readiness": (
            kolaudim_analysis.get("readiness")
            if isinstance(kolaudim_analysis, dict)
            else None
        ),
        "specialist_review_summary": (
            dict(specialist_reviews.get("summary", {}))
            if isinstance(specialist_reviews, dict)
            else {}
        ),
    }


def _safe_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
