# Kolaudimi Teknik

Kolaudimi Teknik is a Telegram-first platform for reviewing Albanian construction
technical folders and generating a professional draft Akt Kolaudimi.

Users can create projects, upload a ZIP containing the technical folder, follow document
processing, ask grounded questions about the active dossier, and generate a cited PDF for
final review by a qualified kolaudator.

## Main Features

- PDF, DOCX, XLSX, image, ZIP and best-effort MPP processing
- Local Albanian and English OCR with Tesseract
- Immutable file versions stored in MinIO
- Project evidence and conflict consolidation
- VKM 610/2022 rule and legal-context support
- LangGraph-based document analysis and Akt Kolaudimi drafting
- Grounded dossier Q&A with verified filenames and source coordinates
- Bounded follow-up questions, clarification prompts and action previews
- OpenAI, Gemini and Groq user-provided API keys
- Background processing, quota handling and automatic Telegram PDF delivery
- Bound Google Drive workspaces with incremental synchronization and change detection
- Versioned PDF output in a managed `Kolaudimi/` Drive subfolder

AI-generated documents are drafts. A qualified professional must verify the source
documents, conclusions and final signatory information before official use.

## Technology

FastAPI, PostgreSQL with pgvector, Redis, Dramatiq, MinIO, LangGraph, aiogram and Caddy.

## Run Locally

Requirements: Docker with Docker Compose.

```bash
cp .env.example .env
docker compose -f infra/docker-compose.yml up -d --build
docker compose -f infra/docker-compose.yml exec api alembic upgrade head
curl http://localhost/healthz
```

Set the Telegram bot token, webhook secret and public HTTPS URL in `.env`, then register
the webhook with Telegram. Never commit `.env` or user API keys.

## Telegram Usage

Common commands:

```text
/start
/projekt_ri Emri i projektit
/projektet
/ai_key gemini YOUR_API_KEY
/ai_model MODEL_NAME
/google_connect
/google_folder LINKU_DRIVE
/google_check
/google_sync
/google_status
/gjenero
/status
/raportet
```

Natural-language workflows are available through `/prompt`:

```text
/prompt Shfaq projektet e mia
/prompt Kush është sipërmarrësi sipas dosjes aktive?
/prompt Po cila është data e kontratës?
/prompt Gjenero Akt Kolaudimin për projektin aktiv dhe ma dërgo PDF-në
/prompt Importo dosjen nga LINKU_DRIVE, gjenero Akt Kolaudimi dhe ruaje në të njëjtin folder
```

A ZIP can also be attached to a `/prompt` message to create or select a project, import
the dossier, estimate generation, request confirmation and deliver the resulting PDF.

## Google Drive

Create a Google Cloud OAuth web client, enable the Google Drive API and register this
exact redirect URI for production:

```text
https://atf.kolaudimi.dev/integrations/google-drive/callback
```

Configure `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET` and
`GOOGLE_OAUTH_REDIRECT_URI` in `.env`. Each user then runs `/google_connect` once and
authorizes the Drive account that owns or can edit the linked folder.

After linking the account, bind the active project with `/google_folder LINKU_DRIVE`.
`/google_check` previews access and pending changes without importing; `/google_sync`
downloads only new or changed files, marks source deletions and reuses analysis for unchanged
versions. Generated reports are stored with sequential version names under `Kolaudimi/`.

The application requests Drive access because it must read arbitrary linked technical
folders and upload the generated PDF. Keep the OAuth app in testing with explicit test
users during development; public use may require Google OAuth verification.

## Verification

```bash
python3 -m compileall backend/app backend/tests
PYTHONPATH=backend pytest backend/tests
```
