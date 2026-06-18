from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.laws.models import LawArticle, LawDocument
from app.rules.models import Rule

VKM_610_CODE = "VKM_610_2022"

VKM_610_RULE_SPECS: tuple[dict[str, Any], ...] = (
    {
        "rule_code": "VKM610-005-001",
        "article_number": "5",
        "title": "Kontrata me mbikëqyrësin dhe detyrimi për raportim",
        "description": (
            "Zhvilluesi duhet të lidhë kontratë me mbikëqyrësin e punimeve dhe "
            "kontrata duhet të përfshijë detyrimin për raportim pranë autoriteteve "
            "çdo 45 ditë."
        ),
        "applies_to": {"project_stage": ["during_construction", "completion_kolaudim"]},
        "required_documents": {"document_types": ["supervisor_contract"]},
        "required_evidence": {
            "evidence_types": ["supervisor_contract", "forty_five_day_reporting_obligation"]
        },
        "severity_if_missing": "major",
    },
    {
        "rule_code": "VKM610-008-001",
        "article_number": "8",
        "title": "Raportimi 45-ditor dhe njoftimet në e-leje",
        "description": (
            "Mbikëqyrësi ka detyrimin të raportojë çdo 45 ditë për kryerjen e "
            "punimeve dhe të njoftojë online në sistemin e-leje sipas fazave të "
            "akteve të kontrollit."
        ),
        "applies_to": {"project_stage": ["during_construction"]},
        "required_documents": {"document_types": ["forty_five_day_report"]},
        "required_evidence": {
            "evidence_types": ["forty_five_day_report", "e_leje_submission_reference"]
        },
        "severity_if_missing": "major",
    },
    {
        "rule_code": "VKM610-029-001",
        "article_number": "29",
        "title": "Sigurimi i përgjegjësisë profesionale të mbikëqyrësit",
        "description": (
            "Mbikëqyrësi i punimeve, si person fizik apo juridik i licencuar, është "
            "i detyruar të lidhë kontratë sigurimi të përgjegjësisë profesionale."
        ),
        "applies_to": {"role": ["supervisor"]},
        "required_documents": {
            "document_types": ["professional_liability_insurance_policy"]
        },
        "required_evidence": {
            "evidence_types": ["professional_liability_insurance_policy"]
        },
        "severity_if_missing": "major",
    },
    {
        "rule_code": "VKM610-041-001",
        "article_number": "41",
        "title": "Dokumentacioni teknik që administrohet në kantier",
        "description": (
            "Mbikëqyrësi i punimeve dhe zhvilluesi administrojnë në kantier "
            "dokumentacionin teknik të kërkuar për objektin dhe mbajnë përgjegjësi "
            "për saktësinë dhe vërtetësinë e tij."
        ),
        "applies_to": {"project_stage": ["during_construction", "completion_kolaudim"]},
        "required_documents": {
            "document_types": [
                "development_permit",
                "construction_permit",
                "site_handover_act",
                "approved_execution_project",
                "technical_opposition",
                "bill_of_quantities",
                "geological_engineering_study",
                "topographic_documentation",
                "seismic_study",
                "construction_organization_plan",
                "professional_license",
                "start_works_notification",
                "start_works_minutes",
                "setting_out_act",
                "level_0_00_control_act",
                "site_book",
                "daily_site_log",
                "monthly_situations",
                "hidden_works_minutes",
                "material_quality_certificate",
                "structural_frame_completion_control_act",
                "technical_declaration",
                "safety_documentation",
                "professional_liability_insurance_policy",
                "photo_video_documentation",
                "as_built_project",
                "maintenance_project",
                "forty_five_day_report",
            ]
        },
        "required_evidence": {
            "evidence_types": ["required_construction_site_documentation"]
        },
        "severity_if_missing": "major",
    },
    {
        "rule_code": "VKM610-042-001",
        "article_number": "42",
        "title": "Aktet e kontrollit sipas fazave të punimeve",
        "description": (
            "Për objektet e banimit, social-kulturore dhe ekonomike mbahen aktet "
            "e kontrollit sipas fazave të përcaktuara të punimeve."
        ),
        "applies_to": {
            "project_type": ["residential", "social_cultural", "economic"],
            "project_stage": ["during_construction", "completion_kolaudim"],
        },
        "required_documents": {
            "document_types": [
                "site_setup_control_act",
                "structure_setting_out_control_act",
                "foundation_completion_and_level_0_00_control_act",
                "structural_frame_completion_control_act",
                "facade_and_finishing_completion_control_act",
                "external_system_completion_control_act",
            ]
        },
        "required_evidence": {"evidence_types": ["control_act_by_phase"]},
        "severity_if_missing": "major",
    },
    {
        "rule_code": "VKM610-043-001",
        "article_number": "43",
        "title": "Dokumentacioni pas verifikimeve të fillimit",
        "description": (
            "Pas verifikimeve të përcaktuara, mbikëqyrësi harton aktin e dorëzimit "
            "të sheshit, aktin e dorëzimit të dokumentacionit teknik dhe administrativ, "
            "dhe shkresën e njoftimit për fillimin e punimeve."
        ),
        "applies_to": {"project_stage": ["before_construction", "during_construction"]},
        "required_documents": {
            "document_types": [
                "site_handover_act",
                "technical_administrative_document_handover_act",
                "start_works_notification_letter",
            ]
        },
        "required_evidence": {"evidence_types": ["start_verification_documentation"]},
        "severity_if_missing": "major",
    },
    {
        "rule_code": "VKM610-044-001",
        "article_number": "44",
        "title": "Dokumentacioni për përfundimin e punimeve",
        "description": (
            "Brenda 30 ditëve nga njoftimi me shkrim për përfundimin e punimeve, "
            "mbikëqyrësi kërkon dokumentacionin për kontratën, kontabilitetin, "
            "procesverbalet, certifikatat e cilësisë dhe deklaratat e përputhshmërisë."
        ),
        "applies_to": {"project_stage": ["completion_kolaudim"]},
        "required_documents": {
            "document_types": [
                "contract_and_related_acts",
                "accounting_records",
                "start_interruption_extension_completion_minutes",
                "material_quality_certificate",
                "technical_declaration",
                "construction_permit_conformity_declaration",
            ]
        },
        "required_evidence": {"evidence_types": ["completion_documentation"]},
        "severity_if_missing": "major",
    },
)


