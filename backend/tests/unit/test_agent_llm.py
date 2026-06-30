import json
from io import BytesIO
from urllib import error

from app.agents.llm import (
    AIQuotaLimitError,
    _parse_model_json_object,
    _post_json,
    _response_format,
    _schema_response_format,
    document_analysis_reasoning_effort,
    kolaudim_draft_input_token_budget,
    kolaudim_draft_max_output_tokens,
    kolaudim_request_timeout_seconds,
    model_output_token_limit,
    model_request_token_limit,
    request_kolaudim_correction,
    request_kolaudim_draft,
    request_document_analysis,
    request_senior_review,
    request_specialist_review,
    request_structured_completion,
    specialist_review_input_token_budget,
    specialist_review_max_output_tokens,
)


class _FakeResponse:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def read(self) -> bytes:
        return json.dumps({"choices": []}).encode("utf-8")


def test_post_json_sends_provider_safe_headers(monkeypatch) -> None:
    captured = {}

    def fake_urlopen(api_request, timeout):
        captured["request"] = api_request
        captured["timeout"] = timeout
        return _FakeResponse()

    monkeypatch.setattr("app.agents.llm.request.urlopen", fake_urlopen)

    _post_json(
        "/chat/completions",
        {"model": "openai/gpt-oss-20b"},
        ai_settings={
            "base_url": "https://api.groq.com/openai/v1",
            "api_key": "gsk_test",
            "provider": "groq",
            "provider_label": "Groq",
        },
    )

    api_request = captured["request"]
    assert api_request.full_url == "https://api.groq.com/openai/v1/chat/completions"
    assert api_request.get_header("Authorization") == "Bearer gsk_test"
    assert api_request.get_header("Accept") == "application/json"
    assert api_request.get_header("Content-type") == "application/json"
    assert api_request.get_header("User-agent") == "AuditimiTeknikBot/0.1"


def test_post_json_retries_transient_provider_error(monkeypatch) -> None:
    attempts = 0

    def fake_urlopen(api_request, timeout):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise error.HTTPError(
                api_request.full_url,
                503,
                "Unavailable",
                {},
                BytesIO(b'{"error":"busy"}'),
            )
        return _FakeResponse()

    monkeypatch.setattr("app.agents.llm.request.urlopen", fake_urlopen)
    monkeypatch.setattr("app.agents.llm.time.sleep", lambda _: None)

    _post_json(
        "/chat/completions",
        {"model": "gemini-2.5-flash"},
        ai_settings={
            "base_url": "https://example.invalid/v1",
            "api_key": "test",
            "provider": "gemini",
        },
        retry_transient=True,
    )

    assert attempts == 3


def test_post_json_raises_quota_error_with_retry_after(monkeypatch) -> None:
    attempts = 0

    def fake_urlopen(api_request, timeout):
        nonlocal attempts
        attempts += 1
        raise error.HTTPError(
            api_request.full_url,
            429,
            "Too Many Requests",
            {"Retry-After": "37"},
            BytesIO(b'{"error":{"message":"quota exceeded"}}'),
        )

    monkeypatch.setattr("app.agents.llm.request.urlopen", fake_urlopen)

    try:
        _post_json(
            "/chat/completions",
            {"model": "gemini-2.5-flash"},
            ai_settings={
                "base_url": "https://example.invalid/v1",
                "api_key": "test",
                "provider": "gemini",
                "model": "gemini-2.5-flash",
            },
            retry_transient=True,
        )
    except AIQuotaLimitError as exc:
        assert exc.provider == "gemini"
        assert exc.model == "gemini-2.5-flash"
        assert exc.status_code == 429
        assert exc.retry_after_seconds == 37
    else:
        raise AssertionError("Expected quota error.")

    assert attempts == 1


def test_response_format_uses_json_object_for_groq() -> None:
    assert _response_format({"provider": "groq"}) == {"type": "json_object"}


def test_response_format_uses_strict_schema_for_groq_gpt_oss() -> None:
    response_format = _schema_response_format(
        {"provider": "groq", "model": "openai/gpt-oss-20b"},
        schema_name="test_schema",
        schema={
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "note": {
                    "anyOf": [{"type": "string"}, {"type": "null"}],
                    "default": None,
                },
            },
            "required": ["name"],
        },
    )

    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["strict"] is True
    strict_schema = response_format["json_schema"]["schema"]
    assert strict_schema["required"] == ["name", "note"]
    assert strict_schema["additionalProperties"] is False
    assert "default" not in strict_schema["properties"]["note"]


