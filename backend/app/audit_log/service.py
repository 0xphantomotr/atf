from sqlalchemy.ext.asyncio import AsyncSession

from app.audit_log.models import AuditLog


async def write_audit_log(
    session: AsyncSession,
    *,
    action: str,
    entity_type: str,
    details: dict,
) -> AuditLog:
    entry = AuditLog(action=action, entity_type=entity_type, details=details)
    session.add(entry)
    await session.flush()
    return entry

