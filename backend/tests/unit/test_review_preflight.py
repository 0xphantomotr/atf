from app.reviews.preflight import build_generation_stages


def _ai_settings() -> dict:
    return {
        "provider": "gemini",
        "model": "gemini-2.5-flash",
        "stage_models": {
            "extraction": "gemini-2.5-flash-lite",
            "drafting": "gemini-3.1-flash-lite",
        },
    }


def test_kolaudim_preflight_routes_models_and_includes_bounded_correction() -> None:
    stages = build_generation_stages(
        job_type="kolaudim_act",
        require_ai_review=True,
        ai_settings=_ai_settings(),
        source_tokens=20_000,
        extraction_calls=3,
        extraction_input_tokens=12_000,
        extraction_output_tokens=6_000,
    )

    assert [stage["stage"] for stage in stages] == [
        "extraction",
        "synthesis",
        "drafting",
        "correction",
    ]
    assert stages[0]["model"] == "gemini-2.5-flash-lite"
    assert stages[1]["model"] == "gemini-2.5-flash"
    assert stages[2]["model"] == "gemini-3.1-flash-lite"
    assert stages[3]["model"] == "gemini-2.5-flash"
    assert stages[0]["estimated_calls"] == 3
    assert stages[3]["conditional"] is True
    assert sum(stage["estimated_calls"] for stage in stages) == 6


def test_preflight_has_no_ai_stages_when_ai_review_is_disabled() -> None:
    assert build_generation_stages(
        job_type="kolaudim_act",
        require_ai_review=False,
        ai_settings=_ai_settings(),
        source_tokens=10_000,
        extraction_calls=2,
        extraction_input_tokens=8_000,
        extraction_output_tokens=4_000,
    ) == []
