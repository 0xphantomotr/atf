import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.agents.llm import LLMReviewError
from app.db.base import Base
from app.db import models as db_models  # noqa: F401
from app.prompting.planner import plan_prompt
from app.prompting.confirmation import (
    confirmation_callback_data,
    parse_confirmation_callback,
)
from app.prompting.generation import (
    generation_estimate_fingerprint,
    report_output_matches_prompt_job,
)
from app.prompting.parsing import (
    format_prompt_parse_summary,
    summarize_prompt_parse_records,
)
from app.prompting.policy import PromptPolicyError, validate_prompt_plan
from app.prompting.models import PromptRun
from app.prompting.schemas import (
    PromptPlan,
    PromptPlanningContext,
    PromptProjectContext,
)
from app.prompting.security import (
    SECRET_REPLACEMENT,
    contains_likely_secret,
    redact_likely_secrets,
)
from app.prompting.status import generation_prompt_controls_latest_job
from app.prompting.service import (
    pending_prompt_notifications,
    queue_prompt_notification,
)


def _plan_payload(actions: list[dict]) -> dict:
    return {
        "version": "prompt-plan-v1",
        "language": "sq-AL",
        "needs_clarification": False,
        "clarification_question": None,
        "actions": actions,
    }


def _action(
    action_type: str,
    *,
    step: int = 1,
    name: str | None = None,
    depends_on: list[str] | None = None,
    requires_confirmation: bool = False,
    job_ref: str | None = None,
) -> dict:
    return {
        "id": f"step-{step}",
        "type": action_type,
        "arguments": {"name": name, "job_ref": job_ref},
        "depends_on": depends_on or [],
        "requires_confirmation": requires_confirmation,
    }


def test_prompt_plan_is_strict_and_normalizes_project_name() -> None:
    plan = PromptPlan.model_validate(
        _plan_payload([_action("create_project", name="  Test   Dosja Teknike  ")])
    )

    assert plan.actions[0].arguments.name == "Test Dosja Teknike"

    invalid = _plan_payload([_action("list_projects")])
    invalid["unexpected"] = True
    with pytest.raises(ValidationError):
        PromptPlan.model_validate(invalid)

    schema = PromptPlan.model_json_schema()
    assert set(schema["required"]) == {
        "version",
        "language",
        "needs_clarification",
        "clarification_question",
        "actions",
    }


def test_prompt_plan_requires_action_specific_arguments() -> None:
    with pytest.raises(ValidationError):
        PromptPlan.model_validate(_plan_payload([_action("create_project")]))

    with pytest.raises(ValidationError):
        PromptPlan.model_validate(
            _plan_payload([_action("show_active_project", name="Not allowed")])
        )


def test_clarification_plan_cannot_execute_actions() -> None:
    with pytest.raises(ValidationError):
        PromptPlan.model_validate(
            {
                "version": "prompt-plan-v1",
                "language": "sq-AL",
                "needs_clarification": True,
                "clarification_question": "Cilin projekt dëshironi?",
                "actions": [_action("list_projects")],
            }
        )


def test_policy_rejects_forward_dependencies_and_confirmation() -> None:
    forward_dependency = PromptPlan.model_validate(
        _plan_payload(
            [
                _action(
                    "show_active_project",
                    step=1,
                    depends_on=["step-2"],
                ),
                _action("list_projects", step=2),
            ]
        )
    )
    with pytest.raises(PromptPolicyError) as dependency_error:
        validate_prompt_plan(forward_dependency)
    assert dependency_error.value.code == "invalid_dependency"

    confirmation = PromptPlan.model_validate(
        _plan_payload(
            [_action("list_projects", requires_confirmation=True)]
        )
    )
    with pytest.raises(PromptPolicyError) as confirmation_error:
        validate_prompt_plan(confirmation)
    assert confirmation_error.value.code == "unexpected_confirmation"


