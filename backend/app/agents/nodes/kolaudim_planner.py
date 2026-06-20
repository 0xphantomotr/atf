from typing import Any

from app.agents.state import AuditGraphState


KOLAUDIM_SECTIONS: tuple[dict[str, Any], ...] = (
    {
        "code": "legal_basis",
        "title": "Baza ligjore dhe administrative",
        "required_fact_categories": ("permit", "formal_references"),
        "required_obligation_codes": ("VKM610-041-LEGAL-BASIS",),
    },
    {
        "code": "project_identity",
        "title": "Identifikimi i objektit dhe palëve",
        "required_fact_categories": (
            "object_name",
            "location",
            "investor",
            "contractor",
            "supervisor",
            "kolaudator",
        ),
        "required_obligation_codes": ("VKM610-041-SUPERVISION-FILE",),
    },
    {
        "code": "document_verification",
        "title": "Verifikimi i dokumentacionit teknik",
        "required_fact_categories": (),
        "required_obligation_codes": (
            "VKM610-038-043-SITE-START",
            "VKM610-041-SUPERVISION-FILE",
            "VKM610-041-QUALITY-HIDDEN-WORKS",
        ),
    },
    {
        "code": "phase_and_fact_verification",
        "title": "Verifikimi faktik sipas fazave të punimeve",
        "required_fact_categories": ("technical_metrics", "timeline"),
        "required_obligation_codes": ("VKM610-042-PHASE-CONTROL",),
    },
    {
        "code": "technical_economic_conclusion",
        "title": "Konkluzioni teknik-ekonomik",
        "required_fact_categories": ("economic_values",),
        "required_obligation_codes": ("VKM610-024-028-COMPLETION",),
    },
    {
        "code": "signature_package",
        "title": "Paketa për nënshkrim dhe përgjegjësi profesionale",
        "required_fact_categories": ("investor", "contractor", "supervisor", "kolaudator"),
        "required_obligation_codes": ("VKM610-041-SAFETY-SPECIAL",),
    },
)


def plan_kolaudim_act(state: AuditGraphState) -> AuditGraphState:
    state.setdefault("agent_trace", []).append("kolaudim_planner")
    fact_categories = state.get("extracted_facts", {}).get("categories", {})
    if not isinstance(fact_categories, dict):
        fact_categories = {}

    obligations = {
        item["code"]: item
        for item in state.get("vkm_obligation_map", {}).get("items", [])
        if isinstance(item, dict) and isinstance(item.get("code"), str)
    }

    sections = [
        _section_status(section, fact_categories, obligations)
        for section in KOLAUDIM_SECTIONS
    ]
    issue_count = int(
        state.get("consistency_review", {})
        .get("summary", {})
        .get("issue_count", 0)
        or 0
    )
    finding_count = len(state.get("verified_findings", state.get("findings", [])))

    readiness = _readiness(sections, issue_count, finding_count)
    human_review_questions = _human_review_questions(sections, issue_count, finding_count)
    state["kolaudim_analysis"] = {
        "target_output": "draft_akt_kolaudimi",
        "readiness": readiness,
        "professional_conclusion": _professional_conclusion(readiness),
        "sections": sections,
        "human_review_questions": human_review_questions,
        "method": (
            "Nxjerrje faktesh nga dokumentet e lexuara, hartëzim me VKM 610, "
            "kontroll konsistence dhe strukturim sipas praktikës së Akt Kolaudimit."
        ),
    }

    if readiness != "draft_ready_for_human_review":
        state["needs_human_review"] = True
    return state


def _section_status(
    section: dict[str, Any],
    fact_categories: dict[str, Any],
    obligations: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    missing_fact_categories = [
        category
        for category in section["required_fact_categories"]
        if not fact_categories.get(category)
    ]
    weak_obligations = [
        code
        for code in section["required_obligation_codes"]
        if obligations.get(code, {}).get("status") != "complete"
    ]

    if not missing_fact_categories and not weak_obligations:
        status = "ready"
    elif len(missing_fact_categories) < len(section["required_fact_categories"]):
        status = "partial"
    elif section["required_obligation_codes"] and len(weak_obligations) < len(
        section["required_obligation_codes"]
    ):
        status = "partial"
    else:
        status = "needs_input"

    return {
        "code": section["code"],
        "title": section["title"],
        "status": status,
        "missing_fact_categories": missing_fact_categories,
        "weak_obligation_codes": weak_obligations,
    }


def _readiness(
    sections: list[dict[str, Any]],
    issue_count: int,
    finding_count: int,
) -> str:
    if any(section["status"] == "needs_input" for section in sections):
        return "needs_core_evidence_before_kolaudim"
    if issue_count or finding_count:
        return "draft_with_reservations"
    return "draft_ready_for_human_review"


def _professional_conclusion(readiness: str) -> str:
    if readiness == "draft_ready_for_human_review":
        return (
            "Dosja ka bazë të mjaftueshme për draft Akt Kolaudimi, por akti final "
            "duhet verifikuar dhe nënshkruar nga profesionistët përgjegjës."
        )
    if readiness == "draft_with_reservations":
        return (
            "Mund të përgatitet draft Akt Kolaudimi me rezerva të shënuara, por "
            "gjetjet dhe çështjet e konsistencës duhet zgjidhur përpara aktit final."
        )
    return (
        "Dosja nuk ka ende evidencë të plotë për një Akt Kolaudimi profesional; "
        "duhen plotësuar faktet dhe dokumentet bazë."
    )


def _human_review_questions(
    sections: list[dict[str, Any]],
    issue_count: int,
    finding_count: int,
) -> list[str]:
    questions: list[str] = []
    for section in sections:
        if section["status"] == "ready":
            continue
        questions.append(
            f"Plotësoni/verifikoni seksionin '{section['title']}' para nënshkrimit final."
        )
    if finding_count:
        questions.append("Mbyllni gjetjet e hapura të dokumentacionit ose arsyetoni rezervat.")
    if issue_count:
        questions.append("Zgjidhni çështjet e konsistencës dhe placeholder-at në dokumente.")
    return questions[:10]
