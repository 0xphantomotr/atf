from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotImplementedYet
from app.db.session import get_session
from app.laws import service
from app.laws.schemas import LawArticleRead, LawDocumentRead
from app.rules.schemas import RuleRead

router = APIRouter(prefix="/laws", tags=["laws"])


@router.post("", response_model=LawDocumentRead)
async def ingest_law_pdf(
    code: str = Form(...),
    title: str = Form(...),
    source_date: date | None = Form(default=None),
    language: str = Form(default="sq-AL"),
    version_label: str | None = Form(default=None),
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
) -> LawDocumentRead:
    law_document = await service.ingest_law_pdf(
        session,
        code=code,
        title=title,
        source_date=source_date,
        language=language,
        version_label=version_label,
        upload=file,
    )
    return LawDocumentRead.model_validate(law_document)


@router.get("", response_model=list[LawDocumentRead])
async def list_laws(session: AsyncSession = Depends(get_session)) -> list[LawDocumentRead]:
    laws = await service.list_laws(session)
    return [LawDocumentRead.model_validate(law) for law in laws]


@router.get("/{law_id}", response_model=LawDocumentRead)
async def get_law(
    law_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> LawDocumentRead:
    law_document = await service.get_law(session, law_id=law_id)
    return LawDocumentRead.model_validate(law_document)


@router.post("/{law_id}/ingest")
async def ingest_existing_law(law_id: UUID, file: UploadFile | None = None) -> None:
    _ = law_id, file
    raise NotImplementedYet("Ri-ingestimi i ligjeve ekzistuese nuk është implementuar ende.")


@router.get("/{law_id}/articles", response_model=list[LawArticleRead])
async def list_law_articles(
    law_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> list[LawArticleRead]:
    articles = await service.list_law_articles(session, law_id=law_id)
    return [LawArticleRead.model_validate(article) for article in articles]


@router.get("/{law_id}/rules", response_model=list[RuleRead])
async def list_law_rules(
    law_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> list[RuleRead]:
    rules = await service.list_law_rules(session, law_id=law_id)
    return [RuleRead.model_validate(rule) for rule in rules]