def test_attachment_import_policy_requires_exactly_one_attachment_action() -> None:
    valid_plan = PromptPlan.model_validate(
        _plan_payload(
            [
                _action("create_project", step=1, name="Dosja A"),
                _action(
                    "import_attachment",
                    step=2,
                    depends_on=["step-1"],
                ),
            ]
        )
    )
    validate_prompt_plan(valid_plan, has_attachment=True)

    with pytest.raises(PromptPolicyError) as missing_import:
        validate_prompt_plan(
            PromptPlan.model_validate(
                _plan_payload([_action("show_active_project")])
            ),
            has_attachment=True,
        )
    assert missing_import.value.code == "attachment_import_missing"

    with pytest.raises(PromptPolicyError) as missing_attachment:
        validate_prompt_plan(valid_plan, has_attachment=False)
    assert missing_attachment.value.code == "attachment_missing"

    import_then_estimate = PromptPlan.model_validate(
        _plan_payload(
            [
                _action("import_attachment", step=1),
                _action(
                    "estimate_kolaudim",
                    step=2,
                    depends_on=["step-1"],
                ),
            ]
        )
    )
    validate_prompt_plan(import_then_estimate, has_attachment=True)


def test_generation_plan_requires_estimate_confirmation_and_exact_delivery_ref() -> None:
    valid_plan = PromptPlan.model_validate(
        _plan_payload(
            [
                _action("estimate_kolaudim", step=1),
                _action(
                    "generate_kolaudim",
                    step=2,
                    depends_on=["step-1"],
                    requires_confirmation=True,
                ),
                _action(
                    "deliver_latest_report",
                    step=3,
                    depends_on=["step-2"],
                    job_ref="step-2",
                ),
            ]
        )
    )
    validate_prompt_plan(valid_plan)

    no_confirmation = PromptPlan.model_validate(
        _plan_payload(
            [
                _action("estimate_kolaudim", step=1),
                _action(
                    "generate_kolaudim",
                    step=2,
                    depends_on=["step-1"],
                ),
            ]
        )
    )
    with pytest.raises(PromptPolicyError) as confirmation_error:
        validate_prompt_plan(no_confirmation)
    assert confirmation_error.value.code == "generation_confirmation_missing"

    wrong_report_ref = valid_plan.model_copy(deep=True)
    wrong_report_ref.actions[2].arguments.job_ref = "step-1"
    with pytest.raises(PromptPolicyError) as report_error:
        validate_prompt_plan(wrong_report_ref)
    assert report_error.value.code == "report_job_reference_invalid"


def test_secret_detection_covers_supported_provider_and_bot_tokens() -> None:
    values = [
        "perdor sk-1234567890abcdefghijklmnop",
        "perdor gsk_1234567890abcdefghijklmnop",
        "api key: AIza1234567890abcdefghijklmnop",
        "token=123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef",
    ]

    for value in values:
        assert contains_likely_secret(value)
        assert SECRET_REPLACEMENT in redact_likely_secrets(value)

    assert not contains_likely_secret("Shfaq konfigurimin e API key pa e zbuluar atë.")


