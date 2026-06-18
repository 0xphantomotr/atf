import uuid
from io import BytesIO
from pathlib import Path

from minio.error import S3Error
from pypdf import PdfReader
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.files.classifier import classify_document
from app.files.models import FileVersion, ParsedDocument, ProjectFile
from app.files.storage import get_minio_client


async def parse_file_version(session: AsyncSession, *, file_version_id: uuid.UUID) -> None:
    file_version = await session.get(FileVersion, file_version_id)
    if file_version is None:
        return

    file_version.parse_status = "processing"
    await session.commit()

    suffix = Path(file_version.original_filename).suffix.lower()
    if suffix != ".pdf":
        file_version.parse_status = "unsupported"
        await session.commit()
        return

    try:
        pdf_bytes = _load_file_version_bytes(file_version)
        parsed = _parse_pdf(pdf_bytes)
        await _save_parsed_document(
            session,
            file_version=file_version,
            text_content=parsed["text_content"],
            page_count=parsed["page_count"],
            metadata=parsed["metadata"],
        )
        file_version.parse_status = "parsed"
        await session.commit()
    except Exception:
        await session.rollback()
        failed_version = await session.get(FileVersion, file_version_id)
        if failed_version is not None:
            failed_version.parse_status = "failed"
            await session.commit()
        raise


def _load_file_version_bytes(file_version: FileVersion) -> bytes:
    client = get_minio_client()
    response = None
    try:
        response = client.get_object(file_version.storage_bucket, file_version.storage_path)
        return response.read()
    except S3Error:
        raise
    finally:
        if response is not None:
            response.close()
            response.release_conn()


def _parse_pdf(pdf_bytes: bytes) -> dict:
    reader = PdfReader(BytesIO(pdf_bytes))
    page_texts: list[str] = []
    pages_with_text = 0

    for index, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        text = text.strip()
        if text:
            pages_with_text += 1
            page_texts.append(f"--- Faqe {index} ---\n{text}")

    return {
        "text_content": "\n\n".join(page_texts),
        "page_count": len(reader.pages),
        "metadata": {
            "parser": "pypdf",
            "pages_with_text": pages_with_text,
        },
    }


async def _save_parsed_document(
    session: AsyncSession,
    *,
    file_version: FileVersion,
    text_content: str,
    page_count: int,
    metadata: dict,
) -> None:
    classification = classify_document(file_version.original_filename, text_content)
    parsed_metadata = {
        **metadata,
        "classification": classification.as_metadata(),
    }

    result = await session.execute(
        select(ParsedDocument).where(ParsedDocument.file_version_id == file_version.id)
    )
    parsed_document = result.scalar_one_or_none()

    if parsed_document is None:
        parsed_document = ParsedDocument(
            file_version_id=file_version.id,
            document_type=classification.document_type,
            language="sq-AL",
            page_count=page_count,
            text_content=text_content,
            document_metadata=parsed_metadata,
        )
        session.add(parsed_document)
        return

    parsed_document.document_type = classification.document_type
    parsed_document.page_count = page_count
    parsed_document.text_content = text_content
    parsed_document.document_metadata = parsed_metadata


async def get_project_id_for_file_version(
    session: AsyncSession,
    *,
    file_version_id: uuid.UUID,
) -> uuid.UUID | None:
    result = await session.execute(
        select(ProjectFile.project_id)
        .join(FileVersion, FileVersion.file_id == ProjectFile.id)
        .where(FileVersion.id == file_version_id)
    )
    return result.scalar_one_or_none()
