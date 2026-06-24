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


def test_verifier_rejects_conformity_without_declaration_and_technical_evidence() -> None:
    state = _grounded_state()
    response = _draft_with_paragraphs()
    response["sections"][0]["paragraphs"][0] = {
        "text": "Punimet janë kryer në përputhje me projektin e zbatimit.",
        "claim_type": "professional_inference",
        "evidence_ids": ["technical_works:0"],
        "confidence": 0.7,
    }
    state["kolaudim_draft"] = _normalize_kolaudim_draft(
        response,
        evidence_catalog=build_claim_evidence_catalog(state),
    )

    result = verify_kolaudim_claims(state)["claim_verification"]

    assert result["status"] == "needs_correction"
    assert "CLAIM-CONFORMITY-EVIDENCE" in {
        issue["code"] for issue in result["issues"]
    }


def test_verifier_accepts_conformity_with_declaration_and_technical_evidence() -> None:
    state = _grounded_state()
    response = _draft_with_paragraphs()
    response["sections"][0]["paragraphs"][0] = {
        "text": (
            "Dokumentacioni teknik dhe deklarata përkatëse mbështesin "
            "përputhshmërinë e dokumentuar të punimeve me projektin."
        ),
        "claim_type": "professional_inference",
        "evidence_ids": ["technical_works:0", "declarations_and_conclusions:0"],
        "confidence": 0.75,
    }
    state["kolaudim_draft"] = _normalize_kolaudim_draft(
        response,
        evidence_catalog=build_claim_evidence_catalog(state),
    )

    result = verify_kolaudim_claims(state)["claim_verification"]

    assert "CLAIM-CONFORMITY-EVIDENCE" not in {
        issue["code"] for issue in result["issues"]
    }


def test_verifier_rejects_unsigned_authorization_language() -> None:
    state = _grounded_state()
    response = _draft_with_paragraphs()
    response["sections"][0]["paragraphs"][0] = {
        "text": "Struktura është e pranuar dhe autorizohet për përdorim.",
        "claim_type": "professional_inference",
        "evidence_ids": [
            "technical_works:0",
            "materials_and_tests:0",
            "declarations_and_conclusions:0",
        ],
        "confidence": 0.7,
    }
    state["kolaudim_draft"] = _normalize_kolaudim_draft(
        response,
        evidence_catalog=build_claim_evidence_catalog(state),
    )

    result = verify_kolaudim_claims(state)["claim_verification"]

    assert result["status"] == "needs_correction"
    assert "CLAIM-UNSIGNED-AUTHORIZATION" in {
        issue["code"] for issue in result["issues"]
    }


