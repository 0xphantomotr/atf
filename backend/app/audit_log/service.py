from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.audit_log.models import AuditLog


async def write_audit_log(
    session: AsyncSession,
    *,
    action: str,
    entity_type: str,
    details: dict,
    actor_user_id: UUID | None = None,
    project_id: UUID | None = None,
    entity_id: UUID | None = None,
) -> AuditLog:
    entry = AuditLog(
        action=action,
        entity_type=entity_type,
        details=details,
        actor_user_id=actor_user_id,
        project_id=project_id,
        entity_id=entity_id,
    )
    session.add(entry)
    await session.flush()
    return entry
