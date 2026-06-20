from functools import lru_cache
from typing import Any

from app.agents.nodes.completeness_auditor import audit_completeness
from app.agents.nodes.consistency_checker import check_professional_consistency
from app.agents.nodes.document_classifier import classify_documents
from app.agents.nodes.evidence_verifier import verify_evidence
from app.agents.nodes.fact_extractor import extract_project_facts
from app.agents.nodes.kolaudim_planner import plan_kolaudim_act
from app.agents.nodes.kolaudim_writer import write_kolaudim_draft
from app.agents.nodes.law_retriever import retrieve_laws
from app.agents.nodes.project_context import load_project_context
from app.agents.nodes.report_writer import write_report
from app.agents.nodes.senior_reviewer import senior_review
from app.agents.nodes.vkm_obligation_mapper import map_vkm_obligations
from app.agents.state import AuditGraphState


@lru_cache(maxsize=1)
def build_audit_graph() -> Any:
    try:
        from langgraph.graph import END, StateGraph
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "LangGraph is required for audit workflow execution. "
            "Install backend dependencies or run the API container."
        ) from exc

    workflow = StateGraph(AuditGraphState)
    workflow.add_node("project_context", load_project_context)
    workflow.add_node("document_inventory", classify_documents)
    workflow.add_node("fact_extractor", extract_project_facts)
    workflow.add_node("law_retriever", retrieve_laws)
    workflow.add_node("vkm_obligation_mapper", map_vkm_obligations)
    workflow.add_node("deterministic_completeness", audit_completeness)
    workflow.add_node("evidence_verifier", verify_evidence)
    workflow.add_node("consistency_checker", check_professional_consistency)
    workflow.add_node("kolaudim_planner", plan_kolaudim_act)
    workflow.add_node("senior_reviewer", senior_review)
    workflow.add_node("kolaudim_writer", write_kolaudim_draft)
    workflow.add_node("report_writer", write_report)

    workflow.set_entry_point("project_context")
    workflow.add_edge("project_context", "document_inventory")
    workflow.add_edge("document_inventory", "fact_extractor")
    workflow.add_edge("fact_extractor", "law_retriever")
    workflow.add_edge("law_retriever", "vkm_obligation_mapper")
    workflow.add_edge("vkm_obligation_mapper", "deterministic_completeness")
    workflow.add_edge("deterministic_completeness", "evidence_verifier")
    workflow.add_edge("evidence_verifier", "consistency_checker")
    workflow.add_edge("consistency_checker", "kolaudim_planner")
    workflow.add_edge("kolaudim_planner", "senior_reviewer")
    workflow.add_edge("senior_reviewer", "kolaudim_writer")
    workflow.add_edge("kolaudim_writer", "report_writer")
    workflow.add_edge("report_writer", END)

    return workflow.compile()


def run_audit_graph(initial_state: AuditGraphState) -> AuditGraphState:
    graph = build_audit_graph()
    return graph.invoke(dict(initial_state))
