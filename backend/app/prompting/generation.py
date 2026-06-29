import hashlib
import json
from uuid import UUID

from app.prompting.schemas import PromptPlan
from app.reviews.schemas import GenerateRequest


BACKGROUND_ACTIONS = {
    "import_attachment",
    "estimate_kolaudim",
    "generate_kolaudim",
    "deliver_latest_report",
    "answer_project_question",
}


def kolaudim_request() -> GenerateRequest:
    return GenerateRequest(
        job_type="kolaudim_act",
        output_format="pdf",
        language="sq-AL",
        law_scope=["VKM_610_2022"],
        require_ai_review=True,
    )


def plan_requires_background(plan: PromptPlan) -> bool:
    return any(action.type in BACKGROUND_ACTIONS for action in plan.actions)


def plan_requests_generation(plan_payload: dict) -> bool:
    actions = plan_payload.get("actions")
    return isinstance(actions, list) and any(
        isinstance(action, dict) and action.get("type") == "generate_kolaudim"
        for action in actions
    )


def generation_estimate_fingerprint(estimate: dict) -> str:
    stable = dict(estimate)
    stable.pop("generated_at", None)
    encoded = json.dumps(
        stable,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def report_output_matches_prompt_job(
    *,
    prompt_review_job_id: UUID | None,
    expected_review_job_id: UUID,
    output_review_job_id: UUID,
) -> bool:
    return (
        prompt_review_job_id is not None
        and prompt_review_job_id == expected_review_job_id
        and output_review_job_id == expected_review_job_id
    )


def format_generation_estimate(project_name: str, estimate: dict) -> str:
    source = dict(estimate.get("source") or {})
    totals = dict(estimate.get("totals") or {})
    lines = [
        "Vlerësimi para gjenerimit",
        "",
        f"Projekti: {project_name}",
        "Dokumente të përdorshme: "
        f"{source.get('eligible_files', 0)}/{source.get('total_files', 0)}",
        f"Fragmente: {source.get('chunk_count', 0)}",
        f"Analiza nga cache: {source.get('analysis_cache_hits', 0)}",
        "",
        f"Thirrje AI (maksimum): {totals.get('estimated_calls', 0)}",
        f"Tokena input: {int(totals.get('estimated_input_tokens', 0)):,}",
        f"Tokena output (maksimum): {int(totals.get('max_output_tokens', 0)):,}",
        f"Tokena gjithsej (maksimum): {int(totals.get('estimated_max_tokens', 0)):,}",
        "",
        "Shënim: Ky është një kufi konservativ para nisjes, jo konsumi real. "
        "Tokenat realë zakonisht janë më të ulët dhe varen nga cache-i, dokumentet "
        "dhe nevoja për korrigjim.",
    ]
    return "\n".join(lines)
