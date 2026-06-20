from app.ai.providers import AI_PROVIDERS, get_provider
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
