from typing import Any

from app.agents.state import AuditGraphState

KOLAUDIM_SECTIONS: tuple[dict[str, Any], ...] = (
    {
        "code": "legal_basis_and_scope",
        "title": "Baza ligjore dhe objekti i kolaudimit",
        "fact_fields": ("object_name", "location"),
        "evidence_sections": ("legal_and_administrative",),
    },
    {
        "code": "project_identity_and_parties",
        "title": "Identifikimi i objektit dhe palëve përgjegjëse",
        "fact_fields": (
            "investor",
            "contractor",
            "contractor_license",
            "supervisor",
            "supervisor_license",
            "designer",
            "kolaudator",
            "kolaudator_license",
        ),
        "evidence_sections": ("parties_and_contracts",),
    },
    {
        "code": "permits_property_and_approved_design",
        "title": "Lejet, pronësia dhe projekti i miratuar",
        "fact_fields": (
            "development_permit_number",
            "construction_permit_number",
            "construction_permit_protocol",
            "construction_permit_date",
            "property_number",
            "cadastral_zone",
        ),
        "evidence_sections": ("legal_and_administrative", "design_and_parameters"),
    },
    {
        "code": "technical_parameters",
        "title": "Të dhënat dhe parametrat teknikë të objektit",
        "fact_fields": (
            "site_area",
            "footprint_area",
            "total_construction_area",
            "maximum_height",
            "floors_above_ground",
            "floors_below_ground",
        ),
        "evidence_sections": ("design_and_parameters",),
    },
    {
        "code": "geology_seismicity_and_setting_out",
        "title": "Kushtet gjeologo-inxhinierike, sizmike dhe piketimi",
        "fact_fields": ("soil_bearing_capacity", "seismic_intensity"),
        "evidence_sections": ("design_and_parameters", "execution_and_chronology"),
    },
    {
        "code": "contracts_values_and_deadlines",
        "title": "Kontratat, vlerat dhe afatet",
        "fact_fields": (
            "planned_value",
            "final_value",
            "start_date",
            "completion_date",
        ),
        "evidence_sections": ("parties_and_contracts", "technical_economic"),
    },
    {
        "code": "execution_chronology",
        "title": "Ecuria dhe fazat e realizimit të punimeve",
        "fact_fields": ("start_date", "completion_date"),
        "evidence_sections": ("execution_and_chronology",),
    },
    {
        "code": "hidden_works_and_structure",
        "title": "Punimet e maskuara dhe elementet konstruktive",
        "fact_fields": (),
        "evidence_sections": ("quality_and_hidden_works", "execution_and_chronology"),
    },
    {
        "code": "materials_tests_and_quality",
        "title": "Materialet, provat dhe kontrolli i cilësisë",
        "fact_fields": (),
        "evidence_sections": ("quality_and_hidden_works",),
    },
    {
        "code": "measurements_and_conformity",
        "title": "Matjet përfundimtare dhe përputhshmëria me projektin",
        "fact_fields": (
            "site_area",
            "footprint_area",
            "total_construction_area",
        ),
        "evidence_sections": ("design_and_parameters", "completion_and_conclusion"),
    },
    {
        "code": "technical_economic_conclusion",
        "title": "Konkluzioni tekniko-ekonomik",
        "fact_fields": ("final_value",),
        "evidence_sections": ("technical_economic", "completion_and_conclusion"),
    },
    {
        "code": "copies_and_signatures",
        "title": "Hartimi i aktit dhe nënshkrimet",
        "fact_fields": ("investor", "contractor", "supervisor", "kolaudator"),
        "evidence_sections": (),
    },
)


def plan_kolaudim_act(state: AuditGraphState) -> AuditGraphState:
    state.setdefault("agent_trace", []).append("kolaudim_planner")
    dossier = state.get("professional_dossier", {})
    canonical_facts = dossier.get("canonical_facts", {}) if isinstance(dossier, dict) else {}
    evidence = dossier.get("evidence_by_section", {}) if isinstance(dossier, dict) else {}
    if not isinstance(canonical_facts, dict):
        canonical_facts = {}
    if not isinstance(evidence, dict):
        evidence = {}

    sections = [
        _section_plan(section, canonical_facts, evidence)
        for section in KOLAUDIM_SECTIONS
    ]
    missing_core_fields = (
        list(dossier.get("missing_core_fields", [])) if isinstance(dossier, dict) else []
    )
    conflicts = list(dossier.get("conflicts", [])) if isinstance(dossier, dict) else []
    state["kolaudim_analysis"] = {
        "target_output": "professional_akt_kolaudimi",
        "generation_mode": "always_generate_with_evidence_qualification",
        "readiness": _readiness(missing_core_fields, conflicts),
        "professional_conclusion": (
            "Akti hartohet automatikisht nga faktet kanonike dhe evidenca e dosjes. "
            "Pasiguritë pasqyrohen vetëm aty ku ndikojnë konkluzionin profesional."
        ),
        "sections": sections,
        "unresolved_core_fields": missing_core_fields,
        "resolved_conflict_count": len(conflicts),
        "method": (
            "Strukturë e Akt-Kolaudimit tekniko-ekonomik sipas praktikës profesionale, "
            "me gjurmueshmëri te dokumenti burimor për çdo fakt material."
        ),
    }
    return state


def _section_plan(
    section: dict[str, Any],
    canonical_facts: dict[str, Any],
    evidence: dict[str, Any],
) -> dict[str, Any]:
    available_fields = [
        field for field in section["fact_fields"] if canonical_facts.get(field)
    ]
    missing_fields = [
        field for field in section["fact_fields"] if not canonical_facts.get(field)
    ]
    source_documents = sorted(
        {
            filename
            for evidence_section in section["evidence_sections"]
            for filename in evidence.get(evidence_section, [])
            if isinstance(filename, str)
        }
    )
    return {
        "code": section["code"],
        "title": section["title"],
        "available_fact_fields": available_fields,
        "unresolved_fact_fields": missing_fields,
        "source_documents": source_documents,
        "writing_instruction": (
            "Shkruaj vetëm nga evidenca e disponueshme; mos përdor gjuhë checklist "
            "dhe mos deklaro kontroll fizik që nuk provohet nga aktet burimore."
        ),
    }


def _readiness(missing_core_fields: list[Any], conflicts: list[Any]) -> str:
    if missing_core_fields:
        return "generating_with_qualified_gaps"
    if conflicts:
        return "generating_with_resolved_conflicts"
    return "evidence_ready"
