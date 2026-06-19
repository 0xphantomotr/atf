from app.agents.state import AuditGraphState


def write_report(state: AuditGraphState) -> AuditGraphState:
    state.setdefault("agent_trace", []).append("report_writer")
    inventory = state.get("document_inventory", {})
    findings = state.get("verified_findings", state.get("findings", []))
    needs_human_review = bool(state.get("needs_human_review", False))

    total_documents = int(inventory.get("total_documents", 0) or 0)
    classified_documents = int(inventory.get("classified_documents", 0) or 0)
    finding_count = len(findings)

    if finding_count:
        summary = (
            f"Workflow-i LangGraph kontrolloi {total_documents} dokumente dhe "
            f"verifikoi {finding_count} gjetje me evidencë të strukturuar."
        )
    else:
        summary = (
            f"Workflow-i LangGraph kontrolloi {total_documents} dokumente dhe nuk "
            "gjeti mungesa nga rregullat e aplikuara."
        )

    if needs_human_review:
        recommendation = "Kërkohet verifikim njerëzor"
    elif finding_count:
        recommendation = "Kërkohet plotësim dokumentacioni"
    else:
        recommendation = "Pa gjetje të rëndësishme"

    state["report"] = {
        "phase": "langgraph_phase_1",
        "summary": summary,
        "recommendation": recommendation,
        "total_documents": total_documents,
        "classified_documents": classified_documents,
        "finding_count": finding_count,
        "verified_finding_count": len(state.get("verified_findings", [])),
        "needs_human_review": needs_human_review,
        "trace": list(state.get("agent_trace", [])),
    }
    return state
