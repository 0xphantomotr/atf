import json
import re
from typing import Any, Literal

from app.agents.claim_grounding import (
    build_claim_evidence_catalog,
    claim_source_references,
)
from app.agents.llm import AIQuotaLimitError, LLMReviewError, request_kolaudim_correction
from app.agents.nodes.kolaudim_writer import _normalize_kolaudim_draft
from app.agents.section_evidence import (
    MATERIAL_SECTION_CODE,
    MATERIAL_SECTION_TITLE,
    is_contextless_numeric_statement,
    is_material_boilerplate,
)
from app.agents.state import AuditGraphState
from app.ai.stages import ai_settings_for_stage


class ClaimVerificationError(RuntimeError):
    def __init__(self, message: str, *, issues: list[dict[str, Any]] | None = None) -> None:
        super().__init__(message)
        self.issues = [dict(issue) for issue in issues or [] if isinstance(issue, dict)]


CONCLUSION_LANGUAGE_CODE = "CLAIM-CONCLUSION-LEVEL-LANGUAGE"
LIMITING_LANGUAGE_PATTERN = re.compile(
    r"\b(?:nuk\s+(?:rezulton|provohet|vertetohet|konfirmohet)|"
    r"(?:nga|sipas|referuar)\s+dokumentacionit|"
    r"bazuar\s+ne\s+dokumentacion|ne\s+baze\s+te\s+dokumentacionit|"
    r"pa\s+u\s+verifikuar|mbetet\s+per\s+t\s+u\s+verifikuar|"
    r"per\s+t\s+u\s+verifikuar|kerkon\s+verifikim|duhet\s+verifikuar|"
    r"me\s+rezerv[eë]|e\s+kufizuar|projekt-akt)\b",
    re.I,
)
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
    except AIQuotaLimitError:
        raise
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
    _preserve_section_structure(normalized, original_draft=draft)
    _apply_verification_replacements(
        normalized,
        verification,
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


def _preserve_section_structure(
    corrected_draft: dict[str, Any],
    *,
    original_draft: dict[str, Any],
) -> None:
    """Keep correction content inside the already validated public section skeleton."""
    original_sections = original_draft.get("sections")
    corrected_sections = corrected_draft.get("sections")
    if not isinstance(original_sections, list) or not 10 <= len(original_sections) <= 12:
        return
    if not isinstance(corrected_sections, list):
        corrected_sections = []

    corrected_by_code = {
        str(section.get("code") or ""): section
        for section in corrected_sections
        if isinstance(section, dict) and str(section.get("code") or "")
    }
    restored_codes: set[str] = set()
    final_sections: list[dict[str, Any]] = []
    for original in original_sections:
        if not isinstance(original, dict):
            continue
        code = str(original.get("code") or "")
        corrected = corrected_by_code.get(code)
        if corrected is not None:
            final_sections.append(corrected)
            continue
        final_sections.append(dict(original))
        restored_codes.add(code)

    if not 10 <= len(final_sections) <= 12:
        return
    corrected_draft["sections"] = final_sections
    _align_claim_ledger_to_sections(
        corrected_draft,
        original_draft=original_draft,
        restored_codes=restored_codes,
    )


def _align_claim_ledger_to_sections(
    corrected_draft: dict[str, Any],
    *,
    original_draft: dict[str, Any],
    restored_codes: set[str],
) -> None:
    corrected_ledger = corrected_draft.get("claim_ledger")
    original_ledger = original_draft.get("claim_ledger")
    if not isinstance(corrected_ledger, list):
        corrected_ledger = []
    if not isinstance(original_ledger, list):
        original_ledger = []

    allowed_statements = {
        statement
        for section in corrected_draft.get("sections", [])
        if isinstance(section, dict)
        for statement in _section_paragraphs(section)
    }
    summary = str(corrected_draft.get("executive_summary") or "").strip()
    if summary:
        allowed_statements.add(summary)

    aligned = [
        claim
        for claim in corrected_ledger
        if isinstance(claim, dict)
        and str(claim.get("statement") or "").strip() in allowed_statements
        and str(claim.get("section_code") or "") not in restored_codes
    ]
    existing_statements = {
        str(claim.get("statement") or "").strip()
        for claim in aligned
        if isinstance(claim, dict)
    }
    for claim in original_ledger:
        if not isinstance(claim, dict):
            continue
        statement = str(claim.get("statement") or "").strip()
        section_code = str(claim.get("section_code") or "")
        if (
            section_code in restored_codes
            and statement in allowed_statements
            and statement not in existing_statements
        ):
            aligned.append(dict(claim))
            existing_statements.add(statement)
    corrected_draft["claim_ledger"] = aligned


def _section_paragraphs(section: dict[str, Any]) -> list[str]:
    body = str(section.get("body") or "")
    return [paragraph.strip() for paragraph in body.split("\n\n") if paragraph.strip()]


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
        if _is_conclusion_language_only(major_issues):
            repaired_count = _apply_conclusion_language_repair(draft, major_issues)
            if repaired_count:
                state["kolaudim_draft"] = draft
                state["kolaudim_language_repair"] = {
                    "status": "applied",
                    "repaired_claim_count": repaired_count,
                    "reason": CONCLUSION_LANGUAGE_CODE,
                }
                state.setdefault("agent_trace", []).append("conclusion_language_repair")
                from app.agents.nodes.claim_verifier import verify_kolaudim_claims

                verify_kolaudim_claims(state)
                verification = state.get("claim_verification", {})
                if isinstance(verification, dict) and verification.get("summary", {}).get(
                    "publishable"
                ):
                    return state
                major_issues = [
                    issue
                    for issue in verification.get("issues", [])
                    if isinstance(issue, dict) and issue.get("severity") == "major"
                ][:8]
        detail = _publish_block_detail(major_issues)
        raise ClaimVerificationError(
            "Drafti i Akt-Kolaudimit mbeti i pambështetur pas një korrigjimi: "
            f"{detail}. Nuk u prodhua dokument publik.",
            issues=major_issues,
        )
    return state


def _publish_block_detail(issues: list[dict[str, Any]]) -> str:
    if not issues:
        return "verification_failed"
    details = [_issue_detail(issue) for issue in issues]
    return " | ".join(detail for detail in details if detail) or "verification_failed"


def _is_conclusion_language_only(issues: list[dict[str, Any]]) -> bool:
    return bool(issues) and all(
        str(issue.get("code") or "") == CONCLUSION_LANGUAGE_CODE for issue in issues
    )


def _apply_conclusion_language_repair(
    draft: dict[str, Any],
    issues: list[dict[str, Any]],
) -> int:
    issue_claim_ids = {
        str(issue.get("claim_id") or "").strip()
        for issue in issues
        if str(issue.get("claim_id") or "").strip()
    }
    claim_ledger = draft.get("claim_ledger")
    if not isinstance(claim_ledger, list):
        return 0

    repaired_count = 0
    for claim in claim_ledger:
        if not isinstance(claim, dict):
            continue
        claim_id = str(claim.get("claim_id") or "").strip()
        if issue_claim_ids and claim_id not in issue_claim_ids:
            continue
        conclusion_level = str(claim.get("conclusion_level") or "").strip()
        if conclusion_level not in {"qualified", "not_proven"}:
            continue
        statement = str(claim.get("statement") or "").strip()
        if not statement or _has_limiting_language(statement):
            continue
        repaired = _qualified_statement(statement, conclusion_level)
        claim["statement"] = repaired
        _replace_statement_in_public_draft(draft, statement, repaired)
        repaired_count += 1
    return repaired_count


def _has_limiting_language(statement: str) -> bool:
    normalized = (
        statement.replace("ë", "e")
        .replace("Ë", "E")
        .replace("ç", "c")
        .replace("Ç", "C")
    )
    return bool(LIMITING_LANGUAGE_PATTERN.search(normalized))


def _qualified_statement(statement: str, conclusion_level: str) -> str:
    statement = statement.strip()
    if conclusion_level == "not_proven":
        return (
            "Nga dokumentacioni i administruar nuk provohet plotësisht se "
            f"{_lower_first(statement)}"
        )
    return f"Me rezervë dhe sipas dokumentacionit të administruar, {statement}"


def _lower_first(statement: str) -> str:
    if not statement:
        return statement
    return statement[:1].lower() + statement[1:]


def _replace_statement_in_public_draft(
    draft: dict[str, Any],
    old_statement: str,
    new_statement: str,
) -> None:
    if isinstance(draft.get("executive_summary"), str):
        draft["executive_summary"] = draft["executive_summary"].replace(
            old_statement,
            new_statement,
        )
    sections = draft.get("sections")
    if not isinstance(sections, list):
        return
    for section in sections:
        if not isinstance(section, dict):
            continue
        if isinstance(section.get("body"), str):
            section["body"] = section["body"].replace(old_statement, new_statement)


def _issue_detail(issue: dict[str, Any]) -> str:
    code = str(issue.get("code") or "verification_failed")
    if code == "PUBLIC-TABLE-FACT-NOT-CURRENT":
        field = str(issue.get("field") or "unknown_field")
        return f"{code}[field={field}]"
    if code == "PUBLIC-DETAIL-MISSING":
        field = str(issue.get("field") or "unknown_field")
        required = _clip(str(issue.get("required_value") or ""))
        sources = _source_list(issue.get("source_documents"))
        parts = [f"{code}[field={field}", f"required={required or '-'}"]
        if sources:
            parts.append(f"sources={sources}")
        return ", ".join(parts) + "]"
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
    issue_ids = _issue_evidence_ids(
        verification.get("correction_instructions", []),
        evidence_catalog,
    )
    allowed_ids = cited_ids | supplemental_ids | issue_ids
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
                "conclusion_level": claim.get("conclusion_level"),
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
        "issue_evidence_ids": sorted(issue_ids),
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


def _apply_verification_replacements(
    draft: dict[str, Any],
    verification: dict[str, Any],
    *,
    evidence_catalog: dict[str, dict[str, Any]] | None = None,
) -> None:
    issues = verification.get("correction_instructions", [])
    if not isinstance(issues, list):
        return
    replacements = [
        issue
        for issue in issues
        if isinstance(issue, dict)
        and issue.get("code") == "PUBLIC-CONFLICT-ALTERNATIVE-USED"
        and str(issue.get("selected_value") or "").strip()
        and str(issue.get("alternative_value") or "").strip()
    ]
    for issue in replacements:
        selected = str(issue.get("selected_value") or "").strip()
        alternative = str(issue.get("alternative_value") or "").strip()
        evidence_ids = [
            str(evidence_id)
            for evidence_id in issue.get("evidence_ids", [])
            if str(evidence_id).strip()
        ]
        for key in ("executive_summary", "signature_note"):
            if isinstance(draft.get(key), str):
                draft[key] = _replace_material_value(draft[key], alternative, selected)
        sections = draft.get("sections")
        if isinstance(sections, list):
            for section in sections:
                if not isinstance(section, dict):
                    continue
                for key in ("title", "body"):
                    if isinstance(section.get(key), str):
                        section[key] = _replace_material_value(
                            section[key],
                            alternative,
                            selected,
                        )
        ledger = draft.get("claim_ledger")
        if isinstance(ledger, list):
            for claim in ledger:
                if not isinstance(claim, dict) or not isinstance(claim.get("statement"), str):
                    continue
                statement = claim["statement"]
                replaced = _replace_material_value(statement, alternative, selected)
                if replaced == statement:
                    continue
                claim["statement"] = replaced
                raw_ids = claim.get("evidence_ids")
                if not isinstance(raw_ids, list):
                    raw_ids = []
                claim["evidence_ids"] = list(
                    dict.fromkeys([*map(str, raw_ids), *evidence_ids])
                )

    for issue in issues:
        if not isinstance(issue, dict) or issue.get("code") != "PUBLIC-DETAIL-MISSING":
            continue
        _append_required_public_detail(
            draft,
            issue,
            evidence_catalog=evidence_catalog or {},
        )

    _remove_public_technical_noise(draft, issues)
    for issue in issues:
        if (
            isinstance(issue, dict)
            and issue.get("code") == "PUBLIC-SECTION-EVIDENCE-MISSING"
        ):
            _append_section_evidence(
                draft,
                issue,
                evidence_catalog=evidence_catalog or {},
            )


def _remove_public_technical_noise(
    draft: dict[str, Any],
    issues: list[object],
) -> None:
    remove_material_generic = any(
        isinstance(issue, dict) and issue.get("code") == "PUBLIC-MATERIAL-GENERIC"
        for issue in issues
    )
    remove_numeric_noise = any(
        isinstance(issue, dict)
        and issue.get("code") == "PUBLIC-CONTEXTLESS-NUMERIC-LIST"
        for issue in issues
    )
    if not remove_material_generic and not remove_numeric_noise:
        return

    ledger = draft.get("claim_ledger")
    if not isinstance(ledger, list):
        return
    retained: list[object] = []
    removed_statements: list[str] = []
    for claim in ledger:
        if not isinstance(claim, dict):
            retained.append(claim)
            continue
        statement = str(claim.get("statement") or "").strip()
        should_remove = (
            remove_material_generic and is_material_boilerplate(statement)
        ) or (
            remove_numeric_noise and is_contextless_numeric_statement(statement)
        )
        if should_remove:
            removed_statements.append(statement)
        else:
            retained.append(claim)
    draft["claim_ledger"] = retained
    for statement in removed_statements:
        _remove_statement_from_public_draft(draft, statement)


def _remove_statement_from_public_draft(
    draft: dict[str, Any],
    statement: str,
) -> None:
    if not statement:
        return
    if isinstance(draft.get("executive_summary"), str):
        draft["executive_summary"] = _remove_paragraph(
            draft["executive_summary"],
            statement,
        )
    sections = draft.get("sections")
    if not isinstance(sections, list):
        return
    for section in sections:
        if isinstance(section, dict) and isinstance(section.get("body"), str):
            section["body"] = _remove_paragraph(section["body"], statement)


def _remove_paragraph(body: str, statement: str) -> str:
    paragraphs = [paragraph.strip() for paragraph in body.split("\n\n")]
    retained = [paragraph for paragraph in paragraphs if paragraph != statement]
    if len(retained) != len(paragraphs):
        return "\n\n".join(paragraph for paragraph in retained if paragraph)
    return re.sub(r"\n{3,}", "\n\n", body.replace(statement, "")).strip()


def _append_section_evidence(
    draft: dict[str, Any],
    issue: dict[str, Any],
    *,
    evidence_catalog: dict[str, dict[str, Any]],
) -> None:
    statement = str(issue.get("required_statement") or "").strip()
    required_values = [
        str(value).strip()
        for value in issue.get("required_values", [])
        if str(value).strip()
    ]
    if not statement or (
        required_values
        and all(_draft_contains_value(draft, value) for value in required_values)
    ):
        return

    section_code = str(issue.get("section_code") or MATERIAL_SECTION_CODE)
    section_title = str(issue.get("section_title") or MATERIAL_SECTION_TITLE)
    section = _find_or_create_material_section(
        draft,
        section_code=section_code,
        section_title=section_title,
    )
    body = str(section.get("body") or "").strip()
    section["body"] = f"{body}\n\n{statement}".strip()

    evidence_ids = [
        str(evidence_id)
        for evidence_id in issue.get("evidence_ids", [])
        if str(evidence_id).strip()
    ]
    ledger = draft.setdefault("claim_ledger", [])
    if not isinstance(ledger, list):
        ledger = []
        draft["claim_ledger"] = ledger
    ledger.append(
        {
            "claim_id": f"deterministic_section_evidence:{len(ledger)}",
            "section_code": str(section.get("code") or section_code),
            "statement": statement,
            "claim_type": "documented_fact",
            "conclusion_level": "proven",
            "evidence_ids": evidence_ids,
            "confidence": 1.0,
            "source_references": claim_source_references(
                evidence_ids,
                evidence_catalog,
            ),
        }
    )


def _find_or_create_material_section(
    draft: dict[str, Any],
    *,
    section_code: str,
    section_title: str,
) -> dict[str, Any]:
    sections = draft.setdefault("sections", [])
    if not isinstance(sections, list):
        sections = []
        draft["sections"] = sections
    for section in sections:
        if not isinstance(section, dict):
            continue
        code = str(section.get("code") or "").lower()
        title = str(section.get("title") or "").lower()
        if code == section_code or any(
            term in f"{code} {title}"
            for term in ("material", "quality", "cilësi", "armatur", "prova")
        ):
            return section
    section = {"code": section_code, "title": section_title, "body": ""}
    sections.append(section)
    return section


def _append_required_public_detail(
    draft: dict[str, Any],
    issue: dict[str, Any],
    *,
    evidence_catalog: dict[str, dict[str, Any]],
) -> None:
    field = str(issue.get("field") or "").strip()
    value = str(issue.get("required_value") or "").strip()
    if not field or not value or _draft_contains_value(draft, value):
        return

    section_code, section_title = _detail_section(
        field,
        str(issue.get("register") or ""),
    )
    statement = _detail_statement(field, value)
    sections = draft.setdefault("sections", [])
    if not isinstance(sections, list):
        sections = []
        draft["sections"] = sections
    section = next(
        (
            item
            for item in sections
            if isinstance(item, dict) and str(item.get("code") or "") == section_code
        ),
        None,
    )
    if section is None:
        section = {"code": section_code, "title": section_title, "body": ""}
        sections.append(section)
    body = str(section.get("body") or "").strip()
    section["body"] = f"{body}\n\n{statement}".strip()

    evidence_ids = [
        str(item)
        for item in issue.get("evidence_ids", [])
        if str(item).strip()
    ]
    ledger = draft.setdefault("claim_ledger", [])
    if not isinstance(ledger, list):
        ledger = []
        draft["claim_ledger"] = ledger
    ledger.append(
        {
            "claim_id": f"deterministic_detail:{field}:{len(ledger)}",
            "section_code": section_code,
            "statement": statement,
            "claim_type": "documented_fact",
            "conclusion_level": "proven",
            "evidence_ids": evidence_ids,
            "confidence": 1.0,
            "source_references": claim_source_references(
                evidence_ids,
                evidence_catalog,
            ),
        }
    )


def _detail_section(field: str, register: str) -> tuple[str, str]:
    if "permit" in field or register == "permits_property_licenses":
        return "legal_and_administrative", "Baza ligjore dhe administrative"
    if "contract" in field or register == "contracts_and_economics":
        return "parties_and_contracts", "Palët dhe marrëdhëniet kontraktore"
    if "date" in field or register == "construction_chronology":
        return "execution_and_chronology", "Kronologjia e punimeve"
    if register == "materials_and_tests":
        return "quality_and_hidden_works", "Materialet, provat dhe cilësia"
    return "design_and_parameters", "Të dhënat teknike të objektit"


def _detail_statement(field: str, value: str) -> str:
    labels = {
        "construction_permit_number": "Leja e ndërtimit",
        "construction_permit_protocol": "Protokolli i lejes së ndërtimit",
        "construction_permit_date": "Data e lejes së ndërtimit",
        "development_permit_number": "Leja e zhvillimit",
        "development_permit_protocol": "Protokolli i lejes së zhvillimit",
        "development_permit_date": "Data e lejes së zhvillimit",
        "contractor_contract_reference": "Referenca e kontratës së sipërmarrjes",
        "supervisor_contract_reference": "Referenca e kontratës së mbikëqyrjes",
        "kolaudator_contract_reference": "Referenca e kontratës së kolaudimit",
    }
    label = labels.get(field, field.replace("_", " ").capitalize())
    return f"{label}: {value}."


def _draft_contains_value(draft: dict[str, Any], value: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "", value.casefold())
    if not normalized:
        return True
    public_parts = [
        str(draft.get("executive_summary") or ""),
        str(draft.get("signature_note") or ""),
    ]
    public_parts.extend(
        str(section.get("body") or "")
        for section in draft.get("sections", [])
        if isinstance(section, dict)
    )
    public_text = re.sub(
        r"[^a-z0-9]+",
        "",
        " ".join(public_parts).casefold(),
    )
    return normalized in public_text


def _replace_material_value(text: str, alternative: str, selected: str) -> str:
    if not alternative or not selected or alternative == selected:
        return text
    if alternative in text:
        return text.replace(alternative, selected)
    if re.fullmatch(r"\d+", alternative):
        return re.sub(rf"(?<!\d){re.escape(alternative)}(?!\d)", selected, text)
    return text


def _issue_evidence_ids(
    correction_issues: object,
    evidence_catalog: dict[str, dict[str, Any]],
) -> set[str]:
    if not isinstance(correction_issues, list):
        return set()
    return {
        str(evidence_id)
        for issue in correction_issues
        if isinstance(issue, dict)
        for evidence_id in issue.get("evidence_ids", [])
        if str(evidence_id) in evidence_catalog
    }


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
