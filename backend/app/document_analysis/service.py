import asyncio
import hashlib
import json
import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Iterable

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.llm import (
    document_analysis_max_output_tokens,
    model_request_token_limit,
    request_document_analysis,
)
from app.document_analysis.models import (
    DocumentAnalysisBatch,
    DocumentAnalysisClaim,
    DocumentAnalysisRun,
)
from app.files.models import DocumentChunk, FileVersion, ParsedDocument, ProjectFile
from app.files.parse_service import CHUNKING_VERSION, parse_file_version

ANALYZER_VERSION = "document-analyzer-v1"
PROMPT_VERSION = "document-analysis-prompt-v1"
SCHEMA_VERSION = "document-analysis-schema-v1"
PROMPT_OVERHEAD_TOKENS = 1_600
CHARS_PER_TOKEN = 4
MAX_BATCH_INPUT_TOKENS = 16_000
DEFAULT_PROVIDER_REQUESTS_PER_MINUTE = {
    "gemini": 15,
    "groq": 30,
    "openai": 60,
}


class DocumentAnalysisError(RuntimeError):
    pass


class ProviderRequestPacer:
    def __init__(self, ai_settings: dict[str, Any]) -> None:
        configured_rpm = ai_settings.get("requests_per_minute")
        if isinstance(configured_rpm, int) and configured_rpm > 0:
            requests_per_minute = configured_rpm
        else:
            provider = str(ai_settings.get("provider") or "").lower()
            requests_per_minute = DEFAULT_PROVIDER_REQUESTS_PER_MINUTE.get(provider, 30)
        self.interval_seconds = (60 / requests_per_minute) + 0.1
        self.last_request_started: float | None = None

    async def wait(self) -> None:
        if self.last_request_started is not None:
            elapsed = time.monotonic() - self.last_request_started
            if elapsed < self.interval_seconds:
                await asyncio.sleep(self.interval_seconds - elapsed)
        self.last_request_started = time.monotonic()