def test_verifier_rejects_final_acceptance_language() -> None:
    state = _grounded_state()
    response = _draft_with_paragraphs()
    response["sections"][0]["paragraphs"][0] = {
        "text": "Punimet janë pranuar dhe struktura është funksionale.",
        "claim_type": "professional_inference",
        "evidence_ids": [
            "technical_works:0",
            "materials_and_tests:0",
            "declarations_and_conclusions:0",
        ],
        "confidence": 0.7,
    }
    state["kolaudim_draft"] = _normalize_kolaudim_draft(
        response,
        evidence_catalog=build_claim_evidence_catalog(state),
    )

    result = verify_kolaudim_claims(state)["claim_verification"]

    assert result["status"] == "needs_correction"
    assert "CLAIM-UNSIGNED-AUTHORIZATION" in {
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
    captured_inputs = []

    def fake_correction_request(correction_input, *, ai_settings):
        captured_inputs.append(correction_input)
        return corrected

    monkeypatch.setattr(
        "app.agents.nodes.kolaudim_corrector.request_kolaudim_correction",
        fake_correction_request,
    )

    assert route_after_claim_verification(state) == "correct"
    result = correct_kolaudim_draft(state)

    assert result["kolaudim_correction"]["status"] == "corrected"
    assert result["kolaudim_correction"]["attempt_count"] == 1
    assert result["kolaudim_draft"]["executive_summary"].startswith("Përmbledhje")
    assert route_after_claim_verification(result) == "finalize"
    assert "declarations_and_conclusions:0" in captured_inputs[0][
        "supplemental_evidence_ids"
    ]
    assert "declarations_and_conclusions:0" in captured_inputs[0][
        "allowed_evidence_ids"
    ]


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


def test_conflict_verifier_explains_selected_and_used_sources() -> None:
    state = _grounded_state()
    state["professional_dossier"]["canonical_facts"]["contractor"] = {
        "value": "EB-2000 sh.p.k.",
        "source_documents": ["Deklaratë sipërmarrësi.docx"],
    }
    state["professional_dossier"]["conflicts"] = [
        {
            "field": "contractor",
            "selected_value": "EB-2000 sh.p.k.",
            "selected_score": 1.8,
            "alternatives": [
                {
                    "value": "KËRPI sh.p.k.",
                    "score": 0.9,
                    "source_documents": ["Kontratë mbikëqyrësi.docx"],
                }
            ],
        }
    ]
    response = _draft_with_paragraphs()
    response["executive_summary"]["text"] = (
        "Akti lidhet me objektin Godinë banimi dhe sipërmarrës KËRPI sh.p.k."
    )
    state["kolaudim_draft"] = _normalize_kolaudim_draft(
        response,
        evidence_catalog=build_claim_evidence_catalog(state),
    )

    verification = verify_kolaudim_claims(state)["claim_verification"]

    conflict_issue = next(
        issue
        for issue in verification["issues"]
        if issue["code"] == "PUBLIC-CONFLICT-ALTERNATIVE-USED"
    )
    assert conflict_issue["severity"] == "major"
    assert conflict_issue["field"] == "contractor"
    assert conflict_issue["selected_value"] == "EB-2000 sh.p.k."
    assert conflict_issue["alternative_value"] == "KËRPI sh.p.k."
    assert conflict_issue["selected_source_documents"] == ["Deklaratë sipërmarrësi.docx"]
    assert conflict_issue["alternative_source_documents"] == [
        "Kontratë mbikëqyrësi.docx"
    ]
    instruction = next(
        item
        for item in verification["correction_instructions"]
        if item["code"] == "PUBLIC-CONFLICT-ALTERNATIVE-USED"
    )
    assert instruction["selected_value"] == "EB-2000 sh.p.k."
    assert instruction["alternative_value"] == "KËRPI sh.p.k."


def test_object_name_alternative_is_diagnostic_not_publication_blocker() -> None:
    state = _grounded_state()
    state["professional_dossier"]["canonical_facts"]["object_name"].update(
        {
            "value": "Shtesë 1 kat në banesën ekzistuese 1 kat",
            "source_documents": ["Njoftim Fillim Punimesh.docx"],
        }
    )
    state["professional_dossier"]["conflicts"] = [
        {
            "field": "object_name",
            "selected_value": "Shtesë 1 kat në banesën ekzistuese 1 kat",
            "selected_score": 1.8,
            "alternatives": [
                {
                    "value": "Ndërtesë banimi 1 kat me podrum",
                    "score": 0.9,
                    "source_documents": ["Njoftim Përfundim Punimesh.docx"],
                }
            ],
        }
    ]
    response = _draft_with_paragraphs()
    response["executive_summary"]["text"] = (
        "Akti lidhet me objektin Ndërtesë banimi 1 kat me podrum."
    )
    state["kolaudim_draft"] = _normalize_kolaudim_draft(
        response,
        evidence_catalog=build_claim_evidence_catalog(state),
    )

    verification = verify_kolaudim_claims(state)["claim_verification"]

    conflict_issue = next(
        issue
        for issue in verification["issues"]
        if issue["code"] == "PUBLIC-CONFLICT-ALTERNATIVE-USED"
    )
    assert conflict_issue["severity"] == "minor"
    assert verification["summary"]["publishable"] is True


def test_publication_gate_includes_conflict_sources_in_error() -> None:
    state = _grounded_state()
    state["kolaudim_draft"] = {"status": "drafted"}
    state["claim_verification"] = {
        "status": "needs_correction",
        "issues": [
            {
                "code": "PUBLIC-CONFLICT-ALTERNATIVE-USED",
                "severity": "major",
                "field": "contractor",
                "selected_value": "EB-2000 sh.p.k.",
                "alternative_value": "KËRPI sh.p.k.",
                "selected_source_documents": ["Deklaratë sipërmarrësi.docx"],
                "alternative_source_documents": ["Kontratë mbikëqyrësi.docx"],
            }
        ],
        "summary": {"publishable": False},
    }

    with pytest.raises(ClaimVerificationError) as exc_info:
        enforce_publishable_kolaudim(state)

    message = str(exc_info.value)
    assert "field=contractor" in message
    assert "canonical=EB-2000 sh.p.k." in message
    assert "used=KËRPI sh.p.k." in message
    assert "Deklaratë sipërmarrësi.docx" in message
    assert "Kontratë mbikëqyrësi.docx" in message
