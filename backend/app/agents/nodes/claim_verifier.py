import re
import unicodedata
from typing import Any

from app.agents.claim_grounding import (
    build_claim_evidence_catalog,
    current_file_version_ids,
    evidence_is_current,
)
from app.agents.state import AuditGraphState

PLACEHOLDER_PATTERN = re.compile(r"(?:\?{2,}|_{3,}|\.{5,}|\bxxx\b|\btodo\b)", re.I)
INTERNAL_TERM_PATTERN = re.compile(
    r"\b(?:verified_findings|consistency_review|document_type|parse_status|"
    r"langgraph|workflow|checklist|ai model|model ai)\b",
    re.I,
)
PHYSICAL_INSPECTION_PATTERN = re.compile(
    r"\b(?:nga|gjate)\s+(?:inspektimi|verifikimi)\s+(?:fizik|ne terren)|"
    r"\bu konstatua\s+ne\s+terren|\bmatjet\s+e\s+kryera\s+nga\s+kolaudatori",
    re.I,
)
CORE_PUBLIC_FACTS = (
    "object_name",
    "location",
    "investor",
    "owner",
    "contractor",
    "supervisor",
    "kolaudator",
)
PUBLIC_TABLE_FACTS = CORE_PUBLIC_FACTS + (
    "supervisor_license",
    "kolaudator_license",
)
CLAIM_TYPES = {"documented_fact", "professional_inference", "qualification"}
DIRECT_EVIDENCE_KINDS = {
    "register_entry",
    "canonical_fact",
    "verified_law_reference",
}

SENSITIVE_CLAIMS: tuple[
    tuple[str, re.Pattern[str], tuple[frozenset[str], ...], str], ...
] = (
    (
        "CLAIM-COMPLETION-EVIDENCE",
        re.compile(
            r"\b(?:punimet|objekti)\b.{0,60}\b(?:jane|eshte|u)\s+"
            r"(?:perfunduar|realizuar\s+teresisht)\b",
            re.I,
        ),
        (frozenset({"construction_chronology", "declarations_and_conclusions"}),),
        "Përfundimi i punimeve kërkon evidencë kronologjie ose deklaratë përfundimi.",
    ),
    (
        "CLAIM-CONFORMITY-EVIDENCE",
        re.compile(r"\b(?:konform|ne perputhje me|perputhet me)\b", re.I),
        (
            frozenset(
                {
                    "declarations_and_conclusions",
                    "technical_works",
                    "project_parameters",
                }
            ),
        ),
        "Përputhshmëria kërkon evidencë teknike ose deklaratë të dokumentuar.",
    ),
    (
        "CLAIM-MEASUREMENT-EVIDENCE",
        re.compile(r"\bmatjet?\s+(?:perfundimtare|faktike|e kryera)\b", re.I),
        (
            frozenset(
                {
                    "project_parameters",
                    "technical_works",
                    "declarations_and_conclusions",
                }
            ),
        ),
        "Pretendimi për matje kërkon evidencë të drejtpërdrejtë të matjeve.",
    ),
    (
        "CLAIM-TEST-EVIDENCE",
        re.compile(r"\b(?:provat?|testet?|certifikatat?)\b.{0,50}\b(?:konfirmojne|"
                   r"vertetojne|deshmojne|rezultojne)\b", re.I),
        (frozenset({"materials_and_tests"}),),
        "Konkluzioni për prova ose certifikata kërkon evidencë të materialeve dhe provave.",
    ),
    (
        "CLAIM-SUITABILITY-EVIDENCE",
        re.compile(
            r"\b(?:i pershtatshem per shfrytezim|i gatshem per shfrytezim|"
            r"mund te merret ne perdorim)\b",
            re.I,
        ),
        (
            frozenset({"declarations_and_conclusions"}),
            frozenset({"technical_works"}),
            frozenset({"materials_and_tests"}),
        ),
        "Përshtatshmëria për shfrytëzim kërkon evidencë përfundimi, punimesh dhe cilësie.",
    ),
)