async def analyze_file_version(
    session: AsyncSession,
    *,
    file_version_id: uuid.UUID,
    requested_by: uuid.UUID,
    ai_settings: dict[str, Any],
    request_pacer: ProviderRequestPacer | None = None,
) -> DocumentAnalysisRun:
    file_version, project_file, parsed_document = await _load_analysis_source(
        session,
        file_version_id=file_version_id,
    )
    chunks = await _load_chunks(session, file_version_id=file_version.id)
    if not chunks:
        raise DocumentAnalysisError(
            f"Dokumenti {file_version.original_filename} nuk ka fragmente të analizuara."
        )

    cache_key = analysis_cache_key(file_version=file_version, ai_settings=ai_settings)
    completed = await _load_completed_run(session, cache_key=cache_key)
    if completed is not None:
        return completed

    batches = build_chunk_batches(chunks, ai_settings=ai_settings)
    run = await _load_resumable_run(session, cache_key=cache_key)
    if run is None:
        run = DocumentAnalysisRun(
            file_version_id=file_version.id,
            project_id=project_file.project_id,
            requested_by=requested_by,
            file_sha256=file_version.sha256_hash,
            cache_key=cache_key,
            provider=str(ai_settings["provider"]),
            model=str(ai_settings["model"]),
            analyzer_version=ANALYZER_VERSION,
            prompt_version=PROMPT_VERSION,
            schema_version=SCHEMA_VERSION,
            status="running",
            chunk_count=len(chunks),
            batch_count=len(batches),
            completed_batch_count=0,
            attempt_count=1,
            document_summary={},
            token_usage={},
            started_at=datetime.now(timezone.utc),
        )
        session.add(run)
        await session.commit()
        await session.refresh(run)
    else:
        run.status = "running"
        run.error_message = None
        run.completed_at = None
        run.attempt_count += 1
        run.chunk_count = len(chunks)
        run.batch_count = len(batches)
        await session.commit()

    persisted_batches = await _load_run_batches(session, run_id=run.id)
    completed_results: list[dict[str, Any]] = []
    completed_usages: list[dict[str, int]] = []
    request_pacer = request_pacer or ProviderRequestPacer(ai_settings)

    for batch_index, batch_chunks in enumerate(batches):
        batch_input = _build_batch_input(
            file_version=file_version,
            parsed_document=parsed_document,
            chunks=batch_chunks,
            batch_index=batch_index,
            batch_count=len(batches),
        )
        input_hash = _stable_hash(batch_input)
        persisted = persisted_batches.get(batch_index)
        if (
            persisted is not None
            and persisted.status == "completed"
            and persisted.input_hash == input_hash
        ):
            completed_results.append(dict(persisted.result or {}))
            completed_usages.append(_normalized_usage(persisted.token_usage))
            continue

        if persisted is None:
            persisted = DocumentAnalysisBatch(
                analysis_run_id=run.id,
                batch_index=batch_index,
                status="running",
                input_hash=input_hash,
                chunk_ids=[str(chunk.id) for chunk in batch_chunks],
                result={},
                token_usage={},
                started_at=datetime.now(timezone.utc),
            )
            session.add(persisted)
        else:
            persisted.status = "running"
            persisted.input_hash = input_hash
            persisted.chunk_ids = [str(chunk.id) for chunk in batch_chunks]
            persisted.result = {}
            persisted.token_usage = {}
            persisted.error_message = None
            persisted.started_at = datetime.now(timezone.utc)
            persisted.completed_at = None
        await session.commit()

        batch_record_id = persisted.id
        run_id = run.id
        try:
            await request_pacer.wait()
            result, usage = request_document_analysis(
                batch_input,
                ai_settings=ai_settings,
            )
            normalized_result = _normalize_batch_result(result, allowed_chunks=batch_chunks)
        except Exception as exc:
            await session.rollback()
            persisted = await session.get(DocumentAnalysisBatch, batch_record_id)
            run = await session.get(DocumentAnalysisRun, run_id)
            if persisted is not None:
                persisted.status = "failed"
                persisted.error_message = str(exc)
                persisted.completed_at = datetime.now(timezone.utc)
            if run is not None:
                run.status = "failed"
                run.error_message = str(exc)
                run.completed_batch_count = len(completed_results)
                run.token_usage = _sum_token_usage(completed_usages)
                run.completed_at = datetime.now(timezone.utc)
            await session.commit()
            raise DocumentAnalysisError(
                f"Analiza e dokumentit {file_version.original_filename} dështoi në "
                f"grupin {batch_index + 1}/{len(batches)}: {exc}"
            ) from exc

        persisted.result = normalized_result
        persisted.token_usage = _normalized_usage(usage)
        persisted.status = "completed"
        persisted.completed_at = datetime.now(timezone.utc)
        completed_results.append(normalized_result)
        completed_usages.append(_normalized_usage(usage))
        run.completed_batch_count = len(completed_results)
        run.token_usage = _sum_token_usage(completed_usages)
        await session.commit()

    consolidated = consolidate_batch_results(completed_results, chunks=chunks)
    await session.execute(
        delete(DocumentAnalysisClaim).where(DocumentAnalysisClaim.analysis_run_id == run.id)
    )
    for claim_index, claim in enumerate(consolidated["claims"]):
        session.add(
            DocumentAnalysisClaim(
                analysis_run_id=run.id,
                file_version_id=file_version.id,
                project_id=project_file.project_id,
                claim_index=claim_index,
                category=claim["category"],
                field_name=claim["field_name"],
                original_value=claim["original_value"],
                normalized_value=claim["normalized_value"] or None,
                confidence=claim["confidence"],
                evidence=claim["evidence"],
                extraction_method="ai_chunk_analysis",
                claim_metadata={"source_batch_indexes": claim["source_batch_indexes"]},
            )
        )

    run.status = "completed"
    run.completed_batch_count = len(completed_results)
    run.document_summary = {
        key: value for key, value in consolidated.items() if key != "claims"
    }
    run.token_usage = _sum_token_usage(completed_usages)
    run.error_message = None
    run.completed_at = datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(run)
    return run


async def ensure_project_document_analyses(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    requested_by: uuid.UUID,
    ai_settings: dict[str, Any],
) -> list[DocumentAnalysisRun]:
    result = await session.execute(
        select(FileVersion)
        .join(ProjectFile, ProjectFile.id == FileVersion.file_id)
        .where(
            ProjectFile.project_id == project_id,
            ProjectFile.deleted_at.is_(None),
            FileVersion.version_number == ProjectFile.current_version,
        )
        .order_by(ProjectFile.created_at, FileVersion.version_number)
    )

    analyses: list[DocumentAnalysisRun] = []
    request_pacer = ProviderRequestPacer(ai_settings)
    for file_version in result.scalars():
        if file_version.parse_status == "parsed":
            chunk_count = await _chunk_count(session, file_version_id=file_version.id)
            if chunk_count == 0:
                await parse_file_version(session, file_version_id=file_version.id)
        if file_version.parse_status != "parsed":
            continue
        analyses.append(
            await analyze_file_version(
                session,
                file_version_id=file_version.id,
                requested_by=requested_by,
                ai_settings=ai_settings,
                request_pacer=request_pacer,
            )
        )
    return analyses


