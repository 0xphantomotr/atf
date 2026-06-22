import csv
import os
import shutil
import subprocess
from functools import lru_cache
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any


class OCRUnavailableError(RuntimeError):
    pass


class OCRProcessingError(RuntimeError):
    pass


@lru_cache(maxsize=1)
def tesseract_version() -> str:
    executable = _require_executable("tesseract")
    result = _run([executable, "--version"], timeout_seconds=15)
    first_line = result.stdout.splitlines()[0] if result.stdout else "tesseract"
    return first_line.strip()


def ocr_pdf_pages(
    pdf_bytes: bytes,
    *,
    page_numbers: list[int],
    languages: str,
    dpi: int,
    min_confidence: float,
    timeout_seconds: int,
    max_chunk_chars: int,
) -> dict[str, Any]:
    if not page_numbers:
        return _empty_result(languages=languages, dpi=dpi)

    renderer = _require_executable("pdftoppm")
    with TemporaryDirectory(prefix="atf-ocr-") as temp_dir_value:
        temp_dir = Path(temp_dir_value)
        pdf_path = temp_dir / "source.pdf"
        pdf_path.write_bytes(pdf_bytes)
        pages: list[dict[str, Any]] = []
        page_errors: list[dict[str, Any]] = []

        for page_number in sorted(set(page_numbers)):
            try:
                output_prefix = temp_dir / f"page-{page_number}"
                _run(
                    [
                        renderer,
                        "-f",
                        str(page_number),
                        "-l",
                        str(page_number),
                        "-singlefile",
                        "-png",
                        "-r",
                        str(dpi),
                        str(pdf_path),
                        str(output_prefix),
                    ],
                    timeout_seconds=timeout_seconds,
                )
                image_path = output_prefix.with_suffix(".png")
                if not image_path.exists():
                    raise OCRProcessingError(
                        f"PDF renderer did not create an image for page {page_number}."
                    )
                pages.append(
                    _ocr_image_path(
                        image_path,
                        page_number=page_number,
                        languages=languages,
                        dpi=dpi,
                        min_confidence=min_confidence,
                        timeout_seconds=timeout_seconds,
                        max_chunk_chars=max_chunk_chars,
                    )
                )
            except OCRProcessingError as exc:
                page_errors.append({"page_number": page_number, "reason": str(exc)[:1_000]})

    result = _result_from_pages(pages, languages=languages, dpi=dpi)
    result["pages_requested"] = len(set(page_numbers))
    result["page_errors"] = page_errors
    return result


def ocr_image_bytes(
    image_bytes: bytes,
    *,
    suffix: str,
    languages: str,
    dpi: int,
    min_confidence: float,
    timeout_seconds: int,
    max_chunk_chars: int,
) -> dict[str, Any]:
    normalized_suffix = suffix if suffix.startswith(".") else f".{suffix}"
    with TemporaryDirectory(prefix="atf-ocr-") as temp_dir_value:
        image_path = Path(temp_dir_value) / f"source{normalized_suffix.lower()}"
        image_path.write_bytes(image_bytes)
        page = _ocr_image_path(
            image_path,
            page_number=1,
            languages=languages,
            dpi=dpi,
            min_confidence=min_confidence,
            timeout_seconds=timeout_seconds,
            max_chunk_chars=max_chunk_chars,
        )
    return _result_from_pages([page], languages=languages, dpi=dpi)


