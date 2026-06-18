import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.files.classifier import UNKNOWN_DOCUMENT_TYPE, classify_document
from app.files.models import FileVersion, ParsedDocument, ProjectFile
from app.laws.models import LawArticle, LawDocument
from app.projects.models import Project, ProjectMember
from app.reviews.models import ReviewFinding, ReviewJob
from app.reviews.schemas import GenerateRequest
from app.rules.models import Rule

DEFAULT_LAW_SCOPE = ("VKM_610_2022",)

DOCUMENT_TYPE_LABELS = {
    "accounting_records": "dokumentacion kontabël",
    "approved_execution_project": "projekt zbatimi i miratuar",
    "as_built_project": "projekt azhornimi/as-built",
    "bill_of_quantities": "preventiv",
    "construction_organization_plan": "planorganizim kantieri",
    "construction_permit": "leje ndërtimi",
    "construction_permit_conformity_declaration": (
        "deklaratë përputhshmërie me lejen e ndërtimit"
    ),
    "contract_and_related_acts": "kontrata dhe aktet përkatëse",
    "daily_site_log": "ditari i objektit",
    "development_permit": "leje zhvillimi",
    "external_system_completion_control_act": "akt kontrolli për rrjetet e jashtme",
    "facade_and_finishing_completion_control_act": "akt kontrolli për fasadën/rifiniturat",
    "forty_five_day_report": "raportim 45-ditor",
    "foundation_completion_and_level_0_00_control_act": (
        "akt kontrolli për themelet dhe kuotën 0.00"
    ),
    "geological_engineering_study": "studim gjeologo-inxhinierik",
    "hidden_works_minutes": "procesverbal për punime të maskuara",
    "level_0_00_control_act": "akt kontrolli në kuotën 0.00",
    "maintenance_project": "projekt mirëmbajtjeje",
    "material_quality_certificate": "certifikatë cilësie materiali",
    "monthly_situations": "situacione mujore",
    "photo_video_documentation": "dokumentacion foto/video",
    "professional_liability_insurance_policy": (
        "policë sigurimi për përgjegjësi profesionale"
    ),
    "professional_license": "licencë/certifikatë profesionale",
    "safety_documentation": "dokumentacion sigurie",
    "seismic_study": "studim sizmik",
    "setting_out_act": "akt piketimi",
    "site_book": "libri i kantierit",
    "site_handover_act": "akt dorëzimi sheshi",
    "site_setup_control_act": "akt kontrolli për ngritjen e kantierit",
    "start_interruption_extension_completion_minutes": (
        "procesverbale fillimi/ndërprerjeje/shtyrjeje/përfundimi"
    ),
    "start_works_minutes": "procesverbal fillimi punimesh",
    "start_works_notification": "njoftim fillimi punimesh",
    "start_works_notification_letter": "shkresë njoftimi për fillimin e punimeve",
    "structural_frame_completion_control_act": "akt kontrolli për karabinanë",
    "structure_setting_out_control_act": "akt kontrolli për piketimin e strukturës",
    "supervisor_contract": "kontratë me mbikëqyrësin",
    "technical_administrative_document_handover_act": (
        "akt dorëzimi i dokumentacionit teknik dhe administrativ"
    ),
    "technical_declaration": "deklaratë teknike",
    "technical_opposition": "oponencë teknike",
    "topographic_documentation": "dokumentacion topografik",
}


@dataclass(frozen=True)
class CurrentFileSnapshot:
    file_id: uuid.UUID
    version_id: uuid.UUID
    original_filename: str
    parse_status: str
    document_type: str | None
    classification_confidence: float | None

    def as_evidence(self) -> dict[str, Any]:
        return {
            "file_id": str(self.file_id),
            "version_id": str(self.version_id),
            "filename": self.original_filename,
            "parse_status": self.parse_status,
            "document_type": self.document_type,
            "classification_confidence": self.classification_confidence,
        }


@dataclass(frozen=True)
class RuleContext:
    rule: Rule
    law_document: LawDocument
    law_article: LawArticle | None


