import re
from urllib.parse import parse_qs, urlparse


class GoogleDriveLinkError(ValueError):
    pass


FOLDER_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{10,200}$")
FOLDER_PATH_PATTERN = re.compile(r"/(?:drive/(?:u/\d+/)?folders|folders)/([A-Za-z0-9_-]+)")


def extract_google_drive_folder_id(value: str) -> str:
    raw = value.strip()
    if FOLDER_ID_PATTERN.fullmatch(raw):
        return raw
    parsed = urlparse(raw)
    if parsed.scheme != "https" or parsed.hostname not in {
        "drive.google.com",
        "www.drive.google.com",
    }:
        raise GoogleDriveLinkError("Përdorni një link HTTPS të folderit në Google Drive.")
    match = FOLDER_PATH_PATTERN.search(parsed.path)
    candidate = match.group(1) if match else ""
    if not candidate:
        candidate = (parse_qs(parsed.query).get("id") or [""])[0]
    if not FOLDER_ID_PATTERN.fullmatch(candidate):
        raise GoogleDriveLinkError(
            "Linku nuk përmban një ID të vlefshme folderi Google Drive."
        )
    return candidate


def extract_google_drive_folder_url(text: str) -> str | None:
    for match in re.finditer(r"https://[^\s<>]+", text):
        candidate = match.group(0).rstrip(".,;:!?)]}\"'")
        try:
            extract_google_drive_folder_id(candidate)
        except GoogleDriveLinkError:
            continue
        return candidate
    return None
