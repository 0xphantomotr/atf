import json
from typing import Any
from urllib import error, request

from app.agents.prompts import SENIOR_REVIEW_SYSTEM_PROMPT
from app.core.config import settings


class LLMReviewError(RuntimeError):
    pass


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


def request_senior_review(
    review_input: dict[str, Any],
    *,
    ai_settings: dict[str, Any],
) -> dict[str, Any]:
    body = {
        "model": ai_settings["model"],
        "messages": [
            {"role": "system", "content": SENIOR_REVIEW_SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(review_input, ensure_ascii=False)},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "atf_senior_review",
                "strict": True,
                "schema": SENIOR_REVIEW_SCHEMA,
            },
        },
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
            "Content-Type": "application/json",
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
        raise LLMReviewError(f"OpenAI API request failed: {exc.code} {details[:500]}") from exc
    except error.URLError as exc:
        raise LLMReviewError(f"OpenAI API request failed: {exc.reason}") from exc

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
