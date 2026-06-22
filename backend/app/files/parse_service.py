import uuid
from io import BytesIO
from pathlib import Path
from typing import Any

from pypdf import PdfReader
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.files.classifier import classify_document
from app.files.models import DocumentChunk, FileVersion, ParsedDocument, ProjectFile

MAX_CHUNK_CHARS = 4_000
CHUNKING_VERSION = 1


async def parse_file_version(session: AsyncSession, *, file_version_id: uuid.UUID) -> None:
    file_version = await session.get(FileVersion, file_version_id)
    if file_version is None:
        return

    file_version.parse_status = "processing"
    await session.commit()

    suffix = Path(file_version.original_filename).suffix.lower()
    try:
        file_bytes = _load_file_version_bytes(file_version)
        if suffix == ".pdf":
            parsed = _parse_pdf(file_bytes)
        elif suffix == ".docx":
            parsed = _parse_docx(file_bytes)
        else:
            file_version.parse_status = "unsupported"
            await session.commit()
            return

        completion_status = _completed_parse_status(
            suffix=suffix,
            chunks=parsed["chunks"],
            page_count=parsed["page_count"],
        )
        parsed["metadata"] = {
            **parsed["metadata"],
            "extraction_status": completion_status,
        }
        await _save_parsed_document(
            session,
            file_version=file_version,
            text_content=parsed["text_content"],
            page_count=parsed["page_count"],
            metadata=parsed["metadata"],
            chunks=parsed["chunks"],
        )
        file_version.parse_status = completion_status
        await session.commit()
    except Exception:
        await session.rollback()
        failed_version = await session.get(FileVersion, file_version_id)
        if failed_version is not None:
            failed_version.parse_status = "failed"
            await session.commit()
        raise


def _completed_parse_status(
    *,
    suffix: str,
    chunks: list[dict[str, Any]],
    page_count: int | None,
) -> str:
    if any(str(chunk.get("text") or "").strip() for chunk in chunks):
        return "parsed"
    if suffix == ".pdf" and bool(page_count):
        return "needs_ocr"
    return "empty"


def _load_file_version_bytes(file_version: FileVersion) -> bytes:
    from app.files.storage import get_minio_client

    client = get_minio_client()
    response = None
    try:
        response = client.get_object(file_version.storage_bucket, file_version.storage_path)
        return response.read()
    finally:
        if response is not None:
            response.close()
            response.release_conn()


def _parse_pdf(pdf_bytes: bytes) -> dict:
    reader = PdfReader(BytesIO(pdf_bytes))
    page_texts: list[str] = []
    chunks: list[dict[str, Any]] = []
    pages_with_text = 0

    for index, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        text = text.strip()
        if text:
            pages_with_text += 1
            page_texts.append(f"--- Faqe {index} ---\n{text}")
            parts = _split_text(text)
            for part_index, part in enumerate(parts, start=1):
                chunks.append(
                    {
                        "text": part,
                        "page_start": index,
                        "page_end": index,
                        "metadata": {
                            "source_type": "pdf_page",
                            "page_number": index,
                            "part_index": part_index,
                            "part_count": len(parts),
                        },
                    }
                )

    return {
        "text_content": "\n\n".join(page_texts),
        "page_count": len(reader.pages),
        "chunks": chunks,
        "metadata": {
            "parser": "pypdf",
            "pages_with_text": pages_with_text,
            "chunk_count": len(chunks),
            "chunking_version": CHUNKING_VERSION,
        },
    }


def _parse_docx(docx_bytes: bytes) -> dict:
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    document = Document(BytesIO(docx_bytes))
    text_blocks: list[str] = []
    chunks: list[dict[str, Any]] = []
    pending_paragraphs: list[dict[str, Any]] = []
    paragraph_index = 0
    table_index = 0
    block_index = 0

    for block in document.iter_inner_content():
        block_index += 1
        if isinstance(block, Paragraph):
            paragraph_index += 1
            text = block.text.strip()
            if not text:
                continue
            text_blocks.append(text)
            pending_paragraphs.append(
                {
                    "text": text,
                    "paragraph_index": paragraph_index,
                    "block_index": block_index,
                }
            )
            continue

        if not isinstance(block, Table):
            continue

        chunks.extend(_docx_paragraph_chunks(pending_paragraphs))
        pending_paragraphs = []
        table_index += 1
        table_rows: list[dict[str, Any]] = []
        for row_index, row in enumerate(block.rows, start=1):
            cells = [_normalize_cell_text(cell.text) for cell in row.cells]
            cells = [cell for cell in cells if cell]
            if cells:
                table_rows.append(
                    {
                        "text": " | ".join(cells),
                        "row_index": row_index,
                    }
                )
        if table_rows:
            text_blocks.append(
                f"--- Tabela {table_index} ---\n"
                + "\n".join(str(row["text"]) for row in table_rows)
            )
            chunks.extend(
                _docx_table_chunks(
                    table_rows,
                    table_index=table_index,
                    block_index=block_index,
                )
            )

    chunks.extend(_docx_paragraph_chunks(pending_paragraphs))

    return {
        "text_content": "\n\n".join(text_blocks),
        "page_count": None,
        "chunks": chunks,
        "metadata": {
            "parser": "python-docx",
            "paragraph_count": len(document.paragraphs),
            "table_count": len(document.tables),
            "text_blocks": len(text_blocks),
            "chunk_count": len(chunks),
            "chunking_version": CHUNKING_VERSION,
        },
    }


def _normalize_cell_text(value: str) -> str:
    return " ".join(value.split())


