import uuid
from types import SimpleNamespace

from app.agents.nodes.completeness_auditor import audit_completeness
from app.agents.nodes.document_classifier import classify_documents
from app.agents.nodes.evidence_verifier import verify_evidence
from app.agents.nodes.law_retriever import retrieve_laws
from app.agents.nodes.project_context import load_project_context
from app.agents.nodes.report_writer import write_report
from app.reviews.service import (
    CurrentFileSnapshot,
    RuleContext,
    _agent_metadata,
    _build_agent_state,
)


def test_phase_one_nodes_build_trace_and_report() -> None:
    state = {
        "project": {
            "id": str(uuid.uuid4()),
            "name": "Godine banimi",
            "project_type": "residential",
            "stage": "during_construction",
            "location": "Tirane",
        },
        "documents": [
            {
                "parse_status": "parsed",
                "document_type": "start_works_notification",
            },
            {
                "parse_status": "parsed",
                "document_type": "unknown",
            },
        ],
        "rules": [
            {
                "rule_code": "VKM610-008-001",
                "law_reference": "VKM 610/2022, Neni 8",
            }
        ],
        "findings": [
            {
                "rule_code": "VKM610-008-001",
                "evidence": {"missing_document_types": ["forty_five_day_report"]},
            }
        ],
        "agent_trace": [],
    }

    for node in (
        load_project_context,
        classify_documents,
        retrieve_laws,
        audit_completeness,
        verify_evidence,
        write_report,
    ):
        state = node(state)

    assert state["agent_trace"] == [
        "project_context",
        "document_inventory",
        "law_retriever",
        "deterministic_completeness",
        "evidence_verifier",
        "report_writer",
    ]
    assert state["document_inventory"]["total_documents"] == 2
    assert state["document_inventory"]["classified_documents"] == 1
    assert state["document_inventory"]["unknown_documents"] == 1
    assert state["law_context"]["rule_count"] == 1
    assert state["findings"][0]["evidence_verified"]
    assert state["needs_human_review"]
    assert state["report"]["phase"] == "langgraph_phase_1"
    assert state["report"]["finding_count"] == 1


def test_evidence_verifier_drops_findings_without_evidence() -> None:
    state = {
        "findings": [
            {"rule_code": "OK", "evidence": {"missing_document_types": ["site_book"]}},
            {"rule_code": "BAD", "evidence": {}},
        ],
        "document_inventory": {"unknown_documents": 0},
        "agent_trace": [],
    }

    verified = verify_evidence(state)

    assert [finding["rule_code"] for finding in verified["findings"]] == ["OK"]
    assert verified["findings"][0]["evidence_verified"]
    assert verified["needs_human_review"]


def test_build_agent_state_serializes_review_inputs() -> None:
    job = SimpleNamespace(
        id=uuid.uuid4(),
        job_type="documentation_checklist",
        language="sq-AL",
        output_format="pdf",
        law_scope={"codes": ["VKM_610_2022"]},
        user_prompt=None,
    )
    project = SimpleNamespace(
        id=uuid.uuid4(),
        name="Dosja Teknike",
        project_type="residential",
        stage="during_construction",
        location="Tirane",
    )
    current_file = CurrentFileSnapshot(
        file_id=uuid.uuid4(),
        version_id=uuid.uuid4(),
        original_filename="1.1 Njoftim Fillim Punimesh OK.docx",
        parse_status="parsed",
        document_type="start_works_notification",
        classification_confidence=0.98,
    )
    rule = SimpleNamespace(
        rule_code="VKM610-008-001",
        title="Raportimi 45-ditor",
        severity_if_missing="major",
        required_documents={"document_types": ["forty_five_day_report"]},
        applies_to={"project_stage": ["during_construction"]},
    )
    rule_context = RuleContext(
        rule=rule,
        law_document=SimpleNamespace(code="VKM_610_2022"),
        law_article=SimpleNamespace(article_number="8"),
    )
    finding = SimpleNamespace(
        severity="major",
        title="Mungon dokument",
        description="Mungon dokumenti.",
        law_reference="VKM 610/2022, Neni 8",
        rule_code="VKM610-008-001",
        evidence={"missing_document_types": ["forty_five_day_report"]},
        required_action="Ngarkoni raportimin 45-ditor.",
        confidence=0.95,
        status="open",
    )

    state = _build_agent_state(
        project=project,
        job=job,
        current_files=[current_file],
        rule_contexts=[rule_context],
        findings=[finding],
    )

    assert state["project"]["id"] == str(project.id)
    assert state["documents"][0]["version_id"] == str(current_file.version_id)
    assert state["rules"][0]["law_reference"] == "VKM 610/2022, Neni 8"
    assert state["findings"][0]["evidence"]["missing_document_types"] == [
        "forty_five_day_report"
    ]


def test_agent_metadata_is_report_safe() -> None:
    metadata = _agent_metadata(
        {
            "agent_trace": ["project_context", "report_writer"],
            "needs_human_review": True,
            "document_inventory": {"total_documents": 2},
            "law_context": {"rule_count": 1},
            "completeness_summary": {"finding_count": 1},
            "report": {"phase": "langgraph_phase_1"},
        }
    )

    assert metadata["phase"] == "langgraph_phase_1"
    assert metadata["trace"] == ["project_context", "report_writer"]
    assert metadata["needs_human_review"] is True
