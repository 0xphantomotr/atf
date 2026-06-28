import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from app.agents.llm import LLMReviewError, request_structured_completion
from app.prompting.schemas import PROMPT_PLAN_VERSION, PromptPlan, PromptPlanningContext

PROMPT_PLANNER_SYSTEM_PROMPT = """
You are the action planner for an Albanian Telegram application that manages technical
construction dossiers. Convert only the user's explicit request into a small structured
plan. Do not answer dossier questions and do not invent projects.

Allowed actions in this release:
- list_projects: list the user's projects.
- create_project: create one residential/during_construction project with an explicit name.
- select_project: select one existing project by its exact displayed name.
- show_active_project: show the active project.
- get_status: show the latest review-job status for the active project.

Rules:
- Return only JSON matching the supplied schema.
- Set version exactly to "prompt-plan-v1" and language exactly to "sq-AL".
- Use sequential IDs step-1, step-2, and so on.
- Dependencies may reference only earlier step IDs.
- requires_confirmation must always be false in this release.
- Never copy, request, store, or expose API keys, tokens, passwords, or secrets.
- If the request is unsupported, ambiguous, or requires a missing project name, return
  needs_clarification=true, one concise Albanian clarification_question, and no actions.
- If an exact project name is available in context, preserve its displayed spelling.
- Do not create a project unless the user explicitly asks to create one.
- Do not select a project unless the user explicitly asks to select/use it, except that a
  newly created project may be selected when the same request asks to use it.
""".strip()


class PromptPlanningError(RuntimeError):
    pass


StructuredRequest = Callable[..., tuple[dict[str, Any], dict[str, int]]]


@dataclass(frozen=True)
class PromptPlannerResult:
    plan: PromptPlan
    token_usage: dict[str, int]


def plan_prompt(
    prompt: str,
    *,
    context: PromptPlanningContext,
    ai_settings: dict[str, Any],
    request_fn: StructuredRequest | None = None,
) -> PromptPlannerResult:
    clean_prompt = prompt.strip()
    if not clean_prompt:
        raise PromptPlanningError("Shkruani kërkesën pas komandës /prompt.")

    structured_request = request_fn or request_structured_completion
    user_content = _planner_user_content(clean_prompt, context=context)
    first_error: Exception | None = None
    token_usage: dict[str, int] = {}
    for attempt in range(2):
        content = user_content
        if attempt == 1:
            content = (
                f"{user_content}\n\n"
                "The previous response was invalid. Return one complete JSON object that "
                "matches the schema exactly. Do not add prose or markdown."
            )
        try:
            payload, usage = structured_request(
                system_prompt=PROMPT_PLANNER_SYSTEM_PROMPT,
                user_content=content,
                schema_name="atf_prompt_plan",
                schema=PromptPlan.model_json_schema(),
                ai_settings=ai_settings,
                max_output_tokens=1_200,
            )
            _merge_token_usage(token_usage, usage)
            payload = _apply_server_owned_fields(payload)
            return PromptPlannerResult(
                plan=PromptPlan.model_validate(payload),
                token_usage=token_usage,
            )
        except ValidationError as exc:
            first_error = exc
        except LLMReviewError as exc:
            if not _is_structural_response_error(exc):
                raise
            first_error = exc

    raise PromptPlanningError(
        "AI nuk ktheu një plan të vlefshëm. Riformuloni kërkesën dhe provoni përsëri."
    ) from first_error


def _planner_user_content(prompt: str, *, context: PromptPlanningContext) -> str:
    return json.dumps(
        {
            "user_request": prompt,
            "context": context.model_dump(mode="json"),
        },
        ensure_ascii=False,
    )


def _apply_server_owned_fields(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload)
    normalized["version"] = PROMPT_PLAN_VERSION
    normalized["language"] = "sq-AL"
    return normalized


def _is_structural_response_error(exc: LLMReviewError) -> bool:
    message = str(exc).lower()
    return any(
        marker in message
        for marker in (
            "invalid json",
            "incomplete json",
            "non-object",
            "did not include choices",
            "did not include a message",
            "content is empty",
        )
    )


def _merge_token_usage(total: dict[str, int], usage: dict[str, int]) -> None:
    for key, value in usage.items():
        if isinstance(value, int) and value >= 0:
            total[key] = total.get(key, 0) + value
