from uuid import UUID

from fastapi import APIRouter, Depends, status
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.reviews import service
from app.reviews.schemas import (
    GenerateRequest,
    GenerationPreflightRead,
    GeneratedOutputRead,
    JobOutputRead,
    ReviewFindingRead,
    ReviewJobRead,
)
from app.users.dependencies import get_current_user
from app.users.models import User

router = APIRouter(tags=["reviews"])


@router.post(
    "/projects/{project_id}/generate/preflight",
    response_model=GenerationPreflightRead,
)
async def generation_preflight(
    project_id: UUID,
    payload: GenerateRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> GenerationPreflightRead:
    plan = await service.estimate_review_job(
        session,
        project_id=project_id,
        user_id=current_user.id,
        payload=payload,
    )
    return GenerationPreflightRead.model_validate(plan)


@router.post(
    "/projects/{project_id}/generate",
    response_model=ReviewJobRead,
    status_code=status.HTTP_201_CREATED,
)
async def generate(
    project_id: UUID,
    payload: GenerateRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ReviewJobRead:
    job = await service.create_review_job(
        session,
        project_id=project_id,
        user_id=current_user.id,
        payload=payload,
    )
    return ReviewJobRead.model_validate(job)


@router.get("/jobs/{job_id}", response_model=ReviewJobRead)
async def get_job(
    job_id: UUID,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ReviewJobRead:
    job = await service.get_review_job(session, job_id=job_id, user_id=current_user.id)
    return ReviewJobRead.model_validate(job)


@router.get("/jobs/{job_id}/findings", response_model=list[ReviewFindingRead])
async def get_job_findings(
    job_id: UUID,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[ReviewFindingRead]:
    findings = await service.list_review_findings(
        session,
        job_id=job_id,
        user_id=current_user.id,
    )
    return [ReviewFindingRead.model_validate(finding) for finding in findings]


@router.get("/jobs/{job_id}/output", response_model=JobOutputRead)
async def get_job_output(
    job_id: UUID,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> JobOutputRead:
    job, outputs = await service.get_review_job_outputs(
        session,
        job_id=job_id,
        user_id=current_user.id,
    )
    return JobOutputRead(
        job=ReviewJobRead.model_validate(job),
        outputs=[GeneratedOutputRead.model_validate(output) for output in outputs],
    )


@router.get("/outputs/{output_id}")
async def get_output(
    output_id: UUID,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Response:
    download = await service.download_generated_output(
        session,
        output_id=output_id,
        user_id=current_user.id,
    )
    safe_filename = download.filename.replace('"', "")
    return Response(
        content=download.content,
        media_type=download.content_type,
        headers={"Content-Disposition": f'attachment; filename="{safe_filename}"'},
    )