async def seed_vkm_610_rules(session: AsyncSession) -> list[Rule]:
    law_document = await _get_vkm_610_law(session)
    article_map = await _get_article_map(session, law_document)
    existing_rules = await _get_existing_rules(session)

    seeded_rules: list[Rule] = []
    for spec in VKM_610_RULE_SPECS:
        rule = existing_rules.get(spec["rule_code"])
        article = article_map.get(spec["article_number"])
        if article is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Mungon Neni {spec['article_number']} për {VKM_610_CODE}.",
            )

        values = {
            "law_document_id": law_document.id,
            "law_article_id": article.id,
            "title": spec["title"],
            "description": spec["description"],
            "applies_to": spec["applies_to"],
            "required_documents": spec["required_documents"],
            "required_evidence": spec["required_evidence"],
            "severity_if_missing": spec["severity_if_missing"],
            "human_validated": True,
        }

        if rule is None:
            rule = Rule(rule_code=spec["rule_code"], **values)
            session.add(rule)
        else:
            for field, value in values.items():
                setattr(rule, field, value)
        seeded_rules.append(rule)

    await session.commit()
    for rule in seeded_rules:
        await session.refresh(rule)
    return seeded_rules


async def list_rules(session: AsyncSession) -> list[Rule]:
    result = await session.execute(select(Rule).order_by(Rule.rule_code))
    return list(result.scalars())


async def _get_vkm_610_law(session: AsyncSession) -> LawDocument:
    result = await session.execute(select(LawDocument).where(LawDocument.code == VKM_610_CODE))
    law_document = result.scalar_one_or_none()
    if law_document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Dokumenti ligjor {VKM_610_CODE} nuk u gjet. Ingestoni VKM 610 më parë.",
        )
    return law_document


async def _get_article_map(
    session: AsyncSession,
    law_document: LawDocument,
) -> dict[str, LawArticle]:
    article_numbers = {spec["article_number"] for spec in VKM_610_RULE_SPECS}
    result = await session.execute(
        select(LawArticle).where(
            LawArticle.law_document_id == law_document.id,
            LawArticle.article_number.in_(article_numbers),
        )
    )
    return {article.article_number: article for article in result.scalars() if article.article_number}


async def _get_existing_rules(session: AsyncSession) -> dict[str, Rule]:
    rule_codes = [spec["rule_code"] for spec in VKM_610_RULE_SPECS]
    result = await session.execute(select(Rule).where(Rule.rule_code.in_(rule_codes)))
    return {rule.rule_code: rule for rule in result.scalars()}