def test_response_format_keeps_json_object_for_unsupported_groq_model() -> None:
    response_format = _schema_response_format(
        {"provider": "groq", "model": "llama-3.3-70b-versatile"},
        schema_name="test_schema",
        schema={"type": "object", "properties": {}},
    )

    assert response_format == {"type": "json_object"}


def test_response_format_uses_strict_schema_for_gemini() -> None:
    response_format = _response_format({"provider": "gemini"})

    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["strict"] is True


def test_response_format_uses_strict_schema_for_other_providers() -> None:
    response_format = _response_format({"provider": "openai"})

    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["strict"] is True


def test_request_structured_completion_uses_bounded_schema_call(monkeypatch) -> None:
    captured = {}

    def fake_post_json(path, body, *, ai_settings, **kwargs):
        captured["path"] = path
        captured["body"] = body
        captured["kwargs"] = kwargs
        return {
            "choices": [{"message": {"content": '{"status":"ok"}'}}],
            "usage": {
                "prompt_tokens": 20,
                "completion_tokens": 5,
                "total_tokens": 25,
            },
        }

    monkeypatch.setattr("app.agents.llm._post_json", fake_post_json)

    payload, usage = request_structured_completion(
        system_prompt="Plan safely.",
        user_content="List projects.",
        schema_name="test_plan",
        schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["status"],
            "properties": {"status": {"type": "string"}},
        },
        ai_settings={
            "provider": "gemini",
            "model": "gemini-2.5-flash",
            "base_url": "https://example.invalid/v1",
            "api_key": "secret",
        },
        max_output_tokens=800,
    )

    assert captured["path"] == "/chat/completions"
    assert captured["body"]["max_tokens"] == 800
    assert captured["body"]["reasoning_effort"] == "none"
    assert captured["body"]["response_format"]["json_schema"]["name"] == "test_plan"
    assert captured["kwargs"]["retry_transient"] is True
    assert payload == {"status": "ok"}
    assert usage["total_tokens"] == 25


def test_request_senior_review_adds_output_shape_to_prompt(monkeypatch) -> None:
    captured = {}

    def fake_post_json(path, body, *, ai_settings):
        captured["path"] = path
        captured["body"] = body
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "status": "reviewed",
                                "executive_summary": "ok",
                            }
                        )
                    }
                }
            ]
        }

    monkeypatch.setattr("app.agents.llm._post_json", fake_post_json)

    review = request_senior_review(
        {"project": {"name": "Test"}},
        ai_settings={
            "provider": "groq",
            "model": "openai/gpt-oss-20b",
            "base_url": "https://api.groq.com/openai/v1",
            "api_key": "gsk_test",
        },
    )

    user_payload = json.loads(captured["body"]["messages"][1]["content"])
    assert captured["path"] == "/chat/completions"
    assert captured["body"]["response_format"]["type"] == "json_schema"
    assert captured["body"]["response_format"]["json_schema"]["strict"] is True
    assert "required_output_shape" in user_payload
    assert user_payload["audit_input"]["project"]["name"] == "Test"
    assert review["status"] == "reviewed"


def test_request_document_analysis_uses_structured_chunk_prompt(monkeypatch) -> None:
    captured = {}

    def fake_post_json(path, body, *, ai_settings, **kwargs):
        captured["path"] = path
        captured["body"] = body
        captured["kwargs"] = kwargs
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "status": "analyzed",
                                "document_summary": "Përmbledhje.",
                                "document_purpose": "Leje ndërtimi.",
                                "authoritative_role": "primary evidence",
                                "claims": [],
                                "limitations": [],
                            }
                        )
                    }
                }
            ],
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 20,
                "total_tokens": 120,
            },
        }

    monkeypatch.setattr("app.agents.llm._post_json", fake_post_json)

    analysis, usage = request_document_analysis(
        {
            "document": {"filename": "leje.pdf"},
            "chunks": [{"chunk_index": 0, "text": "Leja nr. 123"}],
        },
        ai_settings={
            "provider": "gemini",
            "model": "gemini-2.5-flash",
            "base_url": "https://example.invalid/v1beta/openai",
            "api_key": "test",
        },
    )

    user_payload = json.loads(captured["body"]["messages"][1]["content"])
    assert captured["path"] == "/chat/completions"
    assert captured["body"]["response_format"]["type"] == "json_schema"
    assert captured["body"]["max_tokens"] == 12_000
    assert captured["body"]["reasoning_effort"] == "none"
    assert captured["kwargs"]["retry_transient"] is True
    assert user_payload["analysis_input"]["chunks"][0]["chunk_index"] == 0
    assert analysis["status"] == "analyzed"
    assert usage["total_tokens"] == 120


