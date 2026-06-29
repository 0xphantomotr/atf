from collections import Counter
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.files.models import FileVersion

IN_PROGRESS_PARSE_STATUSES = frozenset({"pending", "processing"})
SUCCESS_PARSE_STATUSES = frozenset({"parsed", "parsed_with_ocr"})


@dataclass(frozen=True)
class PromptParseSummary:
    total: int
    counts: dict[str, int]
    filenames_by_status: dict[str, tuple[str, ...]]
    missing_version_count: int = 0

    @property
    def complete(self) -> bool:
        return (
            self.total > 0
            and not any(
                self.counts.get(status, 0) for status in IN_PROGRESS_PARSE_STATUSES
            )
            and self.missing_version_count == 0
        )

    @property
    def readable_count(self) -> int:
        return sum(self.counts.get(status, 0) for status in SUCCESS_PARSE_STATUSES)


async def load_prompt_parse_summary(
    session: AsyncSession,
    *,
    version_ids: list[UUID],
) -> PromptParseSummary:
    if not version_ids:
        return PromptParseSummary(
            total=0,
            counts={},
            filenames_by_status={},
        )
    result = await session.execute(
        select(
            FileVersion.id,
            FileVersion.original_filename,
            FileVersion.parse_status,
        ).where(FileVersion.id.in_(version_ids))
    )
    records = [
        {
            "id": str(version_id),
            "filename": filename,
            "status": parse_status,
        }
        for version_id, filename, parse_status in result.all()
    ]
    return summarize_prompt_parse_records(records, expected_total=len(version_ids))


def summarize_prompt_parse_records(
    records: list[dict[str, str]],
    *,
    expected_total: int | None = None,
) -> PromptParseSummary:
    counts = Counter(str(record.get("status") or "unknown") for record in records)
    filenames: dict[str, list[str]] = {}
    for record in records:
        status = str(record.get("status") or "unknown")
        filename = str(record.get("filename") or "dokument")
        filenames.setdefault(status, []).append(filename)
    total = expected_total if expected_total is not None else len(records)
    return PromptParseSummary(
        total=total,
        counts=dict(counts),
        filenames_by_status={
            status: tuple(values) for status, values in filenames.items()
        },
        missing_version_count=max(0, total - len(records)),
    )


def format_prompt_parse_summary(
    summary: PromptParseSummary,
    *,
    project_name: str,
    skipped_count: int,
) -> str:
    lines = [
        "Përpunimi i attachment-it përfundoi.",
        "",
        f"Projekti: {project_name}",
        f"Dokumente të gjurmuara: {summary.total}",
        f"Të lexuara: {summary.counts.get('parsed', 0)}",
        f"Të lexuara me OCR: {summary.counts.get('parsed_with_ocr', 0)}",
        f"Kërkojnë OCR/verifikim: {summary.counts.get('needs_ocr', 0)}",
        f"Pa tekst të përdorshëm: {summary.counts.get('empty', 0)}",
        f"Formate të papërpunuara: {summary.counts.get('unsupported', 0)}",
        f"Dështuan: {summary.counts.get('failed', 0)}",
        f"Të anashkaluara nga ZIP: {skipped_count}",
    ]
    if summary.missing_version_count:
        lines.append(f"Versione që nuk u gjetën: {summary.missing_version_count}")
    lines.extend(
        [
            "",
            "Përdorni /dokumentet për listën e projektit.",
        ]
    )
    return "\n".join(lines)