async def analysis_run_payloads(
    session: AsyncSession,
    *,
    runs: list[DocumentAnalysisRun],
) -> list[dict[str, Any]]:
    if not runs:
        return []
    run_ids = [run.id for run in runs]
    result = await session.execute(
        select(DocumentAnalysisClaim)
        .where(DocumentAnalysisClaim.analysis_run_id.in_(run_ids))
        .order_by(
            DocumentAnalysisClaim.analysis_run_id,
            DocumentAnalysisClaim.claim_index,
        )
    )
    claims_by_run: defaultdict[uuid.UUID, list[dict[str, Any]]] = defaultdict(list)
    for claim in result.scalars():
        claims_by_run[claim.analysis_run_id].append(
            {
                "category": claim.category,
                "field_name": claim.field_name,
                "original_value": claim.original_value,
                "normalized_value": claim.normalized_value,
                "confidence": float(claim.confidence) if claim.confidence is not None else None,
                "evidence": list(claim.evidence or []),
            }
        )

    return [
        {
            "analysis_run_id": str(run.id),
            "file_version_id": str(run.file_version_id),
            "file_sha256": run.file_sha256,
            "provider": run.provider,
            "model": run.model,
            "analyzer_version": run.analyzer_version,
            "prompt_version": run.prompt_version,
            "schema_version": run.schema_version,
            "summary": dict(run.document_summary or {}),
            "claims": claims_by_run.get(run.id, []),
        }
        for run in runs
    ]


def analysis_cache_key(
    *,
    file_version: FileVersion,
    ai_settings: dict[str, Any],
) -> str:
    return _stable_hash(
        {
            "file_version_id": str(file_version.id),
            "file_sha256": file_version.sha256_hash,
            "provider": str(ai_settings.get("provider") or ""),
            "model": str(ai_settings.get("model") or ""),
            "chunking_version": CHUNKING_VERSION,
            "analyzer_version": ANALYZER_VERSION,
            "prompt_version": PROMPT_VERSION,
            "schema_version": SCHEMA_VERSION,
        }
    )


def build_chunk_batches(
    chunks: list[DocumentChunk],
    *,
    ai_settings: dict[str, Any],
) -> list[list[DocumentChunk]]:
    output_tokens = document_analysis_max_output_tokens(ai_settings)
    available_tokens = (
        model_request_token_limit(ai_settings) - output_tokens - PROMPT_OVERHEAD_TOKENS
    )
    token_budget = max(1_000, min(MAX_BATCH_INPUT_TOKENS, available_tokens))
    char_budget = token_budget * CHARS_PER_TOKEN

    batches: list[list[DocumentChunk]] = []
    current: list[DocumentChunk] = []
    current_chars = 0
    for chunk in chunks:
        chunk_chars = len(chunk.text) + 300
        if current and current_chars + chunk_chars > char_budget:
            batches.append(current)
            current = []
            current_chars = 0
        current.append(chunk)
        current_chars += chunk_chars
    if current:
        batches.append(current)
    return batches


def consolidate_batch_results(
    batch_results: list[dict[str, Any]],
    *,
    chunks: list[DocumentChunk],
) -> dict[str, Any]:
    chunks_by_index = {chunk.chunk_index: chunk for chunk in chunks}
    summaries: list[str] = []
    purposes: list[str] = []
    roles: list[str] = []
    limitations: list[str] = []
    claims_by_key: dict[tuple[str, str, str], dict[str, Any]] = {}

    for batch_index, result in enumerate(batch_results):
        _append_unique(summaries, result.get("document_summary"))
        _append_unique(purposes, result.get("document_purpose"))
        _append_unique(roles, result.get("authoritative_role"))
        for limitation in _string_list(result.get("limitations")):
            _append_unique(limitations, limitation)

        claims = result.get("claims")
        if not isinstance(claims, list):
            continue
        for raw_claim in claims:
            if not isinstance(raw_claim, dict):
                continue
            field_name = _clean_field_name(raw_claim.get("field_name"))
            original_value = _clean_string(raw_claim.get("original_value"))
            if not field_name or not original_value:
                continue
            category = _clean_field_name(raw_claim.get("category")) or "other"
            normalized_value = _clean_string(raw_claim.get("normalized_value"))
            source_indexes = _valid_source_indexes(
                raw_claim.get("source_chunk_indexes"),
                chunks_by_index=chunks_by_index,
            )
            if not source_indexes:
                continue
            evidence = _claim_evidence(
                raw_claim,
                source_indexes=source_indexes,
                chunks_by_index=chunks_by_index,
            )
            confidence = _confidence(raw_claim.get("confidence"))
            key = (
                category,
                field_name,
                (normalized_value or original_value).casefold(),
            )
            existing = claims_by_key.get(key)
            if existing is None:
                claims_by_key[key] = {
                    "category": category,
                    "field_name": field_name,
                    "original_value": original_value,
                    "normalized_value": normalized_value,
                    "confidence": confidence,
                    "evidence": evidence,
                    "source_batch_indexes": [batch_index],
                }
                continue
            existing["confidence"] = max(existing["confidence"], confidence)
            existing["evidence"] = _merge_evidence(existing["evidence"], evidence)
            if batch_index not in existing["source_batch_indexes"]:
                existing["source_batch_indexes"].append(batch_index)

    return {
        "summary": " ".join(summaries),
        "batch_summaries": summaries,
        "document_purposes": purposes,
        "authoritative_roles": roles,
        "limitations": limitations,
        "claim_count": len(claims_by_key),
        "claims": list(claims_by_key.values()),
    }


