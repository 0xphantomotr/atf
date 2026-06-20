import json
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import and_, delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import service as ai_service
from app.agents.graph import run_audit_graph
from app.agents.state import AuditGraphState
from app.core.config import settings
from app.files.classifier import UNKNOWN_DOCUMENT_TYPE, classify_document
from app.files.models import FileVersion, ParsedDocument, ProjectFile
from app.laws.models import LawArticle, LawDocument
from app.projects.models import Project, ProjectMember
from app.reports.schemas import (
    AuditReport,
    ReportCheckedDocument,
    ReportDocumentSummary,
    ReportProject,
)
from app.reviews.models import GeneratedOutput, ReviewFinding, ReviewJob
from app.reviews.schemas import GenerateRequest
from app.rules.models import Rule

DEFAULT_LAW_SCOPE = ("VKM_610_2022",)
SUPPORTED_OUTPUT_FORMATS = {"json", "pdf"}
JSON_CONTENT_TYPE = "application/json; charset=utf-8"
PDF_CONTENT_TYPE = "application/pdf"
AGENT_TEXT_EXCERPT_LIMIT = 6_000

DOCUMENT_TYPE_ALIASES = {
    "foundation_completion_and_level_0_00_control_act": {"level_0_00_control_act"},
    "start_works_notification": {"start_works_notification_letter"},
}

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
    text_content: str | None = None

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


@dataclass(frozen=True)
class OutputDownload:
    filename: str
    content_type: str
    content: bytes


async def run_documentation_checklist(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    user_id: uuid.UUID,
    payload: GenerateRequest,
) -> ReviewJob:
    job = await create_review_job(
        session,
        project_id=project_id,
        user_id=user_id,
        payload=payload,
        enqueue=False,
    )
    await run_queued_review_job(session, job_id=job.id)
    await session.refresh(job)
    return job


async def create_review_job(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    user_id: uuid.UUID,
    payload: GenerateRequest,
    enqueue: bool = True,
) -> ReviewJob:
    output_format = _normalize_output_format(payload.output_format)
    project = await _get_project_for_user(session, project_id=project_id, user_id=user_id)
    law_scope = _normalize_law_scope(payload.law_scope)
    if payload.require_ai_review:
        await ai_service.require_user_ai_setting(session, user_id=user_id)

    job = ReviewJob(
        project_id=project.id,
        requested_by=user_id,
        job_type=payload.job_type,
        status="queued",
        language=payload.language,
        output_format=output_format,
        user_prompt=payload.user_prompt,
        law_scope={
            "codes": list(law_scope),
            "require_ai_review": payload.require_ai_review,
        },
        progress=0,
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)

    if enqueue:
        from app.workers.jobs import run_review_job

        run_review_job.send(str(job.id))
    return job


async def run_queued_review_job(
    session: AsyncSession,
    *,
    job_id: uuid.UUID,
) -> ReviewJob:
    job = await session.get(ReviewJob, job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Procesi i auditimit nuk u gjet.",
        )
    if job.status == "completed":
        return job

    job.status = "running"
    job.progress = 10
    job.error_message = None
    job.started_at = datetime.now(timezone.utc)
    await session.flush()

    try:
        project = await _get_project_for_user(
            session,
            project_id=job.project_id,
            user_id=job.requested_by,
        )
        law_scope = tuple(_law_scope_codes(job))

        current_files = await _load_current_file_snapshots(session, project_id=project.id)
        rule_contexts = await _load_validated_rule_contexts(session, law_scope=law_scope)
        ai_settings = await ai_service.get_user_ai_credentials(
            session,
            user_id=job.requested_by,
        )
        if _job_requires_ai_review(job) and ai_settings is None:
            raise ValueError("Ky auditim kërkon konfigurim të AI API key për përdoruesin.")
        if not rule_contexts:
            raise ValueError(
                "Nuk u gjetën rregulla të validuara për fushën ligjore të kërkuar."
            )

        await _clear_review_job_results(session, job_id=job.id)
        findings = _build_missing_document_findings(
            job=job,
            project=project,
            current_files=current_files,
            rule_contexts=rule_contexts,
        )
        for finding in findings:
            session.add(finding)
        await session.flush()

        agent_state = _run_phase_one_audit_graph(
            project=project,
            job=job,
            current_files=current_files,
            rule_contexts=rule_contexts,
            findings=findings,
            ai_settings=ai_settings,
            require_ai_review=_job_requires_ai_review(job),
        )

        job.progress = 70
        report = _build_audit_report(
            project=project,
            job=job,
            current_files=current_files,
            findings=findings,
            agent_state=agent_state,
        )
        for output in _store_report_outputs(job=job, report=report):
            session.add(output)

        job.status = "completed"
        job.progress = 100
        job.completed_at = datetime.now(timezone.utc)
        await session.commit()
    except Exception as exc:
        await session.rollback()
        job = await session.get(ReviewJob, job_id)
        if job is not None:
            job.status = "failed"
            job.progress = 100
            job.error_message = str(exc)
            job.completed_at = datetime.now(timezone.utc)
            await session.commit()
        raise

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
    return await _load_review_findings_for_job(session, job_id=job_id)


