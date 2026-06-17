UNKNOWN_DOCUMENT_TYPE = "unknown"


def classify_document_type(filename: str, text: str | None) -> tuple[str, float]:
    haystack = f"{filename}\n{text or ''}".lower()
    if "45" in haystack and "raport" in haystack:
        return "forty_five_day_report", 0.75
    if "polic" in haystack and "sigur" in haystack:
        return "professional_liability_insurance_policy", 0.75
    if "libri" in haystack and "kantier" in haystack:
        return "site_book", 0.7
    if "ditari" in haystack and "objekt" in haystack:
        return "daily_site_log", 0.7
    return UNKNOWN_DOCUMENT_TYPE, 0.0

