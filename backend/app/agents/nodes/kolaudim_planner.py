from typing import Any

from app.agents.state import AuditGraphState

KOLAUDIM_SECTIONS: tuple[dict[str, Any], ...] = (
    {
        "code": "legal_basis_and_scope",
        "title": "Baza ligjore dhe objekti i kolaudimit",
        "fact_fields": ("object_name", "location"),
        "evidence_sections": ("legal_and_administrative",),
        "registers": ("permits_property_licenses",),
        "specialists": ("legal_administrative",),
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
        "registers": ("stakeholders",),
        "specialists": ("legal_administrative",),
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
        "registers": ("permits_property_licenses", "project_parameters"),
        "specialists": ("legal_administrative", "project_parameters"),
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
        "registers": ("project_parameters",),
        "specialists": ("project_parameters",),
    },
    {
        "code": "geology_seismicity_and_setting_out",
        "title": "Kushtet gjeologo-inxhinierike, sizmike dhe piketimi",
        "fact_fields": ("soil_bearing_capacity", "seismic_intensity"),
        "evidence_sections": ("design_and_parameters", "execution_and_chronology"),
        "registers": ("project_parameters", "technical_works"),
        "specialists": ("project_parameters", "structural_hidden_works"),
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
        "registers": ("contracts_and_economics", "stakeholders"),
        "specialists": ("contractual_economic", "chronology_completion"),
    },
    {
        "code": "execution_chronology",
        "title": "Ecuria dhe fazat e realizimit të punimeve",
        "fact_fields": ("start_date", "completion_date"),
        "evidence_sections": ("execution_and_chronology",),
        "registers": ("construction_chronology",),
        "specialists": ("chronology_completion",),
    },
    {
        "code": "hidden_works_and_structure",
        "title": "Punimet e maskuara dhe elementet konstruktive",
        "fact_fields": (),
        "evidence_sections": ("quality_and_hidden_works", "execution_and_chronology"),
        "registers": ("technical_works",),
        "specialists": ("structural_hidden_works",),
    },
    {
        "code": "materials_tests_and_quality",
        "title": "Materialet, provat dhe kontrolli i cilësisë",
        "fact_fields": (),
        "evidence_sections": ("quality_and_hidden_works",),
        "registers": ("materials_and_tests",),
        "specialists": ("materials_quality",),
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
        "registers": ("project_parameters", "technical_works"),
        "specialists": ("project_parameters", "structural_hidden_works"),
    },
    {
        "code": "technical_economic_conclusion",
        "title": "Konkluzioni tekniko-ekonomik",
        "fact_fields": ("final_value",),
        "evidence_sections": ("technical_economic", "completion_and_conclusion"),
        "registers": (
            "contracts_and_economics",
            "declarations_and_conclusions",
        ),
        "specialists": (
            "contractual_economic",
            "chronology_completion",
            "structural_hidden_works",
            "materials_quality",
        ),
    },
    {
        "code": "copies_and_signatures",
        "title": "Hartimi i aktit dhe nënshkrimet",
        "fact_fields": ("investor", "contractor", "supervisor", "kolaudator"),
        "evidence_sections": (),
        "registers": ("stakeholders",),
        "specialists": ("legal_administrative",),
    },
)


def plan_kolaudim_act(state: AuditGraphState) -> AuditGraphState:
    state.setdefault("agent_trace", []).append("kolaudim_planner")
    dossier = state.get("professional_dossier", {})
    canonical_facts = dossier.get("canonical_facts", {}) if isinstance(dossier, dict) else {}
    evidence = dossier.get("evidence_by_section", {}) if isinstance(dossier, dict) else {}
    registers = dossier.get("registers", {}) if isinstance(dossier, dict) else {}
    specialist_reviews = state.get("specialist_reviews", {})
    memoranda = (
        specialist_reviews.get("memoranda", [])
        if isinstance(specialist_reviews, dict)
        else []
    )
    if not isinstance(canonical_facts, dict):
        canonical_facts = {}
    if not isinstance(evidence, dict):
        evidence = {}
    if not isinstance(registers, dict):
        registers = {}
    memoranda_by_code = {
        str(memo.get("code")): memo
        for memo in memoranda
        if isinstance(memo, dict) and memo.get("code")
    }

    sections = [
        _section_plan(
            section,
            canonical_facts,
            evidence,
            registers,
            memoranda_by_code,
        )
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
    registers: dict[str, Any],
    memoranda: dict[str, dict[str, Any]],
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
    register_entry_counts = {
        register: len(registers.get(register, []))
        for register in section.get("registers", ())
        if isinstance(registers.get(register), list)
    }
    specialist_context = {
        code: {
            "status": memoranda[code].get("status"),
            "supported_statement_count": sum(
                len(memoranda[code].get(key, []))
                for key in (
                    "established_facts",
                    "technical_assessments",
                    "qualifications",
                    "writer_guidance",
                )
            ),
        }
        for code in section.get("specialists", ())
        if code in memoranda
    }
    return {
        "code": section["code"],
        "title": section["title"],
        "available_fact_fields": available_fields,
        "unresolved_fact_fields": missing_fields,
        "source_documents": source_documents,
        "register_entry_counts": register_entry_counts,
        "specialist_context": specialist_context,
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
