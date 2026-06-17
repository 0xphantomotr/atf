from typing import Any

from typing_extensions import TypedDict


class AuditGraphState(TypedDict, total=False):
    project: dict[str, Any]
    user_prompt: str
    classified_documents: list[dict[str, Any]]
    relevant_rules: list[dict[str, Any]]
    findings: list[dict[str, Any]]
    report: dict[str, Any]

