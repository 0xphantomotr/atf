import re
import unicodedata
from typing import Any

from app.agents.state import AuditGraphState

PLACEHOLDER_PATTERN = re.compile(r"(?:\?{2,}|_{3,}|\.{5,}|\bxxx\b|\btodo\b)", re.I)
INTERNAL_TERM_PATTERN = re.compile(
    r"\b(?:verified_findings|consistency_review|document_type|parse_status|"
    r"langgraph|workflow|checklist|ai model|model ai)\b",
    re.I,
)
CORE_PUBLIC_FACTS = (
    "object_name",
    "location",
    "investor",
    "contractor",
    "supervisor",
    "kolaudator",
)


def verify_kolaudim_claims(state: AuditGraphState) -> AuditGraphState:
    state.setdefault("agent_trace", []).append("claim_verifier")
    draft = state.get("kolaudim_draft", {})
    if not isinstance(draft, dict) or draft.get("status") != "drafted":
        state["claim_verification"] = {
            "status": "skipped",
            "reason": "kolaudim_draft_not_available",
            "summary": {"issue_count": 0, "publishable": False},
        }
        return state

    dossier = state.get("professional_dossier", {})
    canonical = dossier.get("canonical_facts", {}) if isinstance(dossier, dict) else {}
    if not isinstance(canonical, dict):
        canonical = {}

    issues: list[dict[str, Any]] = []
    public_text = _draft_public_text(draft)
    if PLACEHOLDER_PATTERN.search(public_text):
        issues.append(
            {
                "code": "PUBLIC-PLACEHOLDER",
                "severity": "major",
                "message": "Drafti përmban placeholder ose fushë template të paplotësuar.",
            }
        )
    if INTERNAL_TERM_PATTERN.search(public_text):
        issues.append(
            {
                "code": "PUBLIC-INTERNAL-TERMS",
                "severity": "major",
                "message": "Drafti ekspozon terminologji të brendshme të sistemit.",
            }
        )

    sections = draft.get("sections", [])
    if not isinstance(sections, list) or len(sections) < 8:
        issues.append(
            {
                "code": "PUBLIC-STRUCTURE-SHORT",
                "severity": "minor",
                "message": "Akti ka më pak se tetë seksione profesionale.",
            }
        )

    facts_present = []
    for field in CORE_PUBLIC_FACTS:
        fact = canonical.get(field)
        if not isinstance(fact, dict):
            continue
        value = str(fact.get("value") or "").strip()
        if value and _material_value_present(value, public_text):
            facts_present.append(field)
        elif value:
            issues.append(
                {
                    "code": "PUBLIC-CANONICAL-FACT-OMITTED",
                    "severity": "minor",
                    "field": field,
                    "message": f"Fakti kanonik '{field}' nuk u gjet në tekstin publik.",
                }
            )

    for conflict in dossier.get("conflicts", []) if isinstance(dossier, dict) else []:
        if not isinstance(conflict, dict):
            continue
        selected = str(conflict.get("selected_value") or "")
        for alternative in conflict.get("alternatives", []):
            if not isinstance(alternative, dict):
                continue
            alternative_value = str(alternative.get("value") or "")
            if (
                alternative_value
                and _material_value_present(alternative_value, public_text)
                and not _material_value_present(selected, public_text)
            ):
                issues.append(
                    {
                        "code": "PUBLIC-CONFLICT-ALTERNATIVE-USED",
                        "severity": "major",
                        "field": conflict.get("field"),
                        "message": "Drafti përdor alternativën dhe jo faktin e zgjedhur kanonik.",
                    }
                )
                break

    major_count = sum(1 for issue in issues if issue["severity"] == "major")
    state["claim_verification"] = {
        "status": "verified" if not major_count else "needs_correction",
        "issues": issues,
        "summary": {
            "issue_count": len(issues),
            "major_issue_count": major_count,
            "canonical_public_fact_count": len(facts_present),
            "publishable": major_count == 0,
        },
    }
    if major_count:
        state["needs_human_review"] = True
    return state


def _draft_public_text(draft: dict[str, Any]) -> str:
    values = [
        str(draft.get("title") or ""),
        str(draft.get("executive_summary") or ""),
        str(draft.get("signature_note") or ""),
    ]
    sections = draft.get("sections", [])
    if isinstance(sections, list):
        for section in sections:
            if not isinstance(section, dict):
                continue
            values.extend(
                [
                    str(section.get("title") or ""),
                    str(section.get("body") or ""),
                ]
            )
    return "\n".join(values)


def _material_value_present(value: str, public_text: str) -> bool:
    normalized_value = _normalize(value)
    normalized_text = _normalize(public_text)
    if normalized_value and normalized_value in normalized_text:
        return True
    tokens = [
        token
        for token in normalized_value.split()
        if len(token) >= 3 and token not in {"shpk", "bashkia", "fshati"}
    ]
    if not tokens:
        return False
    required = min(len(tokens), 3)
    return sum(1 for token in tokens if token in normalized_text) >= required


def _normalize(value: str) -> str:
    value = unicodedata.normalize("NFKD", value)
    value = "".join(char for char in value if not unicodedata.combining(char))
    value = re.sub(r"[^a-zA-Z0-9]+", " ", value.lower())
    return " ".join(value.split())
