import json
import time
from typing import Any
from urllib import error, request

from app.agents.prompts import (
    DOCUMENT_ANALYSIS_SYSTEM_PROMPT,
    KOLAUDIM_WRITER_SYSTEM_PROMPT,
    SENIOR_REVIEW_SYSTEM_PROMPT,
    SPECIALIST_REVIEW_SYSTEM_PROMPT,
)
from app.core.config import settings


class LLMReviewError(RuntimeError):
    pass


MODEL_REQUEST_TOKEN_LIMITS: dict[tuple[str, str], int] = {
    ("groq", "openai/gpt-oss-20b"): 8_000,
    ("groq", "openai/gpt-oss-120b"): 8_000,
    ("groq", "llama-3.3-70b-versatile"): 8_000,
    ("groq", "llama-3.1-8b-instant"): 8_000,
    ("openai", "gpt-4.1-mini"): 32_000,
    ("openai", "gpt-4.1"): 32_000,
    ("openai", "gpt-4o-mini"): 32_000,
    ("gemini", "gemini-2.5-flash"): 64_000,
    ("gemini", "gemini-2.5-flash-lite"): 64_000,
    ("gemini", "gemini-3-flash"): 64_000,
    ("gemini", "gemini-3.1-flash-lite"): 64_000,
    ("gemini", "gemini-3.5-flash"): 64_000,
    ("gemini", "gemini-2.0-flash"): 64_000,
    ("gemini", "gemini-1.5-flash"): 64_000,
}

PROVIDER_REQUEST_TOKEN_LIMITS = {
    "groq": 8_000,
    "openai": 32_000,
    "gemini": 64_000,
}

MODEL_OUTPUT_TOKEN_LIMITS: dict[tuple[str, str], int] = {
    ("groq", "openai/gpt-oss-20b"): 1_800,
    ("groq", "openai/gpt-oss-120b"): 1_800,
    ("groq", "llama-3.3-70b-versatile"): 1_800,
    ("groq", "llama-3.1-8b-instant"): 1_200,
    ("openai", "gpt-4.1-mini"): 4_000,
    ("openai", "gpt-4.1"): 6_000,
    ("openai", "gpt-4o-mini"): 4_000,
    ("gemini", "gemini-2.5-flash"): 12_000,
    ("gemini", "gemini-2.5-flash-lite"): 10_000,
    ("gemini", "gemini-3-flash"): 12_000,
    ("gemini", "gemini-3.1-flash-lite"): 10_000,
    ("gemini", "gemini-3.5-flash"): 12_000,
}

PROVIDER_OUTPUT_TOKEN_LIMITS = {
    "groq": 1_800,
    "openai": 4_000,
    "gemini": 12_000,
}

KOLAUDIM_PROMPT_OVERHEAD_TOKENS = 1_400
SPECIALIST_PROMPT_OVERHEAD_TOKENS = 1_000
MIN_LONG_FORM_TIMEOUT_SECONDS = 90
MAX_LONG_FORM_TIMEOUT_SECONDS = 300
TRANSIENT_HTTP_STATUS_CODES = {429, 500, 502, 503, 504}


DOCUMENT_ANALYSIS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "status",
        "document_summary",
        "document_purpose",
        "authoritative_role",
        "claims",
        "limitations",
    ],
    "properties": {
        "status": {"type": "string", "enum": ["analyzed"]},
        "document_summary": {"type": "string"},
        "document_purpose": {"type": "string"},
        "authoritative_role": {"type": "string"},
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "category",
                    "field_name",
                    "original_value",
                    "normalized_value",
                    "confidence",
                    "source_chunk_indexes",
                    "supporting_excerpt",
                ],
                "properties": {
                    "category": {"type": "string"},
                    "field_name": {"type": "string"},
                    "original_value": {"type": "string"},
                    "normalized_value": {"type": "string"},
                    "confidence": {"type": "number"},
                    "source_chunk_indexes": {
                        "type": "array",
                        "items": {"type": "integer"},
                    },
                    "supporting_excerpt": {"type": "string"},
                },
            },
        },
        "limitations": {"type": "array", "items": {"type": "string"}},
    },
}


