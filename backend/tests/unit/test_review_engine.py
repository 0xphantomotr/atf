import json
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

from app.core.config import settings
from app.reports.renderer import render_audit_report_html
from app.reviews.service import (
    CurrentFileSnapshot,
    RuleContext,
    _build_audit_report,
    _build_missing_document_findings,
    _found_document_types,
    _json_output_bytes,
    _output_metadata,
    _output_types_for_format,
    _report_output_basename,
    _required_document_types,
    _rule_applies_to_project,
)


def test_rule_applies_to_project_stage_and_type() -> None:
    rule = SimpleNamespace(
        applies_to={
            "project_type": ["residential"],
            "project_stage": ["during_construction"],
        }
    )
    project = SimpleNamespace(project_type="residential", stage="during_construction")

    assert _rule_applies_to_project(rule, project)


def test_rule_does_not_apply_to_other_project_stage() -> None:
    rule = SimpleNamespace(applies_to={"project_stage": ["completion_kolaudim"]})
    project = SimpleNamespace(project_type="residential", stage="during_construction")

    assert not _rule_applies_to_project(rule, project)


def test_required_document_types_ignores_non_string_values() -> None:
    rule = SimpleNamespace(required_documents={"document_types": ["site_book", 123, None]})

    assert _required_document_types(rule) == ["site_book"]


def test_found_document_types_only_counts_parsed_known_documents() -> None:
    snapshots = [
        CurrentFileSnapshot(
            file_id=uuid.uuid4(),
            version_id=uuid.uuid4(),
            original_filename="libri.pdf",
            parse_status="parsed",
            document_type="site_book",
            classification_confidence=0.84,
        ),
        CurrentFileSnapshot(
            file_id=uuid.uuid4(),
            version_id=uuid.uuid4(),
            original_filename="vkm.pdf",
            parse_status="parsed",
            document_type="unknown",
            classification_confidence=0.0,
        ),
        CurrentFileSnapshot(
            file_id=uuid.uuid4(),
            version_id=uuid.uuid4(),
            original_filename="docx.docx",
            parse_status="unsupported",
            document_type=None,
            classification_confidence=None,
        ),
    ]

    assert _found_document_types(snapshots) == {"site_book"}


def test_found_document_types_expands_compatible_aliases() -> None:
    snapshots = [
        CurrentFileSnapshot(
            file_id=uuid.uuid4(),
            version_id=uuid.uuid4(),
            original_filename="akt-kontroll-themele.docx",
            parse_status="parsed",
            document_type="foundation_completion_and_level_0_00_control_act",
            classification_confidence=0.99,
        ),
        CurrentFileSnapshot(
            file_id=uuid.uuid4(),
            version_id=uuid.uuid4(),
            original_filename="njoftim-fillim.docx",
            parse_status="parsed",
            document_type="start_works_notification",
            classification_confidence=0.98,
        ),
    ]

    assert _found_document_types(snapshots) == {
        "foundation_completion_and_level_0_00_control_act",
        "level_0_00_control_act",
        "start_works_notification",
        "start_works_notification_letter",
    }


def test_missing_document_finding_includes_rule_and_files_checked() -> None:
    job = SimpleNamespace(id=uuid.uuid4())
    project = SimpleNamespace(
        id=uuid.uuid4(),
        project_type="residential",
        stage="during_construction",
    )
    rule = SimpleNamespace(
        rule_code="VKM610-008-001",
        title="Raportimi 45-ditor",
        severity_if_missing="major",
        applies_to={"project_stage": ["during_construction"]},
        required_documents={"document_types": ["forty_five_day_report"]},
    )
    context = RuleContext(
        rule=rule,
        law_document=SimpleNamespace(code="VKM_610_2022"),
        law_article=SimpleNamespace(article_number="8"),
    )

    findings = _build_missing_document_findings(
        job=job,
        project=project,
        current_files=[],
        rule_contexts=[context],
    )

    assert len(findings) == 1
    assert findings[0].rule_code == "VKM610-008-001"
    assert findings[0].law_reference == "VKM 610/2022, Neni 8"
    assert findings[0].evidence["missing_document_types"] == ["forty_five_day_report"]
    assert findings[0].evidence["files_checked_count"] == 0
    assert "files_checked" not in findings[0].evidence


