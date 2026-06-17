from datetime import date
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class LawDocumentCreate(BaseModel):
    code: str
    title: str
    source_date: date | None = None
    language: str = "sq-AL"
    version_label: str | None = None


class LawDocumentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    code: str
    title: str
    source_date: date | None
    language: str
    version_label: str | None
    is_active: bool


class LawArticleRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    law_document_id: UUID
    chapter: str | None
    article_number: str | None
    article_title: str | None
    text: str