SENIOR_REVIEW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "status",
        "executive_summary",
        "recommendation",
        "finding_reviews",
        "unknown_document_notes",
        "human_review_required",
        "confidence",
        "limitations",
    ],
    "properties": {
        "status": {"type": "string", "enum": ["reviewed"]},
        "executive_summary": {"type": "string"},
        "recommendation": {"type": "string"},
        "finding_reviews": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "rule_code",
                    "decision",
                    "reasoning_sq",
                    "evidence_assessment",
                    "suggested_action",
                ],
                "properties": {
                    "rule_code": {"type": "string"},
                    "decision": {
                        "type": "string",
                        "enum": [
                            "supported",
                            "needs_human_review",
                            "insufficient_evidence",
                        ],
                    },
                    "reasoning_sq": {"type": "string"},
                    "evidence_assessment": {"type": "string"},
                    "suggested_action": {"type": "string"},
                },
            },
        },
        "unknown_document_notes": {
            "type": "array",
            "items": {"type": "string"},
        },
        "human_review_required": {"type": "boolean"},
        "confidence": {"type": "number"},
        "limitations": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
}


SPECIALIST_STATEMENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["statement", "evidence_ids"],
    "properties": {
        "statement": {"type": "string"},
        "evidence_ids": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
}


SPECIALIST_REVIEW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["status", "memoranda"],
    "properties": {
        "status": {"type": "string", "enum": ["reviewed"]},
        "memoranda": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "code",
                    "established_facts",
                    "technical_assessments",
                    "qualifications",
                    "writer_guidance",
                    "confidence",
                ],
                "properties": {
                    "code": {
                        "type": "string",
                        "enum": [
                            "legal_administrative",
                            "project_parameters",
                            "chronology_completion",
                            "structural_hidden_works",
                            "materials_quality",
                            "contractual_economic",
                        ],
                    },
                    "established_facts": {
                        "type": "array",
                        "items": SPECIALIST_STATEMENT_SCHEMA,
                    },
                    "technical_assessments": {
                        "type": "array",
                        "items": SPECIALIST_STATEMENT_SCHEMA,
                    },
                    "qualifications": {
                        "type": "array",
                        "items": SPECIALIST_STATEMENT_SCHEMA,
                    },
                    "writer_guidance": {
                        "type": "array",
                        "items": SPECIALIST_STATEMENT_SCHEMA,
                    },
                    "confidence": {"type": "number"},
                },
            },
        },
    },
}


KOLAUDIM_DRAFT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "status",
        "title",
        "executive_summary",
        "sections",
        "reservations",
        "human_completion_items",
        "signature_note",
        "confidence",
    ],
    "properties": {
        "status": {"type": "string", "enum": ["drafted"]},
        "title": {"type": "string"},
        "executive_summary": {"type": "string"},
        "sections": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["code", "title", "body", "evidence_notes"],
                "properties": {
                    "code": {"type": "string"},
                    "title": {"type": "string"},
                    "body": {"type": "string"},
                    "evidence_notes": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
            },
        },
        "reservations": {
            "type": "array",
            "items": {"type": "string"},
        },
        "human_completion_items": {
            "type": "array",
            "items": {"type": "string"},
        },
        "signature_note": {"type": "string"},
        "confidence": {"type": "number"},
    },
}


def request_senior_review(
    review_input: dict[str, Any],
    *,
    ai_settings: dict[str, Any],
) -> dict[str, Any]:
    body = {
        "model": ai_settings["model"],
        "messages": [
            {"role": "system", "content": SENIOR_REVIEW_SYSTEM_PROMPT},
            {"role": "user", "content": _review_user_content(review_input)},
        ],
        "response_format": _response_format(ai_settings),
        "temperature": 0,
        "max_tokens": settings.openai_max_output_tokens,
    }
    response_payload = _post_json("/chat/completions", body, ai_settings=ai_settings)
    text = _extract_chat_completion_content(response_payload)
    parsed = _parse_model_json_object(text, error_label="AI reviewer")

    if not isinstance(parsed, dict):
        raise LLMReviewError("AI reviewer returned a non-object JSON response.")
    return parsed


