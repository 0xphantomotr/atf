import math
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.llm import (
    document_analysis_max_output_tokens,
    kolaudim_draft_input_token_budget,
    kolaudim_draft_max_output_tokens,
    specialist_review_input_token_budget,
    specialist_review_max_output_tokens,
)
from app.ai.stages import ai_settings_for_stage
from app.document_analysis.models import DocumentAnalysisRun
from app.document_analysis.service import (
    CHARS_PER_TOKEN,
    PROMPT_OVERHEAD_TOKENS,
    analysis_cache_key,
    build_chunk_batches,
)
from app.files.models import DocumentChunk, FileVersion, ProjectFile
from app.files.status import is_parsed_status

SPECIALIST_PROMPT_OVERHEAD_TOKENS = 1_000
KOLAUDIM_PROMPT_OVERHEAD_TOKENS = 1_400


async def estimate_generation_plan(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    job_type: str,
    require_ai_review: bool,
    ai_settings: dict[str, Any] | None,
) -> dict[str, Any]:
    file_result = await session.execute(
        select(FileVersion)
        .join(ProjectFile, ProjectFile.id == FileVersion.file_id)
        .where(
            ProjectFile.project_id == project_id,
            ProjectFile.deleted_at.is_(None),
            FileVersion.version_number == ProjectFile.current_version,
        )
        .order_by(ProjectFile.created_at, FileVersion.version_number)
    )
    files = list(file_result.scalars())

    chunk_result = await session.execute(
        select(DocumentChunk)
        .where(DocumentChunk.project_id == project_id)
        .order_by(DocumentChunk.file_version_id, DocumentChunk.chunk_index)
    )
    chunks_by_version: dict[uuid.UUID, list[DocumentChunk]] = defaultdict(list)
    for chunk in chunk_result.scalars():
        chunks_by_version[chunk.file_version_id].append(chunk)

    eligible_files = [
        file_version
        for file_version in files
        if is_parsed_status(file_version.parse_status)
        and chunks_by_version.get(file_version.id)
    ]
    source_characters = sum(
        len(chunk.text)
        for file_version in eligible_files
        for chunk in chunks_by_version[file_version.id]
    )
    chunk_count = sum(len(chunks_by_version[item.id]) for item in eligible_files)
    source_tokens = max(1, math.ceil(source_characters / CHARS_PER_TOKEN))

    extraction_calls = 0
    extraction_input_tokens = 0
    cache_hits = 0
    extraction_output_tokens = 0
    extraction_settings: dict[str, Any] | None = None

    if require_ai_review and ai_settings is not None:
        extraction_settings = ai_settings_for_stage(ai_settings, "extraction")
        output_limit = document_analysis_max_output_tokens(extraction_settings)
        for file_version in eligible_files:
            chunks = chunks_by_version[file_version.id]
            cache_key = analysis_cache_key(
                file_version=file_version,
                ai_settings=extraction_settings,
            )
            cached_result = await session.execute(
                select(DocumentAnalysisRun.id).where(
                    DocumentAnalysisRun.cache_key == cache_key,
                    DocumentAnalysisRun.status == "completed",
                ).limit(1)
            )
            if cached_result.scalar_one_or_none() is not None:
                cache_hits += 1
                continue

            batches = build_chunk_batches(chunks, ai_settings=extraction_settings)
            extraction_calls += len(batches)
            extraction_output_tokens += len(batches) * output_limit
            extraction_input_tokens += sum(
                PROMPT_OVERHEAD_TOKENS
                + math.ceil(
                    sum(len(chunk.text) + 300 for chunk in batch)
                    / CHARS_PER_TOKEN
                )
                for batch in batches
            )

    stages = build_generation_stages(
        job_type=job_type,
        require_ai_review=require_ai_review,
        ai_settings=ai_settings,
        source_tokens=source_tokens,
        extraction_calls=extraction_calls,
        extraction_input_tokens=extraction_input_tokens,
        extraction_output_tokens=extraction_output_tokens,
    )
    totals = {
        "estimated_calls": sum(int(stage["estimated_calls"]) for stage in stages),
        "estimated_input_tokens": sum(
            int(stage["estimated_input_tokens"]) for stage in stages
        ),
        "max_output_tokens": sum(int(stage["max_output_tokens"]) for stage in stages),
    }
    totals["estimated_max_tokens"] = (
        totals["estimated_input_tokens"] + totals["max_output_tokens"]
    )

    return {
        "version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "project_id": str(project_id),
        "source": {
            "total_files": len(files),
            "eligible_files": len(eligible_files),
            "skipped_files": len(files) - len(eligible_files),
            "chunk_count": chunk_count,
            "source_characters": source_characters,
            "estimated_source_tokens": source_tokens,
            "analysis_cache_hits": cache_hits,
        },
        "stages": stages,
        "totals": totals,
        "assumptions": [
            "Vlerësimi është kufi konservativ, jo faturë ose garanci kuote.",
            "Korrigjimi llogaritet si një thirrje maksimale dhe kryhet vetëm "
            "kur verifikimi e kërkon.",
            "Dokumentet pa tekst të fragmentuar përjashtohen nga thirrjet AI.",
            "Vetëm analizat e përfunduara me të njëjtin provider, model dhe "
            "version prompti llogariten cache hit.",
        ],
    }


