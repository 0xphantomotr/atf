from app.agents.state import AuditGraphState


def retrieve_laws(state: AuditGraphState) -> AuditGraphState:
    state.setdefault("agent_trace", []).append("law_retriever")
    rules = state.get("rules", [])
    law_references = sorted(
        {
            rule.get("law_reference")
            for rule in rules
            if isinstance(rule.get("law_reference"), str)
        }
    )
    state["law_context"] = {
        "rule_count": len(rules),
        "law_references": law_references,
    }
    return state