def test_request_document_analysis_retries_truncated_json_once(monkeypatch) -> None:
    calls = []

    def fake_post_json(path, body, *, ai_settings, **kwargs):
        calls.append(body)
        if len(calls) == 1:
            return {
                "choices": [{"message": {"content": '{"status":"analyzed"'}}],
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 50,
                    "total_tokens": 150,
                },
            }
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "status": "analyzed",
                                "document_summary": "Përmbledhje.",
                                "document_purpose": "Kontratë.",
                                "authoritative_role": "primary evidence",
                                "claims": [],
                                "limitations": [],
                            }
                        )
                    }
                }
            ],
            "usage": {
                "prompt_tokens": 110,
                "completion_tokens": 30,
                "total_tokens": 140,
            },
        }

    monkeypatch.setattr("app.agents.llm._post_json", fake_post_json)

    analysis, usage = request_document_analysis(
        {
            "document": {"filename": "kontrate.docx"},
            "chunks": [{"chunk_index": 0, "text": "Kontratë mbikëqyrjeje"}],
        },
        ai_settings={
            "provider": "gemini",
            "model": "gemini-2.5-flash",
            "base_url": "https://example.invalid/v1beta/openai",
            "api_key": "test",
        },
    )

    assert len(calls) == 2
    assert len(calls[1]["messages"]) == 3
    assert "malformed or truncated" in calls[1]["messages"][2]["content"]
    assert analysis["status"] == "analyzed"
    assert usage == {
        "prompt_tokens": 210,
        "completion_tokens": 80,
        "total_tokens": 290,
    }


def test_document_analysis_reasoning_effort_is_model_aware() -> None:
    assert document_analysis_reasoning_effort(
        {"provider": "gemini", "model": "gemini-2.5-flash"}
    ) == "none"
    assert document_analysis_reasoning_effort(
        {"provider": "gemini", "model": "gemini-3-flash"}
    ) == "low"
    assert document_analysis_reasoning_effort(
        {"provider": "openai", "model": "gpt-4.1-mini"}
    ) is None


def test_request_specialist_review_uses_one_structured_provider_call(monkeypatch) -> None:
    captured = {}

    def fake_post_json(path, body, *, ai_settings, **kwargs):
        captured["path"] = path
        captured["body"] = body
        captured["kwargs"] = kwargs
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {"status": "reviewed", "memoranda": []}
                        )
                    }
                }
            ]
        }

    monkeypatch.setattr("app.agents.llm._post_json", fake_post_json)
    ai_settings = {
        "provider": "gemini",
        "model": "gemini-2.5-flash",
        "base_url": "https://example.invalid/v1beta/openai",
        "api_key": "test",
    }

    result = request_specialist_review(
        {"domains": [], "evidence_catalog": {}},
        ai_settings=ai_settings,
    )

    user_payload = json.loads(captured["body"]["messages"][1]["content"])
    assert captured["path"] == "/chat/completions"
    assert captured["body"]["response_format"]["type"] == "json_schema"
    assert captured["body"]["max_tokens"] == specialist_review_max_output_tokens(
        ai_settings
    )
    assert captured["kwargs"]["retry_transient"] is True
    assert "required_output_shape" in user_payload
    assert result["status"] == "reviewed"


def test_specialist_review_budget_is_dynamic() -> None:
    groq = {"provider": "groq", "model": "openai/gpt-oss-20b"}
    gemini = {"provider": "gemini", "model": "gemini-2.5-flash"}

    assert specialist_review_max_output_tokens(groq) <= 1_800
    assert specialist_review_input_token_budget(groq) < 8_000
    assert specialist_review_max_output_tokens(gemini) == 4_000
    assert specialist_review_input_token_budget(gemini) > 50_000


def test_request_kolaudim_draft_adds_output_shape_to_prompt(monkeypatch) -> None:
    captured = {}

    def fake_post_json(path, body, *, ai_settings, **kwargs):
        captured["path"] = path
        captured["body"] = body
        captured["post_kwargs"] = kwargs
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "status": "drafted",
                                "title": "Draft Akt Kolaudimi Teknik",
                                "executive_summary": {
                                    "text": "Draft.",
                                    "claim_type": "qualification",
                                    "conclusion_level": "qualified",
                                    "evidence_ids": ["integrity:0"],
                                    "confidence": 0.6,
                                },
                                "sections": [],
                                "reservations": [],
                                "human_completion_items": [],
                                "signature_note": "Për nënshkrim.",
                                "confidence": 0.7,
                            }
                        )
                    }
                }
            ]
        }

    monkeypatch.setattr("app.agents.llm._post_json", fake_post_json)

    draft = request_kolaudim_draft(
        {"project": {"name": "Test"}},
        ai_settings={
            "provider": "groq",
            "model": "openai/gpt-oss-20b",
            "base_url": "https://api.groq.com/openai/v1",
            "api_key": "gsk_test",
        },
    )

    user_payload = json.loads(captured["body"]["messages"][1]["content"])
    assert captured["path"] == "/chat/completions"
    assert captured["body"]["response_format"]["type"] == "json_schema"
    assert captured["body"]["response_format"]["json_schema"]["strict"] is True
    assert captured["body"]["max_tokens"] <= 1800
    assert captured["post_kwargs"]["retry_transient"] is True
    assert captured["post_kwargs"]["timeout_seconds"] >= 90
    assert "required_output_shape" in user_payload
    assert "paragraphs" in user_payload["required_output_shape"]["sections"][0]
    assert (
        user_payload["required_output_shape"]["executive_summary"][
            "conclusion_level"
        ]
        == "proven | qualified | not_proven"
    )
    assert user_payload["draft_input"]["project"]["name"] == "Test"
    assert draft["status"] == "drafted"