async def get_review_job_outputs(
    session: AsyncSession,
    *,
    job_id: uuid.UUID,
    user_id: uuid.UUID,
) -> tuple[ReviewJob, list[GeneratedOutput]]:
    job = await get_review_job(session, job_id=job_id, user_id=user_id)
    outputs = await _list_outputs_for_job(session, job_id=job.id)
    if outputs:
        return job, outputs

    if job.status != "completed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Raporti nuk është ende gati.",
        )

    project = await _get_project_for_user(
        session,
        project_id=job.project_id,
        user_id=user_id,
    )
    current_files = await _load_current_file_snapshots(session, project_id=project.id)
    findings = await _load_review_findings_for_job(session, job_id=job.id)
    rule_contexts = await _load_validated_rule_contexts(
        session,
        law_scope=tuple(_law_scope_codes(job)),
    )
    ai_settings = await ai_service.get_user_ai_credentials(session, user_id=job.requested_by)
    agent_state = _run_phase_one_audit_graph(
        project=project,
        job=job,
        current_files=current_files,
        rule_contexts=rule_contexts,
        findings=findings,
        ai_settings=ai_settings,
        require_ai_review=_job_requires_ai_review(job),
    )
    report = _build_audit_report(
        project=project,
        job=job,
        current_files=current_files,
        findings=findings,
        agent_state=agent_state,
    )
    outputs = _store_report_outputs(job=job, report=report)
    for output in outputs:
        session.add(output)
    await session.commit()

    return job, await _list_outputs_for_job(session, job_id=job.id)


async def get_generated_output(
    session: AsyncSession,
    *,
    output_id: uuid.UUID,
    user_id: uuid.UUID,
) -> GeneratedOutput:
    output = await session.get(GeneratedOutput, output_id)
    if output is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Raporti nuk u gjet.",
        )
    await _get_project_for_user(session, project_id=output.project_id, user_id=user_id)
    return output


async def download_generated_output(
    session: AsyncSession,
    *,
    output_id: uuid.UUID,
    user_id: uuid.UUID,
) -> OutputDownload:
    output = await get_generated_output(
        session,
        output_id=output_id,
        user_id=user_id,
    )
    if not output.storage_bucket or not output.storage_path:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Skedari i raportit nuk u gjet.",
        )

    from app.files.storage import get_minio_client

    client = get_minio_client()
    response = client.get_object(output.storage_bucket, output.storage_path)
    try:
        content = response.read()
    finally:
        response.close()
        response.release_conn()

    metadata = output.output_metadata or {}
    filename = metadata.get("filename")
    content_type = metadata.get("content_type")
    return OutputDownload(
        filename=filename if isinstance(filename, str) else _default_output_filename(output),
        content_type=(
            content_type
            if isinstance(content_type, str)
            else _content_type(output.output_type)
        ),
        content=content,
    )


async def _load_review_findings_for_job(
    session: AsyncSession,
    *,
    job_id: uuid.UUID,
) -> list[ReviewFinding]:
    result = await session.execute(
        select(ReviewFinding)
        .where(ReviewFinding.review_job_id == job_id)
        .order_by(ReviewFinding.severity, ReviewFinding.rule_code, ReviewFinding.created_at)
    )
    return list(result.scalars())


async def _list_outputs_for_job(
    session: AsyncSession,
    *,
    job_id: uuid.UUID,
) -> list[GeneratedOutput]:
    result = await session.execute(
        select(GeneratedOutput)
        .where(GeneratedOutput.review_job_id == job_id)
        .order_by(GeneratedOutput.output_type, GeneratedOutput.created_at)
    )
    return list(result.scalars())


