from typing import Any

from typing_extensions import TypedDict


class AuditGraphState(TypedDict, total=False):
    project: dict[str, Any]
    job: dict[str, Any]
    user_prompt: str
    documents: list[dict[str, Any]]
    document_inventory: dict[str, Any]
    extracted_facts: dict[str, Any]
    rules: list[dict[str, Any]]
    law_context: dict[str, Any]
    vkm_obligation_map: dict[str, Any]
    findings: list[dict[str, Any]]
    completeness_summary: dict[str, Any]
    verified_findings: list[dict[str, Any]]
    consistency_review: dict[str, Any]
    kolaudim_analysis: dict[str, Any]
    ai_settings: dict[str, Any]
    ai_review: dict[str, Any]
    require_ai_review: bool
    report: dict[str, Any]
    needs_human_review: bool
    agent_trace: list[str]
