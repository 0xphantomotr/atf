# Auditimi Teknik Bot

Telegram-first backend platform for Albanian civil engineering technical-folder audits.

The first product milestone is a VKM 610/2022 documentation audit:

1. User creates a project in Telegram.
2. User uploads technical files.
3. Backend stores immutable file versions.
4. Worker parses and classifies documents.
5. Audit engine checks required VKM 610 evidence.
6. User receives an Albanian summary and PDF report.

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
curl http://localhost:8000/healthz
```

