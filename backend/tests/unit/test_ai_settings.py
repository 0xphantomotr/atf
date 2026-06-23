from fastapi import HTTPException

from app.ai.providers import AIProviderError, AI_PROVIDERS, get_provider
from app.ai.service import _validated_models
from app.ai.stages import ai_settings_for_stage, normalize_ai_stage, resolved_stage_models
from app.core.security import decrypt_secret, encrypt_secret, secret_hint


def test_supported_ai_providers_include_initial_targets() -> None:
    assert {"openai", "gemini", "groq"}.issubset(AI_PROVIDERS)
    assert get_provider("openai").base_url == "https://api.openai.com/v1"
    assert "openai/gpt-oss-20b" in get_provider("groq").curated_models


def test_secret_encryption_round_trips_without_plaintext_leak() -> None:
    secret = "sk-test-secret-value"

    encrypted = encrypt_secret(secret)

    assert encrypted != secret
    assert secret not in encrypted
    assert decrypt_secret(encrypted) == secret
    assert secret_hint(secret) == "sk-...alue"


def test_model_validation_falls_back_to_curated_models_for_blocked_model_list(
    monkeypatch,
) -> None:
    def blocked_model_list(*args, **kwargs):
        raise AIProviderError("blocked", status_code=403, allow_curated_fallback=True)

    monkeypatch.setattr("app.ai.service.list_provider_models", blocked_model_list)

    assert _validated_models("groq", "gsk_test") == list(get_provider("groq").curated_models)


def test_model_validation_rejects_auth_errors(monkeypatch) -> None:
    def unauthorized_model_list(*args, **kwargs):
        raise AIProviderError("unauthorized", status_code=401)

    monkeypatch.setattr("app.ai.service.list_provider_models", unauthorized_model_list)

    try:
        _validated_models("groq", "bad-key")
    except HTTPException as exc:
        assert exc.status_code == 400
        assert exc.detail == "unauthorized"
    else:
        raise AssertionError("Expected HTTPException")


def test_stage_models_fall_back_to_default_and_apply_overrides() -> None:
    ai_settings = {
        "provider": "gemini",
        "model": "gemini-2.5-flash",
        "stage_models": {
            "extraction": "gemini-2.5-flash-lite",
            "drafting": "gemini-3.1-flash-lite",
            "unknown": "ignored",
        },
    }

    assert resolved_stage_models(ai_settings) == {
        "extraction": "gemini-2.5-flash-lite",
        "synthesis": "gemini-2.5-flash",
        "drafting": "gemini-3.1-flash-lite",
        "correction": "gemini-2.5-flash",
    }
    assert ai_settings_for_stage(ai_settings, "drafting")["stage"] == "drafting"


def test_invalid_ai_stage_is_rejected() -> None:
    try:
        normalize_ai_stage("reporting")
    except ValueError as exc:
        assert "extraction" in str(exc)
    else:
        raise AssertionError("Expected invalid stage to be rejected")
