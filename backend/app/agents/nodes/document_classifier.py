from app.agents.state import AuditGraphState
from app.files.status import is_parsed_status


def classify_documents(state: AuditGraphState) -> AuditGraphState:
    state.setdefault("agent_trace", []).append("document_inventory")
    documents = state.get("documents", [])
    classified_documents = [
        document
        for document in documents
        if is_parsed_status(document.get("parse_status"))
        and document.get("document_type")
        and document.get("document_type") != "unknown"
    ]
    unknown_documents = [
        document
        for document in documents
        if is_parsed_status(document.get("parse_status"))
        and document.get("document_type") == "unknown"
    ]
    state["document_inventory"] = {
        "total_documents": len(documents),
        "classified_documents": len(classified_documents),
        "unknown_documents": len(unknown_documents),
    }
    return state
