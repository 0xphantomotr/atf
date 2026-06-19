from app.agents.state import AuditGraphState


def audit_completeness(state: AuditGraphState) -> AuditGraphState:
    state.setdefault("agent_trace", []).append("deterministic_completeness")
    findings = state.get("findings", [])
    state["completeness_summary"] = {
        "finding_count": len(findings),
        "rule_codes": [
            finding.get("rule_code")
            for finding in findings
            if isinstance(finding.get("rule_code"), str)
        ],
    }
    return state
