import uuid

from app.agents.dossier_consolidation import (
    REGISTER_CHRONOLOGY,
    REGISTER_ECONOMIC,
    REGISTER_MATERIALS,
    REGISTER_PARAMETERS,
    REGISTER_PERMITS,
    REGISTER_STAKEHOLDERS,
    REGISTER_TECHNICAL,
    canonical_field_name,
    consolidate_project_registers,
)


def _document(version_id: uuid.UUID, filename: str, document_type: str) -> dict:
    return {
        "version_id": str(version_id),
        "sha256_hash": str(version_id).replace("-", "") * 2,
        "original_filename": filename,
        "document_type": document_type,
        "classification_confidence": 0.96,
    }


def _record(
    filename: str,
    document_type: str,
    *,
    role: str = "authoritative_evidence",
    version_id: uuid.UUID | None = None,
) -> dict:
    record = {
        "filename": filename,
        "document_type": document_type,
        "role": role,
        "authority_score": 0.94,
    }
    if version_id is not None:
        record["file_version_id"] = str(version_id)
    return record


def _claim(
    field_name: str,
    value: str,
    *,
    category: str,
    normalized_value: str = "",
    verified: bool = True,
    chunk_index: int = 0,
    supporting_excerpt: str | None = None,
) -> dict:
    return {
        "claim_id": str(uuid.uuid4()),
        "category": category,
        "field_name": field_name,
        "original_value": value,
        "normalized_value": normalized_value,
        "confidence": 0.95,
        "extraction_method": "ai_chunk_analysis",
        "evidence": [
            {
                "chunk_id": str(uuid.uuid4()),
                "chunk_index": chunk_index,
                "page_start": chunk_index + 1,
                "page_end": chunk_index + 1,
                "coordinates": {"page_number": chunk_index + 1},
                "supporting_excerpt": supporting_excerpt or value,
                "excerpt_verified": verified,
            }
        ],
    }


def _analysis(version_id: uuid.UUID, file_sha256: str, claims: list[dict]) -> dict:
    return {
        "analysis_run_id": str(uuid.uuid4()),
        "file_version_id": str(version_id),
        "file_sha256": file_sha256,
        "claims": claims,
    }


def test_consolidation_builds_professional_registers_with_provenance() -> None:
    version_id = uuid.uuid4()
    document = _document(version_id, "dosja.pdf", "construction_permit")
    claims = [
        _claim("developer", "Investitor Test sh.p.k.", category="party"),
        _claim(
            "building_permit_number",
            "Leje nr. 42",
            category="permit",
            chunk_index=1,
        ),
        _claim("total_construction_area", "1 250 m²", category="technical"),
        _claim(
            "foundation_completion_date",
            "15.04.2024",
            normalized_value="2024-04-15",
            category="work_phase",
        ),
        _claim("concrete_test_result", "C25/30 - në përputhje", category="test"),
        _claim("planned_value", "10.000.000 lekë", category="economic"),
        _claim(
            "contractor",
            "Duhet përjashtuar",
            category="party",
            verified=False,
        ),
    ]
    result = consolidate_project_registers(
        analyses=[_analysis(version_id, document["sha256_hash"], claims)],
        documents=[document],
        document_records=[_record("dosja.pdf", "construction_permit")],
        canonical_facts={},
    )
    registers = result["registers"]

    assert registers[REGISTER_STAKEHOLDERS][0]["field_name"] == "investor"
    assert registers[REGISTER_PERMITS][0]["field_name"] == "construction_permit_number"
    assert registers[REGISTER_PARAMETERS][0]["field_name"] == "total_construction_area"
    assert registers[REGISTER_MATERIALS][0]["field_name"] == "concrete_test_result"
    assert registers[REGISTER_ECONOMIC][0]["field_name"] == "planned_value"
    assert any(
        entry["field_name"] == "foundation_completion_date"
        for entry in registers[REGISTER_TECHNICAL]
    )
    chronology = registers[REGISTER_CHRONOLOGY]
    assert chronology[0]["normalized_value"] == "2024-04-15"
    assert chronology[0]["sources"][0]["chunk_references"][0]["chunk_id"]
    assert all(entry["value"] != "Duhet përjashtuar" for entry in registers[REGISTER_STAKEHOLDERS])
    assert result["evidence_coverage"]["analysis_coverage_ratio"] == 1.0


