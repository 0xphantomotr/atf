import re
import unicodedata

ACTION_ORDER = (
    "list_projects",
    "create_project",
    "select_project",
    "show_active_project",
    "get_status",
    "select_ai_model",
    "import_attachment",
    "import_drive_folder",
    "estimate_kolaudim",
    "generate_kolaudim",
    "upload_report_to_drive",
    "deliver_latest_report",
    "answer_project_question",
)


def detect_intent_hints(prompt: str, *, has_attachment: bool = False) -> list[str]:
    text = _normalize(prompt)
    hints: set[str] = set()

    if _contains_any(text, "projektet e mia", "shfaq projektet", "listo projektet"):
        hints.add("list_projects")
    if re.search(r"\b(krijo|hap|shto)\b.{0,30}\bprojekt", text):
        hints.add("create_project")
    if re.search(r"\b(zgjidh|perdor|aktivizo)\b.{0,35}\bprojekt", text):
        hints.add("select_project")
    if "projekti aktiv" in text or "cili projekt eshte aktiv" in text:
        hints.add("show_active_project")
    if _contains_any(text, "status", "progres", "ku ka arritur"):
        hints.add("get_status")
    if re.search(r"\b(ndrysho|zgjidh|perdor|vendos)\b.{0,30}\bmodel", text):
        hints.add("select_ai_model")

    import_requested = _contains_any(
        text,
        "importo",
        "ngarko",
        "perpuno dosjen",
        "lexo dosjen",
        "hap zip",
    )
    if has_attachment and import_requested:
        hints.add("import_attachment")
    has_drive_folder = "drive.google.com" in prompt.casefold()
    if has_drive_folder and import_requested:
        hints.add("import_drive_folder")

    generation_requested = bool(
        re.search(
            r"\b(gjenero|harto|pergatit|krijo|nis|dua)\b.{0,45}"
            r"\b(kolaudim|aktin? e kolaudimit|akt kolaudimi)",
            text,
        )
    )
    if generation_requested:
        hints.update({"estimate_kolaudim", "generate_kolaudim"})
    if _contains_any(text, "ma dergo pdf", "dergo pdf", "raportin e fundit", "raportet"):
        hints.add("deliver_latest_report")
    if has_drive_folder and _contains_any(
        text,
        "ruaje aty",
        "ngarkoje aty",
        "ruaje ne folder",
        "ngarkoje ne folder",
        "google drive",
    ):
        hints.add("upload_report_to_drive")

    management_hints = hints & {
        "list_projects",
        "create_project",
        "select_project",
        "show_active_project",
        "get_status",
        "select_ai_model",
        "import_attachment",
        "import_drive_folder",
        "generate_kolaudim",
        "deliver_latest_report",
        "upload_report_to_drive",
    }
    if not management_hints and _looks_like_dossier_question(text):
        hints.add("answer_project_question")

    return [action for action in ACTION_ORDER if action in hints]


def _looks_like_dossier_question(text: str) -> bool:
    question_start = re.match(
        r"^(kush|cili|cila|cilat|cfare|sa|kur|ku|a ka|a eshte|me trego|shpjego)",
        text,
    )
    dossier_terms = _contains_any(
        text,
        "dosje",
        "dokument",
        "sipermarres",
        "investitor",
        "mbikqyres",
        "kolaudator",
        "leje",
        "kontrate",
        "punime",
        "ndertim",
        "vkm",
        "ligj",
        "vlere",
        "date",
    )
    return bool(question_start and dossier_terms)


def _contains_any(text: str, *values: str) -> bool:
    return any(value in text for value in values)


def _normalize(value: str) -> str:
    text = unicodedata.normalize("NFKD", value)
    text = "".join(character for character in text if not unicodedata.combining(character))
    return re.sub(r"[^a-z0-9]+", " ", text.casefold()).strip()