async def _load_analysis_source(
    session: AsyncSession,
    *,
    file_version_id: uuid.UUID,
) -> tuple[FileVersion, ProjectFile, ParsedDocument]:
    result = await session.execute(
        select(FileVersion, ProjectFile, ParsedDocument)
        .join(ProjectFile, ProjectFile.id == FileVersion.file_id)
        .join(ParsedDocument, ParsedDocument.file_version_id == FileVersion.id)
        .where(FileVersion.id == file_version_id)
    )
    row = result.one_or_none()
    if row is None:
        raise DocumentAnalysisError("Versioni i dokumentit të analizueshëm nuk u gjet.")
    file_version, project_file, parsed_document = row
    if file_version.parse_status != "parsed":
        raise DocumentAnalysisError(
            f"Dokumenti {file_version.original_filename} nuk është në gjendjen parsed."
        )
    return file_version, project_file, parsed_document


async def _load_chunks(
    session: AsyncSession,
    *,
    file_version_id: uuid.UUID,
) -> list[DocumentChunk]:
    result = await session.execute(
        select(DocumentChunk)
        .where(DocumentChunk.file_version_id == file_version_id)
        .order_by(DocumentChunk.chunk_index)
    )
    return list(result.scalars())


async def _chunk_count(session: AsyncSession, *, file_version_id: uuid.UUID) -> int:
    chunks = await _load_chunks(session, file_version_id=file_version_id)
    return len(chunks)


