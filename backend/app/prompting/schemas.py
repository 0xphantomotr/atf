from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

PROMPT_PLAN_VERSION = "prompt-plan-v1"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


PromptActionType = Literal[
    "list_projects",
    "create_project",
    "select_project",
    "show_active_project",
    "get_status",
    "import_attachment",
]


class PromptActionArguments(StrictModel):
    name: str | None = Field(max_length=255)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("Project name cannot be empty.")
        return normalized


class PromptAction(StrictModel):
    id: str = Field(pattern=r"^step-[1-9][0-9]*$", max_length=32)
    type: PromptActionType
    arguments: PromptActionArguments
    depends_on: list[str] = Field(max_length=8)
    requires_confirmation: bool

    @model_validator(mode="after")
    def validate_arguments_for_type(self) -> "PromptAction":
        project_name_actions = {"create_project", "select_project"}
        if self.type in project_name_actions and self.arguments.name is None:
            raise ValueError(f"{self.type} requires a project name.")
        if self.type not in project_name_actions and self.arguments.name is not None:
            raise ValueError(f"{self.type} does not accept a project name.")
        return self


class PromptPlan(StrictModel):
    version: Literal["prompt-plan-v1"]
    language: Literal["sq-AL"]
    needs_clarification: bool
    clarification_question: str | None = Field(max_length=500)
    actions: list[PromptAction] = Field(max_length=8)

    @model_validator(mode="after")
    def validate_clarification_shape(self) -> "PromptPlan":
        if self.needs_clarification:
            if not self.clarification_question:
                raise ValueError("A clarification question is required.")
            if self.actions:
                raise ValueError("A clarification plan cannot contain executable actions.")
        elif not self.actions:
            raise ValueError("An executable plan must contain at least one action.")
        elif self.clarification_question is not None:
            raise ValueError("Executable plans cannot include a clarification question.")
        return self


class PromptProjectContext(StrictModel):
    name: str
    is_active: bool


class PromptPlanningContext(StrictModel):
    projects: list[PromptProjectContext]
    has_ai_settings: bool
    has_attachment: bool = False


class PromptActionResult(StrictModel):
    step_key: str
    action_type: str
    message: str
    data: dict = Field(default_factory=dict)
