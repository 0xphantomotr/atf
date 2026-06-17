from uuid import UUID

from pydantic import BaseModel, ConfigDict


class NotificationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    project_id: UUID | None
    review_job_id: UUID | None
    channel: str
    title: str
    body: str
    status: str