def verify_kolaudim_claims(state: AuditGraphState) -> AuditGraphState:
    state.setdefault("agent_trace", []).append("claim_verifier")
    draft = state.get("kolaudim_draft", {})
    if not isinstance(draft, dict) or draft.get("status") != "drafted":
        state["claim_verification"] = {
            "status": "skipped",
            "reason": "kolaudim_draft_not_available",
            "issues": [],
            "correction_instructions": [],
            "summary": {"issue_count": 0, "major_issue_count": 0, "publishable": False},
        }
        return state

    dossier = state.get("professional_dossier", {})
    canonical = dossier.get("canonical_facts", {}) if isinstance(dossier, dict) else {}
    if not isinstance(canonical, dict):
        canonical = {}

    catalog = build_claim_evidence_catalog(state)
    current_versions = current_file_version_ids(state)
    issues: list[dict[str, Any]] = []
    public_text = _draft_public_text(draft)
    _verify_public_shape(draft, public_text, issues)

    ledger = draft.get("claim_ledger", [])
    if not isinstance(ledger, list) or not ledger:
        issues.append(
            _issue(
                "PUBLIC-CLAIM-LEDGER-MISSING",
                "major",
                "Drafti nuk ka regjistër të brendshëm për pretendimet materiale.",
            )
        )
        ledger = []
    else:
        _verify_ledger_coverage(draft, ledger, issues)
        _verify_unique_claim_ids(ledger, issues)
        for claim in ledger:
            _verify_claim(
                claim,
                catalog=catalog,
                current_versions=current_versions,
                issues=issues,
            )

    _verify_public_table_facts(
        canonical,
        catalog=catalog,
        current_versions=current_versions,
        issues=issues,
    )
    facts_present = _verify_canonical_facts(canonical, public_text, issues)
    _verify_conflicting_alternatives(dossier, public_text, issues)

    major_issues = [issue for issue in issues if issue["severity"] == "major"]
    major_count = len(major_issues)
    claims_with_major_issues = {
        str(issue.get("claim_id"))
        for issue in major_issues
        if issue.get("claim_id")
    }
    correction_attempts = int(
        state.get("kolaudim_correction", {}).get("attempt_count") or 0
    ) if isinstance(state.get("kolaudim_correction"), dict) else 0
    state["claim_verification"] = {
        "status": "verified" if not major_count else "needs_correction",
        "issues": issues,
        "correction_instructions": [
            {
                "code": issue["code"],
                "claim_id": issue.get("claim_id"),
                "instruction": issue["message"],
            }
            for issue in issues
            if issue["severity"] == "major"
        ],
        "summary": {
            "issue_count": len(issues),
            "major_issue_count": major_count,
            "verified_claim_count": max(0, len(ledger) - len(claims_with_major_issues)),
            "claim_count": len(ledger),
            "canonical_public_fact_count": len(facts_present),
            "correction_attempt_count": correction_attempts,
            "publishable": major_count == 0,
        },
    }
    if major_count:
        state["needs_human_review"] = True
    return state


def _verify_public_shape(
    draft: dict[str, Any],
    public_text: str,
    issues: list[dict[str, Any]],
) -> None:
    if PLACEHOLDER_PATTERN.search(public_text):
        issues.append(
            _issue(
                "PUBLIC-PLACEHOLDER",
                "major",
                "Drafti përmban placeholder ose fushë template të paplotësuar.",
            )
        )
    if INTERNAL_TERM_PATTERN.search(public_text):
        issues.append(
            _issue(
                "PUBLIC-INTERNAL-TERMS",
                "major",
                "Drafti ekspozon terminologji të brendshme të sistemit.",
            )
        )
    sections = draft.get("sections", [])
    if not isinstance(sections, list) or not 10 <= len(sections) <= 12:
        issues.append(
            _issue(
                "PUBLIC-STRUCTURE-INVALID",
                "major",
                "Akti duhet të ketë nga dhjetë deri në dymbëdhjetë seksione profesionale.",
            )
        )
    if not str(draft.get("executive_summary") or "").strip():
        issues.append(
            _issue(
                "PUBLIC-SUMMARY-MISSING",
                "major",
                "Akti nuk ka përmbledhje ekzekutive të mbështetur.",
            )
        )
    section_codes: set[str] = set()
    for section in sections if isinstance(sections, list) else []:
        if not isinstance(section, dict):
            continue
        code = str(section.get("code") or "").strip()
        if not str(section.get("body") or "").strip():
            issues.append(
                _issue(
                    "PUBLIC-SECTION-EMPTY",
                    "major",
                    "Një seksion profesional nuk përmban narrativë.",
                )
            )
        if code and code in section_codes:
            issues.append(
                _issue(
                    "PUBLIC-SECTION-DUPLICATE",
                    "major",
                    "Akti përmban kode seksionesh të përsëritura.",
                )
            )
        section_codes.add(code)


