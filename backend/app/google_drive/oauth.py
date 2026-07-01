import asyncio
import hashlib
import logging
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urljoin
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import decrypt_secret, encrypt_secret
from app.google_drive.models import GoogleDriveConnection, GoogleOAuthState

DRIVE_SCOPE = "https://www.googleapis.com/auth/drive"
GOOGLE_AUTH_URI = "https://accounts.google.com/o/oauth2/auth"
GOOGLE_TOKEN_URI = "https://oauth2.googleapis.com/token"
logger = logging.getLogger(__name__)


class GoogleDriveAuthError(RuntimeError):
    pass


@dataclass(frozen=True)
class GoogleDriveConnectionStatus:
    connected: bool
    email: str | None = None
    display_name: str | None = None


def google_drive_is_configured() -> bool:
    return bool(
        settings.google_oauth_client_id.strip()
        and settings.google_oauth_client_secret.strip()
        and google_oauth_redirect_uri()
    )


def google_oauth_redirect_uri() -> str:
    configured = settings.google_oauth_redirect_uri.strip()
    if configured:
        return configured
    base_url = settings.app_base_url.strip().rstrip("/")
    if not base_url:
        return ""
    return urljoin(f"{base_url}/", "integrations/google-drive/callback")


async def create_google_authorization_url(
    session: AsyncSession,
    *,
    user_id: UUID,
) -> str:
    _require_google_configuration()
    now = datetime.now(UTC)
    await session.execute(
        delete(GoogleOAuthState).where(
            GoogleOAuthState.user_id == user_id,
            GoogleOAuthState.used_at.is_(None),
        )
    )
    raw_state = secrets.token_urlsafe(32)
    code_verifier = secrets.token_urlsafe(64)
    session.add(
        GoogleOAuthState(
            user_id=user_id,
            token_hash=_state_hash(raw_state),
            encrypted_code_verifier=encrypt_secret(code_verifier),
            expires_at=now + timedelta(seconds=settings.google_oauth_state_ttl_seconds),
        )
    )
    await session.commit()

    flow = _oauth_flow(state=raw_state, code_verifier=code_verifier)
    authorization_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    return authorization_url


async def complete_google_authorization(
    session: AsyncSession,
    *,
    state: str,
    code: str,
) -> GoogleDriveConnection:
    _require_google_configuration()
    if not state or not code:
        raise GoogleDriveAuthError("Google nuk ktheu kodin e autorizimit.")

    result = await session.execute(
        select(GoogleOAuthState)
        .where(GoogleOAuthState.token_hash == _state_hash(state))
        .with_for_update()
    )
    oauth_state = result.scalar_one_or_none()
    now = datetime.now(UTC)
    if oauth_state is None or oauth_state.used_at is not None:
        raise GoogleDriveAuthError("Lidhja e autorizimit nuk është më e vlefshme.")
    expires_at = oauth_state.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at <= now:
        raise GoogleDriveAuthError("Lidhja e autorizimit ka skaduar. Përdorni /google_connect.")

    code_verifier = decrypt_secret(oauth_state.encrypted_code_verifier)
    flow = _oauth_flow(state=state, code_verifier=code_verifier)
    try:
        await asyncio.to_thread(flow.fetch_token, code=code)
    except Exception as exc:
        logger.exception(
            "Google Drive OAuth token exchange failed: redirect_uri=%s error=%s",
            google_oauth_redirect_uri(),
            exc,
        )
        raise GoogleDriveAuthError("Google refuzoi autorizimin ose kodi ka skaduar.") from exc

    credentials = flow.credentials
    refresh_token = credentials.refresh_token
    existing = await get_google_drive_connection(session, user_id=oauth_state.user_id)
    if not refresh_token and existing is not None:
        refresh_token = decrypt_secret(existing.encrypted_refresh_token)
    if not refresh_token:
        raise GoogleDriveAuthError(
            "Google nuk dha refresh token. Hiqni aksesin e aplikacionit në Google Account "
            "dhe provoni përsëri me /google_connect."
        )

    try:
        profile = await asyncio.to_thread(_drive_user_profile, credentials)
    except Exception:
        profile = {}
    scopes = " ".join(sorted(credentials.scopes or [DRIVE_SCOPE]))
    if existing is None:
        connection = GoogleDriveConnection(
            user_id=oauth_state.user_id,
            encrypted_refresh_token=encrypt_secret(refresh_token),
            google_email=profile.get("emailAddress"),
            google_display_name=profile.get("displayName"),
            scopes=scopes,
        )
        session.add(connection)
    else:
        connection = existing
        connection.encrypted_refresh_token = encrypt_secret(refresh_token)
        connection.google_email = profile.get("emailAddress")
        connection.google_display_name = profile.get("displayName")
        connection.scopes = scopes

    oauth_state.used_at = now
    await session.commit()
    await session.refresh(connection)
    return connection


async def get_google_drive_connection(
    session: AsyncSession,
    *,
    user_id: UUID,
) -> GoogleDriveConnection | None:
    result = await session.execute(
        select(GoogleDriveConnection).where(GoogleDriveConnection.user_id == user_id)
    )
    return result.scalar_one_or_none()


async def google_drive_connection_status(
    session: AsyncSession,
    *,
    user_id: UUID,
) -> GoogleDriveConnectionStatus:
    connection = await get_google_drive_connection(session, user_id=user_id)
    if connection is None:
        return GoogleDriveConnectionStatus(connected=False)
    return GoogleDriveConnectionStatus(
        connected=True,
        email=connection.google_email,
        display_name=connection.google_display_name,
    )


async def delete_google_drive_connection(
    session: AsyncSession,
    *,
    user_id: UUID,
) -> bool:
    connection = await get_google_drive_connection(session, user_id=user_id)
    if connection is None:
        return False
    await session.delete(connection)
    await session.execute(delete(GoogleOAuthState).where(GoogleOAuthState.user_id == user_id))
    await session.commit()
    return True


def google_credentials(connection: GoogleDriveConnection):
    from google.oauth2.credentials import Credentials

    return Credentials(
        token=None,
        refresh_token=decrypt_secret(connection.encrypted_refresh_token),
        token_uri=GOOGLE_TOKEN_URI,
        client_id=settings.google_oauth_client_id,
        client_secret=settings.google_oauth_client_secret,
        scopes=[DRIVE_SCOPE],
    )


def _oauth_flow(*, state: str, code_verifier: str | None = None):
    from google_auth_oauthlib.flow import Flow

    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": settings.google_oauth_client_id,
                "client_secret": settings.google_oauth_client_secret,
                "auth_uri": GOOGLE_AUTH_URI,
                "token_uri": GOOGLE_TOKEN_URI,
                "redirect_uris": [google_oauth_redirect_uri()],
            }
        },
        scopes=[DRIVE_SCOPE],
        state=state,
        code_verifier=code_verifier,
        autogenerate_code_verifier=code_verifier is None,
    )
    flow.redirect_uri = google_oauth_redirect_uri()
    return flow


def _drive_user_profile(credentials) -> dict[str, str]:
    from googleapiclient.discovery import build

    drive = build("drive", "v3", credentials=credentials, cache_discovery=False)
    response = drive.about().get(fields="user(displayName,emailAddress)").execute()
    user = response.get("user")
    return dict(user) if isinstance(user, dict) else {}


def _state_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _require_google_configuration() -> None:
    if not google_drive_is_configured():
        raise GoogleDriveAuthError(
            "Google Drive nuk është konfiguruar në server. Vendosni OAuth client ID, "
            "client secret dhe redirect URI."
        )
