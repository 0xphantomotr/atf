from app.prompting.generation import plan_requests_generation
from app.prompting.models import PromptRun
from app.reviews.models import ReviewJob


def generation_prompt_controls_latest_job(
    run: PromptRun | None,
    latest_job: ReviewJob | None,
) -> bool:
    if run is None or not plan_requests_generation(dict(run.plan or {})):
        return False
    if run.review_job_id is not None:
        return latest_job is None or latest_job.id == run.review_job_id
    if latest_job is None:
        return True
    return run.created_at >= latest_job.created_at


def prompt_generation_status_message(run: PromptRun) -> str:
    labels = {
        "planning": "Duke planifikuar kërkesën",
        "queued": "Në radhë",
        "running": "Duke ekzekutuar hapat",
        "waiting_for_documents": "Duke pritur leximin e dokumenteve",
        "waiting_for_confirmation": "Në pritje të konfirmimit të gjenerimit",
        "waiting_for_review": "Akt-Kolaudimi po gjenerohet",
        "waiting_for_delivery": "PDF-ja po dërgohet",
        "completed": "Përfunduar",
        "failed": "Dështoi",
        "cancelled": "Anuluar",
    }
    lines = [
        "Kërkesa /prompt për Akt-Kolaudim",
        f"Statusi: {labels.get(run.status, run.status)}",
    ]
    if run.status == "waiting_for_confirmation":
        lines.append("Përdorni butonat e konfirmimit ose /anulo.")
    elif run.status == "failed":
        lines.append(f"Gabim: {run.error_detail or 'Gabim i panjohur.'}")
        lines.append("Nuk do të dërgohet një raport i vjetër.")
    elif run.status == "cancelled":
        lines.append("Gjenerimi nuk nisi dhe nuk u prodhua PDF e re.")
    return "\n".join(lines)
