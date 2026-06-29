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
- OpenAI, Gemini and Groq user-provided API keys
- Background processing, quota handling and automatic Telegram PDF delivery

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
/gjenero
/status
/raportet
```

Natural-language workflows are available through `/prompt`:

```text
/prompt Shfaq projektet e mia
/prompt Kush është sipërmarrësi sipas dosjes aktive?
/prompt Gjenero Akt Kolaudimin për projektin aktiv dhe ma dërgo PDF-në
```

A ZIP can also be attached to a `/prompt` message to create or select a project, import
the dossier, estimate generation, request confirmation and deliver the resulting PDF.

## Verification

```bash
python3 -m compileall backend/app backend/tests
PYTHONPATH=backend pytest backend/tests
```
