from hmac import compare_digest


def constant_time_equals(left: str | None, right: str | None) -> bool:
    if left is None or right is None:
        return False
    return compare_digest(left, right)

