from uuid import UUID

from fastapi import APIRouter, UploadFile

from app.core.errors import NotImplementedYet
from app.laws.schemas import LawDocumentCreate

router = APIRouter(prefix="/laws", tags=["laws"])


@router.post("")
async def create_law(_: LawDocumentCreate) -> None:
    raise NotImplementedYet()


@router.get("")
async def list_laws() -> None:
    raise NotImplementedYet()


@router.get("/{law_id}")
async def get_law(law_id: UUID) -> None:
    _ = law_id
    raise NotImplementedYet()


@router.post("/{law_id}/ingest")
async def ingest_law(law_id: UUID, file: UploadFile | None = None) -> None:
    _ = law_id, file
    raise NotImplementedYet()


@router.get("/{law_id}/articles")
async def list_law_articles(law_id: UUID) -> None:
    _ = law_id
    raise NotImplementedYet()


@router.get("/{law_id}/rules")
async def list_law_rules(law_id: UUID) -> None:
    _ = law_id
    raise NotImplementedYet()

