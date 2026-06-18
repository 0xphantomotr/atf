import hashlib
import re
import unicodedata
import uuid
from datetime import date
from io import BytesIO
from pathlib import Path

from fastapi import HTTPException, UploadFile, status
from minio.error import S3Error
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.files.storage import ensure_bucket_exists, get_minio_client
from app.laws.models import LawArticle, LawDocument
from app.laws.parser import extract_pdf_text, split_law_articles
from app.rules.models import Rule

MAX_LAW_UPLOAD_BYTES = 50 * 1024 * 1024


def normalize_law_filename(filename: str) -> str:
    raw_name = Path(filename).name.strip()
    normalized = unicodedata.normalize("NFKD", raw_name).encode("ascii", "ignore").decode("ascii")
    normalized = normalized.lower()
    normalized = re.sub(r"[^a-z0-9._-]+", "-", normalized)
    normalized = re.sub(r"-+", "-", normalized).strip("-._")
    return normalized or "law.pdf"


async def ingest_law_pdf(
    session: AsyncSession,
    *,
    code: str,
    title: str,
    source_date: date | None,
    language: str,
    version_label: str | None,
    upload: UploadFile,
) -> LawDocument:
    if not upload.filename or Path(upload.filename).suffix.lower() != ".pdf":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Dokumenti ligjor duhet të jetë në format PDF.",
        )

    existing = await get_law_by_code(session, code=code)
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Ekziston tashmë një dokument ligjor me këtë kod.",
        )

    pdf_bytes = await upload.read()
    if not pdf_bytes:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Dokumenti është bosh.")
    if len(pdf_bytes) > MAX_LAW_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Dokumenti ligjor është më i madh se kufiri i lejuar prej 50 MB.",
        )

    text, page_count, pages_with_text = extract_pdf_text(pdf_bytes)
    articles = split_law_articles(text)
    if not articles:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Nuk u gjetën nene në dokumentin ligjor.",
        )

    law_id = uuid.uuid4()
    normalized_filename = normalize_law_filename(upload.filename)
    storage_path = f"laws/{law_id}/{normalized_filename}"
    sha256_hash = hashlib.sha256(pdf_bytes).hexdigest()

    _upload_law_pdf(
        object_name=storage_path,
        pdf_bytes=pdf_bytes,
        content_type=upload.content_type or "application/pdf",
    )

    law_document = LawDocument(
        id=law_id,
        code=code,
        title=title,
        source_date=source_date,
        language=language,
        version_label=version_label,
        storage_bucket=settings.minio_bucket,
        storage_path=storage_path,
        sha256_hash=sha256_hash,
        is_active=True,
    )
    session.add(law_document)
    await session.flush()

    for article_number, article_text in articles:
        session.add(
            LawArticle(
                law_document_id=law_document.id,
                chapter=None,
                article_number=article_number,
                article_title=None,
                page_start=None,
                page_end=None,
                text=article_text,
            )
        )

    law_document.version_label = version_label or f"{page_count} faqe, {pages_with_text} me tekst"
    await session.commit()
    await session.refresh(law_document)
    return law_document


def _upload_law_pdf(*, object_name: str, pdf_bytes: bytes, content_type: str) -> None:
    client = get_minio_client()
    try:
        ensure_bucket_exists(client, settings.minio_bucket)
        client.put_object(
            settings.minio_bucket,
            object_name,
            data=BytesIO(pdf_bytes),
            length=len(pdf_bytes),
            content_type=content_type,
        )
    except S3Error as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Nuk arrita ta ruaj dokumentin ligjor në objekt storage: {exc.code}",
        ) from exc


async def get_law_by_code(session: AsyncSession, *, code: str) -> LawDocument | None:
    result = await session.execute(select(LawDocument).where(LawDocument.code == code))
    return result.scalar_one_or_none()


async def list_laws(session: AsyncSession) -> list[LawDocument]:
    result = await session.execute(select(LawDocument).order_by(LawDocument.created_at.desc()))
    return list(result.scalars())


async def get_law(session: AsyncSession, *, law_id: uuid.UUID) -> LawDocument:
    law_document = await session.get(LawDocument, law_id)
    if law_document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Dokumenti ligjor nuk u gjet.",
        )
    return law_document


async def list_law_articles(session: AsyncSession, *, law_id: uuid.UUID) -> list[LawArticle]:
    await get_law(session, law_id=law_id)
    result = await session.execute(
        select(LawArticle)
        .where(LawArticle.law_document_id == law_id)
        .order_by(LawArticle.article_number)
    )
    return list(result.scalars())


async def list_law_rules(session: AsyncSession, *, law_id: uuid.UUID) -> list[Rule]:
    await get_law(session, law_id=law_id)
    result = await session.execute(
        select(Rule).where(Rule.law_document_id == law_id).order_by(Rule.rule_code)
    )
    return list(result.scalars())


async def replace_law_articles(
    session: AsyncSession,
    *,
    law_document: LawDocument,
    text: str,
) -> None:
    await session.execute(delete(LawArticle).where(LawArticle.law_document_id == law_document.id))
    for article_number, article_text in split_law_articles(text):
        session.add(
            LawArticle(
                law_document_id=law_document.id,
                article_number=article_number,
                text=article_text,
            )
        )