async def run_documentation_checklist(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    user_id: uuid.UUID,
    payload: GenerateRequest,
) -> ReviewJob:
    project = await _get_project_for_user(session, project_id=project_id, user_id=user_id)
    law_scope = tuple(payload.law_scope or DEFAULT_LAW_SCOPE)
    now = datetime.now(timezone.utc)

    job = ReviewJob(
        project_id=project.id,
        requested_by=user_id,
        job_type=payload.job_type,
        status="running",
        language=payload.language,
        output_format=payload.output_format,
        user_prompt=payload.user_prompt,
        law_scope={"codes": list(law_scope)},
        progress=10,
        started_at=now,
    )
    session.add(job)
    await session.flush()

    current_files = await _load_current_file_snapshots(session, project_id=project.id)
    rule_contexts = await _load_validated_rule_contexts(session, law_scope=law_scope)
    if not rule_contexts:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Nuk u gjetën rregulla të validuara për fushën ligjore të kërkuar.",
        )

    findings = _build_missing_document_findings(
        job=job,
        project=project,
        current_files=current_files,
        rule_contexts=rule_contexts,
    )
    for finding in findings:
        session.add(finding)

    job.status = "completed"
    job.progress = 100
    job.completed_at = datetime.now(timezone.utc)

    await session.commit()
    await session.refresh(job)
    return job


async def get_review_job(
    session: AsyncSession,
    *,
    job_id: uuid.UUID,
    user_id: uuid.UUID,
) -> ReviewJob:
    job = await session.get(ReviewJob, job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Procesi i auditimit nuk u gjet.",
        )
    await _get_project_for_user(session, project_id=job.project_id, user_id=user_id)
    return job


async def list_review_findings(
    session: AsyncSession,
    *,
    job_id: uuid.UUID,
    user_id: uuid.UUID,
) -> list[ReviewFinding]:
    await get_review_job(session, job_id=job_id, user_id=user_id)
    result = await session.execute(
        select(ReviewFinding)
        .where(ReviewFinding.review_job_id == job_id)
        .order_by(ReviewFinding.severity, ReviewFinding.rule_code, ReviewFinding.created_at)
    )
    return list(result.scalars())


async def _load_current_file_snapshots(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
) -> list[CurrentFileSnapshot]:
    result = await session.execute(
        select(ProjectFile, FileVersion, ParsedDocument)
        .join(
            FileVersion,
            and_(
                FileVersion.file_id == ProjectFile.id,
                FileVersion.version_number == ProjectFile.current_version,
            ),
        )
        .outerjoin(ParsedDocument, ParsedDocument.file_version_id == FileVersion.id)
        .where(ProjectFile.project_id == project_id, ProjectFile.deleted_at.is_(None))
        .order_by(ProjectFile.created_at.desc())
    )

    snapshots: list[CurrentFileSnapshot] = []
    for project_file, file_version, parsed_document in result:
        if parsed_document is not None:
            _ensure_parsed_document_classified(parsed_document, file_version)

        metadata = parsed_document.document_metadata if parsed_document else {}
        classification = metadata.get("classification", {}) if metadata else {}
        snapshots.append(
            CurrentFileSnapshot(
                file_id=project_file.id,
                version_id=file_version.id,
                original_filename=file_version.original_filename,
                parse_status=file_version.parse_status,
                document_type=parsed_document.document_type if parsed_document else None,
                classification_confidence=_safe_float(classification.get("confidence")),
            )
        )
    return snapshots


def _ensure_parsed_document_classified(
    parsed_document: ParsedDocument,
    file_version: FileVersion,
) -> None:
    metadata = dict(parsed_document.document_metadata or {})
    classification_metadata = metadata.get("classification")
    if parsed_document.document_type and classification_metadata:
        return

    classification = classify_document(
        file_version.original_filename,
        parsed_document.text_content,
    )
    metadata["classification"] = classification.as_metadata()

    parsed_document.document_type = classification.document_type
    parsed_document.document_metadata = metadata


async def _get_project_for_user(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    user_id: uuid.UUID,
) -> Project:
    result = await session.execute(
        select(Project)
        .join(ProjectMember, ProjectMember.project_id == Project.id)
        .where(
            Project.id == project_id,
            ProjectMember.user_id == user_id,
            Project.deleted_at.is_(None),
        )
    )
    project = result.scalar_one_or_none()
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Projekti nuk u gjet.",
        )
    return project


