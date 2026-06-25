from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class GenerateRequest(BaseModel):
    job_type: str = "documentation_checklist"
    output_format: str = "pdf"
    language: str = "sq-AL"
    law_scope: list[str] = Field(default_factory=lambda: ["VKM_610_2022"])
    user_prompt: str | None = None
    require_ai_review: bool = True


class ReviewJobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    job_type: str
    status: str
    language: str
    output_format: str
    progress: int
    execution_plan: dict
    current_stage: str | None = None
    progress_details: dict = Field(default_factory=dict)
    retry_after_at: datetime | None = None
    retry_reason: str | None = None
    retry_count: int = 0
    error_message: str | None = None


class GenerationPreflightRead(BaseModel):
    version: int
    generated_at: datetime
    project_id: UUID
    source: dict
    stages: list[dict]
    totals: dict
    assumptions: list[str]


class GeneratedOutputRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    review_job_id: UUID
    project_id: UUID
    output_type: str
    language: str
    storage_bucket: str | None
    storage_path: str | None
    text_preview: str | None
    output_metadata: dict
    created_at: datetime


class JobOutputRead(BaseModel):
    job: ReviewJobRead
    outputs: list[GeneratedOutputRead]


class ReviewFindingRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    review_job_id: UUID
    project_id: UUID
    severity: str
    title: str
    description: str
    law_reference: str | None
    rule_code: str | None
    evidence: dict
    required_action: str | None
    confidence: float | None
    status: str


class StructuredFinding(BaseModel):
    severity: str
    title: str
    description: str
    law_reference: str | None = None
    rule_code: str | None = None
    evidence: dict
    required_action: str | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    status: str = "open"
