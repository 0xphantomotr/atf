from app.prompting.schemas import PromptAction, PromptPlan

ACTION_LABELS = {
    "list_projects": "Shfaq projektet",
    "create_project": "Krijo projektin",
    "select_project": "Zgjidh projektin",
    "show_active_project": "Shfaq projektin aktiv",
    "get_status": "Kontrollo statusin",
    "show_drive_folder": "Shfaq folderin Google Drive",
    "bind_drive_folder": "Lidh folderin Google Drive",
    "check_drive_folder": "Kontrollo aksesin dhe ndryshimet në Drive",
    "sync_drive_folder": "Sinkronizo dosjen nga Google Drive",
    "select_ai_model": "Ndrysho modelin AI",
    "import_attachment": "Importo attachment-in",
    "import_drive_folder": "Importo folderin Google Drive",
    "estimate_kolaudim": "Llogarit vlerësimin",
    "generate_kolaudim": "Gjenero Akt Kolaudimi",
    "deliver_latest_report": "Dërgo PDF-në",
    "upload_report_to_drive": "Ruaj PDF-në në Google Drive",
    "answer_project_question": "Kërko përgjigje në dosje",
}


def format_plan_preview(plan: PromptPlan) -> str:
    lines = ["Plani i kuptuar:"]
    for index, action in enumerate(plan.actions, start=1):
        detail = _action_detail(action)
        suffix = f": {detail}" if detail else ""
        confirmation = " (kërkon konfirmim)" if action.requires_confirmation else ""
        lines.append(
            f"{index}. {ACTION_LABELS.get(action.type, action.type)}{suffix}{confirmation}"
        )
    return "\n".join(lines)


def is_quiet_question_plan(plan: PromptPlan) -> bool:
    """Return true when the user only needs the final dossier answer."""
    action_types = [action.type for action in plan.actions]
    return bool(action_types) and action_types[-1] == "answer_project_question" and all(
        action_type in {"select_project", "answer_project_question"}
        for action_type in action_types
    )


def _action_detail(action: PromptAction) -> str | None:
    if action.arguments.name:
        return action.arguments.name
    if action.arguments.model:
        return action.arguments.model
    if action.type == "answer_project_question":
        return "projekti aktiv"
    return None
