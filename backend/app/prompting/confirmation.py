import base64
import hashlib
import hmac
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit_log.service import write_audit_log
from app.core.config import settings
from app.prompting import service as prompting_service
from app.prompting.generation import (
    generation_estimate_fingerprint,
    kolaudim_request,
)
from app.prompting.models import PromptRun
from app.projects import service as projects_service
from app.reviews import service as reviews_service
from app.users.models import TelegramAccount

CALLBACK_PREFIX = "pc"


class PromptConfirmationError(ValueError):
    def __init__(self, code: str, message: str, *, terminal: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.user_message = message
        self.terminal = terminal


@dataclass(frozen=True)
class PromptConfirmationResult:
    run_id: UUID
    status: str
    message: str


async def prepare_generation_confirmation(
    session: AsyncSession,
    *,
    run: PromptRun,
    estimate_step_key: str,
    estimate_fingerprint: str,
    estimate_message: str,
) -> None:
    if run.project_id is None:
        raise PromptConfirmationError(
            "confirmation_project_missing",
            "Projekti i gjenerimit nuk është ruajtur.",
            terminal=True,
        )
    now = datetime.now(timezone.utc)
    run.confirmation_expires_at = now + timedelta(
        seconds=settings.prompt_confirmation_timeout_seconds
    )
    metadata = dict(run.attachment_metadata or {})
    metadata["confirmation"] = {
        "estimate_step_key": estimate_step_key,
        "estimate_fingerprint": estimate_fingerprint,
        "project_id": str(run.project_id),
        "prepared_at": now.isoformat(),
    }
    run.attachment_metadata = metadata
    token = _confirmation_token(run)
    run.confirmation_token_hash = _token_hash(token)
    prompting_service.queue_prompt_notification(
        run,
        key="generation_confirmation",
        kind="confirmation",
        body=(
            f"{estimate_message}\n\n"
            "Konfirmoni vetëm nëse dëshironi të nisni thirrjet AI për gjenerimin."
        ),
    )
    await write_audit_log(
        session,
        action="prompt.generation.confirmation_requested",
        entity_type="prompt_run",
        entity_id=run.id,
        actor_user_id=run.user_id,
        project_id=run.project_id,
        details={
            "estimate_step_key": estimate_step_key,
            "expires_at": run.confirmation_expires_at.isoformat(),
        },
    )
    await prompting_service.mark_prompt_run_waiting_for_confirmation(
        session,
        run=run,
    )


def confirmation_callback_data(run: PromptRun, *, action: str) -> str:
    if action not in {"confirm", "cancel"}:
        raise ValueError("Unsupported confirmation action.")
    token = _confirmation_token(run)
    verb = "c" if action == "confirm" else "x"
    return f"{CALLBACK_PREFIX}:{verb}:{_encode_uuid(run.id)}:{token}"


async def confirm_generation(
    session: AsyncSession,
    *,
    callback_data: str,
    user_id: UUID,
    telegram_chat_id: int,
) -> PromptConfirmationResult:
    action, run_id, token = parse_confirmation_callback(callback_data)
    if action != "confirm":
        raise PromptConfirmationError(
            "confirmation_action_invalid",
            "Veprimi i konfirmimit nuk është i vlefshëm.",
        )
    run = await _locked_confirmation_run(session, run_id=run_id)
    _validate_owner(run, user_id=user_id, telegram_chat_id=telegram_chat_id)
    if run.confirmed_at is not None:
        return PromptConfirmationResult(
            run_id=run.id,
            status="already_confirmed",
            message="Gjenerimi është konfirmuar më parë.",
        )
    _validate_pending_confirmation(run, token=token)

    active_project_id = await _active_project_id(session, user_id=user_id)
    if active_project_id is None or active_project_id != run.project_id:
        raise PromptConfirmationError(
            "confirmation_project_changed",
            "Projekti aktiv ka ndryshuar. Niseni përsëri kërkesën /prompt.",
            terminal=True,
        )
    metadata = dict(run.attachment_metadata or {})
    confirmation = dict(metadata.get("confirmation") or {})
    expected_fingerprint = confirmation.get("estimate_fingerprint")
    if not isinstance(expected_fingerprint, str) or not expected_fingerprint:
        raise PromptConfirmationError(
            "confirmation_estimate_missing",
            "Vlerësimi i gjenerimit nuk u gjet. Niseni përsëri /prompt.",
            terminal=True,
        )
    current_estimate = await reviews_service.estimate_review_job(
        session,
        project_id=active_project_id,
        user_id=user_id,
        payload=kolaudim_request(),
    )
    if not hmac.compare_digest(
        expected_fingerprint,
        generation_estimate_fingerprint(current_estimate),
    ):
        raise PromptConfirmationError(
            "confirmation_estimate_changed",
            "Dokumentet ose konfigurimi AI kanë ndryshuar pas vlerësimit. "
            "Niseni përsëri kërkesën /prompt për një vlerësim të ri.",
            terminal=True,
        )

    now = datetime.now(timezone.utc)
    run.confirmed_at = now
    run.confirmation_token_hash = None
    run.confirmation_expires_at = None
    run.status = "queued"
    run.worker_lease_until = None
    await write_audit_log(
        session,
        action="prompt.generation.confirmed",
        entity_type="prompt_run",
        entity_id=run.id,
        actor_user_id=run.user_id,
        project_id=run.project_id,
        details={"confirmed_at": now.isoformat()},
    )
    await session.commit()
    return PromptConfirmationResult(
        run_id=run.id,
        status="confirmed",
        message="Gjenerimi u konfirmua dhe u vendos në radhë.",
    )


async def cancel_generation(
    session: AsyncSession,
    *,
    callback_data: str,
    user_id: UUID,
    telegram_chat_id: int,
) -> PromptConfirmationResult:
    action, run_id, token = parse_confirmation_callback(callback_data)
    if action != "cancel":
        raise PromptConfirmationError(
            "confirmation_action_invalid",
            "Veprimi i anulimit nuk është i vlefshëm.",
        )
    run = await _locked_confirmation_run(session, run_id=run_id)
    _validate_owner(run, user_id=user_id, telegram_chat_id=telegram_chat_id)
    if run.status == "cancelled":
        return PromptConfirmationResult(
            run_id=run.id,
            status="already_cancelled",
            message="Kërkesa është anuluar më parë.",
        )
    if run.confirmed_at is not None or run.review_job_id is not None:
        raise PromptConfirmationError(
            "generation_already_started",
            "Gjenerimi ka nisur dhe nuk mund të anulohet nga ky buton.",
        )
    _validate_pending_confirmation(run, token=token)
    await _cancel_run(session, run=run)
    return PromptConfirmationResult(
        run_id=run.id,
        status="cancelled",
        message="Gjenerimi u anulua. Dokumentet dhe projekti nuk u fshinë.",
    )


async def cancel_latest_waiting_confirmation(
    session: AsyncSession,
    *,
    user_id: UUID,
    telegram_chat_id: int,
) -> PromptConfirmationResult | None:
    run = await prompting_service.get_latest_prompt_run(
        session,
        user_id=user_id,
        telegram_chat_id=telegram_chat_id,
        statuses={"waiting_for_confirmation"},
    )
    if run is None:
        return None
    await _cancel_run(session, run=run)
    return PromptConfirmationResult(
        run_id=run.id,
        status="cancelled",
        message="Gjenerimi u anulua. Dokumentet dhe projekti nuk u fshinë.",
    )


def parse_confirmation_callback(value: str) -> tuple[str, UUID, str]:
    parts = value.split(":")
    if len(parts) != 4 or parts[0] != CALLBACK_PREFIX or parts[1] not in {"c", "x"}:
        raise PromptConfirmationError(
            "confirmation_callback_invalid",
            "Konfirmimi nuk është i vlefshëm.",
        )
    try:
        run_id = _decode_uuid(parts[2])
    except (ValueError, TypeError) as exc:
        raise PromptConfirmationError(
            "confirmation_callback_invalid",
            "Konfirmimi nuk është i vlefshëm.",
        ) from exc
    action = "confirm" if parts[1] == "c" else "cancel"
    return action, run_id, parts[3]


async def _locked_confirmation_run(
    session: AsyncSession,
    *,
    run_id: UUID,
) -> PromptRun:
    result = await session.execute(
        select(PromptRun).where(PromptRun.id == run_id).with_for_update()
    )
    run = result.scalar_one_or_none()
    if run is None:
        raise PromptConfirmationError(
            "confirmation_run_missing",
            "Kërkesa /prompt nuk u gjet.",
        )
    return run


def _validate_owner(
    run: PromptRun,
    *,
    user_id: UUID,
    telegram_chat_id: int,
) -> None:
    if run.user_id != user_id or run.telegram_chat_id != telegram_chat_id:
        raise PromptConfirmationError(
            "confirmation_forbidden",
            "Ky konfirmim nuk i përket llogarisë ose bisedës suaj.",
        )


async def _active_project_id(
    session: AsyncSession,
    *,
    user_id: UUID,
) -> UUID | None:
    result = await session.execute(
        select(TelegramAccount.active_project_id)
        .where(TelegramAccount.user_id == user_id)
        .limit(1)
    )
    project_id = result.scalar_one_or_none()
    if project_id is not None:
        await projects_service.get_project(
            session,
            project_id=project_id,
            user_id=user_id,
        )
        return project_id
    projects = await projects_service.list_projects(session, user_id=user_id)
    return projects[0].id if projects else None


def _validate_pending_confirmation(run: PromptRun, *, token: str) -> None:
    if run.status != "waiting_for_confirmation":
        raise PromptConfirmationError(
            "confirmation_not_pending",
            "Kjo kërkesë nuk pret më konfirmim.",
        )
    now = datetime.now(timezone.utc)
    if run.confirmation_expires_at is None or run.confirmation_expires_at <= now:
        raise PromptConfirmationError(
            "confirmation_expired",
            "Konfirmimi ka skaduar. Niseni përsëri kërkesën /prompt.",
            terminal=True,
        )
    expected_hash = run.confirmation_token_hash or ""
    if not expected_hash or not hmac.compare_digest(expected_hash, _token_hash(token)):
        raise PromptConfirmationError(
            "confirmation_token_invalid",
            "Konfirmimi nuk është i vlefshëm.",
        )


async def _cancel_run(session: AsyncSession, *, run: PromptRun) -> None:
    now = datetime.now(timezone.utc)
    run.status = "cancelled"
    run.confirmation_token_hash = None
    run.confirmation_expires_at = None
    run.worker_lease_until = None
    run.completed_at = now
    await write_audit_log(
        session,
        action="prompt.generation.cancelled",
        entity_type="prompt_run",
        entity_id=run.id,
        actor_user_id=run.user_id,
        project_id=run.project_id,
        details={"cancelled_at": now.isoformat()},
    )
    await session.commit()


def _confirmation_token(run: PromptRun) -> str:
    if run.confirmation_expires_at is None:
        raise ValueError("Confirmation expiry is missing.")
    metadata = dict(run.attachment_metadata or {})
    confirmation = dict(metadata.get("confirmation") or {})
    fingerprint = str(confirmation.get("estimate_fingerprint") or "")
    expires = int(run.confirmation_expires_at.timestamp())
    message = f"{run.id}:{expires}:{fingerprint}".encode("utf-8")
    digest = hmac.new(
        settings.user_api_key_encryption_secret.encode("utf-8"),
        message,
        hashlib.sha256,
    ).digest()[:12]
    return _urlsafe(digest)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _encode_uuid(value: UUID) -> str:
    return _urlsafe(value.bytes)


def _decode_uuid(value: str) -> UUID:
    padding = "=" * (-len(value) % 4)
    return UUID(bytes=base64.urlsafe_b64decode(value + padding))


def _urlsafe(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")
