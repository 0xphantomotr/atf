import uuid

import pytest

from app.agents.claim_grounding import build_claim_evidence_catalog
from app.agents.nodes.claim_verifier import verify_kolaudim_claims
from app.agents.nodes.kolaudim_corrector import (
    ClaimVerificationError,
    correct_kolaudim_draft,
    enforce_publishable_kolaudim,
    route_after_claim_verification,
)
from app.agents.nodes.kolaudim_writer import _normalize_kolaudim_draft


def _source(version_id: str, document: str) -> dict:
    return {
        "source_document": document,
        "document_type": "technical_declaration",
        "file_version_id": version_id,
        "chunk_references": [{"chunk_id": str(uuid.uuid4())}],
    }


def _grounded_state() -> dict:
    version_id = str(uuid.uuid4())
    return {
        "job": {"job_type": "kolaudim_act"},
        "documents": [{"version_id": version_id, "original_filename": "dosja.docx"}],
        "professional_dossier": {
            "canonical_facts": {
                "object_name": {
                    "value": "Godinë banimi",
                    "confidence_level": "high",
                }
            },
            "registers": {
                "project_parameters": [
                    {
                        "field_name": "object_name",
                        "value": "Godinë banimi",
                        "sources": [_source(version_id, "dosja.docx")],
                    }
                ],
                "technical_works": [
                    {
                        "field_name": "executed_works",
                        "value": "Punimet konstruktive janë dokumentuar.",
                        "sources": [_source(version_id, "dosja.docx")],
                    }
                ],
                "materials_and_tests": [
                    {
                        "field_name": "material_tests",
                        "value": "Provat e materialeve janë dokumentuar.",
                        "sources": [_source(version_id, "dosja.docx")],
                    }
                ],
                "declarations_and_conclusions": [
                    {
                        "field_name": "completion_declaration",
                        "value": "Deklarata e përfundimit është administruar.",
                        "sources": [_source(version_id, "dosja.docx")],
                    }
                ],
            },
            "conflicts": [],
            "integrity_issues": [],
        },
        "rules": [
            {
                "rule_code": "VKM610-001",
                "law_reference": "VKM nr. 610, datë 22.09.2022",
                "law_document_code": "VKM_610_2022",
            }
        ],
        "agent_trace": [],
    }


def _draft_with_paragraphs() -> dict:
    return {
        "status": "drafted",
        "title": "AKT-KOLAUDIMI TEKNIKO-EKONOMIK",
        "executive_summary": {
            "text": "Akti lidhet me objektin Godinë banimi.",
            "claim_type": "documented_fact",
            "evidence_ids": ["canonical:object_name"],
            "confidence": 0.9,
        },
        "sections": [
            {
                "code": f"section_{index}",
                "title": f"Seksioni {index}",
                "paragraphs": [
                    {
                        "text": "Dosja përmban evidencë teknike për punimet.",
                        "claim_type": "documented_fact",
                        "evidence_ids": ["technical_works:0"],
                        "confidence": 0.85,
                    }
                ],
            }
            for index in range(10)
        ],
        "reservations": [],
        "human_completion_items": [],
        "signature_note": "Për kontroll dhe nënshkrim profesional.",
        "confidence": 0.8,
    }


def test_normalizer_builds_internal_claim_ledger_and_clean_body() -> None:
    state = _grounded_state()
    catalog = build_claim_evidence_catalog(state)

    normalized = _normalize_kolaudim_draft(
        _draft_with_paragraphs(),
        evidence_catalog=catalog,
    )

    assert normalized["executive_summary"].startswith("Akti lidhet")
    assert normalized["sections"][0]["body"].startswith("Dosja përmban")
    assert "paragraphs" not in normalized["sections"][0]
    assert normalized["claim_ledger"][0]["evidence_ids"] == [
        "canonical:object_name"
    ]
    assert normalized["claim_ledger"][0]["source_references"][0][
        "file_version_id"
    ] == state["documents"][0]["version_id"]


