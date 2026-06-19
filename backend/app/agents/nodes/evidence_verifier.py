from app.agents.state import AuditGraphState


def verify_evidence(state: AuditGraphState) -> AuditGraphState:
    state.setdefault("agent_trace", []).append("evidence_verifier")
    findings = state.get("findings", [])
    verified_findings: list[dict] = []
    needs_human_review = bool(state.get("needs_human_review", False))

    for finding in findings:
        evidence = finding.get("evidence")
        if not isinstance(evidence, dict) or not evidence:
            needs_human_review = True
            continue

        missing_document_types = evidence.get("missing_document_types")
        if not isinstance(missing_document_types, list):
            needs_human_review = True

        verified_finding = dict(finding)
        verified_finding["evidence_verified"] = True
        verified_findings.append(verified_finding)

    if state.get("document_inventory", {}).get("unknown_documents", 0):
        needs_human_review = True

    state["verified_findings"] = verified_findings
    state["findings"] = verified_findings
    state["needs_human_review"] = needs_human_review
    return state
