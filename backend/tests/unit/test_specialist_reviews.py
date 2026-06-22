import json
import uuid

from app.agents.llm import (
    LLMReviewError,
    kolaudim_draft_input_token_budget,
    specialist_review_input_token_budget,
)
from app.agents.nodes.kolaudim_writer import _build_kolaudim_writer_input
from app.agents.nodes.specialist_reviews import (
    _build_specialist_review_input,
    _normalize_memoranda,
    review_specialist_domains,
)
from app.agents.nodes.senior_reviewer import senior_review
from app.core.config import settings


def _register_entry(field: str, value: str) -> dict:
    version_id = str(uuid.uuid4())
    chunk_id = str(uuid.uuid4())
    return {
        "field_name": field,
        "value": value,
        "normalized_value": value,
        "confidence": 0.94,
        "confidence_level": "high",
        "corroborating_source_count": 1,
        "sources": [
            {
                "source_document": "leje.pdf",
                "file_version_id": version_id,
                "chunk_references": [{"chunk_id": chunk_id, "chunk_index": 0}],
            }
        ],
    }


def _state() -> dict:
    return {
        "project": {"name": "Objekti Test", "location": "Tiranë"},
        "job": {"job_type": "kolaudim_act", "law_scope": ["VKM_610_2022"]},
        "professional_dossier": {
            "registers": {
                "stakeholders": [_register_entry("investor", "Investitor Test")],
                "permits_property_licenses": [
                    _register_entry("construction_permit_number", "Leje nr. 42")
                ],
                "project_parameters": [
                    _register_entry("total_construction_area", "1 250 m²")
                ],
                "construction_chronology": [
                    _register_entry("start_date", "15.04.2024")
                ],
                "technical_works": [
                    _register_entry("foundation_completion", "Përfunduar")
                ],
                "materials_and_tests": [
                    _register_entry("concrete_test", "C25/30 në përputhje")
                ],
                "contracts_and_economics": [
                    _register_entry("planned_value", "10.000.000 lekë")
                ],
                "declarations_and_conclusions": [],
                "supporting_evidence": [],
            },
            "conflicts": [],
            "integrity_issues": [],
        },
        "rules": [{"law_reference": "VKM 610/2022, neni 8"}],
        "ai_settings": {
            "provider": "groq",
            "model": "openai/gpt-oss-20b",
            "base_url": "https://example.invalid/v1",
            "api_key": "test",
        },
        "agent_trace": [],
    }


def test_specialist_input_routes_evidence_and_respects_model_budget() -> None:
    state = _state()
    review_input = _build_specialist_review_input(
        state,
        ai_settings=state["ai_settings"],
    )

    legal = next(
        domain
        for domain in review_input["domains"]
        if domain["code"] == "legal_administrative"
    )
    assert "permits_property_licenses:0" in legal["evidence_ids"]
    assert "materials_and_tests:0" not in legal["evidence_ids"]
    assert review_input["budget"]["estimated_input_tokens"] <= (
        specialist_review_input_token_budget(state["ai_settings"])
    )


def test_specialist_normalizer_rejects_cross_domain_and_unknown_citations() -> None:
    state = _state()
    review_input = _build_specialist_review_input(
        state,
        ai_settings=state["ai_settings"],
    )
    response = {
        "status": "reviewed",
        "memoranda": [
            {
                "code": "legal_administrative",
                "established_facts": [
                    {
                        "statement": "Leja e ndërtimit është identifikuar.",
                        "evidence_ids": ["permits_property_licenses:0"],
                    },
                    {
                        "statement": "Pretendim nga prova e betonit.",
                        "evidence_ids": ["materials_and_tests:0"],
                    },
                ],
                "technical_assessments": [
                    {
                        "statement": "Pretendim pa burim.",
                        "evidence_ids": ["invented:99"],
                    },
                    {
                        "statement": "Identifikuesit nuk lejohen si tekst.",
                        "evidence_ids": "permits_property_licenses:0",
                    }
                ],
                "qualifications": [],
                "writer_guidance": [],
                "confidence": 0.9,
            }
        ],
    }

    memoranda = _normalize_memoranda(response, review_input)
    legal = next(memo for memo in memoranda if memo["code"] == "legal_administrative")

    assert len(memoranda) == 6
    assert [item["statement"] for item in legal["established_facts"]] == [
        "Leja e ndërtimit është identifikuar."
    ]
    assert legal["technical_assessments"] == []
    assert legal["established_facts"][0]["source_references"][0][
        "file_version_id"
    ]