def test_verifier_accepts_current_project_grounded_paragraphs() -> None:
    state = _grounded_state()
    state["kolaudim_draft"] = _normalize_kolaudim_draft(
        _draft_with_paragraphs(),
        evidence_catalog=build_claim_evidence_catalog(state),
    )

    result = verify_kolaudim_claims(state)["claim_verification"]

    assert result["status"] == "verified"
    assert result["summary"]["publishable"] is True
    assert result["summary"]["claim_count"] == 11


def test_verifier_rejects_sensitive_conclusion_without_full_evidence() -> None:
    state = _grounded_state()
    response = _draft_with_paragraphs()
    response["sections"][0]["paragraphs"][0] = {
        "text": "Objekti është i përshtatshëm për shfrytëzim.",
        "claim_type": "professional_inference",
        "evidence_ids": ["declarations_and_conclusions:0"],
        "confidence": 0.6,
    }
    state["kolaudim_draft"] = _normalize_kolaudim_draft(
        response,
        evidence_catalog=build_claim_evidence_catalog(state),
    )

    result = verify_kolaudim_claims(state)["claim_verification"]

    assert result["status"] == "needs_correction"
    assert "CLAIM-SUITABILITY-EVIDENCE" in {
        issue["code"] for issue in result["issues"]
    }


def test_verifier_rejects_cross_snapshot_evidence() -> None:
    state = _grounded_state()
    response = _draft_with_paragraphs()
    state["documents"][0]["version_id"] = str(uuid.uuid4())
    state["kolaudim_draft"] = _normalize_kolaudim_draft(
        response,
        evidence_catalog=build_claim_evidence_catalog(state),
    )

    result = verify_kolaudim_claims(state)["claim_verification"]

    assert "CLAIM-EVIDENCE-NOT-CURRENT" in {
        issue["code"] for issue in result["issues"]
    }


def test_corrector_runs_once_and_replaces_draft(monkeypatch) -> None:
    state = _grounded_state()
    state["ai_settings"] = {
        "provider": "gemini",
        "model": "gemini-2.5-flash",
        "api_key": "test",
    }
    state["kolaudim_draft"] = _normalize_kolaudim_draft(
        _draft_with_paragraphs(),
        evidence_catalog=build_claim_evidence_catalog(state),
    )
    state["claim_verification"] = {
        "status": "needs_correction",
        "correction_instructions": [
            {
                "code": "CLAIM-CONFORMITY-EVIDENCE",
                "claim_id": "section_0:0",
                "instruction": "Kualifiko pretendimin.",
            }
        ],
    }
    corrected = _draft_with_paragraphs()
    corrected["executive_summary"]["text"] = "Përmbledhje e korrigjuar për Godinë banimi."
    monkeypatch.setattr(
        "app.agents.nodes.kolaudim_corrector.request_kolaudim_correction",
        lambda correction_input, *, ai_settings: corrected,
    )

    assert route_after_claim_verification(state) == "correct"
    result = correct_kolaudim_draft(state)

    assert result["kolaudim_correction"]["status"] == "corrected"
    assert result["kolaudim_correction"]["attempt_count"] == 1
    assert result["kolaudim_draft"]["executive_summary"].startswith("Përmbledhje")
    assert route_after_claim_verification(result) == "finalize"


def test_publication_gate_rejects_unverified_revision() -> None:
    state = _grounded_state()
    state["kolaudim_draft"] = {"status": "drafted"}
    state["claim_verification"] = {
        "status": "needs_correction",
        "issues": [
            {
                "code": "CLAIM-EVIDENCE-MISSING",
                "severity": "major",
                "message": "Mungon evidenca.",
            }
        ],
        "summary": {"publishable": False},
    }

    with pytest.raises(ClaimVerificationError, match="Nuk u prodhua"):
        enforce_publishable_kolaudim(state)
