from typing import Any

from app.agents.state import AuditGraphState
from app.files.status import is_parsed_status


VKM_OBLIGATION_GROUPS: tuple[dict[str, Any], ...] = (
    {
        "code": "VKM610-041-LEGAL-BASIS",
        "title": "Baza ligjore, lejet dhe projekti i miratuar",
        "law_reference": "VKM 610/2022, Neni 41",
        "document_types": (
            "development_permit",
            "construction_permit",
            "approved_execution_project",
            "technical_opposition",
            "bill_of_quantities",
        ),
        "professional_use": (
            "Mbështet pjesën hyrëse të Akt Kolaudimit: leja, projekti, preventivi "
            "dhe aktet mbi të cilat kontrollohet objekti."
        ),
    },
    {
        "code": "VKM610-038-043-SITE-START",
        "title": "Dorëzimi i sheshit dhe fillimi i punimeve",
        "law_reference": "VKM 610/2022, Nenet 38-43",
        "document_types": (
            "site_handover_act",
            "technical_administrative_document_handover_act",
            "start_works_notification",
            "start_works_notification_letter",
            "start_works_minutes",
            "setting_out_act",
            "structure_setting_out_control_act",
        ),
        "professional_use": (
            "Mbështet verifikimin se objekti ka nisur me bazë administrative dhe "
            "teknike të kontrollueshme."
        ),
    },
    {
        "code": "VKM610-041-SUPERVISION-FILE",
        "title": "Dosja e mbikëqyrjes gjatë punimeve",
        "law_reference": "VKM 610/2022, Nenet 8 dhe 41",
        "document_types": (
            "supervisor_contract",
            "forty_five_day_report",
            "site_book",
            "daily_site_log",
            "monthly_situations",
            "professional_liability_insurance_policy",
        ),
        "professional_use": (
            "Tregon vazhdimësinë e kontrollit teknik nga mbikëqyrësi dhe "
            "raportimin periodik."
        ),
    },
    {
        "code": "VKM610-042-PHASE-CONTROL",
        "title": "Aktet e kontrollit sipas fazave",
        "law_reference": "VKM 610/2022, Neni 42",
        "document_types": (
            "site_setup_control_act",
            "foundation_completion_and_level_0_00_control_act",
            "level_0_00_control_act",
            "structural_frame_completion_control_act",
            "facade_and_finishing_completion_control_act",
            "external_system_completion_control_act",
        ),
        "professional_use": (
            "Mbështet konkluzionin profesional mbi ecurinë faktike të objektit "
            "nga kantieri deri te përfundimi."
        ),
    },
    {
        "code": "VKM610-041-QUALITY-HIDDEN-WORKS",
        "title": "Punimet e maskuara, provat dhe cilësia e materialeve",
        "law_reference": "VKM 610/2022, Neni 41",
        "document_types": (
            "hidden_works_minutes",
            "material_quality_certificate",
            "geological_engineering_study",
            "seismic_study",
            "topographic_documentation",
        ),
        "professional_use": (
            "Mbështet vlerësimin teknik të cilësisë, punimeve që nuk shihen më "
            "dhe kushteve gjeoteknike/sizmike."
        ),
    },
    {
        "code": "VKM610-024-028-COMPLETION",
        "title": "Përfundimi, deklarimet dhe baza për kolaudim",
        "law_reference": "VKM 610/2022, Nenet 24-28 dhe 44",
        "document_types": (
            "start_interruption_extension_completion_minutes",
            "technical_declaration",
            "construction_permit_conformity_declaration",
            "as_built_project",
            "maintenance_project",
            "photo_video_documentation",
        ),
        "professional_use": (
            "Mbështet konkluzionin final për përfundimin e punimeve, përputhjen "
            "me lejen/projektin dhe kushtet për përdorim."
        ),
    },
    {
        "code": "VKM610-041-SAFETY-SPECIAL",
        "title": "Siguria dhe dokumentet e veçanta",
        "law_reference": "VKM 610/2022, Neni 41",
        "document_types": (
            "safety_documentation",
            "construction_organization_plan",
            "professional_license",
        ),
        "professional_use": (
            "Mbështet kontrollin mbi përgjegjësitë profesionale, organizimin dhe "
            "kushtet bazë të sigurisë."
        ),
    },
)


def map_vkm_obligations(state: AuditGraphState) -> AuditGraphState:
    state.setdefault("agent_trace", []).append("vkm_obligation_mapper")
    documents = state.get("documents", [])
    present_by_type = _present_documents_by_type(documents)

    items = []
    summary = {"complete": 0, "partial": 0, "missing": 0}
    for group in VKM_OBLIGATION_GROUPS:
        required_types = list(group["document_types"])
        present_types = [
            document_type
            for document_type in required_types
            if document_type in present_by_type
        ]
        missing_types = [
            document_type
            for document_type in required_types
            if document_type not in present_by_type
        ]
        status = _status(required_types, present_types)
        summary[status] += 1
        items.append(
            {
                "code": group["code"],
                "title": group["title"],
                "law_reference": group["law_reference"],
                "professional_use": group["professional_use"],
                "status": status,
                "required_document_types": required_types,
                "present_document_types": present_types,
                "missing_document_types": missing_types,
                "evidence_documents": [
                    document
                    for document_type in present_types
                    for document in present_by_type[document_type]
                ],
            }
        )

    state["vkm_obligation_map"] = {
        "items": items,
        "summary": summary,
        "law_references": sorted({item["law_reference"] for item in items}),
    }
    if summary["missing"] or summary["partial"]:
        state["needs_human_review"] = True
    return state


def _present_documents_by_type(
    documents: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    present: dict[str, list[dict[str, Any]]] = {}
    for document in documents:
        document_type = document.get("document_type")
        if (
            not is_parsed_status(document.get("parse_status"))
            or not isinstance(document_type, str)
            or document_type == "unknown"
        ):
            continue
        present.setdefault(document_type, []).append(
            {
                "filename": document.get("original_filename"),
                "document_type": document_type,
                "classification_confidence": document.get("classification_confidence"),
            }
        )
    return present


def _status(required_types: list[str], present_types: list[str]) -> str:
    if len(present_types) == len(required_types):
        return "complete"
    if present_types:
        return "partial"
    return "missing"
