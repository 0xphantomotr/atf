import json
from typing import Any, Literal

from app.agents.claim_grounding import build_claim_evidence_catalog
from app.agents.llm import LLMReviewError, request_kolaudim_correction
from app.agents.nodes.kolaudim_writer import _normalize_kolaudim_draft
from app.agents.state import AuditGraphState
from app.ai.stages import ai_settings_for_stage


class ClaimVerificationError(RuntimeError):
    pass


MAX_SUPPLEMENTAL_EVIDENCE_PER_REGISTER = 8
ISSUE_SUPPORT_REGISTERS: dict[str, set[str]] = {
    "CLAIM-COMPLETION-EVIDENCE": {
        "construction_chronology",
        "declarations_and_conclusions",
    },
    "CLAIM-CONFORMITY-EVIDENCE": {
        "declarations_and_conclusions",
        "project_parameters",
        "technical_works",
    },
    "CLAIM-MEASUREMENT-EVIDENCE": {
        "declarations_and_conclusions",
        "project_parameters",
        "technical_works",
    },
    "CLAIM-TEST-EVIDENCE": {"materials_and_tests"},
    "CLAIM-SUITABILITY-EVIDENCE": {
        "declarations_and_conclusions",
        "materials_and_tests",
        "technical_works",
    },
}


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
        major_issues = [
            issue
            for issue in verification.get("issues", [])
            if isinstance(issue, dict) and issue.get("severity") == "major"
        ][:8]
        detail = _publish_block_detail(major_issues)
        raise ClaimVerificationError(
            "Drafti i Akt-Kolaudimit mbeti i pambështetur pas një korrigjimi: "
            f"{detail}. Nuk u prodhua dokument publik."
        )
    return state


def _publish_block_detail(issues: list[dict[str, Any]]) -> str:
    if not issues:
        return "verification_failed"
    details = [_issue_detail(issue) for issue in issues]
    return " | ".join(detail for detail in details if detail) or "verification_failed"


def _issue_detail(issue: dict[str, Any]) -> str:
    code = str(issue.get("code") or "verification_failed")
    if code != "PUBLIC-CONFLICT-ALTERNATIVE-USED":
        return code

    field = str(issue.get("field") or "unknown_field")
    selected = _clip(str(issue.get("selected_value") or ""))
    alternative = _clip(str(issue.get("alternative_value") or ""))
    selected_sources = _source_list(issue.get("selected_source_documents"))
    alternative_sources = _source_list(issue.get("alternative_source_documents"))
    parts = [
        f"{code}[field={field}",
        f"canonical={selected or '-'}",
        f"used={alternative or '-'}",
    ]
    if selected_sources:
        parts.append(f"canonical_sources={selected_sources}")
    if alternative_sources:
        parts.append(f"used_sources={alternative_sources}")
    return ", ".join(parts) + "]"


def _clip(value: str, limit: int = 90) -> str:
    value = " ".join(value.split())
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "…"


def _source_list(value: object) -> str:
    if not isinstance(value, list):
        return ""
    names = [str(item) for item in value if str(item).strip()][:3]
    return "; ".join(_clip(name, 70) for name in names)


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
    supplemental_ids = _supplemental_evidence_ids(
        verification.get("correction_instructions", []),
        evidence_catalog,
    )
    allowed_ids = cited_ids | supplemental_ids
    allowed_catalog = {
        evidence_id: _compact_evidence(evidence_catalog[evidence_id])
        for evidence_id in sorted(allowed_ids)
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
        "allowed_evidence_ids": sorted(allowed_ids),
        "supplemental_evidence_ids": sorted(supplemental_ids),
        "evidence_catalog": allowed_catalog,
        "instructions": [
            "Ruaj paragrafët e mbështetur dhe rendin profesional të seksioneve.",
            "Për çdo çështje hiq pretendimin, ktheje në kualifikim të saktë ose "
            "plotëso evidence_ids vetëm nga allowed_evidence_ids kur pretendimi "
            "mbështetet nga evidenca shtesë.",
            "Mos shto fakt ose seksion të ri; përdor evidencë shtesë vetëm për të "
            "mbështetur tekstin ekzistues ose për ta kualifikuar.",
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


def _supplemental_evidence_ids(
    correction_issues: object,
    evidence_catalog: dict[str, dict[str, Any]],
) -> set[str]:
    if not isinstance(correction_issues, list):
        return set()
    requested_registers: set[str] = set()
    for issue in correction_issues:
        if not isinstance(issue, dict):
            continue
        requested_registers.update(
            ISSUE_SUPPORT_REGISTERS.get(str(issue.get("code") or ""), set())
        )
    if not requested_registers:
        return set()

    selected: set[str] = set()
    counts = {register: 0 for register in requested_registers}
    for evidence_id, item in evidence_catalog.items():
        register = str(item.get("register") or "")
        if register not in requested_registers:
            continue
        if counts[register] >= MAX_SUPPLEMENTAL_EVIDENCE_PER_REGISTER:
            continue
        selected.add(evidence_id)
        counts[register] += 1
    return selected


def _compact_evidence(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": item.get("kind"),
        "register": item.get("register"),
        "field_name": item.get("field_name"),
        "value": item.get("value"),
        "law_reference": item.get("law_reference"),
        "description": item.get("description"),
        "selected_value": item.get("selected_value"),
        "source_documents": _compact_source_documents(item.get("source_references")),
    }


def _compact_source_documents(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    names: list[str] = []
    seen: set[str] = set()
    for source in value:
        if not isinstance(source, dict):
            continue
        name = str(source.get("source_document") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        names.append(name)
    return names[:3]