def build_generation_stages(
    *,
    job_type: str,
    require_ai_review: bool,
    ai_settings: dict[str, Any] | None,
    source_tokens: int,
    extraction_calls: int,
    extraction_input_tokens: int,
    extraction_output_tokens: int,
) -> list[dict[str, Any]]:
    if not require_ai_review or ai_settings is None:
        return []

    extraction = ai_settings_for_stage(ai_settings, "extraction")
    stages = [
        _stage(
            "extraction",
            extraction,
            calls=extraction_calls,
            input_tokens=extraction_input_tokens,
            output_tokens=extraction_output_tokens,
        )
    ]

    synthesis = ai_settings_for_stage(ai_settings, "synthesis")
    synthesis_input = min(
        max(1, source_tokens),
        specialist_review_input_token_budget(synthesis),
    ) + SPECIALIST_PROMPT_OVERHEAD_TOKENS
    stages.append(
        _stage(
            "synthesis",
            synthesis,
            calls=1,
            input_tokens=synthesis_input,
            output_tokens=specialist_review_max_output_tokens(synthesis),
        )
    )

    if job_type != "kolaudim_act":
        return stages

    drafting = ai_settings_for_stage(ai_settings, "drafting")
    drafting_input = min(
        max(1, source_tokens),
        kolaudim_draft_input_token_budget(drafting),
    ) + KOLAUDIM_PROMPT_OVERHEAD_TOKENS
    drafting_output = kolaudim_draft_max_output_tokens(drafting)
    stages.append(
        _stage(
            "drafting",
            drafting,
            calls=1,
            input_tokens=drafting_input,
            output_tokens=drafting_output,
        )
    )

    correction = ai_settings_for_stage(ai_settings, "correction")
    correction_source = max(1, min(source_tokens // 4 + drafting_output, source_tokens))
    correction_input = min(
        correction_source,
        kolaudim_draft_input_token_budget(correction),
    ) + KOLAUDIM_PROMPT_OVERHEAD_TOKENS
    stages.append(
        _stage(
            "correction",
            correction,
            calls=1,
            input_tokens=correction_input,
            output_tokens=kolaudim_draft_max_output_tokens(correction),
            conditional=True,
        )
    )
    return stages


def _stage(
    stage: str,
    ai_settings: dict[str, Any],
    *,
    calls: int,
    input_tokens: int,
    output_tokens: int,
    conditional: bool = False,
) -> dict[str, Any]:
    return {
        "stage": stage,
        "provider": str(ai_settings.get("provider") or ""),
        "model": str(ai_settings.get("model") or ""),
        "estimated_calls": calls,
        "estimated_input_tokens": input_tokens,
        "max_output_tokens": output_tokens,
        "estimated_max_tokens": input_tokens + output_tokens,
        "conditional": conditional,
    }
