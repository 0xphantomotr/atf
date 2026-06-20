from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def start_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Krijo projekt", callback_data="project:create")],
            [InlineKeyboardButton(text="Projektet e mia", callback_data="project:list")],
            [InlineKeyboardButton(text="AI settings", callback_data="ai:settings")],
            [InlineKeyboardButton(text="Ndihmë", callback_data="help")],
        ]
    )
