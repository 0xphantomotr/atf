from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from fastapi import HTTPException

from app.ai import service as ai_service
from app.db.session import AsyncSessionLocal
from app.reviews import service as reviews_service
from app.reviews.schemas import GenerateRequest
from app.telegram.service import get_active_project, get_or_create_message_user

router = Router()


@router.message(Command("vlereso", "estimate", "preflight"))
async def estimate_kolaudim_act(message: Message) -> None:
    async with AsyncSessionLocal() as session:
        user = await get_or_create_message_user(session, message)
        project = await get_active_project(session, user_id=user.id)
        if project is None:
            await message.answer("Nuk keni projekt aktiv. Krijojeni me /projekt_ri.")
            return
        try:
            plan = await reviews_service.estimate_review_job(
                session,
                project_id=project.id,
                user_id=user.id,
                payload=_kolaudim_request(),
            )
        except HTTPException as exc:
            await message.answer(f"Vlerësimi nuk u krye.\n\n{exc.detail}")
            return

    await message.answer(_preflight_text(project.name, plan))


@router.message(Command("gjenero", "generate", "kolaudim", "akt"))
async def generate_kolaudim_act(message: Message) -> None:
    async with AsyncSessionLocal() as session:
        user = await get_or_create_message_user(session, message)
        project = await get_active_project(session, user_id=user.id)
        if project is None:
            await message.answer(
                "Nuk keni projekt aktiv.\n\n"
                "Krijoni një projekt me:\n"
                "/projekt_ri Emri i projektit"
            )
            return
        ai_setting = await ai_service.get_user_ai_setting(session, user_id=user.id)
        if ai_setting is None:
            await message.answer(
                "Draft Akt Kolaudimi kërkon API key personale për AI.\n\n"
                "Konfiguroni fillimisht:\n"
                "/ai_key provider api_key\n\n"
                "Provider të mbështetur: openai, gemini, groq\n"
                "Pastaj përdorni /ai_models dhe /ai_model për të zgjedhur modelin."
            )
            return

        job = await reviews_service.create_review_job(
            session,
            project_id=project.id,
            user_id=user.id,
            payload=_kolaudim_request(),
        )

    totals = dict(job.execution_plan.get("totals", {}))
    await message.answer(
        "Draft Akt Kolaudimi u vendos në radhë.\n\n"
        f"Projekti: {project.name}\n"
        f"Statusi: {job.status}\n\n"
        f"Thirrje AI (maksimum): {totals.get('estimated_calls', 0)}\n"
        f"Tokena (maksimum i vlerësuar): {totals.get('estimated_max_tokens', 0):,}\n\n"
        "Nëse provider-i arrin limitin falas/API, procesi ndalon përkohësisht "
        "dhe vazhdon automatikisht pa përsëritur dokumentet e përfunduara.\n\n"
        "Përdorni /status për ecurinë dhe /raportet kur të përfundojë."
    )


def _kolaudim_request() -> GenerateRequest:
    return GenerateRequest(
        job_type="kolaudim_act",
        output_format="pdf",
        language="sq-AL",
        law_scope=["VKM_610_2022"],
    )


def _preflight_text(project_name: str, plan: dict) -> str:
    source = dict(plan.get("source", {}))
    totals = dict(plan.get("totals", {}))
    lines = [
        "Vlerësimi para gjenerimit",
        "",
        f"Projekti: {project_name}",
        "Dokumente të përdorshme: "
        f"{source.get('eligible_files', 0)}/{source.get('total_files', 0)}",
        f"Fragmente: {source.get('chunk_count', 0)}",
        f"Analiza nga cache: {source.get('analysis_cache_hits', 0)}",
        "",
        "Fazat:",
    ]
    for stage in plan.get("stages", []):
        if not isinstance(stage, dict):
            continue
        condition = " (nëse nevojitet)" if stage.get("conditional") else ""
        lines.append(
            f"- {stage.get('stage')}: {stage.get('model')} | "
            f"{stage.get('estimated_calls', 0)} thirrje{condition}"
        )
    lines.extend(
        [
            "",
            f"Thirrje AI (maksimum): {totals.get('estimated_calls', 0)}",
            f"Tokena input: {totals.get('estimated_input_tokens', 0):,}",
            f"Tokena output (maksimum): {totals.get('max_output_tokens', 0):,}",
            f"Tokena gjithsej (maksimum): {totals.get('estimated_max_tokens', 0):,}",
            "",
            "Niseni me /gjenero.",
        ]
    )
    return "\n".join(lines)
