from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    project_type: str
    stage: str
    location: str | None = None
    description: str | None = None


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    project_type: str | None = None
    stage: str | None = None
    location: str | None = None
    description: str | None = None


class ProjectRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    project_type: str
    stage: str
    location: str | None
    description: str | None
    language: str
    created_by: UUID