def parse_tesseract_tsv(
    tsv_text: str,
    *,
    page_number: int,
    languages: str,
    dpi: int,
    min_confidence: float,
    max_chunk_chars: int,
) -> dict[str, Any]:
    words: list[dict[str, Any]] = []
    rejected_word_count = 0
    image_width: int | None = None
    image_height: int | None = None

    reader = csv.DictReader(StringIO(tsv_text), delimiter="\t")
    for row in reader:
        level = _int_value(row.get("level"))
        if level == 1:
            image_width = _int_value(row.get("width")) or image_width
            image_height = _int_value(row.get("height")) or image_height
        if level != 5:
            continue

        text = " ".join(str(row.get("text") or "").split())
        confidence = _float_value(row.get("conf"))
        if not text or confidence is None or confidence < min_confidence:
            if text:
                rejected_word_count += 1
            continue

        words.append(
            {
                "text": text,
                "confidence": confidence,
                "left": _int_value(row.get("left")) or 0,
                "top": _int_value(row.get("top")) or 0,
                "width": _int_value(row.get("width")) or 0,
                "height": _int_value(row.get("height")) or 0,
                "block_num": _int_value(row.get("block_num")) or 0,
                "par_num": _int_value(row.get("par_num")) or 0,
                "line_num": _int_value(row.get("line_num")) or 0,
            }
        )

    lines = _group_words_into_lines(words)
    chunks = _lines_to_chunks(
        lines,
        page_number=page_number,
        languages=languages,
        dpi=dpi,
        image_width=image_width,
        image_height=image_height,
        max_chunk_chars=max_chunk_chars,
    )
    return {
        "page_number": page_number,
        "text": "\n".join(line["text"] for line in lines),
        "chunks": chunks,
        "accepted_word_count": len(words),
        "rejected_word_count": rejected_word_count,
        "average_confidence": _weighted_confidence(words),
        "image_width": image_width,
        "image_height": image_height,
    }


def _ocr_image_path(
    image_path: Path,
    *,
    page_number: int,
    languages: str,
    dpi: int,
    min_confidence: float,
    timeout_seconds: int,
    max_chunk_chars: int,
) -> dict[str, Any]:
    executable = _require_executable("tesseract")
    result = _run(
        [
            executable,
            str(image_path),
            "stdout",
            "-l",
            languages,
            "--oem",
            "1",
            "--psm",
            "3",
            "-c",
            "preserve_interword_spaces=1",
            "tsv",
        ],
        timeout_seconds=timeout_seconds,
    )
    return parse_tesseract_tsv(
        result.stdout,
        page_number=page_number,
        languages=languages,
        dpi=dpi,
        min_confidence=min_confidence,
        max_chunk_chars=max_chunk_chars,
    )


def _group_words_into_lines(words: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[int, int, int], list[dict[str, Any]]] = {}
    for word in words:
        key = (word["block_num"], word["par_num"], word["line_num"])
        grouped.setdefault(key, []).append(word)

    lines: list[dict[str, Any]] = []
    for line_words in grouped.values():
        line_words.sort(key=lambda item: (item["left"], item["top"]))
        lines.append(
            {
                "text": " ".join(str(word["text"]) for word in line_words),
                "bbox": _union_bbox(line_words),
                "confidence": _weighted_confidence(line_words),
                "word_count": len(line_words),
                "top": min(word["top"] for word in line_words),
                "left": min(word["left"] for word in line_words),
            }
        )
    lines.sort(key=lambda item: (item["top"], item["left"]))
    return lines


def _lines_to_chunks(
    lines: list[dict[str, Any]],
    *,
    page_number: int,
    languages: str,
    dpi: int,
    image_width: int | None,
    image_height: int | None,
    max_chunk_chars: int,
) -> list[dict[str, Any]]:
    chunk_lines: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_length = 0

    for line in lines:
        for line_part in _split_line(str(line["text"]), max_chars=max_chunk_chars):
            part = {**line, "text": line_part}
            separator_length = 1 if current else 0
            if current and current_length + separator_length + len(line_part) > max_chunk_chars:
                chunk_lines.append(current)
                current = []
                current_length = 0
            current.append(part)
            current_length += (1 if current_length else 0) + len(line_part)
    if current:
        chunk_lines.append(current)

    chunks: list[dict[str, Any]] = []
    for part_index, items in enumerate(chunk_lines, start=1):
        chunks.append(
            {
                "text": "\n".join(str(item["text"]) for item in items),
                "page_start": page_number,
                "page_end": page_number,
                "metadata": {
                    "source_type": "pdf_ocr",
                    "extraction_method": "tesseract_tsv",
                    "page_number": page_number,
                    "part_index": part_index,
                    "part_count": len(chunk_lines),
                    "ocr_languages": languages,
                    "ocr_dpi": dpi,
                    "ocr_confidence": _line_confidence(items),
                    "bbox": _union_bbox(items),
                    "coordinate_space": {
                        "unit": "pixel",
                        "width": image_width,
                        "height": image_height,
                        "dpi": dpi,
                    },
                    "line_regions": [
                        {
                            "bbox": item["bbox"],
                            "confidence": item["confidence"],
                            "word_count": item["word_count"],
                        }
                        for item in items
                    ],
                },
            }
        )
    return chunks


