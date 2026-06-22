PARSED_STATUSES = frozenset({"parsed", "parsed_with_ocr"})


def is_parsed_status(value: object) -> bool:
    return isinstance(value, str) and value in PARSED_STATUSES