def _split_text(text: str, *, max_chars: int = MAX_CHUNK_CHARS) -> list[str]:
    text = text.strip()
    if not text:
        return []

    blocks = [block.strip() for block in text.splitlines() if block.strip()]
    if not blocks:
        return []

    parts: list[str] = []
    current: list[str] = []
    current_length = 0
    for block in blocks:
        block_parts = _split_oversized_block(block, max_chars=max_chars)
        for block_part in block_parts:
            separator_length = 1 if current else 0
            if current and current_length + separator_length + len(block_part) > max_chars:
                parts.append("\n".join(current))
                current = []
                current_length = 0
            current.append(block_part)
            current_length += (1 if current_length else 0) + len(block_part)

    if current:
        parts.append("\n".join(current))
    return parts


def _split_oversized_block(block: str, *, max_chars: int) -> list[str]:
    if len(block) <= max_chars:
        return [block]

    parts: list[str] = []
    remaining = block
    while len(remaining) > max_chars:
        split_at = remaining.rfind(" ", 0, max_chars + 1)
        if split_at <= 0:
            split_at = max_chars
        parts.append(remaining[:split_at].strip())
        remaining = remaining[split_at:].strip()
    if remaining:
        parts.append(remaining)
    return parts


def _docx_paragraph_chunks(paragraphs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    current: list[dict[str, Any]] = []
    current_length = 0

    def flush() -> None:
        nonlocal current, current_length
        if not current:
            return
        chunks.append(
            {
                "text": "\n\n".join(str(item["text"]) for item in current),
                "page_start": None,
                "page_end": None,
                "metadata": {
                    "source_type": "docx_paragraphs",
                    "paragraph_start": current[0]["paragraph_index"],
                    "paragraph_end": current[-1]["paragraph_index"],
                    "block_start": current[0]["block_index"],
                    "block_end": current[-1]["block_index"],
                },
            }
        )
        current = []
        current_length = 0

    for paragraph in paragraphs:
        paragraph_parts = _split_text(str(paragraph["text"]))
        for part_index, part in enumerate(paragraph_parts, start=1):
            item = {
                **paragraph,
                "text": part,
                "part_index": part_index,
                "part_count": len(paragraph_parts),
            }
            separator_length = 2 if current else 0
            if current and current_length + separator_length + len(part) > MAX_CHUNK_CHARS:
                flush()
            current.append(item)
            current_length += (2 if current_length else 0) + len(part)
    flush()
    return chunks


def _docx_table_chunks(
    rows: list[dict[str, Any]],
    *,
    table_index: int,
    block_index: int,
) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    current: list[dict[str, Any]] = []
    current_length = 0

    def flush() -> None:
        nonlocal current, current_length
        if not current:
            return
        chunks.append(
            {
                "text": "\n".join(str(item["text"]) for item in current),
                "page_start": None,
                "page_end": None,
                "metadata": {
                    "source_type": "docx_table",
                    "table_index": table_index,
                    "row_start": current[0]["row_index"],
                    "row_end": current[-1]["row_index"],
                    "block_index": block_index,
                },
            }
        )
        current = []
        current_length = 0

    for row in rows:
        row_parts = _split_text(str(row["text"]))
        for part in row_parts:
            item = {**row, "text": part}
            separator_length = 1 if current else 0
            if current and current_length + separator_length + len(part) > MAX_CHUNK_CHARS:
                flush()
            current.append(item)
            current_length += (1 if current_length else 0) + len(part)
    flush()
    return chunks


async def _save_parsed_document(
    session: AsyncSession,
    *,
    file_version: FileVersion,
    text_content: str,
    page_count: int | None,
    metadata: dict,
    chunks: list[dict[str, Any]],
) -> None:
    chunks = [chunk for chunk in chunks if str(chunk.get("text") or "").strip()]
    classification = classify_document(file_version.original_filename, text_content)
    parsed_metadata = {
        **metadata,
        "classification": classification.as_metadata(),
        "chunk_count": len(chunks),
        "chunking_version": CHUNKING_VERSION,
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
        await session.flush()
    else:
        parsed_document.document_type = classification.document_type
        parsed_document.page_count = page_count
        parsed_document.text_content = text_content
        parsed_document.document_metadata = parsed_metadata

    project_id = await get_project_id_for_file_version(
        session,
        file_version_id=file_version.id,
    )
    if project_id is None:
        raise ValueError("File version is not linked to a project.")

    await _replace_document_chunks(
        session,
        parsed_document=parsed_document,
        file_version=file_version,
        project_id=project_id,
        chunks=chunks,
    )


async def _replace_document_chunks(
    session: AsyncSession,
    *,
    parsed_document: ParsedDocument,
    file_version: FileVersion,
    project_id: uuid.UUID,
    chunks: list[dict[str, Any]],
) -> None:
    stored_chunks = [chunk for chunk in chunks if str(chunk.get("text") or "").strip()]
    await session.execute(
        delete(DocumentChunk).where(DocumentChunk.file_version_id == file_version.id)
    )
    session.add_all(
        [
            DocumentChunk(
                parsed_document_id=parsed_document.id,
                file_version_id=file_version.id,
                project_id=project_id,
                chunk_index=index,
                page_start=chunk.get("page_start"),
                page_end=chunk.get("page_end"),
                text=str(chunk["text"]),
                chunk_metadata={
                    **dict(chunk.get("metadata") or {}),
                    "chunking_version": CHUNKING_VERSION,
                },
            )
            for index, chunk in enumerate(stored_chunks)
        ]
    )


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