async def _clear_review_job_results(
    session: AsyncSession,
    *,
    job_id: uuid.UUID,
) -> None:
    await session.execute(delete(ReviewFinding).where(ReviewFinding.review_job_id == job_id))
    await session.execute(delete(GeneratedOutput).where(GeneratedOutput.review_job_id == job_id))


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
                text_content=parsed_document.text_content if parsed_document else None,
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
                    "files_checked_count": len(current_files),
                    "parsed_files_count": _count_parsed_files(current_files),
                    "classified_files_count": _count_classified_files(current_files),
                    "unknown_files": _unknown_filenames(current_files),
                    "result": _missing_documents_description(missing_document_types),
                },
                required_action=_required_action(missing_document_types),
                confidence=0.95,
                status="open",
            )
        )

    return findings


def _run_phase_one_audit_graph(
    *,
    project: Project,
    job: ReviewJob,
    current_files: list[CurrentFileSnapshot],
    rule_contexts: list[RuleContext],
    findings: list[ReviewFinding],
    ai_settings: dict[str, Any] | None,
    require_ai_review: bool,
) -> AuditGraphState:
    return run_audit_graph(
        _build_agent_state(
            project=project,
            job=job,
            current_files=current_files,
            rule_contexts=rule_contexts,
            findings=findings,
            ai_settings=ai_settings,
            require_ai_review=require_ai_review,
        )
    )


def _build_agent_state(
    *,
    project: Project,
    job: ReviewJob,
    current_files: list[CurrentFileSnapshot],
    rule_contexts: list[RuleContext],
    findings: list[ReviewFinding],
    ai_settings: dict[str, Any] | None = None,
    require_ai_review: bool = True,
) -> AuditGraphState:
    return {
        "project": {
            "id": str(project.id),
            "name": project.name,
            "project_type": project.project_type,
            "stage": project.stage,
            "location": project.location,
        },
        "job": {
            "id": str(job.id),
            "job_type": job.job_type,
            "language": job.language,
            "output_format": job.output_format,
            "law_scope": _law_scope_codes(job),
        },
        "user_prompt": job.user_prompt or "",
        "documents": [_current_file_as_dict(current_file) for current_file in current_files],
        "rules": [_rule_context_as_dict(context) for context in rule_contexts],
        "findings": [_structured_finding(finding) for finding in findings],
        "ai_settings": ai_settings or {},
        "require_ai_review": require_ai_review,
        "needs_human_review": False,
        "agent_trace": [],
    }


def _current_file_as_dict(current_file: CurrentFileSnapshot) -> dict[str, Any]:
    return {
        "file_id": str(current_file.file_id),
        "version_id": str(current_file.version_id),
        "original_filename": current_file.original_filename,
        "parse_status": current_file.parse_status,
        "document_type": current_file.document_type,
        "classification_confidence": current_file.classification_confidence,
        "text_excerpt": _text_excerpt(current_file.text_content),
    }


def _text_excerpt(text: str | None) -> str:
    if not text:
        return ""
    compact = "\n".join(line.rstrip() for line in text.splitlines() if line.strip())
    return compact[:AGENT_TEXT_EXCERPT_LIMIT]


def _rule_context_as_dict(context: RuleContext) -> dict[str, Any]:
    return {
        "rule_code": context.rule.rule_code,
        "title": context.rule.title,
        "severity_if_missing": context.rule.severity_if_missing,
        "law_reference": _law_reference(context),
        "required_document_types": _required_document_types(context.rule),
        "applies_to": context.rule.applies_to or {},
        "law_document_code": context.law_document.code,
        "law_article_number": (
            context.law_article.article_number if context.law_article else None
        ),
    }


