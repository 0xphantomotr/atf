from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message

from app.db.session import AsyncSessionLocal
from app.telegram.keyboards import menu_back_keyboard, start_keyboard
from app.telegram.messages import WELCOME_MESSAGE
from app.telegram.service import get_or_create_message_user

router = Router()


@router.message(CommandStart())
async def start(message: Message) -> None:
    async with AsyncSessionLocal() as session:
        await get_or_create_message_user(session, message)
    await message.answer(WELCOME_MESSAGE, reply_markup=start_keyboard())


@router.message(Command("ndihme", "help"))
async def help_command(message: Message) -> None:
    await message.answer(_help_text(), reply_markup=start_keyboard())


@router.callback_query(F.data == "help")
async def help_callback(callback: CallbackQuery) -> None:
    if callback.message:
        await callback.message.answer(_help_text(), reply_markup=menu_back_keyboard())
    await callback.answer()


@router.callback_query(F.data == "menu:main")
async def main_menu_callback(callback: CallbackQuery) -> None:
    if callback.message:
        await callback.message.answer(WELCOME_MESSAGE, reply_markup=start_keyboard())
    await callback.answer()


@router.callback_query(F.data == "menu:dossier")
async def dossier_menu_callback(callback: CallbackQuery) -> None:
    if callback.message:
        await callback.message.answer(
            "Dosja teknike\n\n"
            "Ngarkim nga Telegram:\n"
            "- Dërgoni ZIP, PDF, DOCX, XLSX, JPG ose PNG në bisedë.\n"
            "- Dokumentet ruhen te projekti aktiv.\n\n"
            "Komandat:\n"
            "/ngarko - udhëzimet e ngarkimit\n"
            "/dokumentet - dokumentet e projektit aktiv\n"
            "/projektet - zgjidhni projektin aktiv\n\n"
            "Për import nga Google Drive, hapni seksionin Google Drive.",
            reply_markup=menu_back_keyboard(),
        )
    await callback.answer()


@router.callback_query(F.data == "menu:drive")
async def drive_menu_callback(callback: CallbackQuery) -> None:
    if callback.message:
        await callback.message.answer(
            "Google Drive\n\n"
            "Lidhni llogarinë dhe folderin e dosjes teknike me projektin aktiv. "
            "Sinkronizimi shkarkon vetëm dokumentet e reja ose të ndryshuara.\n\n"
            "/google_connect - lidhni llogarinë Google\n"
            "/google_folder LINKU - lidhni folderin me projektin\n"
            "/google_check - kontrolloni aksesin dhe ndryshimet\n"
            "/google_sync - sinkronizoni folderin\n"
            "/google_status - shfaqni lidhjen aktive\n"
            "/google_unbind - shkëputni folderin nga projekti\n"
            "/google_disconnect - shkëputni llogarinë Google",
            reply_markup=menu_back_keyboard(),
        )
    await callback.answer()


@router.callback_query(F.data == "menu:prompt")
async def prompt_menu_callback(callback: CallbackQuery) -> None:
    if callback.message:
        await callback.message.answer(
            "Asistenti /prompt\n\n"
            "Përdoreni për të menaxhuar projektet, pyetur dosjen me burime, "
            "sinkronizuar Drive-in dhe gjeneruar Draft Akt Kolaudimi.\n\n"
            "Shembuj:\n"
            "/prompt Shfaq projektet e mia\n"
            "/prompt Cili është investitori dhe kush është sipërmarrësi?\n"
            "/prompt Sinkronizo folderin Drive dhe trego çfarë ndryshoi\n"
            "/prompt Gjenero Akt Kolaudimi për projektin aktiv dhe ma dërgo PDF-në\n\n"
            "Gjenerimi AI kërkon konfirmim pasi të shfaqet vlerësimi.",
            reply_markup=menu_back_keyboard(),
        )
    await callback.answer()


@router.callback_query(F.data == "menu:review")
async def review_menu_callback(callback: CallbackQuery) -> None:
    if callback.message:
        await callback.message.answer(
            "Gjenerimi dhe raportet\n\n"
            "Para gjenerimit sigurohuni që projekti aktiv ka dokumente të "
            "përpunuara dhe që AI është konfiguruar.\n\n"
            "/vlereso - vlerësimi i thirrjeve dhe tokenave\n"
            "/gjenero - nis Draft Akt Kolaudimi\n"
            "/status - ecuria e gjenerimit\n"
            "/raportet - merrni PDF-në e fundit\n\n"
            "Mund të përdorni edhe /kolaudim ose /akt në vend të /gjenero.",
            reply_markup=menu_back_keyboard(),
        )
    await callback.answer()


def _help_text() -> str:
    return (
        "Komandat e Kolaudimi Teknik\n\n"
        "PROJEKTET\n"
        "/projekt_ri Emri - krijon dhe aktivizon projektin\n"
        "/projektet - shfaq dhe zgjedh projektin aktiv\n\n"
        "DOSJA TEKNIKE\n"
        "/ngarko - udhëzimet e ngarkimit\n"
        "/dokumentet - dokumentet e projektit aktiv\n\n"
        "ASISTENTI\n"
        "/prompt kërkesa - menaxhon, pyet dosjen, importon dhe gjeneron\n"
        "/anulo - anulon kërkesën para nisjes së gjenerimit\n\n"
        "GJENERIMI\n"
        "/vlereso - vlerëson thirrjet dhe tokenat\n"
        "/gjenero - nis Draft Akt Kolaudimi\n"
        "/status - shfaq ecurinë\n"
        "/raportet - dërgon PDF-në e fundit\n\n"
        "GOOGLE DRIVE\n"
        "/google_connect - lidh llogarinë\n"
        "/google_folder LINKU - lidh folderin me projektin\n"
        "/google_check - kontrollon aksesin dhe ndryshimet\n"
        "/google_sync - sinkronizon ndryshimet\n"
        "/google_status - shfaq statusin e lidhjes\n"
        "/google_unbind - shkëput folderin\n"
        "/google_disconnect - shkëput llogarinë\n\n"
        "KONFIGURIMI AI\n"
        "/ai - shfaq konfigurimin\n"
        "/ai_key provider api_key - ruan API key\n"
        "/ai_models - shfaq modelet\n"
        "/ai_model model - zgjedh modelin bazë\n"
        "/ai_stage faza model - zgjedh model sipas fazës\n"
        "/ai_delete - fshin konfigurimin AI\n\n"
        "Përdorni /start për menunë kryesore."
    )
