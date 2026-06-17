from pathlib import Path


SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".xlsx", ".zip", ".jpg", ".jpeg", ".png"}


def is_supported_filename(filename: str) -> bool:
    return Path(filename).suffix.lower() in SUPPORTED_EXTENSIONS