def _build_audit_report(
    *,
    project: Project,
    job: ReviewJob,
    current_files: list[CurrentFileSnapshot],
    findings: list[ReviewFinding],
    agent_state: AuditGraphState | None = None,
) -> AuditReport:
    structured_findings = [_structured_finding(finding) for finding in findings]
    required_actions = sorted(
        {
            finding.required_action
            for finding in findings
            if isinstance(finding.required_action, str) and finding.required_action
        }
    )
    document_summary = _document_summary(current_files)
    law_scope = _law_scope_codes(job)
    appendices = [
        (
            "Raporti kontrollon praninë e dokumenteve të klasifikuara në sistem "
            "dhe nuk zëvendëson verifikimin profesional ose ligjor njerëzor."
        ),
        (
            "Dokumentet e paklasifikuara ose formatet e pambështetura duhet të "
            "verifikohen manualisht përpara vendimmarrjes."
        ),
    ]
    appendices.extend(_agent_appendices(agent_state))

    return AuditReport(
        title=_report_title(job),
        generated_at=job.completed_at or datetime.now(timezone.utc),
        project=ReportProject(
            id=project.id,
            name=project.name,
            project_type=project.project_type,
            stage=project.stage,
            location=project.location,
        ),
        law_scope=law_scope,
        document_summary=document_summary,
        recommendation=_report_recommendation(structured_findings),
        summary=_report_summary(
            document_summary,
            structured_findings,
            agent_state=agent_state,
        ),
        findings=structured_findings,
        professional_analysis=_professional_analysis(agent_state),
        required_actions=required_actions,
        appendices=appendices,
        agent_metadata=_agent_metadata(agent_state),
    )


def _agent_appendices(agent_state: AuditGraphState | None) -> list[str]:
    if not agent_state:
        return []

    trace = agent_state.get("agent_trace") or []
    appendices = [
        (
            "Workflow-i profesional i kolaudimit ekzekutoi kontrollin në nyjet: "
            + " -> ".join(trace)
            + "."
        )
    ]
    ai_review = agent_state.get("ai_review", {})
    if isinstance(ai_review, dict):
        status = ai_review.get("status")
        if status == "reviewed":
            appendices.append(
                "Nyja e auditorit të lartë AI rishikoi gjetjet deterministike dhe "
                "ruajti rezultatin si metadata të raportit."
            )
        elif status == "skipped":
            appendices.append(
                "Nyja e auditorit të lartë AI u anashkalua: "
                f"{ai_review.get('reason', 'arsye e paspecifikuar')}."
            )
        elif status == "failed":
            appendices.append(
                "Nyja e auditorit të lartë AI nuk u përfundua dhe raporti ruajti "
                "kontrollin deterministik si burim kryesor."
            )

    if agent_state.get("needs_human_review"):
        appendices.append(
            "Workflow-i sinjalizoi nevojë për verifikim njerëzor për shkak të "
            "dokumenteve të paklasifikuara, formateve të pambështetura ose evidencës "
            "që kërkon kontroll manual."
        )
    return appendices


def _agent_metadata(agent_state: AuditGraphState | None) -> dict[str, object]:
    if not agent_state:
        return {}

    report = agent_state.get("report", {})
    ai_review = agent_state.get("ai_review", {})
    report_phase = report.get("phase") if isinstance(report, dict) else None
    extracted_facts = agent_state.get("extracted_facts", {})
    vkm_obligations = agent_state.get("vkm_obligation_map", {})
    consistency_review = agent_state.get("consistency_review", {})
    kolaudim_analysis = agent_state.get("kolaudim_analysis", {})
    return {
        "phase": (
            report_phase
            if isinstance(report_phase, str)
            else "professional_kolaudim_phase_1"
        ),
        "trace": list(agent_state.get("agent_trace", [])),
        "needs_human_review": bool(agent_state.get("needs_human_review", False)),
        "document_inventory": dict(agent_state.get("document_inventory", {})),
        "law_context": dict(agent_state.get("law_context", {})),
        "completeness_summary": dict(agent_state.get("completeness_summary", {})),
        "fact_summary": (
            dict(extracted_facts.get("summary", {}))
            if isinstance(extracted_facts, dict)
            else {}
        ),
        "vkm_obligation_summary": (
            dict(vkm_obligations.get("summary", {}))
            if isinstance(vkm_obligations, dict)
            else {}
        ),
        "consistency_summary": (
            dict(consistency_review.get("summary", {}))
            if isinstance(consistency_review, dict)
            else {}
        ),
        "kolaudim_readiness": (
            kolaudim_analysis.get("readiness")
            if isinstance(kolaudim_analysis, dict)
            else None
        ),
        "ai_review": dict(ai_review) if isinstance(ai_review, dict) else {},
        "report": dict(report) if isinstance(report, dict) else {},
    }


def _professional_analysis(agent_state: AuditGraphState | None) -> dict[str, object]:
    if not agent_state:
        return {}

    return {
        "extracted_facts": dict(agent_state.get("extracted_facts", {})),
        "vkm_obligation_map": dict(agent_state.get("vkm_obligation_map", {})),
        "consistency_review": dict(agent_state.get("consistency_review", {})),
        "kolaudim_analysis": dict(agent_state.get("kolaudim_analysis", {})),
    }