def test_consolidation_merges_corroborating_sources_and_isolates_versions() -> None:
    first_id = uuid.uuid4()
    second_id = uuid.uuid4()
    foreign_id = uuid.uuid4()
    first = _document(first_id, "leje.pdf", "construction_permit")
    second = _document(second_id, "kontrate.docx", "contract_and_related_acts")
    shared_claim = _claim("investor", "Shoqëria Alfa sh.p.k.", category="party")
    result = consolidate_project_registers(
        analyses=[
            _analysis(first_id, first["sha256_hash"], [shared_claim]),
            _analysis(second_id, second["sha256_hash"], [shared_claim]),
            _analysis(
                foreign_id,
                "f" * 64,
                [_claim("investor", "Projekt tjetër", category="party")],
            ),
        ],
        documents=[first, second],
        document_records=[
            _record("leje.pdf", "construction_permit"),
            _record("kontrate.docx", "contract_and_related_acts"),
        ],
        canonical_facts={},
    )

    stakeholder = result["registers"][REGISTER_STAKEHOLDERS][0]
    assert stakeholder["value"] == "Shoqëria Alfa sh.p.k."
    assert stakeholder["corroborating_source_count"] == 2
    assert stakeholder["source_documents"] == ["kontrate.docx", "leje.pdf"]
    assert "Projekt tjetër" not in str(result)


def test_consolidation_uses_version_identity_for_same_name_documents() -> None:
    target_id = uuid.uuid4()
    foreign_id = uuid.uuid4()
    target = _document(target_id, "akti.docx", "technical_declaration")
    foreign = _document(foreign_id, "akti.docx", "technical_declaration")
    result = consolidate_project_registers(
        analyses=[
            _analysis(
                target_id,
                target["sha256_hash"],
                [_claim("investor", "Investitor i projektit", category="party")],
            ),
            _analysis(
                foreign_id,
                foreign["sha256_hash"],
                [_claim("investor", "Investitor i huaj", category="party")],
            ),
        ],
        documents=[target, foreign],
        document_records=[
            _record("akti.docx", "technical_declaration", version_id=target_id),
            _record(
                "akti.docx",
                "technical_declaration",
                role="foreign_project_reference",
                version_id=foreign_id,
            ),
        ],
        canonical_facts={},
    )

    stakeholders = result["registers"][REGISTER_STAKEHOLDERS]
    assert [entry["value"] for entry in stakeholders] == ["Investitor i projektit"]
    assert result["evidence_coverage"]["eligible_document_count"] == 1


def test_consolidation_blocks_permit_claims_from_contract_documents() -> None:
    contract_id = uuid.uuid4()
    permit_id = uuid.uuid4()
    contract = _document(
        contract_id,
        "0. Kontrate Mbikqyresin.docx",
        "supervisor_contract",
    )
    permit = _document(permit_id, "1.5 Akt kontroll kantieri.docx", "control_act")

    result = consolidate_project_registers(
        analyses=[
            _analysis(
                contract_id,
                contract["sha256_hash"],
                [
                    _claim(
                        "building_permit",
                        "Nr. 558, datë 04.07.2025, AN020620250016",
                        category="permit",
                    ),
                ],
            ),
            _analysis(
                permit_id,
                permit["sha256_hash"],
                [
                    _claim(
                        "building_permit",
                        "Nr.01, Nr. 263/4 Prot., datë 07.03.2023",
                        category="permit",
                        supporting_excerpt=(
                            "Leje Ndërtimi Nr.01, Nr. 263/4 Prot., datë 07.03.2023"
                        ),
                    ),
                ],
            ),
        ],
        documents=[contract, permit],
        document_records=[
            _record(
                "0. Kontrate Mbikqyresin.docx",
                "supervisor_contract",
                version_id=contract_id,
            ),
            _record("1.5 Akt kontroll kantieri.docx", "control_act", version_id=permit_id),
        ],
        canonical_facts={},
    )

    permits = result["registers"][REGISTER_PERMITS]
    assert len(permits) == 1
    assert permits[0]["field_name"] == "construction_permit_number"
    assert permits[0]["value"] == "Nr.01, Nr. 263/4 Prot., datë 07.03.2023"
    assert permits[0]["source_documents"] == ["1.5 Akt kontroll kantieri.docx"]
    register_values = [
        str(entry.get("value") or "")
        for entries in result["registers"].values()
        if isinstance(entries, list)
        for entry in entries
        if isinstance(entry, dict)
    ]
    assert "Nr. 558" not in register_values