def test_planner_retries_one_invalid_structured_response() -> None:
    calls: list[dict] = []

    def fake_request(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return _plan_payload([_action("create_project")]), {
                "total_tokens": 10
            }
        return _plan_payload(
            [_action("create_project", name="Test Dosja Teknike")]
        ), {"total_tokens": 12}

    result = plan_prompt(
        "Krijo projektin Test Dosja Teknike",
        context=PromptPlanningContext(
            projects=[],
            has_ai_settings=True,
            has_attachment=False,
        ),
        ai_settings={
            "provider": "gemini",
            "model": "gemini-2.5-flash",
        },
        request_fn=fake_request,
    )

    assert len(calls) == 2
    assert "previous response was invalid" in calls[1]["user_content"]
    assert result.plan.actions[0].arguments.name == "Test Dosja Teknike"
    assert result.token_usage["total_tokens"] == 22


def test_planner_owns_version_and_language_metadata() -> None:
    def fake_request(**kwargs):
        payload = _plan_payload([_action("list_projects")])
        payload["version"] = "1.0"
        payload["language"] = "sq"
        return payload, {}

    result = plan_prompt(
        "Shfaq projektet",
        context=PromptPlanningContext(
            projects=[],
            has_ai_settings=True,
            has_attachment=False,
        ),
        ai_settings={"provider": "gemini", "model": "gemini-3.1-flash-lite"},
        request_fn=fake_request,
    )

    assert result.plan.version == "prompt-plan-v1"
    assert result.plan.language == "sq-AL"


def test_planner_discards_irrelevant_names_from_non_project_actions() -> None:
    def fake_request(**kwargs):
        payload = _plan_payload([_action("import_attachment", name="Dosja A")])
        return payload, {}

    result = plan_prompt(
        "Importo attachment-in",
        context=PromptPlanningContext(
            projects=[PromptProjectContext(name="Dosja A", is_active=True)],
            has_ai_settings=True,
            has_attachment=True,
        ),
        ai_settings={"provider": "gemini", "model": "gemini-3.1-flash-lite"},
        request_fn=fake_request,
    )

    assert result.plan.actions[0].arguments.name is None


def test_planner_owns_generation_confirmation_and_delivery_reference() -> None:
    def fake_request(**kwargs):
        return _plan_payload(
            [
                _action("estimate_kolaudim", step=1),
                _action(
                    "generate_kolaudim",
                    step=2,
                    depends_on=["step-1"],
                ),
                _action(
                    "deliver_latest_report",
                    step=3,
                    depends_on=["step-2"],
                ),
            ]
        ), {}

    result = plan_prompt(
        "Gjenero Akt-Kolaudimin dhe ma dërgo PDF-në",
        context=PromptPlanningContext(
            projects=[PromptProjectContext(name="Dosja A", is_active=True)],
            has_ai_settings=True,
            has_attachment=False,
        ),
        ai_settings={"provider": "gemini", "model": "gemini-3.1-flash-lite"},
        request_fn=fake_request,
    )

    assert result.plan.actions[1].requires_confirmation
    assert result.plan.actions[2].arguments.job_ref == "step-2"


def test_planner_does_not_retry_provider_failures() -> None:
    calls = 0

    def fake_request(**kwargs):
        nonlocal calls
        calls += 1
        raise LLMReviewError("Gemini API request failed: 401 unauthorized")

    with pytest.raises(LLMReviewError):
        plan_prompt(
            "Shfaq projektet",
            context=PromptPlanningContext(
                projects=[
                    PromptProjectContext(name="Test", is_active=True),
                ],
                has_ai_settings=True,
                has_attachment=False,
            ),
            ai_settings={"provider": "gemini", "model": "gemini-2.5-flash"},
            request_fn=fake_request,
        )

    assert calls == 1


def test_planner_context_contains_no_project_ids_or_credentials() -> None:
    captured = {}

    def fake_request(**kwargs):
        captured.update(kwargs)
        return _plan_payload([_action("list_projects")]), {}

    plan_prompt(
        "Shfaq projektet",
        context=PromptPlanningContext(
            projects=[PromptProjectContext(name="Dosja A", is_active=True)],
            has_ai_settings=True,
            has_attachment=False,
        ),
        ai_settings={"provider": "groq", "model": "model", "api_key": "secret"},
        request_fn=fake_request,
    )

    payload = json.loads(captured["user_content"])
    assert payload["context"]["projects"] == [
        {"name": "Dosja A", "is_active": True}
    ]
    assert "api_key" not in captured["user_content"]
    assert "secret" not in captured["user_content"]


def test_prompt_tables_are_registered_in_sqlalchemy_metadata() -> None:
    assert "prompt_runs" in Base.metadata.tables
    assert "prompt_run_steps" in Base.metadata.tables
    assert "worker_lease_until" in Base.metadata.tables["prompt_runs"].columns
    assert "worker_attempt_count" in Base.metadata.tables["prompt_runs"].columns
    assert "confirmation_expires_at" in Base.metadata.tables["prompt_runs"].columns
    assert "prompt_run_id" in Base.metadata.tables["review_jobs"].columns


def test_prompt_notifications_are_persisted_idempotently_in_metadata() -> None:
    run = PromptRun(
        user_id=uuid4(),
        telegram_chat_id=100,
        telegram_message_id=200,
        status="waiting_for_documents",
        original_prompt="importo dosjen",
        plan={},
        planner_metadata={},
        attachment_metadata={"notifications": {}},
        pending_clarification={},
    )

    queue_prompt_notification(run, key="completed", body="Përfundoi.")
    queue_prompt_notification(run, key="completed", body="Duplikat.")

    pending = pending_prompt_notifications(run)
    assert len(pending) == 1
    assert pending[0].key == "completed"
    assert pending[0].kind == "text"
    assert pending[0].body == "Përfundoi."


def test_confirmation_callback_is_short_and_round_trips_run_id() -> None:
    run = PromptRun(
        id=uuid4(),
        user_id=uuid4(),
        telegram_chat_id=100,
        telegram_message_id=200,
        status="waiting_for_confirmation",
        original_prompt="gjenero",
        plan={},
        planner_metadata={},
        attachment_metadata={
            "confirmation": {"estimate_fingerprint": "a" * 64}
        },
        pending_clarification={},
        confirmation_expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
    )

    value = confirmation_callback_data(run, action="confirm")
    action, run_id, token = parse_confirmation_callback(value)

    assert len(value.encode("utf-8")) <= 64
    assert action == "confirm"
    assert run_id == run.id
    assert token


def test_generation_estimate_fingerprint_ignores_timestamp_only() -> None:
    first = {
        "generated_at": "2026-06-29T10:00:00Z",
        "project_id": str(uuid4()),
        "totals": {"estimated_calls": 10},
    }
    second = {**first, "generated_at": "2026-06-29T10:01:00Z"}
    changed = {**second, "totals": {"estimated_calls": 11}}

    assert generation_estimate_fingerprint(first) == generation_estimate_fingerprint(second)
    assert generation_estimate_fingerprint(first) != generation_estimate_fingerprint(changed)


def test_new_generation_prompt_blocks_stale_review_job() -> None:
    now = datetime.now(timezone.utc)
    run = SimpleNamespace(
        plan={"actions": [{"type": "generate_kolaudim"}]},
        review_job_id=None,
        created_at=now,
    )
    old_job = SimpleNamespace(id=uuid4(), created_at=now - timedelta(minutes=5))
    new_job = SimpleNamespace(id=uuid4(), created_at=now + timedelta(minutes=5))

    assert generation_prompt_controls_latest_job(run, old_job)
    assert not generation_prompt_controls_latest_job(run, new_job)


def test_report_output_must_match_the_prompt_review_job() -> None:
    linked_job_id = uuid4()

    assert report_output_matches_prompt_job(
        prompt_review_job_id=linked_job_id,
        expected_review_job_id=linked_job_id,
        output_review_job_id=linked_job_id,
    )
    assert not report_output_matches_prompt_job(
        prompt_review_job_id=linked_job_id,
        expected_review_job_id=linked_job_id,
        output_review_job_id=uuid4(),
    )
    assert not report_output_matches_prompt_job(
        prompt_review_job_id=None,
        expected_review_job_id=linked_job_id,
        output_review_job_id=linked_job_id,
    )


def test_prompt_parse_summary_waits_for_in_progress_versions() -> None:
    summary = summarize_prompt_parse_records(
        [
            {"filename": "a.pdf", "status": "parsed"},
            {"filename": "b.docx", "status": "processing"},
        ]
    )

    assert not summary.complete
    assert summary.readable_count == 1
    assert summary.counts["processing"] == 1


def test_prompt_parse_summary_reports_terminal_outcomes() -> None:
    summary = summarize_prompt_parse_records(
        [
            {"filename": "a.pdf", "status": "parsed"},
            {"filename": "b.pdf", "status": "parsed_with_ocr"},
            {"filename": "c.pdf", "status": "needs_ocr"},
            {"filename": "d.xlsx", "status": "unsupported"},
            {"filename": "e.docx", "status": "failed"},
            {"filename": "f.docx", "status": "empty"},
        ]
    )

    assert summary.complete
    assert summary.readable_count == 2
    message = format_prompt_parse_summary(
        summary,
        project_name="Dosja A",
        skipped_count=3,
    )
    assert "Të lexuara: 1" in message
    assert "Të lexuara me OCR: 1" in message
    assert "Kërkojnë OCR/verifikim: 1" in message
    assert "Formate të papërpunuara: 1" in message
    assert "Dështuan: 1" in message
    assert "Të anashkaluara nga ZIP: 3" in message


def test_missing_prompt_file_version_keeps_summary_incomplete() -> None:
    summary = summarize_prompt_parse_records(
        [{"filename": "a.pdf", "status": "parsed"}],
        expected_total=2,
    )

    assert not summary.complete
    assert summary.missing_version_count == 1
