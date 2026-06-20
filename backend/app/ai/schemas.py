from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class AIProviderRead(BaseModel):
    id: str
    label: str
    default_model: str
    curated_models: list[str]


class AISettingRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    provider: str
    selected_model: str
    api_key_hint: str
    is_enabled: bool
    created_at: datetime
    updated_at: datetime


class AISettingUpsert(BaseModel):
    provider: str = Field(min_length=1, max_length=32)
    api_key: str = Field(min_length=8, max_length=4096)


class AIModelUpdate(BaseModel):
    model: str = Field(min_length=1, max_length=255)


class AIModelsRead(BaseModel):
    provider: str
    models: list[str]
    source: str
