import pytest

pytest.importorskip("aiogram")

from app.telegram.handlers.start import _help_text
from app.telegram.keyboards import start_keyboard
from app.telegram.messages import WELCOME_MESSAGE


def test_start_menu_exposes_primary_workflow_sections() -> None:
    callbacks = {
        button.callback_data
        for row in start_keyboard().inline_keyboard
        for button in row
    }

    assert {
        "project:create",
        "project:list",
        "menu:dossier",
        "menu:drive",
        "menu:prompt",
        "ai:settings",
        "menu:review",
        "help",
    } == callbacks


def test_start_content_organizes_the_recommended_workflow() -> None:
    assert "Rrjedha e rekomanduar" in WELCOME_MESSAGE
    assert "/prompt" in WELCOME_MESSAGE

    help_text = _help_text()
    for section in (
        "PROJEKTET",
        "DOSJA TEKNIKE",
        "ASISTENTI",
        "GJENERIMI",
        "GOOGLE DRIVE",
        "KONFIGURIMI AI",
    ):
        assert section in help_text
