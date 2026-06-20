import json
from typing import Any
from urllib import error, request

from app.agents.prompts import KOLAUDIM_WRITER_SYSTEM_PROMPT, SENIOR_REVIEW_SYSTEM_PROMPT
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
    ("gemini", "gemini-2.5-flash"): 32_000,
    ("gemini", "gemini-2.0-flash"): 32_000,
    ("gemini", "gemini-1.5-flash"): 32_000,
}

PROVIDER_REQUEST_TOKEN_LIMITS = {
    "groq": 8_000,
    "openai": 32_000,
    "gemini": 32_000,
}

KOLAUDIM_PROMPT_OVERHEAD_TOKENS = 1_400


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
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LLMReviewError("AI reviewer returned invalid JSON.") from exc

    if not isinstance(parsed, dict):
        raise LLMReviewError("AI reviewer returned a non-object JSON response.")
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
    response_payload = _post_json("/chat/completions", body, ai_settings=ai_settings)
    text = _extract_chat_completion_content(response_payload)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LLMReviewError("AI kolaudim writer returned invalid JSON.") from exc

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


def kolaudim_draft_max_output_tokens(ai_settings: dict[str, Any]) -> int:
    request_limit = model_request_token_limit(ai_settings)
    configured_limit = max(900, int(settings.openai_max_output_tokens or 1800))
    if request_limit <= 9_000:
        return min(configured_limit, 1_800, max(900, request_limit // 4))
    return min(configured_limit, 4_000, max(1_600, request_limit // 5))


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


def _kolaudim_user_content(draft_input: dict[str, Any]) -> str:
    payload = {
        "required_output_shape": {
            "status": "drafted",
            "title": "Draft Akt Kolaudimi Teknik",
            "executive_summary": "string",
            "sections": [
                {
                    "code": "legal_basis | project_identity | document_verification | fact_verification | technical_economic_conclusion | reservations | signature_package",
                    "title": "string",
                    "body": "paragraphs in Albanian",
                    "evidence_notes": ["short evidence/source notes"],
                }
            ],
            "reservations": ["string"],
            "human_completion_items": ["string"],
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


def _post_json(
    path: str,
    body: dict[str, Any],
    *,
    ai_settings: dict[str, Any],
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

    try:
        with request.urlopen(  # nosec B310 - URL is configured API endpoint.
            api_request,
            timeout=settings.openai_timeout_seconds,
        ) as response:
            data = response.read().decode("utf-8")
    except error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        provider_label = ai_settings.get("provider_label") or ai_settings.get("provider") or "AI"
        raise LLMReviewError(
            f"{provider_label} API request failed: {exc.code} {details[:500]}"
        ) from exc
    except error.URLError as exc:
        provider_label = ai_settings.get("provider_label") or ai_settings.get("provider") or "AI"
        raise LLMReviewError(f"{provider_label} API request failed: {exc.reason}") from exc

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
