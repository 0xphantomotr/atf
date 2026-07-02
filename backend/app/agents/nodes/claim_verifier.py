import re
import unicodedata
from typing import Any

from app.agents.claim_grounding import (
    build_claim_evidence_catalog,
    canonical_evidence_id,
    current_file_version_ids,
    evidence_is_current,
)
from app.agents.public_details import select_required_public_details
from app.agents.section_evidence import (
    build_section_evidence,
    is_contextless_numeric_statement,
    is_material_boilerplate,
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
UNSIGNED_AUTHORIZATION_PATTERN = re.compile(
    r"\b(?:autorizohet|lejohet|miratohet|pranohet)\s+(?:per\s+)?"
    r"(?:perdorim|shfrytezim)\b|"
    r"\bstruktura\s+eshte\s+(?:e\s+pranuar|funksionale)\b|"
    r"\bpunimet\s+jane\s+pranuar\b|"
    r"\bobjekti\s+eshte\s+(?:i\s+pranuar|funksional)\b",
    re.I,
)
LIMITING_STATEMENT_PATTERN = re.compile(
    r"\b(?:nuk\s+(?:rezulton|provohet|vertetohet|konfirmohet)|"
    r"(?:nga|sipas|referuar)\s+dokumentacionit|"
    r"bazuar\s+ne\s+dokumentacion|ne\s+baze\s+te\s+dokumentacionit|"
    r"pa\s+u\s+verifikuar|mbetet\s+per\s+t\s+u\s+verifikuar|"
    r"per\s+t\s+u\s+verifikuar|kerkon\s+verifikim|duhet\s+verifikuar|"
    r"me\s+rezerv[eë]|e\s+kufizuar|projekt-akt)\b",
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
PUBLIC_BLOCKING_CONFLICT_FIELDS = {
    "location",
    "investor",
    "owner",
    "contractor",
    "supervisor",
    "kolaudator",
    "construction_permit_number",
    "construction_permit_protocol",
    "construction_permit_date",
    "development_permit_number",
    "development_permit_protocol",
    "development_permit_date",
    "property_number",
    "cadastral_zone",
}
CLAIM_TYPES = {"documented_fact", "professional_inference", "qualification"}
CONCLUSION_LEVELS = {"proven", "qualified", "not_proven"}
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
            frozenset({"declarations_and_conclusions"}),
            frozenset({"technical_works", "project_parameters"}),
        ),
        (
            "Përputhshmëria kërkon njëkohësisht evidencë deklarative dhe "
            "evidencë teknike/projektuese; ndryshe formuloje si kualifikim."
        ),
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

    _enforce_canonical_public_values(draft, dossier)
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
    _verify_required_public_details(dossier, public_text, issues)
    _verify_section_specific_evidence(draft, dossier, public_text, issues)

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
            _correction_instruction(issue)
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
    conclusion_level = str(claim.get("conclusion_level") or "").strip()
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
    if conclusion_level not in CONCLUSION_LEVELS:
        issues.append(
            _issue(
                "CLAIM-CONCLUSION-LEVEL-INVALID",
                "major",
                "Pretendimi nuk ka conclusion_level të vlefshëm: proven, qualified ose not_proven.",
                claim_id,
            )
        )
    if claim_type == "qualification" and conclusion_level == "proven":
        issues.append(
            _issue(
                "CLAIM-CONCLUSION-LEVEL-MISMATCH",
                "major",
                "Kualifikimi nuk mund të shënohet si konkluzion proven.",
                claim_id,
            )
        )
    if conclusion_level == "not_proven" and claim_type != "qualification":
        issues.append(
            _issue(
                "CLAIM-CONCLUSION-LEVEL-MISMATCH",
                "major",
                "not_proven duhet të formulohet si qualification, jo si fakt pozitiv.",
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

    if conclusion_level in {"qualified", "not_proven"} and not _is_limiting_statement(
        normalized_statement,
        claim_type,
    ):
        issues.append(
            _issue(
                "CLAIM-CONCLUSION-LEVEL-LANGUAGE",
                "major",
                "qualified/not_proven kërkon formulim kufizues të qartë në tekst.",
                claim_id,
            )
        )

    if UNSIGNED_AUTHORIZATION_PATTERN.search(normalized_statement):
        issues.append(
            _issue(
                "CLAIM-UNSIGNED-AUTHORIZATION",
                "major",
                (
                    "Projekt-akti i gjeneruar nuk mund të autorizojë përdorim, "
                    "shfrytëzim ose pranim përfundimtar pa kontroll dhe nënshkrim "
                    "profesional."
                ),
                claim_id,
            )
        )

    evidence_registers = _evidence_registers(evidence_ids, catalog)
    for code, pattern, required_groups, message in SENSITIVE_CLAIMS:
        if not pattern.search(normalized_statement):
            continue
        if _is_limiting_statement(normalized_statement, claim_type):
            continue
        if conclusion_level != "proven":
            issues.append(
                _issue(
                    "CLAIM-CONCLUSION-LEVEL-MISMATCH",
                    "major",
                    (
                        "Pretendimi pozitiv për përfundim, përputhshmëri, matje, "
                        "prova ose shfrytëzim duhet të jetë proven ose të kualifikohet qartë."
                    ),
                    claim_id,
                )
            )
        if all(evidence_registers.isdisjoint(group) for group in required_groups):
            issues.append(_issue(code, "major", message, claim_id))
            continue
        missing_group = next(
            (group for group in required_groups if evidence_registers.isdisjoint(group)),
            None,
        )
        if missing_group is not None:
            issues.append(_issue(code, "major", message, claim_id))


def _is_limiting_statement(normalized_statement: str, claim_type: str) -> bool:
    return claim_type == "qualification" or bool(
        LIMITING_STATEMENT_PATTERN.search(normalized_statement)
    )


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


def _verify_required_public_details(
    dossier: dict[str, Any],
    public_text: str,
    issues: list[dict[str, Any]],
) -> None:
    for detail in select_required_public_details(dossier, max_items=18):
        if not detail.get("must_include"):
            continue
        value = str(detail.get("value") or "")
        if not value or _material_value_present(value, public_text):
            continue
        issues.append(
            {
                "code": "PUBLIC-DETAIL-MISSING",
                "severity": "major",
                "message": (
                    "Drafti nuk ruan një detaj teknik-ekonomik të provuar që duhet "
                    "të shfaqet në Akt."
                ),
                "field": detail.get("field_name"),
                "register": detail.get("register"),
                "required_value": value,
                "source_documents": detail.get("source_documents", []),
                "evidence_ids": [detail["evidence_id"]],
            }
        )


def _verify_section_specific_evidence(
    draft: dict[str, Any],
    dossier: dict[str, Any],
    public_text: str,
    issues: list[dict[str, Any]],
) -> None:
    section_evidence = build_section_evidence(dossier)
    material = section_evidence.get("materials_reinforcement")
    if not isinstance(material, dict):
        return

    required_values = [
        str(item.get("source_value") or "").strip()
        for item in material.get("quantities", [])
        if isinstance(item, dict) and str(item.get("source_value") or "").strip()
    ]
    if required_values and not all(
        _material_value_present(value, public_text) for value in required_values
    ):
        issues.append(
            {
                "code": "PUBLIC-SECTION-EVIDENCE-MISSING",
                "severity": "major",
                "message": (
                    "Seksioni i materialeve nuk ruan sintezën konkrete të sasive "
                    "projektuese të dokumentuara në dosje."
                ),
                "section_code": material.get("section_code"),
                "section_title": material.get("section_title"),
                "required_statement": material.get("statement"),
                "required_values": required_values,
                "evidence_ids": material.get("evidence_ids", []),
            }
        )

    ledger = draft.get("claim_ledger")
    if not isinstance(ledger, list):
        return
    for claim in ledger:
        if not isinstance(claim, dict):
            continue
        statement = str(claim.get("statement") or "").strip()
        if not statement:
            continue
        if is_material_boilerplate(statement):
            issues.append(
                {
                    "code": "PUBLIC-MATERIAL-GENERIC",
                    "severity": "major",
                    "message": (
                        "Seksioni i materialeve përdor formulim të përgjithshëm në "
                        "vend të fakteve konkrete që përmban dosja."
                    ),
                    "claim_id": claim.get("claim_id"),
                    "section_code": claim.get("section_code"),
                    "statement": statement,
                    "required_statement": material.get("statement"),
                    "evidence_ids": material.get("evidence_ids", []),
                }
            )
        if is_contextless_numeric_statement(statement):
            issues.append(
                {
                    "code": "PUBLIC-CONTEXTLESS-NUMERIC-LIST",
                    "severity": "major",
                    "message": (
                        "Teksti publik përmban një listë numerike pa etiketa, njësi "
                        "ose kuptim teknik të provueshëm."
                    ),
                    "claim_id": claim.get("claim_id"),
                    "section_code": claim.get("section_code"),
                    "statement": statement,
                }
            )


def _verify_conflicting_alternatives(
    dossier: object,
    public_text: str,
    issues: list[dict[str, Any]],
) -> None:
    if not isinstance(dossier, dict):
        return
    canonical = dossier.get("canonical_facts", {})
    if not isinstance(canonical, dict):
        canonical = {}
    for conflict in dossier.get("conflicts", []):
        if not isinstance(conflict, dict):
            continue
        field = str(conflict.get("field") or "").strip()
        selected = str(conflict.get("selected_value") or "")
        selected_fact = canonical.get(field)
        selected_sources = (
            _source_documents(selected_fact)
            if isinstance(selected_fact, dict)
            else []
        )
        for alternative in conflict.get("alternatives", []):
            if not isinstance(alternative, dict):
                continue
            alternative_value = str(alternative.get("value") or "")
            if (
                alternative_value
                and not _material_values_equivalent(selected, alternative_value)
                and _material_value_present(alternative_value, public_text)
                and not _material_value_present(selected, public_text)
            ):
                severity = (
                    "major" if field in PUBLIC_BLOCKING_CONFLICT_FIELDS else "minor"
                )
                issues.append(
                    {
                        "code": "PUBLIC-CONFLICT-ALTERNATIVE-USED",
                        "severity": severity,
                        "field": field,
                        "selected_value": selected,
                        "alternative_value": alternative_value,
                        "selected_score": conflict.get("selected_score"),
                        "alternative_score": alternative.get("score"),
                        "selected_source_documents": selected_sources,
                        "alternative_source_documents": _source_documents(alternative),
                        "evidence_ids": [canonical_evidence_id(field)]
                        if isinstance(selected_fact, dict)
                        else [],
                        "message": (
                            "Drafti përdor alternativën dhe jo faktin kanonik "
                            f"për fushën '{field}'."
                        ),
                    }
                )
                break


PERMIT_REFERENCE_FIELDS = {
    "construction_permit_number",
    "construction_permit_protocol",
    "construction_permit_date",
    "development_permit_number",
    "development_permit_protocol",
    "development_permit_date",
}


def _enforce_canonical_public_values(
    draft: dict[str, Any],
    dossier: object,
) -> None:
    if not isinstance(dossier, dict):
        return
    conflicts = dossier.get("conflicts")
    if not isinstance(conflicts, list):
        return

    changes: list[dict[str, str]] = []
    for conflict in conflicts:
        if not isinstance(conflict, dict):
            continue
        field = str(conflict.get("field") or "").strip()
        if field not in PUBLIC_BLOCKING_CONFLICT_FIELDS:
            continue
        selected = str(conflict.get("selected_value") or "").strip()
        if not selected:
            continue
        evidence_ids = [canonical_evidence_id(field)]
        alternatives = conflict.get("alternatives")
        if not isinstance(alternatives, list):
            continue
        for alternative in alternatives:
            if not isinstance(alternative, dict):
                continue
            value = str(alternative.get("value") or "").strip()
            if not value or _material_values_equivalent(selected, value):
                continue
            replacement_count = _replace_public_draft_value(
                draft,
                field=field,
                alternative=value,
                selected=selected,
                evidence_ids=evidence_ids,
            )
            if replacement_count:
                changes.append(
                    {
                        "field": field,
                        "selected_value": selected,
                        "alternative_value": value,
                    }
                )

    if changes:
        draft["canonical_public_enforcement"] = changes


def _replace_public_draft_value(
    draft: dict[str, Any],
    *,
    field: str,
    alternative: str,
    selected: str,
    evidence_ids: list[str],
) -> int:
    replacement_count = 0
    for key in ("executive_summary", "signature_note"):
        if not isinstance(draft.get(key), str):
            continue
        replacement, count = _replace_public_value(
            draft[key],
            field=field,
            alternative=alternative,
            selected=selected,
        )
        if count:
            draft[key] = replacement
            replacement_count += count

    sections = draft.get("sections")
    if isinstance(sections, list):
        for section in sections:
            if not isinstance(section, dict):
                continue
            for key in ("title", "body"):
                if not isinstance(section.get(key), str):
                    continue
                replacement, count = _replace_public_value(
                    section[key],
                    field=field,
                    alternative=alternative,
                    selected=selected,
                )
                if count:
                    section[key] = replacement
                    replacement_count += count

    ledger = draft.get("claim_ledger")
    if isinstance(ledger, list):
        for claim in ledger:
            if not isinstance(claim, dict) or not isinstance(
                claim.get("statement"),
                str,
            ):
                continue
            replacement, count = _replace_public_value(
                claim["statement"],
                field=field,
                alternative=alternative,
                selected=selected,
            )
            if not count:
                continue
            claim["statement"] = replacement
            raw_ids = claim.get("evidence_ids")
            if not isinstance(raw_ids, list):
                raw_ids = []
            claim["evidence_ids"] = list(
                dict.fromkeys([*map(str, raw_ids), *evidence_ids])
            )
            replacement_count += count

    return replacement_count


def _replace_public_value(
    text: str,
    *,
    field: str,
    alternative: str,
    selected: str,
) -> tuple[str, int]:
    if not alternative or not selected or alternative == selected:
        return text, 0
    if field in PERMIT_REFERENCE_FIELDS:
        replacement, count = _replace_permit_reference(text, alternative, selected)
        if count:
            return replacement, count
    if alternative in text:
        return text.replace(alternative, selected), text.count(alternative)
    if re.fullmatch(r"\d+", alternative):
        return re.subn(rf"(?<!\d){re.escape(alternative)}(?!\d)", selected, text)
    return text, 0


def _replace_permit_reference(
    text: str,
    alternative: str,
    selected: str,
) -> tuple[str, int]:
    pattern = re.compile(
        r"\b((?:leje|lejes?)\s+(?:s[eë]\s+)?nd[eë]rtimit\s+)"
        r"(?:nr\.?\s*)?"
        rf"{re.escape(alternative)}"
        r"(?:\s*,\s*(?:protokoll|prot\.?)\s*(?:nr\.?\s*)?[\w./-]+)?"
        r"(?:\s*,\s*dat[eë]?\s*[\d./-]+)?",
        re.I,
    )
    return pattern.subn(lambda match: f"{match.group(1)}{selected}", text)


def _correction_instruction(issue: dict[str, Any]) -> dict[str, Any]:
    instruction = {
        "code": issue["code"],
        "claim_id": issue.get("claim_id"),
        "instruction": _correction_message(issue),
    }
    for key in (
        "field",
        "selected_value",
        "alternative_value",
        "selected_source_documents",
        "alternative_source_documents",
        "required_value",
        "source_documents",
        "register",
        "evidence_ids",
        "section_code",
        "section_title",
        "statement",
        "required_statement",
        "required_values",
    ):
        if key in issue:
            instruction[key] = issue[key]
    return instruction


def _correction_message(issue: dict[str, Any]) -> str:
    code = str(issue.get("code") or "")
    if code == "CLAIM-CONCLUSION-LEVEL-LANGUAGE":
        return (
            "Rishkruaje pretendimin me gjuhë kufizuese të qartë, p.sh. "
            "'nga dokumentacioni rezulton...', 'sipas dokumentacionit...' ose "
            "'mbetet për t'u verifikuar...', dhe mos e paraqit si konkluzion pozitiv."
        )
    if code == "CLAIM-CONCLUSION-LEVEL-MISMATCH":
        return (
            "Përputh claim_type, conclusion_level dhe tekstin publik: përdor proven "
            "vetëm kur evidenca e provon drejtpërdrejt; ndryshe vendos qualification/"
            "qualified dhe shto kufizim të qartë në tekst."
        )
    if code == "CLAIM-CONFORMITY-EVIDENCE":
        return (
            "Nëse mungon njëkohësisht evidenca deklarative dhe teknike/projektuese, "
            "mos deklaro përputhshmëri pozitive; formuloje si kualifikim sipas "
            "dokumentacionit dhe me verifikim profesional."
        )
    if code == "PUBLIC-DETAIL-MISSING":
        return (
            "Përfshi vlerën e provuar required_value në seksionin përkatës të Aktit "
            "duke përdorur vetëm evidence_ids e dhëna."
        )
    if code == "PUBLIC-CONFLICT-ALTERNATIVE-USED":
        return (
            "Zëvendëso alternative_value me selected_value në tekstin publik dhe "
            "në claim_ledger për fushën përkatëse. Mos e përdor alternative_value "
            "si fakt publik; përdor evidence_ids e dhëna për vlerën kanonike."
        )
    if code == "PUBLIC-SECTION-EVIDENCE-MISSING":
        return (
            "Përfshi required_statement në seksionin e materialeve duke ruajtur "
            "evidence_ids. Paraqiti sasitë vetëm si sasi projektuese të dokumentuara."
        )
    if code == "PUBLIC-MATERIAL-GENERIC":
        return (
            "Hiq formulimin e përgjithshëm për materiale/prova dhe zëvendësoje me "
            "required_statement dhe evidence_ids e dhëna."
        )
    if code == "PUBLIC-CONTEXTLESS-NUMERIC-LIST":
        return (
            "Hiq listën numerike pa etiketa. Mos publiko vlera vizatimi pa emërtim, "
            "njësi dhe lidhje të qartë me elementin teknik."
        )
    return str(issue.get("message") or "Korrigjo çështjen e verifikimit.")


def _source_documents(item: dict[str, Any]) -> list[str]:
    sources = item.get("source_documents")
    if not isinstance(sources, list):
        return []
    return [str(source) for source in sources if str(source).strip()][:5]


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
    numbers = _number_tokens(normalized_value)
    if len(set(numbers)) >= 2 and all(
        number in _number_tokens(normalized_text) for number in numbers
    ):
        return True
    tokens = [
        token
        for token in normalized_value.split()
        if len(token) >= 3 and token not in _LOW_SIGNAL_VALUE_TOKENS
    ]
    if not tokens:
        return False
    required = min(len(tokens), 3)
    return sum(1 for token in tokens if token in normalized_text) >= required


_LOW_SIGNAL_VALUE_TOKENS = {
    "bashkia",
    "date",
    "dat",
    "fshati",
    "kol",
    "leje",
    "ndertimi",
    "nr",
    "prot",
    "rep",
    "shpk",
}


def _material_values_equivalent(left: str, right: str) -> bool:
    left_normalized = _normalize(left)
    right_normalized = _normalize(right)
    if not left_normalized or not right_normalized:
        return False
    if left_normalized == right_normalized:
        return True
    left_numbers = set(_number_tokens(left_normalized))
    right_numbers = set(_number_tokens(right_normalized))
    if len(left_numbers) >= 2 and left_numbers == right_numbers:
        return True
    left_tokens = _significant_value_tokens(left_normalized)
    right_tokens = _significant_value_tokens(right_normalized)
    if not left_tokens or not right_tokens:
        return False
    return left_tokens.issubset(right_tokens) or right_tokens.issubset(left_tokens)


def _significant_value_tokens(value: str) -> set[str]:
    return {
        token
        for token in value.split()
        if len(token) >= 3 and token not in _LOW_SIGNAL_VALUE_TOKENS
    }


def _number_tokens(value: str) -> list[str]:
    return re.findall(r"\d+", value)


def _normalize(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(character for character in text if not unicodedata.combining(character))
    text = re.sub(r"[^a-zA-Z0-9]+", " ", text.lower())
    return " ".join(text.split())
