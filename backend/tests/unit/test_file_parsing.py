import asyncio
import uuid
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.files import parse_service
from app.files.models import ParsedDocument
from app.files.ocr import parse_tesseract_tsv
from app.files.parse_service import (
    CHUNKING_VERSION,
    MAX_CHUNK_CHARS,
    _completed_parse_status,
    _parse_image,
    _parse_docx,
    _parse_mpp,
    _parse_pdf,
    _replace_document_chunks,
    _split_text,
)


def test_textless_pdf_requires_ocr() -> None:
    assert _completed_parse_status(suffix=".pdf", chunks=[], page_count=3) == "needs_ocr"


def test_parse_completion_status_distinguishes_readable_and_empty_files() -> None:
    assert (
        _completed_parse_status(
            suffix=".pdf",
            chunks=[{"text": "Tekst i lexueshëm"}],
            page_count=1,
        )
        == "parsed"
    )
    assert _completed_parse_status(suffix=".docx", chunks=[], page_count=None) == "empty"


def test_parse_completion_status_marks_ocr_text_explicitly() -> None:
    assert (
        _completed_parse_status(
            suffix=".pdf",
            chunks=[
                {
                    "text": "Tekst i lexuar me OCR",
                    "metadata": {"extraction_method": "tesseract_tsv"},
                }
            ],
            page_count=1,
        )
        == "parsed_with_ocr"
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

    parsed = _parse_pdf(b"synthetic-pdf", ocr_enabled=False)

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


def test_parse_pdf_ocr_only_textless_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    reader = SimpleNamespace(
        pages=[
            SimpleNamespace(extract_text=lambda: "Tekst origjinal"),
            SimpleNamespace(extract_text=lambda: ""),
        ]
    )
    ocr_result = {
        "pages": [
            {
                "page_number": 2,
                "text": "Tekst nga skanimi",
                "chunks": [],
                "accepted_word_count": 3,
                "rejected_word_count": 0,
            }
        ],
        "chunks": [
            {
                "text": "Tekst nga skanimi",
                "page_start": 2,
                "page_end": 2,
                "metadata": {
                    "source_type": "pdf_ocr",
                    "extraction_method": "tesseract_tsv",
                    "page_number": 2,
                    "part_index": 1,
                },
            }
        ],
        "engine": "tesseract",
        "engine_version": "tesseract 5",
        "languages": "sqi+eng",
        "dpi": 300,
        "pages_requested": 1,
        "pages_with_text": 1,
        "accepted_word_count": 3,
        "rejected_word_count": 0,
    }
    ocr_mock = MagicMock(return_value=ocr_result)
    monkeypatch.setattr(parse_service, "PdfReader", lambda _: reader)
    monkeypatch.setattr(parse_service, "ocr_pdf_pages", ocr_mock)

    parsed = _parse_pdf(b"synthetic-pdf", ocr_enabled=True)

    assert [chunk["page_start"] for chunk in parsed["chunks"]] == [1, 2]
    assert parsed["metadata"]["native_pages_with_text"] == 1
    assert parsed["metadata"]["ocr_pages_with_text"] == 1
    assert parsed["metadata"]["ocr"]["status"] == "completed"
    assert "Tekst origjinal" in parsed["text_content"]
    assert "Tekst nga skanimi" in parsed["text_content"]
    assert ocr_mock.call_args.kwargs["page_numbers"] == [2]


def test_parse_image_uses_ocr_chunks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        parse_service,
        "ocr_image_bytes",
        lambda *_args, **_kwargs: {
            "pages": [
                {
                    "page_number": 1,
                    "text": "Leje ndërtimi nr. 12",
                    "chunks": [],
                    "accepted_word_count": 4,
                    "rejected_word_count": 0,
                }
            ],
            "chunks": [
                {
                    "text": "Leje ndërtimi nr. 12",
                    "page_start": 1,
                    "page_end": 1,
                    "metadata": {
                        "source_type": "pdf_ocr",
                        "extraction_method": "tesseract_tsv",
                    },
                }
            ],
            "engine": "tesseract",
            "engine_version": "tesseract 5",
            "languages": "sqi+eng",
            "dpi": 300,
            "pages_requested": 1,
            "pages_with_text": 1,
            "accepted_word_count": 4,
            "rejected_word_count": 0,
        },
    )

    parsed = _parse_image(b"image", suffix=".png", ocr_enabled=True)

    assert parsed["chunks"][0]["metadata"]["source_type"] == "image_ocr"
    assert parsed["chunks"][0]["metadata"]["extraction_method"] == "tesseract_tsv"
    assert parsed["metadata"]["ocr_pages_with_text"] == 1


def test_tesseract_tsv_preserves_coordinates_and_confidence() -> None:
    tsv = "\n".join(
        [
            "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\t"
            "width\theight\tconf\ttext",
            "1\t1\t0\t0\t0\t0\t0\t0\t1200\t1600\t-1\t",
            "5\t1\t1\t1\t1\t1\t100\t200\t60\t30\t95\tAkt",
            "5\t1\t1\t1\t1\t2\t170\t200\t160\t30\t85\tkolaudimi",
            "5\t1\t1\t1\t2\t1\t100\t250\t120\t30\t90\tObjekti",
            "5\t1\t1\t1\t2\t2\t230\t250\t80\t30\t10\tbllokuar",
        ]
    )

    result = parse_tesseract_tsv(
        tsv,
        page_number=3,
        languages="sqi+eng",
        dpi=300,
        min_confidence=30,
        max_chunk_chars=MAX_CHUNK_CHARS,
    )

    assert result["text"] == "Akt kolaudimi\nObjekti"
    assert result["accepted_word_count"] == 3
    assert result["rejected_word_count"] == 1
    chunk = result["chunks"][0]
    assert chunk["page_start"] == 3
    assert chunk["metadata"]["ocr_confidence"] == 88.38
    assert chunk["metadata"]["bbox"] == {
        "left": 100,
        "top": 200,
        "width": 230,
        "height": 80,
    }
    assert chunk["metadata"]["coordinate_space"] == {
        "unit": "pixel",
        "width": 1200,
        "height": 1600,
        "dpi": 300,
    }


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


def test_parse_mpp_extracts_schedule_strings_from_binary() -> None:
    task_lines = "\n".join(
        [
            "AFATI I PUNIMEVE",
            "Njoftim fillim punimesh | Duration: 0 days | Start: Mon 3/20/23 | Finish: Mon 3/20/23",
            "Ndertimi i themeleve | Duration: 487 days | Start: Mon 4/24/23 | Finish: Tue 3/4/25",
            "Akti I Kolaudimit | Duration: 10 days | Start: Thu 3/27/25 | Finish: Wed 4/9/25",
        ]
    )
    payload = b"\xd0\xcf\x11\xe0" + task_lines.encode("utf-16le")

    parsed = _parse_mpp(payload)

    assert parsed["metadata"]["parser"] == "mpp-best-effort-strings"
    assert parsed["metadata"]["extraction_status"] == "partial"
    assert parsed["chunks"][0]["metadata"]["source_type"] == "mpp_schedule"
    assert parsed["chunks"][0]["metadata"]["extraction_method"] == "binary_string_scan"
    assert "AFATI I PUNIMEVE" in parsed["text_content"]
    assert "Akti I Kolaudimit" in parsed["text_content"]
    assert "Start: Thu 3/27/25" in parsed["text_content"]


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
    assert all(
        chunk.chunk_metadata["chunking_version"] == CHUNKING_VERSION for chunk in stored
    )
