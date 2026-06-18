from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotImplementedYet
from app.db.session import get_session
from app.reviews import service
from app.reviews.schemas import GenerateRequest, ReviewFindingRead, ReviewJobRead
from app.users.dependencies import get_current_user
from app.users.models import User

router = APIRouter(tags=["reviews"])


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
    job = await service.run_documentation_checklist(
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


@router.get("/jobs/{job_id}/output")
async def get_job_output(job_id: UUID) -> None:
    _ = job_id
    raise NotImplementedYet()


@router.get("/outputs/{output_id}")
async def get_output(output_id: UUID) -> None:
    _ = output_id
    raise NotImplementedYet()
