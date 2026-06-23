# Auditimi Teknik Bot

Telegram-first backend platform for Albanian civil engineering technical-folder reviews
and professional Akt Kolaudimi generation.

The core workflow is:

1. User creates a project in Telegram.
2. User uploads technical files.
3. Backend stores immutable file versions.
4. Worker parses and classifies documents.
5. Agent workflow consolidates project evidence and applies VKM 610/2022.
6. User receives a professional Albanian Akt Kolaudimi PDF.

See [plan.md](plan.md) for the implementation plan.

## Local Development

Copy environment defaults:

```bash
cp .env.example .env
```

Start infrastructure and app services:

```bash
docker compose -f infra/docker-compose.yml up --build
```

API health check:

```bash
curl http://localhost/healthz
```

## Local OCR

The API and worker images include Poppler and Tesseract with Albanian (`sqi`) and English
(`eng`) language data. Textless PDF pages and uploaded PNG/JPEG files are OCRed locally;
no AI API key is required for this stage. Successful OCR versions use the
`parsed_with_ocr` status and remain eligible for document and agent analysis.

OCR defaults can be adjusted through `OCR_ENABLED`, `OCR_LANGUAGES`, `OCR_DPI`,
`OCR_MIN_CONFIDENCE` and `OCR_PAGE_TIMEOUT_SECONDS`.

Review the stored chunks for a file version:

```text
GET /projects/{project_id}/files/{file_id}/versions/{version_id}/chunks
```

Queue the same immutable version for parsing/OCR again:

```text
POST /projects/{project_id}/files/{file_id}/versions/{version_id}/reprocess
```

## AI Stage Models And Preflight

Each user keeps one default provider/model and may optionally override the model used for
document extraction, specialist synthesis, Akt drafting, or the bounded correction pass.
All stage models use the same encrypted user API key and must belong to that provider.

Telegram commands:

```text
/ai
/ai_models
/ai_model default-model-name
/ai_stage extraction economical-model-name
/ai_stage drafting stronger-model-name
/ai_stage extraction default
/vlereso
/gjenero
```

The `default` value removes a stage override. `/vlereso` inspects the active project's
current files and cache, then reports a conservative maximum call/token estimate without
starting a job.

The same preflight is available through the API:

```text
POST /projects/{project_id}/generate/preflight
```
