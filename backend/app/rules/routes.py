from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.rules.schemas import RuleRead
from app.rules.service import list_rules, seed_vkm_610_rules

router = APIRouter(prefix="/rules", tags=["rules"])


@router.post("/seed/vkm-610", response_model=list[RuleRead])
async def seed_vkm_610(
    session: AsyncSession = Depends(get_session),
) -> list[RuleRead]:
    rules = await seed_vkm_610_rules(session)
    return [RuleRead.model_validate(rule) for rule in rules]


@router.get("", response_model=list[RuleRead])
async def get_rules(session: AsyncSession = Depends(get_session)) -> list[RuleRead]:
    rules = await list_rules(session)
    return [RuleRead.model_validate(rule) for rule in rules]

