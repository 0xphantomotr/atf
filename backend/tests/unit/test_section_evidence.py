from app.agents.nodes.kolaudim_corrector import (
    _apply_verification_replacements,
    _preserve_section_structure,
)
from app.agents.nodes.claim_verifier import verify_kolaudim_claims
from app.agents.section_evidence import (
    build_section_evidence,
    is_contextless_numeric_statement,
    is_material_boilerplate,
    is_public_register_entry,
)


def _source(filename: str) -> dict:
    return {
        "source_document": filename,
        "file_version_id": filename,
        "chunk_references": [{"chunk_id": f"chunk:{filename}", "excerpt": "tabela"}],
    }


def _dossier() -> dict:
    return {
        "canonical_facts": {},
        "conflicts": [],
        "registers": {
            "technical_works": [
                {
                    "field_name": "total_mass",
                    "value": "1861 kg",
                    "source_documents": ["Hekuri i Mureve-Model.pdf"],
                    "sources": [_source("Hekuri i Mureve-Model.pdf")],
                },
                {
                    "field_name": "total_mass",
                    "value": "5112 kg",
                    "source_documents": ["Hekuri i Trareve-Model.pdf"],
                    "sources": [_source("Hekuri i Trareve-Model.pdf")],
                },
                {
                    "field_name": "numeric_values",
                    "value": "2.30, 2.30, 1.20, 0.50, 0.30, 2.20",
                    "source_documents": ["Themelet.pdf"],
                    "sources": [_source("Themelet.pdf")],
                },
                {
                    "field_name": "rebar_mark_20",
                    "value": "20∅16120012001502841.0155.6%Trau",
                    "source_documents": ["Trari.pdf"],
                    "sources": [_source("Trari.pdf")],
                },
                {
                    "field_name": "iron_specification_1",
                    "value": "∅10, 600 cm, 324 copë, 1198.55 kg",
                    "source_documents": ["Tabela e armaturës.pdf"],
                    "sources": [_source("Tabela e armaturës.pdf")],
                },
            ],
            "materials_and_tests": [],
        },
    }


def test_section_evidence_aggregates_readable_reinforcement_quantities() -> None:
    material = build_section_evidence(_dossier())["materials_reinforcement"]

    assert material["statement"] == (
        "Dosja përmban tabela dhe specifikime armature për trarët dhe muret, "
        "përfshirë sasi të dokumentuara prej 5,112 kg dhe 1,861 kg. Këto "
        "dokumente provojnë ekzistencën e specifikimeve dhe sasive projektuese."
    )
    assert material["evidence_ids"] == ["technical_works:1", "technical_works:0"]


def test_public_register_filter_rejects_extraction_noise() -> None:
    entries = _dossier()["registers"]["technical_works"]

    assert is_public_register_entry(entries[0]) is True
    assert is_public_register_entry(entries[1]) is True
    assert is_public_register_entry(entries[2]) is False
    assert is_public_register_entry(entries[3]) is False
    assert is_public_register_entry(entries[4]) is True
    assert is_contextless_numeric_statement(
        "Parametrat janë: 2.30, 2.30, 1.20, 0.50, 0.30, 2.20."
    )


def test_material_boilerplate_is_detected() -> None:
    assert is_material_boilerplate(
        "Materialet e përdorura në ndërtim janë deklaruar në dosjen teknike, "
        "megjithatë provat laboratorike specifike kërkojnë verifikim të mëtejshëm."
    )


def test_verifier_requires_specific_material_section_evidence() -> None:
    generic = (
        "Materialet e përdorura në ndërtim janë deklaruar në dosjen teknike, "
        "megjithatë provat laboratorike specifike kërkojnë verifikim të mëtejshëm."
    )
    sections = [
        {
            "code": "quality_and_hidden_works" if index == 0 else f"section_{index}",
            "title": "Materialet" if index == 0 else f"Seksioni {index}",
            "body": generic if index == 0 else f"Përmbajtja e seksionit {index}.",
        }
        for index in range(10)
    ]
    ledger = [
        {
            "claim_id": "quality_and_hidden_works:0:0",
            "section_code": "quality_and_hidden_works",
            "statement": generic,
            "claim_type": "qualification",
            "conclusion_level": "qualified",
            "confidence": 0.7,
            "evidence_ids": ["technical_works:0"],
        },
        *[
            {
                "claim_id": f"section_{index}:0",
                "section_code": f"section_{index}",
                "statement": f"Përmbajtja e seksionit {index}.",
                "claim_type": "documented_fact",
                "conclusion_level": "proven",
                "confidence": 0.8,
                "evidence_ids": ["technical_works:0"],
            }
            for index in range(1, 10)
        ],
    ]
    state = {
        "agent_trace": [],
        "professional_dossier": _dossier(),
        "kolaudim_draft": {
            "status": "drafted",
            "title": "AKT-KOLAUDIMI TEKNIKO-EKONOMIK",
            "executive_summary": "Përmbledhje e dokumentuar.",
            "sections": sections,
            "claim_ledger": [
                {
                    "claim_id": "executive_summary:0",
                    "section_code": "executive_summary",
                    "statement": "Përmbledhje e dokumentuar.",
                    "claim_type": "documented_fact",
                    "conclusion_level": "proven",
                    "confidence": 0.8,
                    "evidence_ids": ["technical_works:0"],
                },
                *ledger,
            ],
        },
    }

    result = verify_kolaudim_claims(state)["claim_verification"]
    codes = {issue["code"] for issue in result["issues"]}

    assert "PUBLIC-SECTION-EVIDENCE-MISSING" in codes
    assert "PUBLIC-MATERIAL-GENERIC" in codes


