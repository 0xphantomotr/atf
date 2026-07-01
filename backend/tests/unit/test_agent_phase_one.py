import uuid
from types import SimpleNamespace

from app.agents.nodes.completeness_auditor import audit_completeness
from app.agents.nodes.claim_verifier import verify_kolaudim_claims
from app.agents.nodes.consistency_checker import check_professional_consistency
from app.agents.nodes.document_classifier import classify_documents
from app.agents.nodes.evidence_verifier import verify_evidence
from app.agents.nodes.fact_extractor import extract_project_facts
from app.agents.nodes.kolaudim_planner import plan_kolaudim_act
from app.agents.llm import kolaudim_draft_input_token_budget
from app.agents.nodes.kolaudim_writer import (
    _build_kolaudim_writer_input,
    write_kolaudim_draft,
)
from app.agents.public_formatting import format_public_text, format_public_value
from app.agents.public_details import select_required_public_details
from app.agents.nodes.law_retriever import retrieve_laws
from app.agents.nodes.project_context import load_project_context
from app.agents.nodes.professional_dossier import build_professional_dossier
from app.agents.nodes.report_writer import write_report
from app.agents.nodes.senior_reviewer import senior_review
from app.agents.nodes.specialist_reviews import review_specialist_domains
from app.agents.nodes.vkm_obligation_mapper import map_vkm_obligations
from app.core.config import settings
from app.reviews.service import (
    CurrentFileSnapshot,
    RuleContext,
    _agent_metadata,
    _build_agent_state,
)


