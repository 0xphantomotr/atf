from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.reviews.schemas import StructuredFinding


class ReportProject(BaseModel):
    id: UUID
    name: str
    project_type: str
    stage: str
    location: str | None = None


class ReportCheckedDocument(BaseModel):
    filename: str
    parse_status: str
    document_type: str | None = None
    document_label: str | None = None
    classification_confidence: float | None = Field(default=None, ge=0, le=1)


class ReportDocumentSummary(BaseModel):
    total_files: int
    parsed_files: int
    classified_files: int
    unknown_files: int
    document_type_counts: dict[str, int] = Field(default_factory=dict)
    checked_documents: list[ReportCheckedDocument] = Field(default_factory=list)


class AuditReport(BaseModel):
    title: str = "Raport Auditimi Teknik"
    generated_at: datetime
    project: ReportProject
    law_scope: list[str] = Field(default_factory=list)
    document_summary: ReportDocumentSummary
    recommendation: str
    summary: str
    findings: list[StructuredFinding] = Field(default_factory=list)
    required_actions: list[str] = Field(default_factory=list)
    appendices: list[str] = Field(default_factory=list)