def _verify_ledger_coverage(
    draft: dict[str, Any],
    ledger: list[object],
    issues: list[dict[str, Any]],
) -> None:
    ledger_statements = {
        _normalize(claim.get("statement"))
        for claim in ledger
        if isinstance(claim, dict) and claim.get("statement")
    }
    public_paragraphs = [str(draft.get("executive_summary") or "").strip()]
    for section in draft.get("sections", []):
        if not isinstance(section, dict):
            continue
        public_paragraphs.extend(str(section.get("body") or "").split("\n\n"))
    for paragraph in public_paragraphs:
        normalized = _normalize(paragraph)
        if normalized and normalized not in ledger_statements:
            issues.append(
                _issue(
                    "PUBLIC-CLAIM-UNCATALOGUED",
                    "major",
                    "Një paragraf publik nuk është regjistruar për verifikim evidence.",
                )
            )


def _verify_unique_claim_ids(
    ledger: list[object],
    issues: list[dict[str, Any]],
) -> None:
    seen: set[str] = set()
    for claim in ledger:
        if not isinstance(claim, dict):
            continue
        claim_id = str(claim.get("claim_id") or "").strip()
        if not claim_id:
            issues.append(
                _issue("CLAIM-ID-MISSING", "major", "Pretendimi nuk ka claim_id.")
            )
        elif claim_id in seen:
            issues.append(
                _issue(
                    "CLAIM-ID-DUPLICATE",
                    "major",
                    "Dy pretendime përdorin të njëjtin claim_id.",
                    claim_id,
                )
            )
        seen.add(claim_id)


def _verify_public_table_facts(
    canonical: dict[str, Any],
    *,
    catalog: dict[str, dict[str, Any]],
    current_versions: set[str],
    issues: list[dict[str, Any]],
) -> None:
    if not current_versions:
        return
    for field in PUBLIC_TABLE_FACTS:
        fact = canonical.get(field)
        if not isinstance(fact, dict) or not str(fact.get("value") or "").strip():
            continue
        evidence_id = f"canonical:{field}"
        evidence = catalog.get(evidence_id)
        if evidence is None or not evidence_is_current(
            evidence,
            current_version_ids=current_versions,
        ):
            issues.append(
                {
                    "code": "PUBLIC-TABLE-FACT-NOT-CURRENT",
                    "severity": "major",
                    "field": field,
                    "evidence_ids": [evidence_id],
                    "message": (
                        f"Fakti publik '{field}' nuk ka burim në versionet aktuale të projektit."
                    ),
                }
            )