def test_phase_one_nodes_build_trace_and_report(monkeypatch) -> None:
    monkeypatch.setattr(settings, "ai_senior_review_enabled", True)
    state = {
        "project": {
            "id": str(uuid.uuid4()),
            "name": "Godine banimi",
            "project_type": "residential",
            "stage": "during_construction",
            "location": "Tirane",
        },
        "job": {
            "job_type": "kolaudim_act",
            "language": "sq-AL",
        },
        "documents": [
            {
                "parse_status": "parsed",
                "document_type": "start_works_notification",
                "original_filename": "1.1 Njoftim Fillim Punimesh OK.docx",
                "text_excerpt": "Objekti: Godine banimi 5 kate\nInvestitor: Test shpk",
            },
            {
                "parse_status": "parsed",
                "document_type": "unknown",
                "original_filename": "draft.docx",
                "text_excerpt": "Objekti: ??????",
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
        "require_ai_review": True,
        "agent_trace": [],
    }

    for node in (
        load_project_context,
        classify_documents,
        extract_project_facts,
        build_professional_dossier,
        retrieve_laws,
        map_vkm_obligations,
        audit_completeness,
        verify_evidence,
        check_professional_consistency,
        review_specialist_domains,
        plan_kolaudim_act,
        senior_review,
        write_kolaudim_draft,
        verify_kolaudim_claims,
        write_report,
    ):
        state = node(state)

    assert state["agent_trace"] == [
        "project_context",
        "document_inventory",
        "fact_extractor",
        "professional_dossier",
        "law_retriever",
        "vkm_obligation_mapper",
        "deterministic_completeness",
        "evidence_verifier",
        "consistency_checker",
        "specialist_reviews",
        "kolaudim_planner",
        "senior_reviewer",
        "kolaudim_writer",
        "claim_verifier",
        "report_writer",
    ]
    assert state["document_inventory"]["total_documents"] == 2
    assert state["document_inventory"]["classified_documents"] == 1
    assert state["document_inventory"]["unknown_documents"] == 1
    assert state["law_context"]["rule_count"] == 1
    assert state["extracted_facts"]["summary"]["fact_count"] >= 1
    assert state["vkm_obligation_map"]["summary"]["missing"] >= 1
    assert state["consistency_review"]["summary"]["issue_count"] >= 1
    assert state["professional_dossier"]["summary"]["documents_received"] == 2
    assert state["specialist_reviews"]["status"] == "skipped"
    assert state["kolaudim_analysis"]["target_output"] == "professional_akt_kolaudimi"
    assert (
        state["kolaudim_analysis"]["generation_mode"]
        == "always_generate_with_evidence_qualification"
    )
    assert state["findings"][0]["evidence_verified"]
    assert state["needs_human_review"]
    assert state["ai_review"]["status"] == "skipped"
    assert state["ai_review"]["reason"] == "missing_user_ai_settings"
    assert state["kolaudim_draft"]["status"] == "skipped"
    assert state["kolaudim_draft"]["reason"] == "missing_user_ai_settings"
    assert state["claim_verification"]["status"] == "skipped"
    assert state["report"]["phase"] == "professional_kolaudim_dossier"
    assert state["report"]["kolaudim_draft_status"] == "skipped"
    assert state["report"]["ai_review_status"] == "skipped"
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
        text_content="Objekti: Dosja Teknike\nInvestitor: Test shpk",
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
    assert state["documents"][0]["text_excerpt"].startswith("Objekti: Dosja Teknike")
    assert state["rules"][0]["law_reference"] == "VKM 610/2022, Neni 8"
    assert state["findings"][0]["evidence"]["missing_document_types"] == [
        "forty_five_day_report"
    ]


def test_senior_reviewer_can_be_disabled(monkeypatch) -> None:
    monkeypatch.setattr(settings, "ai_senior_review_enabled", False)

    state = senior_review({"agent_trace": [], "require_ai_review": True})

    assert state["agent_trace"] == ["senior_reviewer"]
    assert state["ai_review"]["status"] == "skipped"
    assert state["ai_review"]["reason"] == "ai_senior_review_disabled"
    assert state["needs_human_review"]

def test_agent_metadata_is_report_safe() -> None:
    metadata = _agent_metadata(
        {
            "agent_trace": ["project_context", "senior_reviewer", "report_writer"],
            "needs_human_review": True,
            "document_inventory": {"total_documents": 2},
            "law_context": {"rule_count": 1},
            "completeness_summary": {"finding_count": 1},
            "ai_review": {"status": "skipped", "reason": "missing_user_ai_settings"},
            "report": {"phase": "langgraph_phase_2"},
        }
    )

    assert metadata["phase"] == "langgraph_phase_2"
    assert metadata["trace"] == ["project_context", "senior_reviewer", "report_writer"]
    assert metadata["ai_review"]["status"] == "skipped"
    assert metadata["needs_human_review"] is True


def test_kolaudim_writer_input_respects_model_budget() -> None:
    ai_settings = {
        "provider": "groq",
        "model": "openai/gpt-oss-20b",
        "api_key": "gsk_test",
    }
    long_text = "\n".join(
        [
            "Objekti: Godine banimi. Investitor: Test shpk. Leje ndertimi Nr. 1.",
            "Ky tekst i gjate nuk duhet te dergohet i plote. " * 120,
        ]
    )
    documents = [
        {
            "original_filename": f"doc-{index}.docx",
            "parse_status": "parsed",
            "document_type": "kolaudim_act" if index == 0 else "hidden_works_minutes",
            "classification_confidence": 0.9,
            "text_excerpt": long_text,
        }
        for index in range(50)
    ]
    state = {
        "project": {"name": "Godine banimi"},
        "job": {"job_type": "kolaudim_act"},
        "documents": documents,
        "document_inventory": {"total_documents": len(documents)},
        "extracted_facts": {
            "categories": {
                "object_name": [
                    {"value": "Godine banimi", "source_document": "doc-0.docx"}
                ]
            }
        },
        "vkm_obligation_map": {"items": []},
        "findings": [],
        "consistency_review": {"issues": []},
        "kolaudim_analysis": {"readiness": "draft_ready_for_human_review"},
    }

    writer_input = _build_kolaudim_writer_input(state, ai_settings=ai_settings)

    assert writer_input["budget"]["estimated_input_tokens"] <= (
        kolaudim_draft_input_token_budget(ai_settings)
    )
    assert writer_input["budget"]["selected_document_count"] <= len(documents)
    assert all(
        len(document["evidence_excerpt"]) <= 900
        for document in writer_input["document_evidence"]
    )


def test_professional_dossier_resolves_authoritative_facts_and_excludes_template() -> None:
    state = {
        "agent_trace": [],
        "documents": [
            {
                "original_filename": "0. Kontrate Kolaudatorin.docx",
                "parse_status": "parsed",
                "document_type": "contract_and_related_acts",
                "classification_confidence": 0.98,
                "text_excerpt": (
                    "Për objektin: “Ndërtesë banimi 1 kat me podrum”, me adrese "
                    "Fshati Ngraçan, Bashkia Mallakaster, me Leje Ndërtimi Nr.01, "
                    "Nr. 263/4 Prot., datë 07.03.2023.\n"
                    "Investitori: z. Mitat Shanaj, me NID E50715088S.\n"
                    "Kolaudatori: Ing. Beqir Ademi, me nr. Liç. MK.0215/1."
                ),
            },
            {
                "original_filename": "X.Akt Kolaudimi.docx",
                "parse_status": "parsed",
                "document_type": "kolaudim_act",
                "classification_confidence": 0.99,
                "text_excerpt": (
                    "Objekti: Objekt bujqësor i pasaktë\n"
                    "Investitori: z. Person Template\n"
                    "Kolaudatori: ??????"
                ),
            },
            {
                "original_filename": "Njoftim fillim punimesh.docx",
                "parse_status": "parsed",
                "document_type": "start_works_notification",
                "classification_confidence": 0.98,
                "text_excerpt": "Punimet filluan me datë 20/03/2023.",
            },
            {
                "original_filename": "Procesverbal perfundimi.docx",
                "parse_status": "parsed",
                "document_type": "completion_minutes",
                "classification_confidence": 0.98,
                "text_excerpt": "Punimet përfunduan me datë 06.03.2025.",
            },
        ],
    }

    dossier = build_professional_dossier(state)["professional_dossier"]
    facts = dossier["canonical_facts"]

    assert facts["object_name"]["value"] == "Ndërtesë banimi 1 kat me podrum"
    assert facts["investor"]["value"] == "z. Mitat Shanaj"
    assert facts["kolaudator"]["value"] == "Ing. Beqir Ademi"
    assert facts["kolaudator_license"]["value"] == "MK-0215/1"
    assert facts["construction_permit_number"]["value"] == "Nr. 01"
    assert facts["construction_permit_protocol"]["value"] == "Nr. Prot. 263/4"
    assert facts["start_date"]["value"] == "20.03.2023"
    assert facts["completion_date"]["value"] == "06.03.2025"
    assert "Person Template" not in str(facts)
    assert dossier["summary"]["style_reference_documents"] == 1
    assert len(dossier["chronology"]) == 2


def test_professional_dossier_uses_verified_persisted_analysis_claims() -> None:
    version_id = uuid.uuid4()
    state = {
        "documents": [
            {
                "version_id": str(version_id),
                "original_filename": "leje-ndertimi.pdf",
                "parse_status": "parsed",
                "document_type": "construction_permit",
                "classification_confidence": 0.98,
                "text_excerpt": "Leje ndërtimi për objekt banimi.",
            }
        ],
        "document_analyses": [
            {
                "analysis_run_id": str(uuid.uuid4()),
                "file_version_id": str(version_id),
                "claims": [
                    {
                        "field_name": "investor",
                        "original_value": "Shoqëria Test sh.p.k.",
                        "normalized_value": "Shoqeria Test shpk",
                        "confidence": 0.96,
                        "evidence": [
                            {
                                "chunk_id": str(uuid.uuid4()),
                                "chunk_index": 2,
                                "supporting_excerpt": "Investitori: Shoqëria Test sh.p.k.",
                                "excerpt_verified": True,
                            }
                        ],
                    },
                    {
                        "field_name": "contractor",
                        "original_value": "Nuk duhet përdorur",
                        "confidence": 0.9,
                        "evidence": [
                            {
                                "chunk_id": str(uuid.uuid4()),
                                "chunk_index": 3,
                                "supporting_excerpt": "Nuk duhet përdorur",
                                "excerpt_verified": False,
                            }
                        ],
                    },
                ],
            }
        ],
        "extracted_facts": {},
        "agent_trace": [],
    }

    dossier = build_professional_dossier(state)["professional_dossier"]

    assert dossier["canonical_facts"]["investor"]["value"] == "Shoqëria Test sh.p.k"
    assert "contractor" not in dossier["canonical_facts"]
    assert dossier["summary"]["persisted_analysis_count"] == 1
    assert dossier["summary"]["persisted_claim_candidate_count"] == 1


def test_professional_dossier_blocks_permit_facts_from_contract_analysis() -> None:
    contract_id = uuid.uuid4()
    start_id = uuid.uuid4()
    permit_id = uuid.uuid4()
    contract_claim_id = str(uuid.uuid4())
    start_claim_id = str(uuid.uuid4())
    permit_claim_id = str(uuid.uuid4())
    state = {
        "documents": [
            {
                "version_id": str(contract_id),
                "original_filename": "0. Kontrate Mbikqyresin me z. Oltion Kaba.docx",
                "parse_status": "parsed",
                "document_type": "supervisor_contract",
                "classification_confidence": 0.98,
                "text_excerpt": "Kontratë mbikëqyrjeje me nr. 558, datë 04.07.2025.",
            },
            {
                "version_id": str(start_id),
                "original_filename": "1.3 Proces Verbal Fillim OK.docx",
                "parse_status": "parsed",
                "document_type": "start_works_minutes",
                "classification_confidence": 0.98,
                "text_excerpt": "Proces Verbal Fillim Punimesh Nr. 558.",
            },
            {
                "version_id": str(permit_id),
                "original_filename": "1.5 [1] Akt kontroll i ngritjes se kantierit.docx",
                "parse_status": "parsed",
                "document_type": "control_act",
                "classification_confidence": 0.98,
                "text_excerpt": "Leje Ndërtimi Nr.01, Nr. 263/4 Prot., datë 07.03.2023.",
            },
        ],
        "document_analyses": [
            {
                "analysis_run_id": str(uuid.uuid4()),
                "file_version_id": str(contract_id),
                "claims": [
                    {
                        "claim_id": contract_claim_id,
                        "field_name": "building_permit",
                        "category": "permit",
                        "original_value": "Nr. 558, datë 04.07.2025, AN020620250016",
                        "confidence": 0.96,
                        "evidence": [
                            {
                                "chunk_id": str(uuid.uuid4()),
                                "chunk_index": 1,
                                "supporting_excerpt": "Nr. 558, datë 04.07.2025",
                                "excerpt_verified": True,
                            }
                        ],
                    }
                ],
            },
            {
                "analysis_run_id": str(uuid.uuid4()),
                "file_version_id": str(start_id),
                "claims": [
                    {
                        "claim_id": start_claim_id,
                        "field_name": "building_permit",
                        "category": "permit",
                        "original_value": "558",
                        "confidence": 0.96,
                        "evidence": [
                            {
                                "chunk_id": str(uuid.uuid4()),
                                "chunk_index": 1,
                                "supporting_excerpt": "Proces Verbal Fillim Punimesh Nr. 558",
                                "excerpt_verified": True,
                            }
                        ],
                    }
                ],
            },
            {
                "analysis_run_id": str(uuid.uuid4()),
                "file_version_id": str(permit_id),
                "claims": [
                    {
                        "claim_id": permit_claim_id,
                        "field_name": "building_permit",
                        "category": "permit",
                        "original_value": "Nr.01, Nr. 263/4 Prot., datë 07.03.2023",
                        "confidence": 0.94,
                        "evidence": [
                            {
                                "chunk_id": str(uuid.uuid4()),
                                "chunk_index": 1,
                                "supporting_excerpt": (
                                    "Leje Ndërtimi Nr.01, Nr. 263/4 Prot., "
                                    "datë 07.03.2023"
                                ),
                                "excerpt_verified": True,
                            }
                        ],
                    }
                ],
            },
        ],
        "extracted_facts": {},
        "agent_trace": [],
    }

    dossier = build_professional_dossier(state)["professional_dossier"]

    permit_fact = dossier["canonical_facts"]["construction_permit_number"]
    assert permit_fact["value"] == "Nr.01, Nr. 263/4 Prot., datë 07.03.2023"
    assert permit_fact["source_documents"] == [
        "1.5 [1] Akt kontroll i ngritjes se kantierit.docx"
    ]
    assert "Nr. 558" not in str(dossier["canonical_facts"])
    assert '"558"' not in str(dossier["canonical_facts"])
    assert "Nr. 558" not in str(dossier["registers"]["permits_property_licenses"])
    assert '"558"' not in str(dossier["registers"]["permits_property_licenses"])


def test_professional_dossier_scopes_document_and_stage_facts_out_of_canonical() -> None:
    first_id = uuid.uuid4()
    second_id = uuid.uuid4()
    state = {
        "documents": [
            {
                "version_id": str(first_id),
                "original_filename": "themele.docx",
                "parse_status": "parsed",
                "document_type": "hidden_works_minutes",
                "classification_confidence": 0.98,
                "text_excerpt": "Procesverbal punime të maskuara.",
            },
            {
                "version_id": str(second_id),
                "original_filename": "soleta.docx",
                "parse_status": "parsed",
                "document_type": "hidden_works_minutes",
                "classification_confidence": 0.98,
                "text_excerpt": "Procesverbal punime të maskuara.",
            },
        ],
        "document_analyses": [
            {
                "analysis_run_id": str(uuid.uuid4()),
                "file_version_id": str(first_id),
                "claims": [
                    {
                        "field_name": "date_of_document",
                        "original_value": "26.04.2023",
                        "confidence": 0.96,
                        "evidence": [
                            {
                                "chunk_id": str(uuid.uuid4()),
                                "chunk_index": 1,
                                "supporting_excerpt": "Datë 26.04.2023",
                                "excerpt_verified": True,
                            }
                        ],
                    },
                    {
                        "field_name": "element_name",
                        "original_value": "Themel Plint",
                        "confidence": 0.95,
                        "evidence": [
                            {
                                "chunk_id": str(uuid.uuid4()),
                                "chunk_index": 2,
                                "supporting_excerpt": "Elementi: Themel Plint",
                                "excerpt_verified": True,
                            }
                        ],
                    },
                    {
                        "field_name": "kontrata_sipermarrjes",
                        "original_value": "Nr. 2026 Rep., Nr. 1251 Kol.",
                        "confidence": 0.9,
                        "evidence": [
                            {
                                "chunk_id": str(uuid.uuid4()),
                                "chunk_index": 3,
                                "supporting_excerpt": "Kontrata e sipërmarrjes...",
                                "excerpt_verified": True,
                            }
                        ],
                    },
                ],
            },
            {
                "analysis_run_id": str(uuid.uuid4()),
                "file_version_id": str(second_id),
                "claims": [
                    {
                        "field_name": "date_of_document",
                        "original_value": "07.03.2024",
                        "confidence": 0.96,
                        "evidence": [
                            {
                                "chunk_id": str(uuid.uuid4()),
                                "chunk_index": 1,
                                "supporting_excerpt": "Datë 07.03.2024",
                                "excerpt_verified": True,
                            }
                        ],
                    },
                    {
                        "field_name": "element_name",
                        "original_value": "Soleta B/A",
                        "confidence": 0.95,
                        "evidence": [
                            {
                                "chunk_id": str(uuid.uuid4()),
                                "chunk_index": 2,
                                "supporting_excerpt": "Elementi: Soleta B/A",
                                "excerpt_verified": True,
                            }
                        ],
                    },
                ],
            },
        ],
        "extracted_facts": {},
        "agent_trace": [],
    }

    dossier = build_professional_dossier(state)["professional_dossier"]

    assert "document_date" not in dossier["canonical_facts"]
    assert "work_element" not in dossier["canonical_facts"]
    assert "contractor_contract_reference" not in dossier["canonical_facts"]
    assert dossier["conflicts"] == []
    assert {
        item["value"] for item in dossier["scoped_facts"]["document_metadata"]
    } == {"26.04.2023", "07.03.2024"}
    assert {item["value"] for item in dossier["scoped_facts"]["work_stages"]} == {
        "Themel Plint",
        "Soleta B/A",
    }
    assert dossier["scoped_facts"]["contract_references"][0]["value"] == (
        "Nr. 2026 Rep., Nr. 1251 Kol"
    )


def test_professional_dossier_extracts_kolaudator_contract_role_from_filename_with_cached_analysis() -> None:
    version_id = uuid.uuid4()
    state = {
        "documents": [
            {
                "version_id": str(version_id),
                "original_filename": "0. Kontrate Kolaudatorin me z.Naqe Bala.docx",
                "parse_status": "parsed",
                "document_type": "contract_and_related_acts",
                "classification_confidence": 0.94,
                "text_excerpt": (
                    "Kontratë shërbimi kolaudimi. "
                    "Nr. repertori 4519, Nr. koleksioni 2115, datë 17.09.2025."
                ),
            }
        ],
        "document_analyses": [
            {
                "analysis_run_id": str(uuid.uuid4()),
                "file_version_id": str(version_id),
                "claims": [],
            }
        ],
        "extracted_facts": {},
        "agent_trace": [],
    }

    dossier = build_professional_dossier(state)["professional_dossier"]

    assert dossier["canonical_facts"]["kolaudator"]["value"] == "z. Naqe Bala"
    assert dossier["canonical_facts"]["kolaudator"]["evidence"][0][
        "source_file_version_id"
    ] == str(version_id)
    assert dossier["scoped_facts"]["contract_references"][0]["field_name"] == (
        "kolaudator_contract_reference"
    )
    assert dossier["scoped_facts"]["contract_references"][0]["value"] == (
        "Nr. Rep. 4519, Nr. Kol. 2115, datë 17.09.2025"
    )


def test_professional_dossier_rejects_narrative_as_designer_identity() -> None:
    state = {
        "agent_trace": [],
        "documents": [
            {
                "original_filename": "Deklarate konformiteti.docx",
                "parse_status": "parsed",
                "document_type": "construction_permit_conformity_declaration",
                "classification_confidence": 0.98,
                "text_excerpt": (
                    "Zërat e punimeve janë realizuar sipas preventivit të miratuar "
                    "dhe ndryshimeve të miratuara nga projektuesi, investitori dhe "
                    "organet përkatëse."
                ),
            },
            {
                "original_filename": "Njoftim perfundimi.docx",
                "parse_status": "parsed",
                "document_type": "start_works_notification",
                "classification_confidence": 0.98,
                "text_excerpt": "Projektuesi: 6D – PLAN me Lic Nr. N6760/11",
            },
            {
                "original_filename": "Njoftim faze.docx",
                "parse_status": "parsed",
                "document_type": "start_works_notification",
                "classification_confidence": 0.98,
                "text_excerpt": "Vlera e objektit: 3,434,985 Lek me TVSH",
            },
        ],
    }

    extract_project_facts(state)
    dossier = build_professional_dossier(state)["professional_dossier"]

    assert dossier["canonical_facts"]["designer"]["value"] == "6D – PLAN"
    assert dossier["canonical_facts"]["planned_value"]["value"] == "3.434.985 lekë"
    assert "realizuar" not in str(dossier["canonical_facts"]["designer"])


def test_writer_input_contains_professional_dossier_without_audit_payload() -> None:
    documents = [
        {
            "original_filename": f"doc-{index}.docx",
            "parse_status": "parsed",
            "document_type": "hidden_works_minutes",
            "classification_confidence": 0.9,
            "text_excerpt": "Procesverbal për punime të maskuara. " * 100,
        }
        for index in range(12)
    ]
    state = {
        "project": {"name": "Dosja Teknike"},
        "job": {"job_type": "kolaudim_act", "law_scope": ["VKM_610_2022"]},
        "documents": documents,
        "professional_dossier": {
            "canonical_facts": {
                "object_name": {
                    "value": "Ndërtesë banimi",
                    "confidence_level": "high",
                    "source_documents": ["doc-0.docx"],
                    "evidence": [],
                    "alternatives": [],
                }
            },
            "document_records": [
                {
                    "filename": f"doc-{index}.docx",
                    "role": "authoritative_evidence",
                }
                for index in range(12)
            ],
            "chronology": [],
            "technical_observations": [],
            "conflicts": [],
            "evidence_by_section": {},
            "style_references": [],
            "missing_core_fields": [],
            "summary": {},
        },
        "kolaudim_analysis": {"sections": []},
        "rules": [],
        "verified_findings": [{"title": "Nuk duhet të hyjë në shkrues"}],
        "consistency_review": {"issues": [{"title": "Nuk duhet të hyjë"}]},
    }
    ai_settings = {
        "provider": "gemini",
        "model": "gemini-2.5-flash",
        "api_key": "test",
    }

    writer_input = _build_kolaudim_writer_input(state, ai_settings=ai_settings)

    assert "professional_dossier" in writer_input
    assert "verified_findings" not in writer_input
    assert "consistency_review" not in writer_input
    assert "vkm_obligation_map" not in writer_input
    assert len(writer_input["document_evidence"]) == len(documents)


def test_writer_excludes_foreign_and_style_reference_text() -> None:
    state = {
        "project": {"name": "Dosja Teknike"},
        "job": {"job_type": "kolaudim_act", "law_scope": ["VKM_610_2022"]},
        "documents": [
            {
                "original_filename": "target.docx",
                "parse_status": "parsed",
                "document_type": "control_act",
                "classification_confidence": 0.9,
                "text_excerpt": "Objekti: Ndërtesë banimi",
            },
            {
                "original_filename": "foreign.docx",
                "parse_status": "parsed",
                "document_type": "contract_and_related_acts",
                "classification_confidence": 0.9,
                "text_excerpt": "Person dhe objekt i huaj",
            },
            {
                "original_filename": "example.docx",
                "parse_status": "parsed",
                "document_type": "kolaudim_act",
                "classification_confidence": 0.9,
                "text_excerpt": "Akt shembull me fakte të huaja",
            },
        ],
        "professional_dossier": {
            "canonical_facts": {},
            "document_records": [
                {"filename": "target.docx", "role": "authoritative_evidence"},
                {"filename": "foreign.docx", "role": "foreign_project_reference"},
                {"filename": "example.docx", "role": "style_reference"},
            ],
            "chronology": [],
            "technical_observations": [],
            "conflicts": [],
            "evidence_by_section": {},
            "style_references": [{"filename": "example.docx"}],
            "missing_core_fields": [],
            "summary": {"foreign_project_documents": 1},
        },
        "kolaudim_analysis": {"sections": []},
        "rules": [],
    }

    writer_input = _build_kolaudim_writer_input(
        state,
        ai_settings={
            "provider": "gemini",
            "model": "gemini-2.5-flash",
            "api_key": "test",
        },
    )

    assert [item["filename"] for item in writer_input["document_evidence"]] == [
        "target.docx"
    ]
    compact_records = writer_input["professional_dossier"]["document_records"]
    assert [item["filename"] for item in compact_records] == ["target.docx"]
    assert "example.docx" not in str(writer_input)


def test_writer_prefers_consolidated_registers_over_raw_excerpts() -> None:
    state = {
        "project": {"name": "Dosja Teknike"},
        "job": {"job_type": "kolaudim_act", "law_scope": ["VKM_610_2022"]},
        "documents": [
            {
                "original_filename": "leje.pdf",
                "parse_status": "parsed",
                "document_type": "construction_permit",
                "classification_confidence": 0.98,
                "text_excerpt": "Ky tekst i papërpunuar nuk duhet të dërgohet.",
            }
        ],
        "professional_dossier": {
            "canonical_facts": {
                "construction_permit_number": {
                    "value": "Nr. 42",
                    "confidence_level": "high",
                    "source_documents": ["leje.pdf"],
                    "evidence": [],
                    "alternatives": [],
                }
            },
            "registers": {
                "permits_property_licenses": [
                    {
                        "field_name": "construction_permit_number",
                        "value": "Nr. 42",
                        "confidence_level": "high",
                        "source_documents": ["leje.pdf"],
                        "sources": [
                            {
                                "source_document": "leje.pdf",
                                "document_type": "construction_permit",
                                "chunk_references": [
                                    {
                                        "chunk_index": 0,
                                        "page_start": 1,
                                        "page_end": 1,
                                        "excerpt": "Leje ndërtimi nr. 42",
                                    }
                                ],
                            }
                        ],
                    }
                ]
            },
            "economic_summary": {},
            "evidence_coverage": {"analysis_coverage_ratio": 1.0},
            "integrity_issues": [],
            "document_records": [
                {"filename": "leje.pdf", "role": "authoritative_evidence"}
            ],
            "chronology": [],
            "technical_observations": [],
            "conflicts": [],
            "evidence_by_section": {},
            "style_references": [],
            "missing_core_fields": [],
            "summary": {"persisted_analysis_count": 1},
        },
        "specialist_reviews": {
            "status": "reviewed",
            "summary": {"memorandum_count": 6},
            "memoranda": [
                {
                    "code": "legal_administrative",
                    "title": "Dokumentacioni ligjor dhe administrativ",
                    "status": "reviewed",
                    "confidence": 0.9,
                    "established_facts": [
                        {
                            "statement": "Leja nr. 42 është identifikuar.",
                            "evidence_ids": ["permits_property_licenses:0"],
                            "source_references": [
                                {
                                    "source_document": "leje.pdf",
                                    "file_version_id": "version-1",
                                    "chunk_ids": ["chunk-1"],
                                }
                            ],
                        }
                    ],
                    "technical_assessments": [],
                    "qualifications": [],
                    "writer_guidance": [],
                }
            ],
        },
        "kolaudim_analysis": {"sections": []},
        "rules": [],
    }

    writer_input = _build_kolaudim_writer_input(
        state,
        ai_settings={
            "provider": "gemini",
            "model": "gemini-2.5-flash",
            "api_key": "test",
        },
    )

    assert writer_input["document_evidence"] == []
    register = writer_input["professional_dossier"]["registers"]
    assert register["permits_property_licenses"][0]["value"] == "Nr. 42"
    assert writer_input["required_public_details"][0]["value"] == "Nr. 42"
    assert writer_input["required_public_details"][0]["evidence_id"] == (
        "permits_property_licenses:0"
    )
    specialist = writer_input["specialist_memoranda"]["memoranda"][0]
    assert specialist["established_facts"][0]["statement"].startswith("Leja nr. 42")
    assert "Ky tekst i papërpunuar" not in str(writer_input)


def test_required_public_details_skip_stakeholders_and_normalize_permit_alias() -> None:
    details = select_required_public_details(
        {
            "registers": {
                "permits_property_licenses": [
                    {
                        "field_name": "building_permit",
                        "value": "Nr.01, Nr. 263/4 Prot.",
                    },
                    {
                        "field_name": "construction_permit_number",
                        "value": "Nr.01, Nr. 263/4 Prot., datë 07.03.2023",
                    }
                ],
                "contracts_and_economics": [
                    {
                        "field_name": "contractor",
                        "value": "SHOQERIA:“EB-2000” shpk",
                    },
                    {
                        "field_name": "contractor_legal_representative",
                        "value": "Lumturi BEQIRAJ",
                    }
                ],
            }
        }
    )

    assert details == [
        {
            "evidence_id": "permits_property_licenses:1",
            "group": "permit_property",
            "register": "permits_property_licenses",
            "field_name": "construction_permit_number",
            "value": "Nr.01, Nr. 263/4 Prot., datë 07.03.2023",
            "priority": 1,
            "must_include": True,
            "confidence_level": None,
            "source_documents": [],
        }
    ]


def test_required_public_details_skip_bare_permit_reference() -> None:
    details = select_required_public_details(
        {
            "registers": {
                "permits_property_licenses": [
                    {
                        "field_name": "construction_permit_number",
                        "value": "558",
                        "source_documents": ["1.3 Proces Verbal Fillim OK.docx"],
                    },
                    {
                        "field_name": "building_permit",
                        "value": "Nr. 558",
                        "source_documents": ["1.3 Proces Verbal Fillim OK.docx"],
                    },
                ],
            }
        }
    )

    assert details == []


def test_required_public_details_exclude_noncanonical_permit_alternatives() -> None:
    details = select_required_public_details(
        {
            "canonical_facts": {
                "construction_permit_number": {"value": "Nr. 274"},
            },
            "registers": {
                "permits_property_licenses": [
                    {
                        "field_name": "construction_permit_number",
                        "value": "4571/4, datë 21/08/2020",
                        "source_documents": ["Akt Dorezim Sheshi.docx"],
                    },
                    {
                        "field_name": "construction_permit_number",
                        "value": "Nr. 274",
                        "confidence_level": "high",
                        "source_documents": ["Leje Ndërtimi.pdf"],
                    },
                ],
            },
        }
    )

    assert [detail["value"] for detail in details] == ["Nr. 274"]


def test_user_confirmed_fact_override_becomes_canonical() -> None:
    state = {
        "documents": [],
        "document_analyses": [],
        "user_fact_overrides": {"construction_permit_number": "Nr. 274"},
        "agent_trace": [],
    }

    dossier = build_professional_dossier(state)["professional_dossier"]

    fact = dossier["canonical_facts"]["construction_permit_number"]
    assert fact["value"] == "Nr. 274"
    assert fact["user_confirmed"] is True
    assert dossier["user_confirmations"] == [
        {"field": "construction_permit_number", "value": "Nr. 274"}
    ]


def test_required_public_details_skip_role_name_text_alias() -> None:
    details = select_required_public_details(
        {
            "registers": {
                "contracts_and_economics": [
                    {
                        "field_name": "contractor_name_text",
                        "value": "EB-2000 shpk",
                        "source_documents": ["1.3 Proces Verbal Fillim OK.docx"],
                    },
                    {
                        "field_name": "supervisor_name_text",
                        "value": "Alisha Kerpi",
                        "source_documents": ["1.3 Proces Verbal Fillim OK.docx"],
                    },
                ],
            }
        }
    )

    assert details == []


def test_public_formatting_cleans_units_and_common_albanian_display() -> None:
    assert format_public_text("Sipërfaqja është 94.7(54m2 podrum).") == (
        "Sipërfaqja është 94.7 m², nga të cilat 54 m² podrum."
    )
    assert format_public_text("Rezistenca është 2500 kg/cm2.") == (
        "Rezistenca është 2500 kg/cm²."
    )
    assert format_public_value(
        "Fshati Ngraçan, Bashkia Mallakaster",
        "location",
    ) == "Fshati Ngraçan, Bashkia Mallakastër"


def test_required_public_details_skip_generic_contract_reference() -> None:
    details = select_required_public_details(
        {
            "registers": {
                "contracts_and_economics": [
                    {
                        "field_name": "contract_reference",
                        "value": "Nr. Rep. 1, Nr. Kol. 2",
                    },
                    {
                        "field_name": "contractor_contract_reference",
                        "value": "Nr. Rep. 2026, Nr. Kol. 1251",
                    },
                ],
            }
        }
    )

    assert len(details) == 1
    assert details[0]["field_name"] == "contractor_contract_reference"


def test_writer_input_separates_contract_references_by_role() -> None:
    state = {
        "project": {"name": "Dosja Teknike"},
        "job": {"job_type": "kolaudim_act", "law_scope": ["VKM_610_2022"]},
        "documents": [],
        "professional_dossier": {
            "canonical_facts": {},
            "registers": {
                "contracts_and_economics": [
                    {
                        "field_name": "contract_reference",
                        "value": "Nr. Rep. 100, Nr. Kol. 200",
                        "source_documents": ["Kontrate e paqartë.docx"],
                    },
                    {
                        "field_name": "contractor_contract_reference",
                        "value": "Nr. Rep. 2026, Nr. Kol. 1251, datë 18.11.2022",
                        "source_documents": ["Kontrate Sipermarrje.docx"],
                    },
                    {
                        "field_name": "supervisor_contract_reference",
                        "value": "Nr. Rep. 4519, Nr. Kol. 2115, datë 17.09.2025",
                        "source_documents": ["Kontrate Mbikqyresin.docx"],
                    },
                ],
            },
            "scoped_facts": {},
            "economic_summary": {},
            "evidence_coverage": {},
            "integrity_issues": [],
            "document_records": [],
            "chronology": [],
            "technical_observations": [],
            "conflicts": [],
            "evidence_by_section": {},
            "style_references": [],
            "missing_core_fields": [],
            "summary": {"persisted_analysis_count": 1},
        },
        "kolaudim_analysis": {"sections": []},
        "rules": [],
    }

    writer_input = _build_kolaudim_writer_input(
        state,
        ai_settings={
            "provider": "gemini",
            "model": "gemini-2.5-flash",
            "api_key": "test",
        },
    )

    summary = writer_input["contract_reference_policy"]["role_specific_references"]
    assert summary["role_specific"]["contractor"]["value"] == (
        "Nr. Rep. 2026, Nr. Kol. 1251, datë 18.11.2022"
    )
    assert summary["role_specific"]["supervisor"]["value"] == (
        "Nr. Rep. 4519, Nr. Kol. 2115, datë 17.09.2025"
    )
    assert summary["generic_references"][0]["value"] == "Nr. Rep. 100, Nr. Kol. 200"


def test_claim_verifier_accepts_clean_human_style_act() -> None:
    summary = (
        "Për Ndërtesë banimi në Fshati Ngraçan, Bashkia Mallakastër, "
        "me investitor Mitat Shanaj."
    )
    section_body = (
        "Zbatues EB-2000 shpk, mbikëqyrës Alisha Kerpi dhe "
        "kolaudator Beqir Ademi."
    )
    state = {
        "agent_trace": [],
        "professional_dossier": {
            "canonical_facts": {
                "object_name": {"value": "Ndërtesë banimi"},
                "location": {"value": "Fshati Ngraçan, Bashkia Mallakastër"},
                "investor": {"value": "Mitat Shanaj"},
                "contractor": {"value": "EB-2000 shpk"},
                "supervisor": {"value": "Alisha Kerpi"},
                "kolaudator": {"value": "Beqir Ademi"},
            },
            "conflicts": [],
        },
        "kolaudim_draft": {
            "status": "drafted",
            "title": "AKT-KOLAUDIMI TEKNIKO-EKONOMIK",
            "executive_summary": summary,
            "sections": [
                {
                    "title": f"Seksioni {index}",
                    "body": section_body,
                }
                for index in range(10)
            ],
            "claim_ledger": [
                {
                    "claim_id": "executive_summary:0",
                    "section_code": "executive_summary",
                    "statement": summary,
                    "claim_type": "documented_fact",
                    "conclusion_level": "proven",
                    "confidence": 0.9,
                    "evidence_ids": [
                        "canonical:object_name",
                        "canonical:location",
                        "canonical:investor",
                    ],
                },
                *[
                    {
                        "claim_id": f"section_{index}:0",
                        "section_code": f"section_{index}",
                        "statement": section_body,
                        "claim_type": "documented_fact",
                        "conclusion_level": "proven",
                        "confidence": 0.9,
                        "evidence_ids": [
                            "canonical:contractor",
                            "canonical:supervisor",
                            "canonical:kolaudator",
                        ],
                    }
                    for index in range(10)
                ],
            ],
            "signature_note": "Akti nënshkruhet nga palët përgjegjëse.",
        },
    }

    verified = verify_kolaudim_claims(state)["claim_verification"]

    assert verified["status"] == "verified"
    assert verified["summary"]["publishable"] is True
