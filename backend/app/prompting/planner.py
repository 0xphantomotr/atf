import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from app.agents.llm import LLMReviewError, request_structured_completion
from app.google_drive.links import extract_google_drive_folder_url
from app.prompting.intents import detect_intent_hints
from app.prompting.schemas import PROMPT_PLAN_VERSION, PromptPlan, PromptPlanningContext

PROMPT_PLANNER_SYSTEM_PROMPT = """
You are the action planner for an Albanian Telegram application that manages technical
construction dossiers. Convert only the user's explicit request into a small structured
plan. Do not answer dossier questions directly; route them to the dedicated action. Do not
invent projects.

Allowed actions in this release:
- list_projects: list the user's projects.
- create_project: create one residential/during_construction project with an explicit name.
- select_project: select one existing project by its exact displayed name.
- show_active_project: show the active project.
- get_status: show the latest review-job status for the active project.
- show_drive_folder: show the Google Drive folder currently bound to the active project.
- bind_drive_folder: bind the exact Google Drive folder URL to the active project without
  importing it.
- check_drive_folder: check read/write access and preview new, changed, unchanged, deleted,
  and skipped files. Use the supplied URL, or the bound folder when no URL is supplied.
- sync_drive_folder: incrementally synchronize the active project's already bound folder.
- import_attachment: import the attached document or ZIP into the selected project.
- import_drive_folder: recursively import supported documents from the exact Google Drive
  folder URL supplied by the user.
- estimate_kolaudim: calculate the existing professional generation preflight.
- generate_kolaudim: start one professional Akt Kolaudimi after confirmation.
- deliver_latest_report: send the PDF from the referenced generation, or the latest
  completed report when the user explicitly asks only for an existing report.
- upload_report_to_drive: upload the PDF from the referenced generation, or the latest
  completed report, into the managed Kolaudimi subfolder of the project's bound Drive
  folder. An explicit Drive URL may be used to bind/change the folder first.
- answer_project_question: answer one informational question from the active project's
  technical dossier. This action never creates, changes, imports, generates, or sends files.
- select_ai_model: select one exact model name for the user's already configured provider.

Rules:
- Return only JSON matching the supplied schema.
- Set version exactly to "prompt-plan-v1" and language exactly to "sq-AL".
- Use sequential IDs step-1, step-2, and so on.
- Dependencies may reference only earlier step IDs.
- requires_confirmation must be true only for generate_kolaudim and false otherwise.
- Never copy, request, store, or expose API keys, tokens, passwords, or secrets.
- If the request is unsupported, ambiguous, or requires a missing project name, return
  needs_clarification=true, one concise Albanian clarification_question, a clarification_kind
  of project, model, or action, and no actions.
- Use context.pending_clarification to combine the original request with the user's current
  clarification response. Do not repeat a completed earlier action from recent_turns unless
  the current request explicitly asks for it.
- recent_turns contain only requests and accepted action names, never dossier evidence.
  An elliptical follow-up after answer_project_question should remain a dossier question.
- If an exact project name is available in context, preserve its displayed spelling.
- Do not create a project unless the user explicitly asks to create one.
- Do not select a project unless the user explicitly asks to select/use it, except that a
  newly created project may be selected when the same request asks to use it.
- Use import_attachment exactly once when context.has_attachment is true and the user
  asks to upload, import, process, or analyze the attached dossier. Generation actions
  may follow it and must depend on it through the action chain.
- Use import_drive_folder when the user explicitly asks to import, read, process, or analyze
  a Google Drive technical folder. It does not require a Telegram attachment.
- Use bind_drive_folder when the user asks only to link/store a Drive folder without import.
- Use sync_drive_folder when the user asks to update or synchronize the linked Drive folder.
- Use check_drive_folder for access checks or a preview of pending Drive changes.
- Use show_drive_folder when the user asks which Drive folder is linked.
- For every generation request, add estimate_kolaudim followed by generate_kolaudim.
  generate_kolaudim must depend on estimate_kolaudim.
- If the user asks to send/deliver the newly generated PDF, add deliver_latest_report,
  set arguments.job_ref to the generate_kolaudim step ID, and depend on that step.
- If the user asks to save/upload the report into the linked Google Drive folder, add
  upload_report_to_drive, set arguments.job_ref to the generate_kolaudim step ID for a new
  generation, and depend on that step. Place it before deliver_latest_report when both apply.
- Set arguments.name to null except for create_project and select_project.
- Set arguments.model to null except for select_ai_model. Model selection requires an exact
  model name; otherwise ask a model clarification and suggest /ai_models.
- When select_ai_model is followed by estimate_kolaudim or answer_project_question, that
  AI action must depend directly on the model-selection step.
- Set arguments.question to null. The server attaches the original user request only to
  answer_project_question after planning.
- Set arguments.drive_url to null. The server attaches only a validated folder URL copied
  from the original request to Drive actions that accept a URL. Upload and preflight may
  use the project's existing bound folder when the request contains no URL.
- Set arguments.job_ref to null except for deliver_latest_report and upload_report_to_drive.
- Never use import_attachment when context.has_attachment is false.
- For a question about facts, dates, parties, contracts, technical evidence, conflicts,
  or VKM 610 in the active dossier, use answer_project_question. It may follow an explicit
  select_project action, but do not combine it with creation, import, generation, status,
  listing, or report delivery.
- If no project is active and the request does not explicitly select an existing project,
  ask which project should be used instead of planning answer_project_question.
- Deterministic intent_hints are advisory. The final plan must still obey the user's explicit
  request and every policy rule.
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
            payload = _apply_server_owned_fields(
                payload,
                prompt=clean_prompt,
                context=context,
            )
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
            "intent_hints": detect_intent_hints(
                prompt,
                has_attachment=context.has_attachment,
            ),
            "context": context.model_dump(mode="json"),
        },
        ensure_ascii=False,
    )


def _apply_server_owned_fields(
    payload: dict[str, Any],
    *,
    prompt: str,
    context: PromptPlanningContext,
) -> dict[str, Any]:
    normalized = dict(payload)
    normalized["version"] = PROMPT_PLAN_VERSION
    normalized["language"] = "sq-AL"
    needs_clarification = normalized.get("needs_clarification") is True
    if needs_clarification:
        kind = normalized.get("clarification_kind")
        if kind not in {"project", "model", "action"}:
            kind = _infer_clarification_kind(normalized, context=context)
        normalized["clarification_kind"] = kind
        normalized["clarification_options"] = _clarification_options(
            kind,
            context=context,
        )
    else:
        normalized["clarification_kind"] = None
        normalized["clarification_options"] = []
    actions = normalized.get("actions")
    if isinstance(actions, list):
        normalized_actions: list[Any] = []
        latest_generation_id: str | None = None
        for value in actions:
            if not isinstance(value, dict):
                normalized_actions.append(value)
                continue
            action = dict(value)
            action_type = action.get("type")
            arguments = action.get("arguments")
            normalized_arguments = dict(arguments) if isinstance(arguments, dict) else {}
            selected_option = _selected_clarification_option(prompt, context=context)
            if (
                action_type == "select_project"
                and context.pending_clarification is not None
                and context.pending_clarification.kind == "project"
                and selected_option is not None
            ):
                normalized_arguments["name"] = selected_option
            elif action_type not in {"create_project", "select_project"}:
                normalized_arguments["name"] = None
            if action_type == "select_ai_model":
                candidate_model = normalized_arguments.get("model")
                if (
                    context.pending_clarification is not None
                    and context.pending_clarification.kind == "model"
                    and selected_option is not None
                ):
                    normalized_arguments["model"] = selected_option
                elif (
                    not isinstance(candidate_model, str)
                    or candidate_model.casefold() not in prompt.casefold()
                ):
                    normalized_arguments["model"] = None
            else:
                normalized_arguments["model"] = None
            normalized_arguments["question"] = (
                _server_owned_question(prompt, context=context)
                if action_type == "answer_project_question"
                else None
            )
            if action_type == "generate_kolaudim":
                latest_generation_id = str(action.get("id") or "") or None
                action["requires_confirmation"] = True
            else:
                action["requires_confirmation"] = False
            normalized_arguments["drive_url"] = (
                _server_owned_drive_url(prompt, context=context)
                if action_type
                in {
                    "bind_drive_folder",
                    "check_drive_folder",
                    "import_drive_folder",
                    "upload_report_to_drive",
                }
                else None
            )
            if action_type in {"deliver_latest_report", "upload_report_to_drive"}:
                normalized_arguments["job_ref"] = latest_generation_id
            else:
                normalized_arguments["job_ref"] = None
            action["arguments"] = normalized_arguments
            normalized_actions.append(action)
        normalized["actions"] = normalized_actions
    return normalized


def _server_owned_question(
    prompt: str,
    *,
    context: PromptPlanningContext,
) -> str:
    pending = context.pending_clarification
    if pending is None:
        return prompt
    return (
        f"Kërkesa fillestare: {pending.original_request}\n"
        f"Sqarimi i përdoruesit: {prompt}"
    )


def _server_owned_drive_url(
    prompt: str,
    *,
    context: PromptPlanningContext,
) -> str | None:
    current = extract_google_drive_folder_url(prompt)
    if current is not None:
        return current
    pending = context.pending_clarification
    if pending is None:
        return None
    return extract_google_drive_folder_url(pending.original_request)


def _selected_clarification_option(
    prompt: str,
    *,
    context: PromptPlanningContext,
) -> str | None:
    pending = context.pending_clarification
    if pending is None or not pending.options:
        return None
    clean = " ".join(prompt.split()).strip()
    if clean.isdigit():
        index = int(clean) - 1
        if 0 <= index < len(pending.options):
            return pending.options[index]
    for option in pending.options:
        if clean.casefold() == option.casefold():
            return option
    return None


def _infer_clarification_kind(
    payload: dict[str, Any],
    *,
    context: PromptPlanningContext,
) -> str:
    question = str(payload.get("clarification_question") or "").casefold()
    if any(word in question for word in ("projekt", "dosje")):
        return "project"
    if any(word in question for word in ("model", "provider", "ai")):
        return "model"
    if not any(project.is_active for project in context.projects) and context.projects:
        return "project"
    return "action"


def _clarification_options(
    kind: str,
    *,
    context: PromptPlanningContext,
) -> list[str]:
    if kind == "project":
        return [project.name for project in context.projects][:8]
    if kind == "model":
        return list(dict.fromkeys(context.configured_models))[:8]
    return [
        "Menaxho projektin",
        "Pyet dosjen teknike",
        "Importo dokumente",
        "Gjenero Akt Kolaudimi",
        "Merr PDF-në e fundit",
    ]


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
