from app.agents.state import AuditGraphState


def load_project_context(state: AuditGraphState) -> AuditGraphState:
    state.setdefault("agent_trace", []).append("project_context")
    project = state.get("project", {})
    state["project"] = {
        "id": project.get("id"),
        "name": project.get("name"),
        "project_type": project.get("project_type"),
        "stage": project.get("stage"),
        "location": project.get("location"),
    }
    return state