def _verify_claim(
    claim: object,
    *,
    catalog: dict[str, dict[str, Any]],
    current_versions: set[str],
    issues: list[dict[str, Any]],
) -> None:
    if not isinstance(claim, dict):
        issues.append(_issue("CLAIM-MALFORMED", "major", "Pretendimi ka format të pavlefshëm."))
        return
    claim_id = str(claim.get("claim_id") or "").strip() or None
    statement = str(claim.get("statement") or "").strip()
    claim_type = str(claim.get("claim_type") or "").strip()
    confidence = claim.get("confidence")
    raw_ids = claim.get("evidence_ids")
    evidence_ids = (
        list(dict.fromkeys(str(item) for item in raw_ids if str(item).strip()))
        if isinstance(raw_ids, list)
        else []
    )
    if not statement:
        issues.append(_issue("CLAIM-TEXT-MISSING", "major", "Pretendimi nuk ka tekst.", claim_id))
    if claim_type not in CLAIM_TYPES:
        issues.append(
            _issue(
                "CLAIM-TYPE-INVALID",
                "major",
                "Pretendimi nuk është klasifikuar si fakt, inferencë ose kualifikim.",
                claim_id,
            )
        )
    if not isinstance(confidence, int | float) or not 0 <= float(confidence) <= 1:
        issues.append(
            _issue(
                "CLAIM-CONFIDENCE-INVALID",
                "major",
                "Pretendimi nuk ka confidence të vlefshëm nga 0 deri në 1.",
                claim_id,
            )
        )
    if not evidence_ids:
        issues.append(
            _issue(
                "CLAIM-EVIDENCE-MISSING",
                "major",
                "Pretendimi material nuk ka evidence_id.",
                claim_id,
            )
        )
        return

    known = [catalog[evidence_id] for evidence_id in evidence_ids if evidence_id in catalog]
    unknown = [evidence_id for evidence_id in evidence_ids if evidence_id not in catalog]
    if unknown:
        issues.append(
            _issue(
                "CLAIM-EVIDENCE-UNKNOWN",
                "major",
                "Pretendimi citon evidencë që nuk ekziston në snapshot-in e punës.",
                claim_id,
                evidence_ids=unknown,
            )
        )
    stale = [
        str(item.get("evidence_id"))
        for item in known
        if current_versions
        and not evidence_is_current(item, current_version_ids=current_versions)
    ]
    if stale:
        issues.append(
            _issue(
                "CLAIM-EVIDENCE-NOT-CURRENT",
                "major",
                "Pretendimi citon evidencë pa burim në versionet aktuale të projektit.",
                claim_id,
                evidence_ids=stale,
            )
        )
    if claim_type == "documented_fact" and known and not any(
        item.get("kind") in DIRECT_EVIDENCE_KINDS for item in known
    ):
        issues.append(
            _issue(
                "CLAIM-FACT-WITHOUT-DIRECT-EVIDENCE",
                "major",
                "Fakti i dokumentuar mbështetet vetëm në konflikt ose kufizim.",
                claim_id,
            )
        )

    normalized_statement = _normalize(statement)
    if PHYSICAL_INSPECTION_PATTERN.search(normalized_statement):
        issues.append(
            _issue(
                "CLAIM-PHYSICAL-INSPECTION-UNSUPPORTED",
                "major",
                "Teksti pretendon inspektim ose matje fizike të pakryer nga sistemi.",
                claim_id,
            )
        )

    evidence_registers = _evidence_registers(evidence_ids, catalog)
    for code, pattern, required_groups, message in SENSITIVE_CLAIMS:
        if not pattern.search(normalized_statement):
            continue
        if all(evidence_registers.isdisjoint(group) for group in required_groups):
            issues.append(_issue(code, "major", message, claim_id))
            continue
        missing_group = next(
            (group for group in required_groups if evidence_registers.isdisjoint(group)),
            None,
        )
        if missing_group is not None:
            issues.append(_issue(code, "major", message, claim_id))


def _evidence_registers(
    evidence_ids: list[str],
    catalog: dict[str, dict[str, Any]],
) -> set[str]:
    registers: set[str] = set()
    pending = list(evidence_ids)
    visited: set[str] = set()
    while pending:
        evidence_id = pending.pop()
        if evidence_id in visited:
            continue
        visited.add(evidence_id)
        item = catalog.get(evidence_id)
        if not isinstance(item, dict):
            continue
        if item.get("register"):
            registers.add(str(item["register"]))
        pending.extend(
            str(value)
            for value in item.get("supporting_evidence_ids", [])
            if str(value)
        )
    return registers


def _verify_canonical_facts(
    canonical: dict[str, Any],
    public_text: str,
    issues: list[dict[str, Any]],
) -> list[str]:
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
    return facts_present


def _verify_conflicting_alternatives(
    dossier: object,
    public_text: str,
    issues: list[dict[str, Any]],
) -> None:
    if not isinstance(dossier, dict):
        return
    for conflict in dossier.get("conflicts", []):
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
                        "message": "Drafti përdor alternativën dhe jo faktin kanonik.",
                    }
                )
                break


def _issue(
    code: str,
    severity: str,
    message: str,
    claim_id: str | None = None,
    *,
    evidence_ids: list[str] | None = None,
) -> dict[str, Any]:
    issue: dict[str, Any] = {"code": code, "severity": severity, "message": message}
    if claim_id:
        issue["claim_id"] = claim_id
    if evidence_ids:
        issue["evidence_ids"] = evidence_ids
    return issue


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
            values.extend([str(section.get("title") or ""), str(section.get("body") or "")])
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


def _normalize(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(character for character in text if not unicodedata.combining(character))
    text = re.sub(r"[^a-zA-Z0-9]+", " ", text.lower())
    return " ".join(text.split())
