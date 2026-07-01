from html import escape

from fastapi import APIRouter, Query
from fastapi.responses import HTMLResponse

from app.db.session import AsyncSessionLocal
from app.google_drive.oauth import GoogleDriveAuthError, complete_google_authorization

router = APIRouter(prefix="/integrations/google-drive", tags=["google-drive"])


@router.get("/callback", response_class=HTMLResponse)
async def google_drive_callback(
    state: str = Query(default="", max_length=512),
    code: str = Query(default="", max_length=4096),
    error: str = Query(default="", max_length=512),
) -> HTMLResponse:
    if error:
        return _page(
            "Lidhja u anulua",
            "Google Drive nuk u lidh. Mund ta mbyllni këtë faqe dhe të provoni përsëri.",
            status_code=400,
        )
    try:
        async with AsyncSessionLocal() as session:
            connection = await complete_google_authorization(
                session,
                state=state,
                code=code,
            )
    except GoogleDriveAuthError as exc:
        return _page("Lidhja dështoi", str(exc), status_code=400)

    identity = connection.google_email or connection.google_display_name or "llogaria Google"
    return _page(
        "Google Drive u lidh",
        f"U lidh {identity}. Kthehuni në Telegram dhe vazhdoni me /prompt.",
    )


def _page(title: str, message: str, *, status_code: int = 200) -> HTMLResponse:
    safe_title = escape(title)
    safe_message = escape(message)
    return HTMLResponse(
        status_code=status_code,
        content=(
            "<!doctype html><html lang='sq'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{safe_title}</title></head><body>"
            f"<main><h1>{safe_title}</h1><p>{safe_message}</p></main></body></html>"
        ),
    )