def _result_from_pages(
    pages: list[dict[str, Any]],
    *,
    languages: str,
    dpi: int,
) -> dict[str, Any]:
    chunks = [chunk for page in pages for chunk in page["chunks"]]
    return {
        "pages": pages,
        "chunks": chunks,
        "engine": "tesseract",
        "engine_version": tesseract_version(),
        "languages": languages,
        "dpi": dpi,
        "pages_requested": len(pages),
        "pages_with_text": sum(1 for page in pages if page["text"].strip()),
        "accepted_word_count": sum(page["accepted_word_count"] for page in pages),
        "rejected_word_count": sum(page["rejected_word_count"] for page in pages),
    }


def _empty_result(*, languages: str, dpi: int) -> dict[str, Any]:
    return {
        "pages": [],
        "chunks": [],
        "engine": "tesseract",
        "engine_version": None,
        "languages": languages,
        "dpi": dpi,
        "pages_requested": 0,
        "pages_with_text": 0,
        "accepted_word_count": 0,
        "rejected_word_count": 0,
    }


def _require_executable(name: str) -> str:
    executable = shutil.which(name)
    if executable is None:
        raise OCRUnavailableError(f"Required OCR executable '{name}' is not installed.")
    return executable


def _run(command: list[str], *, timeout_seconds: int) -> subprocess.CompletedProcess[str]:
    environment = {**os.environ, "OMP_THREAD_LIMIT": "1"}
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            env=environment,
        )
    except subprocess.TimeoutExpired as exc:
        raise OCRProcessingError(
            f"OCR command timed out after {timeout_seconds} seconds."
        ) from exc
    except OSError as exc:
        raise OCRUnavailableError(f"Could not start OCR command: {exc}") from exc

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "unknown OCR error").strip()
        raise OCRProcessingError(detail[:1_000])
    return result


def _union_bbox(items: list[dict[str, Any]]) -> dict[str, int]:
    left = min(int(item.get("left", item.get("bbox", {}).get("left", 0))) for item in items)
    top = min(int(item.get("top", item.get("bbox", {}).get("top", 0))) for item in items)
    right = max(
        int(item.get("left", item.get("bbox", {}).get("left", 0)))
        + int(item.get("width", item.get("bbox", {}).get("width", 0)))
        for item in items
    )
    bottom = max(
        int(item.get("top", item.get("bbox", {}).get("top", 0)))
        + int(item.get("height", item.get("bbox", {}).get("height", 0)))
        for item in items
    )
    return {"left": left, "top": top, "width": right - left, "height": bottom - top}


def _weighted_confidence(items: list[dict[str, Any]]) -> float | None:
    if not items:
        return None
    weights = [max(1, len(str(item.get("text") or ""))) for item in items]
    value = sum(
        float(item["confidence"]) * weight
        for item, weight in zip(items, weights, strict=True)
    )
    return round(value / sum(weights), 2)


def _line_confidence(items: list[dict[str, Any]]) -> float | None:
    weighted = [
        {
            "text": item["text"],
            "confidence": item["confidence"] if item["confidence"] is not None else 0,
        }
        for item in items
    ]
    return _weighted_confidence(weighted)


def _split_line(text: str, *, max_chars: int) -> list[str]:
    parts: list[str] = []
    remaining = text.strip()
    while len(remaining) > max_chars:
        split_at = remaining.rfind(" ", 0, max_chars + 1)
        if split_at <= 0:
            split_at = max_chars
        parts.append(remaining[:split_at].strip())
        remaining = remaining[split_at:].strip()
    if remaining:
        parts.append(remaining)
    return parts


def _int_value(value: object) -> int | None:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _float_value(value: object) -> float | None:
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None
