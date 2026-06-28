from app.prompting.schemas import PromptPlan
from app.prompting.security import contains_likely_secret

PHASE_ONE_ACTIONS = {
    "list_projects",
    "create_project",
    "select_project",
    "show_active_project",
    "get_status",
}
MAX_PROMPT_ACTIONS = 8


class PromptPolicyError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.user_message = message


def validate_prompt_plan(plan: PromptPlan) -> None:
    if plan.needs_clarification:
        return
    if len(plan.actions) > MAX_PROMPT_ACTIONS:
        raise PromptPolicyError(
            "too_many_actions",
            "Kërkesa përmban shumë veprime. Ndajeni në kërkesa më të vogla.",
        )

    action_ids: set[str] = set()
    completed_ids: set[str] = set()
    create_count = 0
    for action in plan.actions:
        if action.type not in PHASE_ONE_ACTIONS:
            raise PromptPolicyError(
                "action_not_allowed",
                f"Veprimi {action.type} nuk mbështetet ende nga /prompt.",
            )
        if action.id in action_ids:
            raise PromptPolicyError(
                "duplicate_step",
                "Plani përmban hapa të përsëritur dhe nuk mund të ekzekutohet.",
            )
        action_ids.add(action.id)

        if len(set(action.depends_on)) != len(action.depends_on):
            raise PromptPolicyError(
                "duplicate_dependency",
                f"Hapi {action.id} përmban varësi të përsëritura.",
            )
        missing_dependencies = set(action.depends_on) - completed_ids
        if missing_dependencies:
            raise PromptPolicyError(
                "invalid_dependency",
                f"Hapi {action.id} varet nga një hap i panjohur ose i mëvonshëm.",
            )
        if action.requires_confirmation:
            raise PromptPolicyError(
                "unexpected_confirmation",
                "Ky plan kërkon një konfirmim që nuk mbështetet ende në fazën e parë.",
            )

        arguments = action.arguments.model_dump(mode="json")
        if contains_likely_secret(str(arguments)):
            raise PromptPolicyError(
                "secret_in_plan",
                "Plani përmban një sekret dhe u refuzua për siguri.",
            )
        if action.type == "create_project":
            create_count += 1
            if create_count > 1:
                raise PromptPolicyError(
                    "multiple_project_creation",
                    "Një kërkesë /prompt mund të krijojë vetëm një projekt.",
                )

        completed_ids.add(action.id)