def request_document_analysis(
    analysis_input: dict[str, Any],
    *,
    ai_settings: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, int]]:
    body = {
        "model": ai_settings["model"],
        "messages": [
            {"role": "system", "content": DOCUMENT_ANALYSIS_SYSTEM_PROMPT},
            {"role": "user", "content": _document_analysis_user_content(analysis_input)},
        ],
        "response_format": _schema_response_format(
            ai_settings,
            schema_name="atf_document_analysis",
            schema=DOCUMENT_ANALYSIS_SCHEMA,
        ),
        "temperature": 0,
        "max_tokens": document_analysis_max_output_tokens(ai_settings),
    }
    reasoning_effort = document_analysis_reasoning_effort(ai_settings)
    if reasoning_effort is not None:
        body["reasoning_effort"] = reasoning_effort

    response_payload = _post_json(
        "/chat/completions",
        body,
        ai_settings=ai_settings,
        timeout_seconds=max(int(settings.openai_timeout_seconds), 90),
        retry_transient=True,
    )
    response_payloads = [response_payload]
    try:
        parsed = _parse_document_analysis_response(response_payload)
    except LLMReviewError:
        retry_body = {
            **body,
            "messages": [
                *body["messages"],
                {
                    "role": "user",
                    "content": (
                        "The previous completion was malformed or truncated. Return one "
                        "complete JSON object matching the required schema. Keep summaries, "
                        "excerpts, and values concise, but preserve every material supported "
                        "claim. Do not add prose outside JSON."
                    ),
                },
            ],
        }
        response_payload = _post_json(
            "/chat/completions",
            retry_body,
            ai_settings=ai_settings,
            timeout_seconds=max(int(settings.openai_timeout_seconds), 90),
            retry_transient=True,
        )
        response_payloads.append(response_payload)
        parsed = _parse_document_analysis_response(response_payload)

    return parsed, _sum_response_token_usage(response_payloads)


def request_specialist_review(
    review_input: dict[str, Any],
    *,
    ai_settings: dict[str, Any],
) -> dict[str, Any]:
    body = {
        "model": ai_settings["model"],
        "messages": [
            {"role": "system", "content": SPECIALIST_REVIEW_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": _specialist_review_user_content(review_input),
            },
        ],
        "response_format": _schema_response_format(
            ai_settings,
            schema_name="atf_specialist_review",
            schema=SPECIALIST_REVIEW_SCHEMA,
        ),
        "temperature": 0,
        "max_tokens": specialist_review_max_output_tokens(ai_settings),
    }
    response_payload = _post_json(
        "/chat/completions",
        body,
        ai_settings=ai_settings,
        timeout_seconds=max(int(settings.openai_timeout_seconds), 90),
        retry_transient=True,
    )
    text = _extract_chat_completion_content(response_payload)
    parsed = _parse_model_json_object(text, error_label="AI specialist reviewer")
    if not isinstance(parsed, dict):
        raise LLMReviewError("AI specialist reviewer returned a non-object JSON response.")
    return parsed


def request_kolaudim_draft(
    draft_input: dict[str, Any],
    *,
    ai_settings: dict[str, Any],
) -> dict[str, Any]:
    max_output_tokens = kolaudim_draft_max_output_tokens(ai_settings)
    body = {
        "model": ai_settings["model"],
        "messages": [
            {"role": "system", "content": KOLAUDIM_WRITER_SYSTEM_PROMPT},
            {"role": "user", "content": _kolaudim_user_content(draft_input)},
        ],
        "response_format": _schema_response_format(
            ai_settings,
            schema_name="atf_kolaudim_draft",
            schema=KOLAUDIM_DRAFT_SCHEMA,
        ),
        "temperature": 0,
        "max_tokens": max_output_tokens,
    }
    response_payload = _post_json(
        "/chat/completions",
        body,
        ai_settings=ai_settings,
        timeout_seconds=kolaudim_request_timeout_seconds(
            draft_input,
            ai_settings=ai_settings,
        ),
        retry_transient=True,
    )
    text = _extract_chat_completion_content(response_payload)
    parsed = _parse_model_json_object(text, error_label="AI kolaudim writer")

    if not isinstance(parsed, dict):
        raise LLMReviewError("AI kolaudim writer returned a non-object JSON response.")
    return parsed