def _store_report_outputs(
    *,
    job: ReviewJob,
    report: AuditReport,
) -> list[GeneratedOutput]:
    output_types = _output_types_for_format(job.output_format)
    stored_outputs: list[GeneratedOutput] = []

    objects: list[tuple[str, bytes, str, str]] = [
        (
            "json",
            _json_report_bytes(report),
            JSON_CONTENT_TYPE,
            "audit-report.json",
        )
    ]
    if "pdf" in output_types:
        from app.reports.pdf import html_to_pdf_bytes
        from app.reports.renderer import render_audit_report_html

        html = render_audit_report_html(report)
        objects.append(("pdf", html_to_pdf_bytes(html), PDF_CONTENT_TYPE, "audit-report.pdf"))

    from app.files.storage import ensure_bucket_exists, get_minio_client

    client = get_minio_client()
    ensure_bucket_exists(client, settings.minio_bucket)

    for output_type, content, content_type, filename in objects:
        if output_type not in output_types:
            continue

        storage_path = (
            f"projects/{job.project_id}/review-jobs/{job.id}/outputs/{filename}"
        )
        client.put_object(
            settings.minio_bucket,
            storage_path,
            BytesIO(content),
            length=len(content),
            content_type=content_type,
        )
        stored_outputs.append(
            GeneratedOutput(
                review_job_id=job.id,
                project_id=job.project_id,
                output_type=output_type,
                language=job.language,
                storage_bucket=settings.minio_bucket,
                storage_path=storage_path,
                text_preview=report.summary[:1000],
                output_metadata={
                    "filename": filename,
                    "content_type": content_type,
                    "size_bytes": len(content),
                    "finding_count": len(report.findings),
                    "recommendation": report.recommendation,
                    "law_scope": report.law_scope,
                    "agent_phase": report.agent_metadata.get("phase"),
                    "agent_trace": report.agent_metadata.get("trace", []),
                    "kolaudim_readiness": report.agent_metadata.get(
                        "kolaudim_readiness"
                    ),
                    "ai_review_status": (
                        report.agent_metadata.get("ai_review", {}).get("status")
                        if isinstance(report.agent_metadata.get("ai_review"), dict)
                        else None
                    ),
                    "needs_human_review": report.agent_metadata.get(
                        "needs_human_review",
                        False,
                    ),
                },
            )
        )

    return stored_outputs


def _structured_finding(finding: ReviewFinding) -> Any:
    return {
        "severity": finding.severity,
        "title": finding.title,
        "description": finding.description,
        "law_reference": finding.law_reference,
        "rule_code": finding.rule_code,
        "evidence": _compact_finding_evidence(finding.evidence),
        "required_action": finding.required_action,
        "confidence": _safe_float(finding.confidence),
        "status": finding.status,
    }


def _compact_finding_evidence(evidence: dict[str, Any] | None) -> dict[str, Any]:
    compact = dict(evidence or {})
    files_checked = compact.pop("files_checked", None)
    if isinstance(files_checked, list):
        compact.setdefault("files_checked_count", len(files_checked))
    return compact


def _document_summary(current_files: list[CurrentFileSnapshot]) -> ReportDocumentSummary:
    document_type_counts = Counter(
        file.document_type
        for file in current_files
        if (
            file.parse_status == "parsed"
            and file.document_type
            and file.document_type != UNKNOWN_DOCUMENT_TYPE
        )
    )
    checked_documents = [
        ReportCheckedDocument(
            filename=file.original_filename,
            parse_status=file.parse_status,
            document_type=file.document_type,
            document_label=(
                _document_type_label(file.document_type)
                if file.document_type and file.document_type != UNKNOWN_DOCUMENT_TYPE
                else None
            ),
            classification_confidence=file.classification_confidence,
        )
        for file in current_files
    ]

    return ReportDocumentSummary(
        total_files=len(current_files),
        parsed_files=_count_parsed_files(current_files),
        classified_files=_count_classified_files(current_files),
        unknown_files=len(_unknown_filenames(current_files)),
        document_type_counts=dict(sorted(document_type_counts.items())),
        checked_documents=checked_documents,
    )


def _count_parsed_files(current_files: list[CurrentFileSnapshot]) -> int:
    return sum(1 for file in current_files if file.parse_status == "parsed")


