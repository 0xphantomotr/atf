import uuid

from app.document_analysis.service import (
    ProviderRequestPacer,
    analysis_cache_key,
    build_chunk_batches,
    consolidate_batch_results,
)
from app.files.models import DocumentChunk, FileVersion


def _chunk(index: int, text: str) -> DocumentChunk:
    return DocumentChunk(
        id=uuid.uuid4(),
        parsed_document_id=uuid.uuid4(),
        file_version_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        chunk_index=index,
        page_start=index + 1,
        page_end=index + 1,
        text=text,
        chunk_metadata={"source_type": "pdf_page", "page_number": index + 1},
    )


def test_chunk_batches_cover_every_chunk_and_follow_model_budget() -> None:
    chunks = [_chunk(index, "x" * 4_000) for index in range(5)]

    groq_batches = build_chunk_batches(
        chunks,
        ai_settings={"provider": "groq", "model": "openai/gpt-oss-20b"},
    )
    gemini_batches = build_chunk_batches(
        chunks,
        ai_settings={"provider": "gemini", "model": "gemini-2.5-flash"},
    )

    assert [chunk.chunk_index for batch in groq_batches for chunk in batch] == list(range(5))
    assert [chunk.chunk_index for batch in gemini_batches for chunk in batch] == list(range(5))
    assert len(groq_batches) > len(gemini_batches)


def test_provider_pacer_uses_configured_request_limit() -> None:
    pacer = ProviderRequestPacer(
        {
            "provider": "gemini",
            "requests_per_minute": 20,
        }
    )

    assert pacer.interval_seconds == 3.1


def test_analysis_cache_key_changes_for_file_or_model() -> None:
    file_version = FileVersion(
        id=uuid.uuid4(),
        file_id=uuid.uuid4(),
        version_number=1,
        original_filename="permit.pdf",
        storage_bucket="atf",
        storage_path="permit.pdf",
        sha256_hash="a" * 64,
        mime_type="application/pdf",
        size_bytes=100,
        parse_status="parsed",
        created_by=uuid.uuid4(),
    )

    first = analysis_cache_key(
        file_version=file_version,
        ai_settings={"provider": "gemini", "model": "gemini-2.5-flash"},
    )
    second = analysis_cache_key(
        file_version=file_version,
        ai_settings={"provider": "gemini", "model": "gemini-3.1-flash-lite"},
    )
    file_version.sha256_hash = "b" * 64
    third = analysis_cache_key(
        file_version=file_version,
        ai_settings={"provider": "gemini", "model": "gemini-2.5-flash"},
    )

    assert first != second
    assert first != third


def test_consolidation_deduplicates_claims_and_preserves_provenance() -> None:
    chunks = [
        _chunk(0, "Leja e ndërtimit nr. 123, datë 01.02.2024."),
        _chunk(1, "Sipas lejes nr. 123 filluan punimet."),
    ]
    result = consolidate_batch_results(
        [
            {
                "document_summary": "Dokumenti përmban lejen.",
                "document_purpose": "Autorizim ndërtimi",
                "authoritative_role": "primary evidence",
                "limitations": [],
                "claims": [
                    {
                        "category": "permit",
                        "field_name": "construction_permit_number",
                        "original_value": "123",
                        "normalized_value": "123",
                        "confidence": 0.9,
                        "source_chunk_indexes": [0],
                        "supporting_excerpt": "Leja e ndërtimit nr. 123",
                    }
                ],
            },
            {
                "document_summary": "Leja referohet sërish.",
                "document_purpose": "Autorizim ndërtimi",
                "authoritative_role": "primary evidence",
                "limitations": ["Data e fillimit nuk përcaktohet."],
                "claims": [
                    {
                        "category": "permit",
                        "field_name": "construction_permit_number",
                        "original_value": "123",
                        "normalized_value": "123",
                        "confidence": 0.95,
                        "source_chunk_indexes": [1],
                        "supporting_excerpt": "lejes nr. 123",
                    },
                    {
                        "category": "party",
                        "field_name": "investor",
                        "original_value": "I pavlefshëm",
                        "normalized_value": "",
                        "confidence": 0.5,
                        "source_chunk_indexes": [999],
                        "supporting_excerpt": "I pavlefshëm",
                    },
                ],
            },
        ],
        chunks=chunks,
    )

    assert result["claim_count"] == 1
    claim = result["claims"][0]
    assert claim["field_name"] == "construction_permit_number"
    assert claim["confidence"] == 0.95
    assert {item["chunk_index"] for item in claim["evidence"]} == {0, 1}
    assert claim["source_batch_indexes"] == [0, 1]
    assert result["limitations"] == ["Data e fillimit nuk përcaktohet."]
