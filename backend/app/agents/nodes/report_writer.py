from app.agents.state import AuditGraphState


def write_report(state: AuditGraphState) -> AuditGraphState:
    state.setdefault("agent_trace", []).append("report_writer")
    inventory = state.get("document_inventory", {})
    findings = state.get("verified_findings", state.get("findings", []))
    ai_review = state.get("ai_review", {})
    kolaudim_analysis = state.get("kolaudim_analysis", {})
    specialist_reviews = state.get("specialist_reviews", {})
    kolaudim_draft = state.get("kolaudim_draft", {})
    kolaudim_correction = state.get("kolaudim_correction", {})
    claim_verification = state.get("claim_verification", {})
    needs_human_review = bool(state.get("needs_human_review", False))
    is_kolaudim = state.get("job", {}).get("job_type") == "kolaudim_act"

    total_documents = int(inventory.get("total_documents", 0) or 0)
    classified_documents = int(inventory.get("classified_documents", 0) or 0)
    finding_count = len(findings)

    if (
        is_kolaudim
        and isinstance(kolaudim_draft, dict)
        and kolaudim_draft.get("status") == "drafted"
    ):
        summary = str(kolaudim_draft.get("executive_summary") or "").strip()
        recommendation = "Projekt-akt i gatshëm për kontroll dhe nënshkrim profesional"
    elif is_kolaudim:
        summary = "Akt-Kolaudimi nuk u gjenerua."
        recommendation = "Kërkohet rigjenerim"
    elif finding_count:
        summary = (
            f"Workflow-i LangGraph kontrolloi {total_documents} dokumente dhe "
            f"verifikoi {finding_count} gjetje me evidencë të strukturuar."
        )
    else:
        summary = (
            f"Workflow-i LangGraph kontrolloi {total_documents} dokumente dhe nuk "
            "gjeti mungesa nga rregullat e aplikuara."
        )

    if is_kolaudim:
        pass
    elif needs_human_review:
        recommendation = "Kërkohet verifikim njerëzor"
    elif finding_count:
        recommendation = "Kërkohet plotësim dokumentacioni"
    else:
        recommendation = "Pa gjetje të rëndësishme"

    state["report"] = {
        "phase": "professional_kolaudim_dossier",
        "summary": summary,
        "recommendation": recommendation,
        "total_documents": total_documents,
        "classified_documents": classified_documents,
        "finding_count": finding_count,
        "verified_finding_count": len(state.get("verified_findings", [])),
        "ai_review_status": ai_review.get("status", "not_run"),
        "specialist_review_status": (
            specialist_reviews.get("status", "not_run")
            if isinstance(specialist_reviews, dict)
            else "not_run"
        ),
        "kolaudim_draft_status": (
            kolaudim_draft.get("status")
            if isinstance(kolaudim_draft, dict)
            else "not_run"
        ),
        "kolaudim_correction_status": (
            kolaudim_correction.get("status", "not_run")
            if isinstance(kolaudim_correction, dict)
            else "not_run"
        ),
        "claim_verification_status": (
            claim_verification.get("status", "not_run")
            if isinstance(claim_verification, dict)
            else "not_run"
        ),
        "professional_readiness": (
            kolaudim_analysis.get("readiness")
            if isinstance(kolaudim_analysis, dict)
            else None
        ),
        "needs_human_review": needs_human_review,
        "trace": list(state.get("agent_trace", [])),
    }
    return state
