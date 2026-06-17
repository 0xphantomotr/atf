from uuid import UUID

from pydantic import BaseModel, ConfigDict


class RuleRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    rule_code: str
    law_document_id: UUID
    law_article_id: UUID | None
    title: str
    description: str
    applies_to: dict
    required_documents: dict | None
    required_evidence: dict | None
    severity_if_missing: str
    human_validated: bool