def test_consolidation_rejects_bare_permit_number_from_process_minutes() -> None:
    start_id = uuid.uuid4()
    control_id = uuid.uuid4()
    start = _document(start_id, "1.3 Proces Verbal Fillim OK.docx", "start_works_minutes")
    control = _document(control_id, "1.5 [1] Akt kontroll kantieri.docx", "control_act")

    result = consolidate_project_registers(
        analyses=[
            _analysis(
                start_id,
                start["sha256_hash"],
                [
                    _claim(
                        "building_permit",
                        "558",
                        category="permit",
                        supporting_excerpt="Proces Verbal Fillim Punimesh Nr. 558",
                    ),
                ],
            ),
            _analysis(
                control_id,
                control["sha256_hash"],
                [
                    _claim(
                        "building_permit",
                        "Nr.01, Nr. 263/4 Prot., datë 07.03.2023",
                        category="permit",
                        supporting_excerpt=(
                            "Leje Ndërtimi Nr.01, Nr. 263/4 Prot., datë 07.03.2023"
                        ),
                    ),
                ],
            ),
        ],
        documents=[start, control],
        document_records=[
            _record("1.3 Proces Verbal Fillim OK.docx", "start_works_minutes", version_id=start_id),
            _record("1.5 [1] Akt kontroll kantieri.docx", "control_act", version_id=control_id),
        ],
        canonical_facts={},
    )

    permits = result["registers"][REGISTER_PERMITS]
    assert len(permits) == 1
    assert permits[0]["value"] == "Nr.01, Nr. 263/4 Prot., datë 07.03.2023"
    register_values = [
        str(entry.get("value") or "")
        for entries in result["registers"].values()
        if isinstance(entries, list)
        for entry in entries
        if isinstance(entry, dict)
    ]
    assert "558" not in register_values


def test_consolidation_rejects_bare_permit_number_from_misclassified_minutes() -> None:
    start_id = uuid.uuid4()
    start = _document(
        start_id,
        "1.3 Proces Verbal Fillim OK.docx",
        "construction_permit",
    )

    result = consolidate_project_registers(
        analyses=[
            _analysis(
                start_id,
                start["sha256_hash"],
                [
                    _claim(
                        "building_permit",
                        "558",
                        category="permit",
                        supporting_excerpt="Proces Verbal Fillim Punimesh Nr. 558",
                    ),
                ],
            ),
        ],
        documents=[start],
        document_records=[
            _record(
                "1.3 Proces Verbal Fillim OK.docx",
                "construction_permit",
                version_id=start_id,
            ),
        ],
        canonical_facts={},
    )

    assert result["registers"][REGISTER_PERMITS] == []


def test_consolidation_calculates_economic_variance_and_chronology_integrity() -> None:
    result = consolidate_project_registers(
        analyses=[],
        documents=[],
        document_records=[],
        canonical_facts={
            "planned_value": {"value": "10.000.000 lekë", "confidence": 0.9},
            "final_value": {"value": "9.500.000 lekë", "confidence": 0.9},
            "start_date": {"value": "20.06.2025", "confidence": 0.9},
            "completion_date": {"value": "10.06.2025", "confidence": 0.9},
        },
    )

    assert result["economic_summary"]["difference"] == -500_000
    assert result["economic_summary"]["difference_percent"] == -5.0
    assert result["integrity_issues"][0]["code"] == "DOSSIER-CHRONOLOGY-ORDER"


def test_consolidation_reports_incomplete_current_version_coverage() -> None:
    analyzed_id = uuid.uuid4()
    missing_id = uuid.uuid4()
    analyzed = _document(analyzed_id, "leje.pdf", "construction_permit")
    missing = _document(missing_id, "ditari.pdf", "daily_site_log")
    result = consolidate_project_registers(
        analyses=[
            _analysis(
                analyzed_id,
                analyzed["sha256_hash"],
                [_claim("investor", "Shoqëria Alfa sh.p.k.", category="party")],
            )
        ],
        documents=[analyzed, missing],
        document_records=[
            _record("leje.pdf", "construction_permit"),
            _record("ditari.pdf", "daily_site_log"),
        ],
        canonical_facts={},
    )

    coverage = result["evidence_coverage"]
    assert coverage["eligible_document_count"] == 2
    assert coverage["analyzed_document_count"] == 1
    assert coverage["analysis_coverage_ratio"] == 0.5
    assert result["integrity_issues"] == [
        {
            "code": "DOSSIER-ANALYSIS-COVERAGE",
            "severity": "major",
            "description": (
                "Një ose më shumë dokumente të lexueshme nuk kanë analizë të "
                "përfunduar për versionin aktual."
            ),
            "unanalyzed_document_count": 1,
        }
    ]


def test_canonical_field_aliases_are_project_agnostic() -> None:
    assert canonical_field_name("building permit number") == "construction_permit_number"
    assert canonical_field_name("construction_permit") == "construction_permit_number"
    assert canonical_field_name("Construction Company") == "contractor"
    assert canonical_field_name("contractor_name") == "contractor"
    assert canonical_field_name("contractor_name_text") == "contractor"
    assert canonical_field_name("supervisor_name_text") == "supervisor"
    assert canonical_field_name("kolaudator_name_text") == "kolaudator"
    assert canonical_field_name("Total Construction Area") == "total_construction_area"
    assert canonical_field_name("emri_objektit") == "object_name"
    assert canonical_field_name("sipermarresi") == "contractor"
    assert canonical_field_name("kontrata_sipemarrjes") == "contractor_contract_reference"
    assert canonical_field_name("date_of_document") == "document_date"
    assert canonical_field_name("element_name") == "work_element"
