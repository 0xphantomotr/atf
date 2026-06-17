from app.agents.state import AuditGraphState


def verify_evidence(state: AuditGraphState) -> AuditGraphState:
    findings = state.get("findings", [])
    state["findings"] = [finding for finding in findings if finding.get("evidence")]
    return state