def model_request_token_limit(ai_settings: dict[str, Any]) -> int:
    provider = str(ai_settings.get("provider") or "").strip().lower()
    model = str(ai_settings.get("model") or "").strip()
    return MODEL_REQUEST_TOKEN_LIMITS.get(
        (provider, model),
        PROVIDER_REQUEST_TOKEN_LIMITS.get(provider, 16_000),
    )


def document_analysis_max_output_tokens(ai_settings: dict[str, Any]) -> int:
    return model_output_token_limit(ai_settings)


def document_analysis_reasoning_effort(
    ai_settings: dict[str, Any],
) -> str | None:
    if str(ai_settings.get("provider") or "").strip().lower() != "gemini":
        return None

    model = str(ai_settings.get("model") or "").strip().lower()
    if model.startswith("gemini-2.5-"):
        return "none"
    if model.startswith("gemini-3"):
        return "low"
    return None


def specialist_review_max_output_tokens(ai_settings: dict[str, Any]) -> int:
    request_limit = model_request_token_limit(ai_settings)
    output_limit = model_output_token_limit(ai_settings)
    configured_limit = max(1_200, int(settings.openai_max_output_tokens or 1_800))
    if request_limit <= 9_000:
        return min(configured_limit, output_limit, 1_800)
    return min(max(configured_limit, 4_000), output_limit, 6_000)


def specialist_review_input_token_budget(ai_settings: dict[str, Any]) -> int:
    request_limit = model_request_token_limit(ai_settings)
    output_tokens = specialist_review_max_output_tokens(ai_settings)
    budget = request_limit - output_tokens - SPECIALIST_PROMPT_OVERHEAD_TOKENS
    return max(1_800, budget)