def test_specialist_node_normalizes_one_ai_call(monkeypatch) -> None:
    state = _state()
    calls = []

    def fake_request(review_input, *, ai_settings):
        calls.append((review_input, ai_settings))
        return {
            "status": "reviewed",
            "memoranda": [
                {
                    "code": domain["code"],
                    "established_facts": [
                        {
                            "statement": f"Përmbledhje për {domain['code']}.",
                            "evidence_ids": domain["evidence_ids"][:1],
                        }
                    ]
                    if domain["evidence_ids"]
                    else [],
                    "technical_assessments": [],
                    "qualifications": [],
                    "writer_guidance": [],
                    "confidence": 0.85,
                }
                for domain in review_input["domains"]
            ],
        }

    monkeypatch.setattr(settings, "ai_senior_review_enabled", True)
    monkeypatch.setattr(
        "app.agents.nodes.specialist_reviews.request_specialist_review",
        fake_request,
    )

    result = review_specialist_domains(state)

    assert len(calls) == 1
    assert result["specialist_reviews"]["status"] == "reviewed"
    assert result["specialist_reviews"]["summary"]["memorandum_count"] == 6
    assert result["agent_trace"] == ["specialist_reviews"]


def test_specialist_node_prepares_evidence_when_key_is_missing(monkeypatch) -> None:
    state = _state()
    state["ai_settings"] = {}
    monkeypatch.setattr(settings, "ai_senior_review_enabled", True)

    result = review_specialist_domains(state)

    assert result["specialist_reviews"]["status"] == "skipped"
    assert result["specialist_reviews"]["reason"] == "missing_user_ai_settings"
    assert len(result["specialist_reviews"]["memoranda"]) == 6


def test_specialist_node_preserves_evidence_packet_on_provider_failure(monkeypatch) -> None:
    state = _state()

    def fail_request(review_input, *, ai_settings):
        raise LLMReviewError("provider unavailable")

    monkeypatch.setattr(settings, "ai_senior_review_enabled", True)
    monkeypatch.setattr(
        "app.agents.nodes.specialist_reviews.request_specialist_review",
        fail_request,
    )

    result = review_specialist_domains(state)

    assert result["specialist_reviews"]["status"] == "failed"
    assert result["specialist_reviews"]["memoranda"][0]["evidence_count"] > 0
    assert result["needs_human_review"] is True


def test_specialist_node_rejects_empty_model_result(monkeypatch) -> None:
    state = _state()

    monkeypatch.setattr(settings, "ai_senior_review_enabled", True)
    monkeypatch.setattr(
        "app.agents.nodes.specialist_reviews.request_specialist_review",
        lambda review_input, *, ai_settings: {
            "status": "reviewed",
            "memoranda": [],
        },
    )

    result = review_specialist_domains(state)

    assert result["specialist_reviews"]["status"] == "invalid_model_output"
    assert result["specialist_reviews"]["summary"]["reviewed_count"] == 0
    assert result["needs_human_review"] is True


def test_senior_review_is_replaced_after_successful_specialist_review(monkeypatch) -> None:
    monkeypatch.setattr(settings, "ai_senior_review_enabled", True)
    state = senior_review(
        {
            "job": {"job_type": "kolaudim_act"},
            "specialist_reviews": {"status": "reviewed"},
            "agent_trace": [],
        }
    )

    assert state["ai_review"] == {
        "status": "skipped",
        "reason": "replaced_by_specialist_review_stage",
    }
    assert state["agent_trace"] == ["senior_reviewer"]


def test_writer_compacts_large_specialist_memoranda_to_model_budget() -> None:
    state = _state()
    long_statement = "Vlerësim teknik i mbështetur nga evidenca. " * 30
    state["professional_dossier"].update(
        {
            "canonical_facts": {},
            "economic_summary": {},
            "evidence_coverage": {},
            "integrity_issues": [],
            "chronology": [],
            "technical_observations": [],
            "document_records": [],
            "evidence_by_section": {},
            "style_references": [],
            "missing_core_fields": [],
            "summary": {"persisted_analysis_count": 1},
        }
    )
    statement = {
        "statement": long_statement,
        "evidence_ids": ["technical_works:0"],
        "source_references": [
            {
                "source_document": "akti.docx",
                "file_version_id": str(uuid.uuid4()),
                "chunk_ids": [str(uuid.uuid4())],
            }
        ],
    }
    state["specialist_reviews"] = {
        "status": "reviewed",
        "summary": {"memorandum_count": 6},
        "memoranda": [
            {
                "code": f"domain-{index}",
                "title": f"Domain {index}",
                "status": "reviewed",
                "confidence": 0.9,
                "established_facts": [dict(statement) for _ in range(5)],
                "technical_assessments": [dict(statement) for _ in range(5)],
                "qualifications": [dict(statement) for _ in range(3)],
                "writer_guidance": [dict(statement) for _ in range(3)],
            }
            for index in range(6)
        ],
    }
    state["kolaudim_analysis"] = {"sections": []}

    writer_input = _build_kolaudim_writer_input(
        state,
        ai_settings=state["ai_settings"],
    )

    assert writer_input["budget"]["estimated_input_tokens"] <= (
        kolaudim_draft_input_token_budget(state["ai_settings"])
    )
    assert writer_input["specialist_memoranda"]["memoranda"]


def test_specialist_result_is_json_serializable(monkeypatch) -> None:
    state = _state()
    state["ai_settings"] = {}
    monkeypatch.setattr(settings, "ai_senior_review_enabled", True)

    result = review_specialist_domains(state)

    assert json.loads(json.dumps(result["specialist_reviews"]))["status"] == "skipped"
