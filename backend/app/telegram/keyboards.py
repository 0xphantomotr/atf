from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def start_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Krijo projekt", callback_data="project:create"),
                InlineKeyboardButton(text="Projektet", callback_data="project:list"),
            ],
            [
                InlineKeyboardButton(text="Dosja teknike", callback_data="menu:dossier"),
                InlineKeyboardButton(text="Google Drive", callback_data="menu:drive"),
            ],
            [
                InlineKeyboardButton(text="Pyet me /prompt", callback_data="menu:prompt"),
                InlineKeyboardButton(text="AI", callback_data="ai:settings"),
            ],
            [
                InlineKeyboardButton(
                    text="Gjenerimi dhe raportet",
                    callback_data="menu:review",
                ),
            ],
            [InlineKeyboardButton(text="Të gjitha komandat", callback_data="help")],
        ]
    )


def menu_back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Kthehu te menuja", callback_data="menu:main")]
        ]
    )