async def _load_validated_rule_contexts(
    session: AsyncSession,
    *,
    law_scope: tuple[str, ...],
) -> list[RuleContext]:
    result = await session.execute(
        select(Rule, LawDocument, LawArticle)
        .join(LawDocument, LawDocument.id == Rule.law_document_id)
        .outerjoin(LawArticle, LawArticle.id == Rule.law_article_id)
        .where(
            Rule.human_validated.is_(True),
            LawDocument.code.in_(law_scope),
            LawDocument.is_active.is_(True),
        )
        .order_by(Rule.rule_code)
    )
    return [
        RuleContext(rule=rule, law_document=law_document, law_article=law_article)
        for rule, law_document, law_article in result
    ]


def _build_missing_document_findings(
    *,
    job: ReviewJob,
    project: Project,
    current_files: list[CurrentFileSnapshot],
    rule_contexts: list[RuleContext],
) -> list[ReviewFinding]:
    found_document_types = _found_document_types(current_files)
    findings: list[ReviewFinding] = []

    for context in rule_contexts:
        if not _rule_applies_to_project(context.rule, project):
            continue

        required_document_types = _required_document_types(context.rule)
        if not required_document_types:
            continue

        missing_document_types = [
            document_type
            for document_type in required_document_types
            if document_type not in found_document_types
        ]
        if not missing_document_types:
            continue

        findings.append(
            ReviewFinding(
                review_job_id=job.id,
                project_id=project.id,
                severity=context.rule.severity_if_missing,
                title=_missing_documents_title(context.rule),
                description=_missing_documents_description(missing_document_types),
                law_reference=_law_reference(context),
                rule_code=context.rule.rule_code,
                evidence={
                    "type": "missing_document",
                    "rule_code": context.rule.rule_code,
                    "required_document_types": required_document_types,
                    "missing_document_types": missing_document_types,
                    "found_document_types": sorted(found_document_types),
                    "files_checked": [file.as_evidence() for file in current_files],
                    "result": _missing_documents_description(missing_document_types),
                },
                required_action=_required_action(missing_document_types),
                confidence=0.95,
                status="open",
            )
        )

    return findings


def _found_document_types(current_files: list[CurrentFileSnapshot]) -> set[str]:
    return {
        current_file.document_type
        for current_file in current_files
        if current_file.parse_status == "parsed"
        and current_file.document_type
        and current_file.document_type != UNKNOWN_DOCUMENT_TYPE
    }


def _rule_applies_to_project(rule: Rule, project: Project) -> bool:
    applies_to = rule.applies_to or {}
    if not _matches_filter(applies_to.get("project_type"), project.project_type):
        return False
    if not _matches_filter(applies_to.get("project_stage"), project.stage):
        return False
    if not _matches_filter(applies_to.get("stage"), project.stage):
        return False
    return True


def _matches_filter(allowed_values: object, actual_value: str) -> bool:
    if allowed_values is None:
        return True
    if isinstance(allowed_values, str):
        return allowed_values == actual_value
    if isinstance(allowed_values, list):
        return actual_value in allowed_values
    return True


def _required_document_types(rule: Rule) -> list[str]:
    required_documents = rule.required_documents or {}
    document_types = required_documents.get("document_types", [])
    if not isinstance(document_types, list):
        return []
    return [document_type for document_type in document_types if isinstance(document_type, str)]


def _missing_documents_title(rule: Rule) -> str:
    return f"Mungojnë dokumente të kërkuara për: {rule.title}"


def _missing_documents_description(missing_document_types: list[str]) -> str:
    labels = [_document_type_label(document_type) for document_type in missing_document_types]
    return "Nuk u gjetën dokumente të klasifikuara si: " + ", ".join(labels) + "."


def _required_action(missing_document_types: list[str]) -> str:
    labels = [_document_type_label(document_type) for document_type in missing_document_types]
    return "Ngarkoni ose përditësoni dokumentet: " + ", ".join(labels) + "."


def _document_type_label(document_type: str) -> str:
    return DOCUMENT_TYPE_LABELS.get(document_type, document_type.replace("_", " "))


def _law_reference(context: RuleContext) -> str:
    if context.law_document.code == "VKM_610_2022":
        base_reference = "VKM 610/2022"
    else:
        base_reference = context.law_document.code

    if context.law_article and context.law_article.article_number:
        return f"{base_reference}, Neni {context.law_article.article_number}"
    return base_reference


def _safe_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