def test_request_kolaudim_correction_uses_grounded_schema(monkeypatch) -> None:
    captured = {}

    def fake_post_json(path, body, *, ai_settings, **kwargs):
        captured["path"] = path
        captured["body"] = body
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "status": "drafted",
                                "title": "AKT-KOLAUDIMI TEKNIKO-EKONOMIK",
                                "executive_summary": {
                                    "text": "Përmbledhje e korrigjuar.",
                                    "claim_type": "qualification",
                                    "conclusion_level": "qualified",
                                    "evidence_ids": ["integrity:0"],
                                    "confidence": 0.6,
                                },
                                "sections": [],
                                "reservations": [],
                                "human_completion_items": [],
                                "signature_note": "Për nënshkrim.",
                                "confidence": 0.7,
                            }
                        )
                    }
                }
            ]
        }

    monkeypatch.setattr("app.agents.llm._post_json", fake_post_json)

    result = request_kolaudim_correction(
        {
            "current_draft": {},
            "correction_issues": [{"code": "CLAIM-EVIDENCE-MISSING"}],
            "allowed_evidence_ids": ["integrity:0"],
            "budget": {"estimated_input_tokens": 100},
        },
        ai_settings={
            "provider": "gemini",
            "model": "gemini-2.5-flash",
            "base_url": "https://example.test/v1",
            "api_key": "test",
        },
    )

    payload = json.loads(captured["body"]["messages"][1]["content"])
    assert captured["path"] == "/chat/completions"
    assert "paragraphs" in payload["required_output_shape"]["sections"][0]
    assert (
        payload["required_output_shape"]["executive_summary"]["conclusion_level"]
        == "proven | qualified | not_proven"
    )
    assert payload["correction_input"]["correction_issues"][0]["code"] == (
        "CLAIM-EVIDENCE-MISSING"
    )
    assert result["status"] == "drafted"


def test_kolaudim_draft_budget_is_dynamic_for_groq() -> None:
    ai_settings = {"provider": "groq", "model": "openai/gpt-oss-20b"}

    assert model_request_token_limit(ai_settings) == 8000
    assert model_output_token_limit(ai_settings) == 1800
    assert kolaudim_draft_max_output_tokens(ai_settings) <= 1800
    assert kolaudim_draft_input_token_budget(ai_settings) < 8000


def test_kolaudim_draft_budget_allows_larger_gemini_output() -> None:
    ai_settings = {"provider": "gemini", "model": "gemini-2.5-flash"}

    assert model_request_token_limit(ai_settings) == 64000
    assert model_output_token_limit(ai_settings) == 12000
    assert kolaudim_draft_max_output_tokens(ai_settings) == 12000
    assert kolaudim_draft_input_token_budget(ai_settings) == 50600


def test_kolaudim_timeout_scales_with_large_prompt() -> None:
    ai_settings = {"provider": "gemini", "model": "gemini-2.5-flash"}

    timeout = kolaudim_request_timeout_seconds(
        {"budget": {"estimated_input_tokens": 45_000}},
        ai_settings=ai_settings,
    )

    assert timeout > 180
    assert timeout <= 300


def test_parse_model_json_object_accepts_markdown_fenced_json() -> None:
    parsed = _parse_model_json_object(
        '```json\n{"status":"drafted","sections":[]}\n```',
        error_label="AI kolaudim writer",
    )

    assert parsed["status"] == "drafted"


def test_parse_model_json_object_accepts_prefaced_json() -> None:
    parsed = _parse_model_json_object(
        'Ja drafti:\n{"status":"drafted","sections":[]}\n',
        error_label="AI kolaudim writer",
    )

    assert parsed["sections"] == []
