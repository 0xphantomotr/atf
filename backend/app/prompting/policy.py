from app.prompting.schemas import PromptPlan
from app.prompting.security import contains_likely_secret

PROMPT_ACTIONS = {
    "list_projects",
    "create_project",
    "select_project",
    "show_active_project",
    "get_status",
    "import_attachment",
    "estimate_kolaudim",
    "generate_kolaudim",
    "deliver_latest_report",
}
MAX_PROMPT_ACTIONS = 8


class PromptPolicyError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.user_message = message


def validate_prompt_plan(plan: PromptPlan, *, has_attachment: bool = False) -> None:
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
    import_count = 0
    estimate_ids: list[str] = []
    generation_ids: list[str] = []
    delivery_count = 0
    for action in plan.actions:
        if action.type not in PROMPT_ACTIONS:
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
        if action.type == "generate_kolaudim" and not action.requires_confirmation:
            raise PromptPolicyError(
                "generation_confirmation_missing",
                "Gjenerimi i Akt-Kolaudimit kërkon konfirmim të shprehur.",
            )
        if action.type != "generate_kolaudim" and action.requires_confirmation:
            raise PromptPolicyError(
                "unexpected_confirmation",
                f"Veprimi {action.type} nuk duhet të kërkojë konfirmim.",
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
        if action.type == "import_attachment":
            import_count += 1
            if import_count > 1:
                raise PromptPolicyError(
                    "multiple_attachment_imports",
                    "Një kërkesë /prompt mund ta importojë attachment-in vetëm një herë.",
                )
        if action.type == "estimate_kolaudim":
            estimate_ids.append(action.id)
            if len(estimate_ids) > 1:
                raise PromptPolicyError(
                    "multiple_generation_estimates",
                    "Një kërkesë /prompt mund të vlerësojë gjenerimin vetëm një herë.",
                )
        if action.type == "generate_kolaudim":
            generation_ids.append(action.id)
            if len(generation_ids) > 1:
                raise PromptPolicyError(
                    "multiple_generations",
                    "Një kërkesë /prompt mund të nisë vetëm një Akt Kolaudimi.",
                )
            if not estimate_ids or estimate_ids[-1] not in action.depends_on:
                raise PromptPolicyError(
                    "generation_estimate_missing",
                    "Gjenerimi duhet të varet nga vlerësimi paraprak.",
                )
        if action.type == "deliver_latest_report":
            delivery_count += 1
            if delivery_count > 1:
                raise PromptPolicyError(
                    "multiple_report_deliveries",
                    "Një kërkesë /prompt mund ta dërgojë raportin vetëm një herë.",
                )
            if generation_ids:
                generation_id = generation_ids[-1]
                if action.arguments.job_ref != generation_id:
                    raise PromptPolicyError(
                        "report_job_reference_invalid",
                        "Dërgimi i PDF-së duhet t'i referohet gjenerimit të kësaj kërkese.",
                    )
                if generation_id not in action.depends_on:
                    raise PromptPolicyError(
                        "report_generation_dependency_missing",
                        "Dërgimi i PDF-së duhet të varet nga gjenerimi.",
                    )

        completed_ids.add(action.id)

    if has_attachment and import_count != 1:
        raise PromptPolicyError(
            "attachment_import_missing",
            "Kërkesa ka attachment, por plani nuk përmban hapin e importimit.",
        )
    if not has_attachment and import_count:
        raise PromptPolicyError(
            "attachment_missing",
            "Kërkesa kërkon importim, por nuk ka dokument ose ZIP të bashkëngjitur.",
        )
