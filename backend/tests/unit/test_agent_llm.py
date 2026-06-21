import json
from io import BytesIO
from urllib import error

from app.agents.llm import (
    _parse_model_json_object,
    _post_json,
    _response_format,
    kolaudim_draft_input_token_budget,
    kolaudim_draft_max_output_tokens,
    kolaudim_request_timeout_seconds,
    model_output_token_limit,
    model_request_token_limit,
    request_kolaudim_draft,
    request_senior_review,
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


def test_response_format_uses_json_object_for_groq() -> None:
    assert _response_format({"provider": "groq"}) == {"type": "json_object"}


def test_response_format_uses_json_object_for_gemini() -> None:
    assert _response_format({"provider": "gemini"}) == {"type": "json_object"}


def test_response_format_uses_strict_schema_for_other_providers() -> None:
    response_format = _response_format({"provider": "openai"})

    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["strict"] is True


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
    assert captured["body"]["response_format"] == {"type": "json_object"}
    assert "required_output_shape" in user_payload
    assert user_payload["audit_input"]["project"]["name"] == "Test"
    assert review["status"] == "reviewed"


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
                                "executive_summary": "Draft.",
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
    assert captured["body"]["response_format"] == {"type": "json_object"}
    assert captured["body"]["max_tokens"] <= 1800
    assert captured["post_kwargs"]["retry_transient"] is True
    assert captured["post_kwargs"]["timeout_seconds"] >= 90
    assert "required_output_shape" in user_payload
    assert user_payload["draft_input"]["project"]["name"] == "Test"
    assert draft["status"] == "drafted"


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