def test_deterministic_repair_replaces_generic_material_paragraph() -> None:
    dossier = _dossier()
    material = build_section_evidence(dossier)["materials_reinforcement"]
    generic = (
        "Materialet e përdorura në ndërtim janë deklaruar në dosjen teknike, "
        "megjithatë provat laboratorike specifike kërkojnë verifikim të mëtejshëm."
    )
    draft = {
        "executive_summary": "Përmbledhje.",
        "sections": [
            {
                "code": "quality_and_hidden_works",
                "title": "Materialet",
                "body": generic,
            }
        ],
        "claim_ledger": [
            {
                "claim_id": "quality_and_hidden_works:0:0",
                "section_code": "quality_and_hidden_works",
                "statement": generic,
                "claim_type": "qualification",
                "conclusion_level": "qualified",
                "evidence_ids": ["technical_works:0"],
                "confidence": 0.7,
            }
        ],
    }
    issues = {
        "correction_instructions": [
            {
                "code": "PUBLIC-MATERIAL-GENERIC",
                "claim_id": "quality_and_hidden_works:0:0",
            },
            {
                "code": "PUBLIC-SECTION-EVIDENCE-MISSING",
                "section_code": material["section_code"],
                "section_title": material["section_title"],
                "required_statement": material["statement"],
                "required_values": [
                    item["source_value"] for item in material["quantities"]
                ],
                "evidence_ids": material["evidence_ids"],
            },
        ]
    }

    _apply_verification_replacements(draft, issues, evidence_catalog={})

    assert generic not in draft["sections"][0]["body"]
    assert material["statement"] in draft["sections"][0]["body"]
    assert draft["claim_ledger"][-1]["evidence_ids"] == material["evidence_ids"]


def test_correction_cannot_drop_or_add_public_sections() -> None:
    original_sections = [
        {"code": f"section_{index}", "title": f"Seksioni {index}", "body": f"Origjinal {index}."}
        for index in range(10)
    ]
    original = {
        "sections": original_sections,
        "claim_ledger": [
            {
                "claim_id": f"section_{index}:0",
                "section_code": f"section_{index}",
                "statement": f"Origjinal {index}.",
                "claim_type": "documented_fact",
                "conclusion_level": "proven",
                "evidence_ids": ["technical_works:0"],
                "confidence": 0.8,
            }
            for index in range(10)
        ],
    }
    corrected = {
        "executive_summary": "",
        "sections": [
            {
                "code": f"section_{index}",
                "title": f"Seksioni {index}",
                "body": f"Korrigjuar {index}.",
            }
            for index in range(9)
        ]
        + [
            {"code": "extra_a", "title": "Shtesë A", "body": "A."},
            {"code": "extra_b", "title": "Shtesë B", "body": "B."},
            {"code": "extra_c", "title": "Shtesë C", "body": "C."},
            {"code": "extra_d", "title": "Shtesë D", "body": "D."},
        ],
        "claim_ledger": [
            {
                "claim_id": f"corrected:{index}",
                "section_code": f"section_{index}",
                "statement": f"Korrigjuar {index}.",
                "claim_type": "documented_fact",
                "conclusion_level": "proven",
                "evidence_ids": ["technical_works:0"],
                "confidence": 0.8,
            }
            for index in range(9)
        ],
    }

    _preserve_section_structure(corrected, original_draft=original)

    assert [section["code"] for section in corrected["sections"]] == [
        f"section_{index}" for index in range(10)
    ]
    assert corrected["sections"][-1]["body"] == "Origjinal 9."
    assert corrected["claim_ledger"][-1]["statement"] == "Origjinal 9."