def kolaudim_draft_max_output_tokens(ai_settings: dict[str, Any]) -> int:
    provider = str(ai_settings.get("provider") or "").strip().lower()
    request_limit = model_request_token_limit(ai_settings)
    configured_limit = max(900, int(settings.openai_max_output_tokens or 1800))
    output_limit = model_output_token_limit(ai_settings)
    if request_limit <= 9_000:
        return min(configured_limit, output_limit, max(900, request_limit // 4))

    provider_default = PROVIDER_OUTPUT_TOKEN_LIMITS.get(provider, 4_000)
    target_output_tokens = max(configured_limit, provider_default)
    return min(target_output_tokens, output_limit, max(1_600, request_limit // 5))


def model_output_token_limit(ai_settings: dict[str, Any]) -> int:
    provider = str(ai_settings.get("provider") or "").strip().lower()
    model = str(ai_settings.get("model") or "").strip()
    return MODEL_OUTPUT_TOKEN_LIMITS.get(
        (provider, model),
        PROVIDER_OUTPUT_TOKEN_LIMITS.get(provider, 4_000),
    )


def kolaudim_request_timeout_seconds(
    draft_input: dict[str, Any],
    *,
    ai_settings: dict[str, Any],
) -> int:
    budget = draft_input.get("budget")
    estimated_input_tokens = 0
    if isinstance(budget, dict):
        estimated_input_tokens = int(budget.get("estimated_input_tokens") or 0)

    total_tokens = estimated_input_tokens + kolaudim_draft_max_output_tokens(ai_settings)
    estimated_seconds = 60 + (total_tokens // 400)
    return max(
        int(settings.openai_timeout_seconds),
        MIN_LONG_FORM_TIMEOUT_SECONDS,
        min(MAX_LONG_FORM_TIMEOUT_SECONDS, estimated_seconds),
    )


def kolaudim_draft_input_token_budget(ai_settings: dict[str, Any]) -> int:
    request_limit = model_request_token_limit(ai_settings)
    output_tokens = kolaudim_draft_max_output_tokens(ai_settings)
    budget = request_limit - output_tokens - KOLAUDIM_PROMPT_OVERHEAD_TOKENS
    return max(1_500, budget)


def _review_user_content(review_input: dict[str, Any]) -> str:
    payload = {
        "required_output_shape": {
            "status": "reviewed",
            "executive_summary": "string",
            "recommendation": "string",
            "finding_reviews": [
                {
                    "rule_code": "string",
                    "decision": "supported | needs_human_review | insufficient_evidence",
                    "reasoning_sq": "string",
                    "evidence_assessment": "string",
                    "suggested_action": "string",
                }
            ],
            "unknown_document_notes": ["string"],
            "human_review_required": "boolean",
            "confidence": "number between 0 and 1",
            "limitations": ["string"],
        },
        "audit_input": review_input,
    }
    return json.dumps(payload, ensure_ascii=False)


def _document_analysis_user_content(analysis_input: dict[str, Any]) -> str:
    payload = {
        "required_output_shape": {
            "status": "analyzed",
            "document_summary": "concise summary of only these chunks",
            "document_purpose": "purpose evidenced by these chunks",
            "authoritative_role": "primary evidence | supporting evidence | reference | unknown",
            "claims": [
                {
                    "category": (
                        "identity | party | permit | property | chronology | contract | "
                        "economic | technical | work_phase | control_act | material | "
                        "test | declaration | reservation | conclusion | other"
                    ),
                    "field_name": "stable snake_case field name",
                    "original_value": "value exactly as stated",
                    "normalized_value": "normalized value or empty string",
                    "confidence": "number between 0 and 1",
                    "source_chunk_indexes": ["integer indexes from analysis_input.chunks"],
                    "supporting_excerpt": "short exact supporting excerpt",
                }
            ],
            "limitations": ["limitations specific to these chunks"],
        },
        "analysis_input": analysis_input,
    }
    return json.dumps(payload, ensure_ascii=False)


def _specialist_review_user_content(review_input: dict[str, Any]) -> str:
    statement_shape = {
        "statement": "pohim profesional në shqip",
        "evidence_ids": ["vetëm ID të lejuara për domain-in"],
    }
    payload = {
        "required_output_shape": {
            "status": "reviewed",
            "memoranda": [
                {
                    "code": "one exact code from specialist_input.domains",
                    "established_facts": [statement_shape],
                    "technical_assessments": [statement_shape],
                    "qualifications": [statement_shape],
                    "writer_guidance": [statement_shape],
                    "confidence": "number between 0 and 1",
                }
            ],
        },
        "specialist_input": review_input,
    }
    return json.dumps(payload, ensure_ascii=False)


def _kolaudim_user_content(draft_input: dict[str, Any]) -> str:
    payload = {
        "required_output_shape": {
            "status": "drafted",
            "title": "AKT-KOLAUDIMI TEKNIKO-EKONOMIK",
            "executive_summary": "string",
            "sections": [
                {
                    "code": "one code from draft_input.section_blueprint",
                    "title": "string",
                    "body": "detailed professional paragraphs in Albanian",
                    "evidence_notes": ["short evidence/source notes"],
                }
            ],
            "reservations": ["only material technical qualifications; no document checklist"],
            "human_completion_items": ["internal metadata only; never placeholders in public body"],
            "signature_note": "string",
            "confidence": "number between 0 and 1",
        },
        "draft_input": draft_input,
    }
    return json.dumps(payload, ensure_ascii=False)


def _response_format(ai_settings: dict[str, Any]) -> dict[str, Any]:
    return _schema_response_format(
        ai_settings,
        schema_name="atf_senior_review",
        schema=SENIOR_REVIEW_SCHEMA,
    )


def _schema_response_format(
    ai_settings: dict[str, Any],
    *,
    schema_name: str,
    schema: dict[str, Any],
) -> dict[str, Any]:
    if ai_settings.get("provider") == "groq":
        return {"type": "json_object"}

    return {
        "type": "json_schema",
        "json_schema": {
            "name": schema_name,
            "strict": True,
            "schema": schema,
        },
    }


def _parse_document_analysis_response(
    response_payload: dict[str, Any],
) -> dict[str, Any]:
    text = _extract_chat_completion_content(response_payload)
    return _parse_model_json_object(text, error_label="AI document analyzer")


def _parse_model_json_object(text: str, *, error_label: str) -> dict[str, Any]:
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = _loads_embedded_json_object(text)

    if not isinstance(parsed, dict):
        raise LLMReviewError(f"{error_label} returned a non-object JSON response.")
    return parsed


def _loads_embedded_json_object(text: str) -> dict[str, Any]:
    clean_text = _strip_markdown_json_fence(text.strip())
    try:
        parsed = json.loads(clean_text)
    except json.JSONDecodeError:
        parsed = _loads_first_balanced_json_object(clean_text)

    if not isinstance(parsed, dict):
        raise LLMReviewError(
            "AI model returned invalid JSON. "
            f"Response preview: {_response_preview(text)}"
        )
    return parsed


def _strip_markdown_json_fence(text: str) -> str:
    if not text.startswith("```"):
        return text

    lines = text.splitlines()
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _loads_first_balanced_json_object(text: str) -> dict[str, Any]:
    start = text.find("{")
    if start < 0:
        raise LLMReviewError(
            "AI model returned invalid JSON. "
            f"Response preview: {_response_preview(text)}"
        )

    depth = 0
    in_string = False
    escaped = False
    for index, character in enumerate(text[start:], start=start):
        if escaped:
            escaped = False
            continue
        if character == "\\" and in_string:
            escaped = True
            continue
        if character == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                parsed = json.loads(text[start : index + 1])
                if not isinstance(parsed, dict):
                    raise LLMReviewError("AI model returned a non-object JSON response.")
                return parsed

    raise LLMReviewError(
        "AI model returned incomplete JSON. "
        f"Response preview: {_response_preview(text)}"
    )


def _response_preview(text: str) -> str:
    return " ".join(text.split())[:240]


def _post_json(
    path: str,
    body: dict[str, Any],
    *,
    ai_settings: dict[str, Any],
    timeout_seconds: int | None = None,
    retry_transient: bool = False,
) -> dict[str, Any]:
    base_url = str(ai_settings["base_url"]).rstrip("/")
    api_request = request.Request(
        f"{base_url}{path}",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {ai_settings['api_key']}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "AuditimiTeknikBot/0.1",
        },
        method="POST",
    )

    provider_label = ai_settings.get("provider_label") or ai_settings.get("provider") or "AI"
    request_timeout = timeout_seconds or settings.openai_timeout_seconds
    attempts = 3 if retry_transient else 1
    data = ""
    for attempt in range(attempts):
        try:
            with request.urlopen(  # nosec B310 - URL is configured API endpoint.
                api_request,
                timeout=request_timeout,
            ) as response:
                data = response.read().decode("utf-8")
            break
        except error.HTTPError as exc:
            if exc.code in TRANSIENT_HTTP_STATUS_CODES and attempt + 1 < attempts:
                retry_after = exc.headers.get("Retry-After") if exc.headers else None
                try:
                    delay = max(2, min(10, int(retry_after or 0)))
                except ValueError:
                    delay = 2
                time.sleep(delay)
                continue
            details = exc.read().decode("utf-8", errors="replace")
            raise LLMReviewError(
                f"{provider_label} API request failed: {exc.code} {details[:500]}"
            ) from exc
        except (TimeoutError, error.URLError) as exc:
            if attempt + 1 < attempts:
                time.sleep(2)
                continue
            reason = exc.reason if isinstance(exc, error.URLError) else str(exc)
            raise LLMReviewError(
                f"{provider_label} API request timed out after "
                f"{request_timeout} seconds: {reason or 'read timeout'}"
            ) from exc

    try:
        parsed = json.loads(data)
    except json.JSONDecodeError as exc:
        raise LLMReviewError("OpenAI API returned invalid JSON.") from exc

    if not isinstance(parsed, dict):
        raise LLMReviewError("OpenAI API returned a non-object response.")
    return parsed


def _extract_chat_completion_content(payload: dict[str, Any]) -> str:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise LLMReviewError("OpenAI API response did not include choices.")

    first_choice = choices[0]
    if not isinstance(first_choice, dict):
        raise LLMReviewError("OpenAI API response choice has invalid shape.")

    message = first_choice.get("message")
    if not isinstance(message, dict):
        raise LLMReviewError("OpenAI API response did not include a message.")

    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise LLMReviewError("OpenAI API response message content is empty.")
    return content


def _extract_token_usage(payload: dict[str, Any]) -> dict[str, int]:
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return {}

    normalized: dict[str, int] = {}
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        value = usage.get(key)
        if isinstance(value, int) and value >= 0:
            normalized[key] = value
    return normalized


def _sum_response_token_usage(
    payloads: list[dict[str, Any]],
) -> dict[str, int]:
    total: dict[str, int] = {}
    for payload in payloads:
        for key, value in _extract_token_usage(payload).items():
            total[key] = total.get(key, 0) + value
    return total
