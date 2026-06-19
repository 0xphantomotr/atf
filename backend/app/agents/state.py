from typing import Any

from typing_extensions import TypedDict


class AuditGraphState(TypedDict, total=False):
    project: dict[str, Any]
    job: dict[str, Any]
    user_prompt: str
    documents: list[dict[str, Any]]
    document_inventory: dict[str, Any]
    rules: list[dict[str, Any]]
    law_context: dict[str, Any]
    findings: list[dict[str, Any]]
    completeness_summary: dict[str, Any]
    verified_findings: list[dict[str, Any]]
    ai_review: dict[str, Any]
    report: dict[str, Any]
    needs_human_review: bool
    agent_trace: list[str]
