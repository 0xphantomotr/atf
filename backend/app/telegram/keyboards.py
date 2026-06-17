from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def start_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Krijo projekt", callback_data="project:create")],
            [InlineKeyboardButton(text="Projektet e mia", callback_data="project:list")],
            [InlineKeyboardButton(text="Ndihmë", callback_data="help")],
        ]
    )

