import asyncio
import uuid
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.files import parse_service
from app.files.models import ParsedDocument
from app.files.parse_service import (
    MAX_CHUNK_CHARS,
    _parse_docx,
    _parse_pdf,
    _replace_document_chunks,
    _split_text,
)


def test_split_text_preserves_all_content_with_bounded_chunks() -> None:
    source = " ".join(f"fjala-{index}" for index in range(1_200))

    parts = _split_text(source)

    assert len(parts) > 1
    assert all(0 < len(part) <= MAX_CHUNK_CHARS for part in parts)
    assert " ".join(parts) == source


def test_parse_pdf_creates_page_aware_chunks(monkeypatch: pytest.MonkeyPatch) -> None:
    first_page = "Përmbajtja e faqes së parë."
    second_page = " ".join(f"seksioni-{index}" for index in range(900))
    reader = SimpleNamespace(
        pages=[
            SimpleNamespace(extract_text=lambda: first_page),
            SimpleNamespace(extract_text=lambda: second_page),
            SimpleNamespace(extract_text=lambda: ""),
        ]
    )
    monkeypatch.setattr(parse_service, "PdfReader", lambda _: reader)

    parsed = _parse_pdf(b"synthetic-pdf")

    assert parsed["page_count"] == 3
    assert parsed["metadata"]["pages_with_text"] == 2
    assert parsed["metadata"]["chunk_count"] == len(parsed["chunks"])
    assert parsed["chunks"][0]["page_start"] == 1
    assert parsed["chunks"][0]["metadata"]["source_type"] == "pdf_page"
    second_page_chunks = [
        chunk for chunk in parsed["chunks"] if chunk["metadata"]["page_number"] == 2
    ]
    assert len(second_page_chunks) > 1
    assert " ".join(chunk["text"] for chunk in second_page_chunks) == second_page
    assert all(len(chunk["text"]) <= MAX_CHUNK_CHARS for chunk in parsed["chunks"])


def test_parse_docx_preserves_paragraph_and_table_coordinates() -> None:
    docx = pytest.importorskip("docx")
    Document = docx.Document
    document = Document()
    document.add_paragraph("Përshkrimi i objektit dhe vendndodhjes.")
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "Fusha"
    table.cell(0, 1).text = "Vlera"
    table.cell(1, 0).text = "Sipërfaqja"
    table.cell(1, 1).text = "120 m2"
    document.add_paragraph("Konkluzioni teknik pas tabelës.")
    payload = BytesIO()
    document.save(payload)

    parsed = _parse_docx(payload.getvalue())

    source_types = [chunk["metadata"]["source_type"] for chunk in parsed["chunks"]]
    assert source_types == ["docx_paragraphs", "docx_table", "docx_paragraphs"]
    assert parsed["chunks"][0]["metadata"]["paragraph_start"] == 1
    assert parsed["chunks"][1]["metadata"] == {
        "source_type": "docx_table",
        "table_index": 1,
        "row_start": 1,
        "row_end": 2,
        "block_index": 2,
    }
    assert parsed["chunks"][2]["metadata"]["paragraph_start"] == 2
    assert "Fusha | Vlera" in parsed["text_content"]
    assert "Konkluzioni teknik" in parsed["text_content"]


def test_replace_document_chunks_deletes_old_rows_and_restarts_indexes() -> None:
    session = MagicMock()
    session.execute = AsyncMock()
    session.add_all = MagicMock()
    parsed_document = ParsedDocument(
        id=uuid.uuid4(),
        file_version_id=uuid.uuid4(),
        document_type="construction_permit",
        language="sq-AL",
        page_count=2,
        text_content="Dokumenti",
        document_metadata={},
    )
    file_version = SimpleNamespace(id=parsed_document.file_version_id)
    project_id = uuid.uuid4()
    chunks = [
        {
            "text": "Faqja e parë",
            "page_start": 1,
            "page_end": 1,
            "metadata": {"source_type": "pdf_page"},
        },
        {
            "text": "Faqja e dytë",
            "page_start": 2,
            "page_end": 2,
            "metadata": {"source_type": "pdf_page"},
        },
    ]

    asyncio.run(
        _replace_document_chunks(
            session,
            parsed_document=parsed_document,
            file_version=file_version,
            project_id=project_id,
            chunks=chunks,
        )
    )

    session.execute.assert_awaited_once()
    stored = session.add_all.call_args.args[0]
    assert [chunk.chunk_index for chunk in stored] == [0, 1]
    assert [chunk.page_start for chunk in stored] == [1, 2]
    assert all(chunk.project_id == project_id for chunk in stored)
    assert all(chunk.chunk_metadata["chunking_version"] == 1 for chunk in stored)
