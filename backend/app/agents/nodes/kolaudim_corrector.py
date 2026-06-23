import json
from typing import Any, Literal

from app.agents.claim_grounding import build_claim_evidence_catalog
from app.agents.llm import LLMReviewError, request_kolaudim_correction
from app.agents.nodes.kolaudim_writer import _normalize_kolaudim_draft
from app.agents.state import AuditGraphState
from app.ai.stages import ai_settings_for_stage


class ClaimVerificationError(RuntimeError):
    pass


def route_after_claim_verification(
    state: AuditGraphState,
) -> Literal["correct", "finalize"]:
    verification = state.get("claim_verification", {})
    correction = state.get("kolaudim_correction", {})
    attempt_count = (
        int(correction.get("attempt_count") or 0)
        if isinstance(correction, dict)
        else 0
    )
    if (
        state.get("job", {}).get("job_type") == "kolaudim_act"
        and isinstance(verification, dict)
        and verification.get("status") == "needs_correction"
        and attempt_count < 1
        and isinstance(state.get("ai_settings"), dict)
        and state["ai_settings"].get("api_key")
    ):
        return "correct"
    return "finalize"


def correct_kolaudim_draft(state: AuditGraphState) -> AuditGraphState:
    state.setdefault("agent_trace", []).append("kolaudim_corrector")
    verification = state.get("claim_verification", {})
    draft = state.get("kolaudim_draft", {})
    ai_settings = state.get("ai_settings", {})
    if (
        not isinstance(verification, dict)
        or verification.get("status") != "needs_correction"
        or not isinstance(draft, dict)
        or draft.get("status") != "drafted"
        or not isinstance(ai_settings, dict)
        or not ai_settings.get("api_key")
    ):
        state["kolaudim_correction"] = {
            "status": "skipped",
            "attempt_count": 0,
            "reason": "correction_not_available",
        }
        return state
    ai_settings = ai_settings_for_stage(ai_settings, "correction")

    catalog = build_claim_evidence_catalog(state)
    correction_input = _build_correction_input(
        draft,
        verification=verification,
        evidence_catalog=catalog,
        ai_settings=ai_settings,
    )
    try:
        corrected = request_kolaudim_correction(
            correction_input,
            ai_settings=ai_settings,
        )
    except LLMReviewError as exc:
        state["kolaudim_correction"] = {
            "status": "failed",
            "attempt_count": 1,
            "reason": str(exc)[:500],
            "provider": ai_settings.get("provider"),
            "model": ai_settings.get("model"),
        }
        return state

    normalized = _normalize_kolaudim_draft(
        corrected,
        evidence_catalog=catalog,
    )
    normalized["provider"] = ai_settings.get("provider")
    normalized["model"] = ai_settings.get("model")
    normalized["api_key_hint"] = ai_settings.get("api_key_hint")
    state["kolaudim_draft"] = normalized
    state["kolaudim_correction"] = {
        "status": "corrected",
        "attempt_count": 1,
        "issue_count": len(verification.get("correction_instructions", [])),
        "provider": ai_settings.get("provider"),
        "model": ai_settings.get("model"),
    }
    return state


def enforce_publishable_kolaudim(state: AuditGraphState) -> AuditGraphState:
    state.setdefault("agent_trace", []).append("claim_verification_gate")
    if state.get("job", {}).get("job_type") != "kolaudim_act":
        return state
    draft = state.get("kolaudim_draft", {})
    verification = state.get("claim_verification", {})
    if not isinstance(draft, dict) or draft.get("status") != "drafted":
        return state
    if not isinstance(verification, dict) or not verification.get("summary", {}).get(
        "publishable"
    ):
        issue_codes = [
            str(issue.get("code"))
            for issue in verification.get("issues", [])
            if isinstance(issue, dict) and issue.get("severity") == "major"
        ][:8]
        detail = ", ".join(issue_codes) or "verification_failed"
        raise ClaimVerificationError(
            "Drafti i Akt-Kolaudimit mbeti i pambështetur pas një korrigjimi: "
            f"{detail}. Nuk u prodhua dokument publik."
        )
    return state


def _build_correction_input(
    draft: dict[str, Any],
    *,
    verification: dict[str, Any],
    evidence_catalog: dict[str, dict[str, Any]],
    ai_settings: dict[str, Any],
) -> dict[str, Any]:
    cited_ids = {
        str(evidence_id)
        for claim in draft.get("claim_ledger", [])
        if isinstance(claim, dict)
        for evidence_id in claim.get("evidence_ids", [])
        if str(evidence_id) in evidence_catalog
    }
    allowed_catalog = {
        evidence_id: _compact_evidence(evidence_catalog[evidence_id])
        for evidence_id in sorted(cited_ids)
    }
    public_draft = {
        "title": draft.get("title"),
        "executive_summary": draft.get("executive_summary"),
        "sections": [
            {
                "code": section.get("code"),
                "title": section.get("title"),
                "body": section.get("body"),
            }
            for section in draft.get("sections", [])
            if isinstance(section, dict)
        ],
        "claim_ledger": [
            {
                "claim_id": claim.get("claim_id"),
                "section_code": claim.get("section_code"),
                "statement": claim.get("statement"),
                "claim_type": claim.get("claim_type"),
                "evidence_ids": claim.get("evidence_ids", []),
                "confidence": claim.get("confidence"),
            }
            for claim in draft.get("claim_ledger", [])
            if isinstance(claim, dict)
        ],
        "reservations": draft.get("reservations", []),
        "human_completion_items": draft.get("human_completion_items", []),
        "signature_note": draft.get("signature_note"),
        "confidence": draft.get("confidence"),
    }
    payload = {
        "current_draft": public_draft,
        "correction_issues": verification.get("correction_instructions", []),
        "allowed_evidence_ids": sorted(cited_ids),
        "evidence_catalog": allowed_catalog,
        "instructions": [
            "Ruaj paragrafët e mbështetur dhe rendin profesional të seksioneve.",
            "Për çdo çështje hiq pretendimin ose ktheje në kualifikim të saktë.",
            "Mos shto fakt, evidencë ose seksion të ri.",
        ],
        "budget": {
            "provider": ai_settings.get("provider"),
            "model": ai_settings.get("model"),
            "estimated_input_tokens": 0,
        },
    }
    payload["budget"]["estimated_input_tokens"] = max(
        1,
        len(json.dumps(payload, ensure_ascii=False, separators=(",", ":"))) // 3,
    )
    return payload


def _compact_evidence(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": item.get("kind"),
        "register": item.get("register"),
        "field_name": item.get("field_name"),
        "value": item.get("value"),
        "law_reference": item.get("law_reference"),
        "description": item.get("description"),
        "selected_value": item.get("selected_value"),
    }
