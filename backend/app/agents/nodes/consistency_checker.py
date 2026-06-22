import re
import unicodedata
from typing import Any

from app.agents.state import AuditGraphState

PLACEHOLDER_PATTERN = re.compile(r"(\?{2,}|_{3,}|\.{5,}|\bxxx\b|\bTODO\b)", re.I)
SINGLE_SOURCE_FACT_CATEGORIES = (
    "object_name",
    "location",
    "investor",
    "contractor",
    "supervisor",
    "kolaudator",
)


def check_professional_consistency(state: AuditGraphState) -> AuditGraphState:
    state.setdefault("agent_trace", []).append("consistency_checker")
    issues: list[dict[str, Any]] = []

    dossier = state.get("professional_dossier", {})
    dossier_summary = dossier.get("summary", {}) if isinstance(dossier, dict) else {}
    has_persisted_analysis = (
        isinstance(dossier_summary, dict)
        and int(dossier_summary.get("persisted_analysis_count") or 0) > 0
    )
    if has_persisted_analysis:
        issues.extend(_dossier_conflict_issues(dossier))
        issues.extend(_dossier_integrity_issues(dossier))
    else:
        facts = state.get("extracted_facts", {}).get("categories", {})
        if isinstance(facts, dict):
            issues.extend(_fact_consistency_issues(facts))

    issues.extend(_placeholder_issues(state.get("documents", [])))
    issues.extend(_document_parse_issues(state.get("documents", [])))

    severity_counts = {"critical": 0, "major": 0, "minor": 0, "notice": 0}
    for issue in issues:
        severity = issue.get("severity")
        if severity in severity_counts:
            severity_counts[severity] += 1

    state["consistency_review"] = {
        "status": "needs_review" if issues else "clear",
        "issues": issues,
        "summary": {
            "issue_count": len(issues),
            "severity_counts": severity_counts,
        },
    }
    if any(issue["severity"] in {"critical", "major"} for issue in issues):
        state["needs_human_review"] = True
    return state


def _dossier_conflict_issues(dossier: dict[str, Any]) -> list[dict[str, Any]]:
    conflicts = dossier.get("conflicts")
    if not isinstance(conflicts, list):
        return []
    return [
        {
            "code": f"DOSSIER-CONFLICT-{str(conflict.get('field') or 'FACT').upper()}",
            "severity": "notice",
            "title": f"Vlera alternative për {conflict.get('field') or 'faktin'}",
            "description": (
                "Dosja përmban vlera alternative. Vlera kanonike është zgjedhur "
                "sipas autoritetit, besueshmërisë dhe konfirmimit ndërmjet burimeve."
            ),
            "evidence": {
                "selected_value": conflict.get("selected_value"),
                "alternatives": conflict.get("alternatives", [])[:4],
            },
        }
        for conflict in conflicts[:20]
        if isinstance(conflict, dict)
    ]


def _dossier_integrity_issues(dossier: dict[str, Any]) -> list[dict[str, Any]]:
    integrity_issues = dossier.get("integrity_issues")
    if not isinstance(integrity_issues, list):
        return []
    return [
        {
            "code": issue.get("code") or "DOSSIER-INTEGRITY",
            "severity": issue.get("severity") or "major",
            "title": "Mospërputhje në dosjen e konsoliduar",
            "description": issue.get("description") or "Kërkohet verifikim profesional.",
            "evidence": dict(issue),
        }
        for issue in integrity_issues
        if isinstance(issue, dict)
    ]


def _fact_consistency_issues(
    facts: dict[str, Any],
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for category in SINGLE_SOURCE_FACT_CATEGORIES:
        values = facts.get(category)
        if not isinstance(values, list):
            continue

        normalized_values: dict[str, list[dict[str, Any]]] = {}
        for item in values:
            if not isinstance(item, dict):
                continue
            value = _normalize_fact_value(str(item.get("value") or ""))
            if not value:
                continue
            normalized_values.setdefault(value, []).append(item)

        if len(normalized_values) <= 1:
            continue

        issues.append(
            {
                "code": f"CONSISTENCY-{category.upper()}",
                "severity": "notice",
                "title": f"U gjetën disa kandidatë për {category}",
                "description": (
                    "Dokumentet përmbajnë më shumë se një formulim të mundshëm. "
                    "Kjo nuk është domosdoshmërisht kontradiktë, por duhet zgjedhur "
                    "vlera që hyn në Akt Kolaudimi."
                ),
                "evidence": [
                    {
                        "value": source_items[0].get("value"),
                        "source_document": source_items[0].get("source_document"),
                    }
                    for source_items in normalized_values.values()
                ][:6],
            }
        )
    return issues


def _placeholder_issues(documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for document in documents:
        text = str(document.get("text_excerpt") or "")
        if not text or not PLACEHOLDER_PATTERN.search(text):
            continue

        snippets = []
        for line in text.splitlines():
            if PLACEHOLDER_PATTERN.search(line):
                snippets.append(" ".join(line.split())[:220])
            if len(snippets) >= 3:
                break

        issues.append(
            {
                "code": "CONSISTENCY-PLACEHOLDER",
                "severity": "major",
                "title": "Dokumenti përmban placeholder ose fusha të paplotësuara",
                "description": (
                    "Një Akt Kolaudimi profesional nuk duhet të ketë shenja template "
                    "si pikëpyetje, vija bosh ose pika të gjata."
                ),
                "source_document": document.get("original_filename"),
                "evidence": snippets,
            }
        )
    return issues


def _document_parse_issues(documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unknown = [
        document.get("original_filename")
        for document in documents
        if document.get("parse_status") == "parsed"
        and document.get("document_type") == "unknown"
    ]
    unsupported = [
        document.get("original_filename")
        for document in documents
        if document.get("parse_status") not in {"parsed", "pending"}
    ]

    issues: list[dict[str, Any]] = []
    if unknown:
        issues.append(
            {
                "code": "CONSISTENCY-UNKNOWN-DOCUMENTS",
                "severity": "major",
                "title": "Ka dokumente të lexuara por të paklasifikuara",
                "description": (
                    "Këto dokumente mund të përmbajnë evidencë për kolaudim dhe "
                    "duhet verifikuar para konkluzionit final."
                ),
                "evidence": unknown[:20],
            }
        )
    if unsupported:
        issues.append(
            {
                "code": "CONSISTENCY-UNSUPPORTED-DOCUMENTS",
                "severity": "major",
                "title": "Ka dokumente që nuk u lexuan nga sistemi",
                "description": (
                    "Dokumentet në format të pambështetur nuk mund të përdoren si "
                    "evidencë automatike për Akt Kolaudimi."
                ),
                "evidence": unsupported[:20],
            }
        )
    return issues


def _normalize_fact_value(value: str) -> str:
    value = unicodedata.normalize("NFKD", value)
    value = "".join(char for char in value if not unicodedata.combining(char))
    value = re.sub(r"[^a-zA-Z0-9]+", " ", value.lower())
    value = re.sub(
        r"\b(objekti|investitor|zhvillues|vendndodhja|adresa|kolaudator|mbikeqyres)\b",
        " ",
        value,
    )
    return " ".join(value.split())
