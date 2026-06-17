from uuid import UUID

from fastapi import APIRouter

from app.core.errors import NotImplementedYet
from app.reviews.schemas import GenerateRequest

router = APIRouter(tags=["reviews"])


@router.post("/projects/{project_id}/generate")
async def generate(project_id: UUID, _: GenerateRequest) -> None:
    _ = project_id
    raise NotImplementedYet()


@router.get("/jobs/{job_id}")
async def get_job(job_id: UUID) -> None:
    _ = job_id
    raise NotImplementedYet()


@router.get("/jobs/{job_id}/findings")
async def get_job_findings(job_id: UUID) -> None:
    _ = job_id
    raise NotImplementedYet()


@router.get("/jobs/{job_id}/output")
async def get_job_output(job_id: UUID) -> None:
    _ = job_id
    raise NotImplementedYet()


@router.get("/outputs/{output_id}")
async def get_output(output_id: UUID) -> None:
    _ = output_id
    raise NotImplementedYet()