def test_audit_report_compacts_historical_files_checked_evidence() -> None:
    project = SimpleNamespace(
        id=uuid.uuid4(),
        name="Godine banimi",
        project_type="residential",
        stage="during_construction",
        location="Tirane",
    )
    job = SimpleNamespace(
        id=uuid.uuid4(),
        job_type="kolaudim_act",
        completed_at=None,
        law_scope={"codes": ["VKM_610_2022"]},
    )
    current_files = [
        CurrentFileSnapshot(
            file_id=uuid.uuid4(),
            version_id=uuid.uuid4(),
            original_filename="1.1 Njoftim Fillim Punimesh OK.docx",
            parse_status="parsed",
            document_type="start_works_notification",
            classification_confidence=0.98,
        ),
        CurrentFileSnapshot(
            file_id=uuid.uuid4(),
            version_id=uuid.uuid4(),
            original_filename="2.2 Njoftim perfundim.docx",
            parse_status="parsed",
            document_type="unknown",
            classification_confidence=0.0,
        ),
    ]
    finding = SimpleNamespace(
        severity="major",
        title="Mungon dokument",
        description="Mungon dokumenti.",
        law_reference="VKM 610/2022, Neni 8",
        rule_code="VKM610-008-001",
        evidence={
            "type": "missing_document",
            "files_checked": [{"filename": "a.pdf"}, {"filename": "b.pdf"}],
            "missing_document_types": ["forty_five_day_report"],
        },
        required_action="Ngarkoni raportimin 45-ditor.",
        confidence=0.95,
        status="open",
    )

    report = _build_audit_report(
        project=project,
        job=job,
        current_files=current_files,
        findings=[finding],
        agent_state={
            "extracted_facts": {"summary": {"fact_count": 1}},
            "vkm_obligation_map": {"summary": {"complete": 1}},
            "consistency_review": {"summary": {"issue_count": 0}},
            "kolaudim_analysis": {
                "readiness": "draft_with_reservations",
                "professional_conclusion": "Draft me rezerva.",
            },
            "kolaudim_draft": {
                "status": "drafted",
                "title": "Draft Akt Kolaudimi Teknik",
                "executive_summary": "Ky është drafti narrativ.",
                "sections": [
                    {
                        "code": "legal_basis",
                        "title": "Baza ligjore",
                        "body": "Drafti mbështetet në VKM 610/2022.",
                        "evidence_notes": ["VKM 610/2022"],
                    }
                ],
                "reservations": ["Ka gjetje të hapura."],
                "human_completion_items": ["Verifikoni palët."],
                "signature_note": "Për nënshkrim nga palët përgjegjëse.",
                "confidence": 0.75,
            },
            "report": {"phase": "professional_kolaudim_phase_1"},
        },
    )

    assert report.title == "Akt-Kolaudimi Tekniko-Ekonomik"
    assert report.summary.startswith("Ky është drafti narrativ.")
    assert report.professional_analysis["kolaudim_analysis"]["readiness"] == (
        "draft_with_reservations"
    )
    html = render_audit_report_html(report)
    assert "Akt-Kolaudimi Tekniko-Ekonomik" in html
    assert "Drafti mbështetet në VKM 610/2022." in html
    assert "Për nënshkrim nga palët përgjegjëse." in html
    assert "Dokumentet e kontrolluara" not in html
    assert "Tabela e gjetjeve" not in html
    assert "Shtojca - lista e dokumenteve" not in html
    assert report.project.name == "Godine banimi"
    assert report.document_summary.total_files == 2
    assert report.document_summary.classified_files == 1
    assert report.document_summary.unknown_files == 1
    assert report.findings[0].evidence["files_checked_count"] == 2
    assert "files_checked" not in report.findings[0].evidence


def test_kolaudim_report_date_uses_configured_local_timezone(monkeypatch) -> None:
    monkeypatch.setattr(settings, "app_timezone", "Europe/Tirane")
    report = _sample_kolaudim_report()
    report.generated_at = datetime(2026, 6, 24, 22, 11, tzinfo=timezone.utc)

    html = render_audit_report_html(report)

    assert "Hartuar më 25.06.2026" in html
    assert "Hartuar më 24.06.2026" not in html


def test_output_types_include_json_before_pdf() -> None:
    assert _output_types_for_format("json") == ("json",)
    assert _output_types_for_format("pdf") == ("json", "pdf")


def test_kolaudim_output_basename_is_clean() -> None:
    assert _report_output_basename(SimpleNamespace(job_type="kolaudim_act")) == (
        "akt-kolaudimi"
    )
    assert _report_output_basename(SimpleNamespace(job_type="documentation_checklist")) == (
        "audit-report"
    )


def test_kolaudim_json_output_is_clean_act_payload() -> None:
    report = _sample_kolaudim_report()
    job = SimpleNamespace(job_type="kolaudim_act")

    payload = json.loads(_json_output_bytes(job, report).decode("utf-8"))
    metadata = _output_metadata(
        job,
        report,
        filename="akt-kolaudimi.json",
        content_type="application/json; charset=utf-8",
        size_bytes=100,
    )

    assert payload["title"] == "Akt-Kolaudimi Tekniko-Ekonomik"
    assert payload["sections"][0]["title"] == "Baza ligjore"
    assert "document_summary" not in payload
    assert "findings" not in payload
    assert "required_actions" not in payload
    assert "agent_metadata" not in payload
    assert "evidence_notes" not in payload["sections"][0]
    assert "reservations" not in payload
    assert "human_completion_items" not in payload
    assert "verification" not in payload
    assert "confidence" not in payload
    assert "finding_count" not in metadata
    assert "recommendation" not in metadata
    assert "agent_trace" not in metadata


def _sample_kolaudim_report():
    project = SimpleNamespace(
        id=uuid.uuid4(),
        name="Godine banimi",
        project_type="residential",
        stage="during_construction",
        location="Tirane",
    )
    job = SimpleNamespace(
        id=uuid.uuid4(),
        job_type="kolaudim_act",
        completed_at=None,
        law_scope={"codes": ["VKM_610_2022"]},
    )
    return _build_audit_report(
        project=project,
        job=job,
        current_files=[],
        findings=[],
        agent_state={
            "job": {"job_type": "kolaudim_act"},
            "kolaudim_analysis": {
                "readiness": "draft_ready_for_human_review",
                "professional_conclusion": "Draft i aktit të kolaudimit.",
            },
            "kolaudim_draft": {
                "status": "drafted",
                "title": "Draft Akt Kolaudimi Teknik",
                "executive_summary": "Draft profesional.",
                "sections": [
                    {
                        "code": "legal_basis",
                        "title": "Baza ligjore",
                        "body": "Mbështetet në VKM 610/2022.",
                        "evidence_notes": ["VKM 610/2022"],
                    }
                ],
                "reservations": [],
                "human_completion_items": ["Plotësoni datën e aktit final."],
                "signature_note": "Për nënshkrim.",
                "confidence": 0.8,
            },
            "report": {"phase": "professional_kolaudim_phase_1"},
        },
    )
