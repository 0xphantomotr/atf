import json

from app.agents.llm import (
    _post_json,
    _response_format,
    kolaudim_draft_input_token_budget,
    kolaudim_draft_max_output_tokens,
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


def test_response_format_uses_json_object_for_groq() -> None:
    assert _response_format({"provider": "groq"}) == {"type": "json_object"}


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

    def fake_post_json(path, body, *, ai_settings):
        captured["path"] = path
        captured["body"] = body
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
    assert "required_output_shape" in user_payload
    assert user_payload["draft_input"]["project"]["name"] == "Test"
    assert draft["status"] == "drafted"


def test_kolaudim_draft_budget_is_dynamic_for_groq() -> None:
    ai_settings = {"provider": "groq", "model": "openai/gpt-oss-20b"}

    assert model_request_token_limit(ai_settings) == 8000
    assert kolaudim_draft_max_output_tokens(ai_settings) <= 1800
    assert kolaudim_draft_input_token_budget(ai_settings) < 8000