async def _load_completed_run(
    session: AsyncSession,
    *,
    cache_key: str,
) -> DocumentAnalysisRun | None:
    result = await session.execute(
        select(DocumentAnalysisRun)
        .where(
            DocumentAnalysisRun.cache_key == cache_key,
            DocumentAnalysisRun.status == "completed",
        )
        .order_by(DocumentAnalysisRun.completed_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _load_resumable_run(
    session: AsyncSession,
    *,
    cache_key: str,
) -> DocumentAnalysisRun | None:
    result = await session.execute(
        select(DocumentAnalysisRun)
        .where(
            DocumentAnalysisRun.cache_key == cache_key,
            DocumentAnalysisRun.status.in_(("running", "failed")),
        )
        .order_by(DocumentAnalysisRun.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _load_run_batches(
    session: AsyncSession,
    *,
    run_id: uuid.UUID,
) -> dict[int, DocumentAnalysisBatch]:
    result = await session.execute(
        select(DocumentAnalysisBatch)
        .where(DocumentAnalysisBatch.analysis_run_id == run_id)
        .order_by(DocumentAnalysisBatch.batch_index)
    )
    return {batch.batch_index: batch for batch in result.scalars()}


def _build_batch_input(
    *,
    file_version: FileVersion,
    parsed_document: ParsedDocument,
    chunks: list[DocumentChunk],
    batch_index: int,
    batch_count: int,
) -> dict[str, Any]:
    return {
        "document": {
            "file_version_id": str(file_version.id),
            "filename": file_version.original_filename,
            "sha256": file_version.sha256_hash,
            "mime_type": file_version.mime_type,
            "document_type": parsed_document.document_type,
            "language": parsed_document.language,
            "batch_index": batch_index,
            "batch_count": batch_count,
        },
        "chunks": [
            {
                "chunk_index": chunk.chunk_index,
                "page_start": chunk.page_start,
                "page_end": chunk.page_end,
                "coordinates": dict(chunk.chunk_metadata or {}),
                "text": chunk.text,
            }
            for chunk in chunks
        ],
    }


def _normalize_batch_result(
    result: dict[str, Any],
    *,
    allowed_chunks: list[DocumentChunk],
) -> dict[str, Any]:
    if result.get("status") != "analyzed":
        raise DocumentAnalysisError("AI document analyzer returned an invalid status.")
    allowed_indexes = {chunk.chunk_index for chunk in allowed_chunks}
    normalized_claims: list[dict[str, Any]] = []
    claims = result.get("claims")
    if isinstance(claims, list):
        for claim in claims:
            if not isinstance(claim, dict):
                continue
            source_indexes = [
                index
                for index in _integer_list(claim.get("source_chunk_indexes"))
                if index in allowed_indexes
            ]
            if not source_indexes:
                continue
            normalized_claims.append(
                {
                    "category": _clean_field_name(claim.get("category")) or "other",
                    "field_name": _clean_field_name(claim.get("field_name")),
                    "original_value": _clean_string(claim.get("original_value")),
                    "normalized_value": _clean_string(claim.get("normalized_value")),
                    "confidence": _confidence(claim.get("confidence")),
                    "source_chunk_indexes": source_indexes,
                    "supporting_excerpt": _clean_string(claim.get("supporting_excerpt"))[:500],
                }
            )
    return {
        "status": "analyzed",
        "document_summary": _clean_string(result.get("document_summary")),
        "document_purpose": _clean_string(result.get("document_purpose")),
        "authoritative_role": _clean_string(result.get("authoritative_role")),
        "claims": normalized_claims,
        "limitations": _string_list(result.get("limitations")),
    }


def _valid_source_indexes(
    value: Any,
    *,
    chunks_by_index: dict[int, DocumentChunk],
) -> list[int]:
    return [index for index in _integer_list(value) if index in chunks_by_index]


def _claim_evidence(
    claim: dict[str, Any],
    *,
    source_indexes: list[int],
    chunks_by_index: dict[int, DocumentChunk],
) -> list[dict[str, Any]]:
    excerpt = _clean_string(claim.get("supporting_excerpt"))[:500]
    evidence: list[dict[str, Any]] = []
    for chunk_index in source_indexes:
        chunk = chunks_by_index[chunk_index]
        evidence.append(
            {
                "chunk_id": str(chunk.id),
                "chunk_index": chunk.chunk_index,
                "page_start": chunk.page_start,
                "page_end": chunk.page_end,
                "coordinates": dict(chunk.chunk_metadata or {}),
                "supporting_excerpt": excerpt,
                "excerpt_verified": _normalized_text(excerpt) in _normalized_text(chunk.text),
            }
        )
    return evidence


def _merge_evidence(left: list[dict], right: list[dict]) -> list[dict]:
    merged = list(left)
    seen = {(item.get("chunk_id"), item.get("supporting_excerpt")) for item in merged}
    for item in right:
        key = (item.get("chunk_id"), item.get("supporting_excerpt"))
        if key not in seen:
            merged.append(item)
            seen.add(key)
    return merged


def _sum_token_usage(usages: Iterable[dict[str, int]]) -> dict[str, int]:
    totals: defaultdict[str, int] = defaultdict(int)
    for usage in usages:
        for key, value in _normalized_usage(usage).items():
            totals[key] += value
    return dict(totals)


def _normalized_usage(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    return {
        key: item
        for key, item in value.items()
        if key in {"prompt_tokens", "completion_tokens", "total_tokens"}
        and isinstance(item, int)
        and item >= 0
    }


def _stable_hash(value: Any) -> str:
    serialized = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _integer_list(value: Any) -> list[int]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, int) and not isinstance(item, bool)]


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [cleaned for item in value if (cleaned := _clean_string(item))]


def _append_unique(values: list[str], value: Any) -> None:
    cleaned = _clean_string(value)
    if cleaned and cleaned not in values:
        values.append(cleaned)


def _clean_field_name(value: Any) -> str:
    cleaned = _clean_string(value).lower().replace("-", " ")
    return "_".join(cleaned.split())[:128]


def _clean_string(value: Any) -> str:
    return " ".join(value.split()) if isinstance(value, str) else ""


def _confidence(value: Any) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return 0.0
    return max(0.0, min(1.0, float(value)))


def _normalized_text(value: str) -> str:
    return " ".join(value.casefold().split())
