import uuid
from types import SimpleNamespace

from app.reviews.service import (
    CurrentFileSnapshot,
    RuleContext,
    _build_missing_document_findings,
    _found_document_types,
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
    assert findings[0].evidence["files_checked"] == []
