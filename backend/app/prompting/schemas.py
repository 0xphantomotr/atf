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
    "estimate_kolaudim",
    "generate_kolaudim",
    "deliver_latest_report",
    "answer_project_question",
    "select_ai_model",
]

PromptClarificationKind = Literal["project", "model", "action"]


class PromptActionArguments(StrictModel):
    name: str | None = Field(default=None, max_length=255)
    model: str | None = Field(default=None, max_length=255)
    question: str | None = Field(default=None, max_length=3_000)
    job_ref: str | None = Field(
        default=None,
        pattern=r"^step-[1-9][0-9]*$",
        max_length=32,
    )

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("Project name cannot be empty.")
        return normalized

    @field_validator("model")
    @classmethod
    def normalize_model(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("AI model name cannot be empty.")
        return normalized

    @field_validator("question")
    @classmethod
    def normalize_question(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("Project question cannot be empty.")
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
        if self.type == "select_ai_model" and self.arguments.model is None:
            raise ValueError("select_ai_model requires a model name.")
        if self.type != "select_ai_model" and self.arguments.model is not None:
            raise ValueError(f"{self.type} does not accept a model name.")
        if self.type == "answer_project_question" and self.arguments.question is None:
            raise ValueError("answer_project_question requires a question.")
        if self.type != "answer_project_question" and self.arguments.question is not None:
            raise ValueError(f"{self.type} does not accept a question.")
        if self.type != "deliver_latest_report" and self.arguments.job_ref is not None:
            raise ValueError(f"{self.type} does not accept a job reference.")
        return self


class PromptPlan(StrictModel):
    version: Literal["prompt-plan-v1"]
    language: Literal["sq-AL"]
    needs_clarification: bool
    clarification_question: str | None = Field(max_length=500)
    clarification_kind: PromptClarificationKind | None = None
    clarification_options: list[str] = Field(default_factory=list, max_length=8)
    actions: list[PromptAction] = Field(max_length=8)

    @model_validator(mode="after")
    def validate_clarification_shape(self) -> "PromptPlan":
        if self.needs_clarification:
            if not self.clarification_question:
                raise ValueError("A clarification question is required.")
            if self.clarification_kind is None:
                raise ValueError("A clarification kind is required.")
            if self.actions:
                raise ValueError("A clarification plan cannot contain executable actions.")
        elif not self.actions:
            raise ValueError("An executable plan must contain at least one action.")
        elif self.clarification_question is not None:
            raise ValueError("Executable plans cannot include a clarification question.")
        elif self.clarification_kind is not None or self.clarification_options:
            raise ValueError("Executable plans cannot include clarification metadata.")
        return self


class PromptProjectContext(StrictModel):
    name: str
    is_active: bool


class PromptRecentTurn(StrictModel):
    request: str = Field(max_length=500)
    action_types: list[str] = Field(max_length=8)


class PromptClarificationContext(StrictModel):
    original_request: str = Field(max_length=1_500)
    kind: PromptClarificationKind
    question: str = Field(max_length=500)
    options: list[str] = Field(default_factory=list, max_length=8)


class PromptPlanningContext(StrictModel):
    projects: list[PromptProjectContext]
    has_ai_settings: bool
    has_attachment: bool = False
    configured_models: list[str] = Field(default_factory=list, max_length=8)
    recent_turns: list[PromptRecentTurn] = Field(default_factory=list, max_length=5)
    pending_clarification: PromptClarificationContext | None = None


class PromptActionResult(StrictModel):
    step_key: str
    action_type: str
    message: str
    data: dict = Field(default_factory=dict)