def _count_classified_files(current_files: list[CurrentFileSnapshot]) -> int:
    return sum(
        1
        for file in current_files
        if (
            file.parse_status == "parsed"
            and file.document_type
            and file.document_type != UNKNOWN_DOCUMENT_TYPE
        )
    )


def _unknown_filenames(current_files: list[CurrentFileSnapshot]) -> list[str]:
    return [
        file.original_filename
        for file in current_files
        if file.parse_status == "parsed" and file.document_type == UNKNOWN_DOCUMENT_TYPE
    ]


def _report_title(job: ReviewJob) -> str:
    if getattr(job, "job_type", None) == "kolaudim_act":
        return "Draft Akt Kolaudimi Teknik"
    return "Raport Auditimi Teknik"


def _report_recommendation(findings: list[Any]) -> str:
    if not findings:
        return "Pa gjetje të rëndësishme"
    if any(finding["severity"] == "critical" for finding in findings):
        return "Kërkohet shqyrtim njerëzor"
    return "Kërkohet plotësim dokumentacioni"


def _report_summary(
    document_summary: ReportDocumentSummary,
    findings: list[Any],
    *,
    agent_state: AuditGraphState | None = None,
) -> str:
    professional_prefix = _professional_summary_prefix(agent_state)
    if not findings:
        return professional_prefix + (
            f"U kontrolluan {document_summary.total_files} dokumente. "
            "Nuk u identifikuan mungesa dokumentacioni nga rregullat e aplikuara."
        )

    return professional_prefix + (
        f"U kontrolluan {document_summary.total_files} dokumente, prej të cilave "
        f"{document_summary.classified_files} u klasifikuan. "
        f"Sistemi identifikoi {len(findings)} gjetje të hapura për plotësim "
        "dokumentacioni sipas rregullave të validuara."
    )


def _professional_summary_prefix(agent_state: AuditGraphState | None) -> str:
    if not agent_state:
        return ""
    kolaudim_analysis = agent_state.get("kolaudim_analysis", {})
    if not isinstance(kolaudim_analysis, dict):
        return ""
    conclusion = str(kolaudim_analysis.get("professional_conclusion") or "").strip()
    if not conclusion:
        return ""
    return conclusion + " "


def _law_scope_codes(job: ReviewJob) -> list[str]:
    law_scope = job.law_scope or {}
    codes = law_scope.get("codes") if isinstance(law_scope, dict) else None
    if isinstance(codes, list):
        return [code for code in codes if isinstance(code, str)]
    return list(DEFAULT_LAW_SCOPE)


def _job_requires_ai_review(job: ReviewJob) -> bool:
    law_scope = job.law_scope or {}
    if isinstance(law_scope, dict) and "require_ai_review" in law_scope:
        return bool(law_scope.get("require_ai_review"))
    return True


def _normalize_law_scope(law_scope: list[str] | None) -> tuple[str, ...]:
    normalized = tuple(code for code in law_scope or DEFAULT_LAW_SCOPE if code)
    return normalized or DEFAULT_LAW_SCOPE


def _json_report_bytes(report: AuditReport) -> bytes:
    report_json = json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2)
    return report_json.encode("utf-8")


def _normalize_output_format(output_format: str) -> str:
    normalized = (output_format or "").strip().lower()
    if normalized not in SUPPORTED_OUTPUT_FORMATS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Formati i raportit duhet të jetë 'pdf' ose 'json'.",
        )
    return normalized


def _output_types_for_format(output_format: str) -> tuple[str, ...]:
    normalized = _normalize_output_format(output_format)
    if normalized == "json":
        return ("json",)
    return ("json", "pdf")


def _content_type(output_type: str) -> str:
    if output_type == "pdf":
        return PDF_CONTENT_TYPE
    return JSON_CONTENT_TYPE


def _default_output_filename(output: GeneratedOutput) -> str:
    suffix = "pdf" if output.output_type == "pdf" else "json"
    return f"audit-report-{output.id}.{suffix}"


def _found_document_types(current_files: list[CurrentFileSnapshot]) -> set[str]:
    found_types: set[str] = set()
    for current_file in current_files:
        if (
            current_file.parse_status != "parsed"
            or not current_file.document_type
            or current_file.document_type == UNKNOWN_DOCUMENT_TYPE
        ):
            continue

        found_types.add(current_file.document_type)
        found_types.update(DOCUMENT_TYPE_ALIASES.get(current_file.document_type, set()))
    return found_types


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
