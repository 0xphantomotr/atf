from pydantic import BaseModel, Field

from app.reviews.schemas import StructuredFinding


class AuditReport(BaseModel):
    title: str = "Raport Auditimi Teknik"
    recommendation: str
    summary: str
    findings: list[StructuredFinding] = Field(default_factory=list)
    required_actions: list[str] = Field(default_factory=list)
    appendices: list[str] = Field(default_factory=list)

