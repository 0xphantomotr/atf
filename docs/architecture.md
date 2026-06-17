# Architecture

Telegram is the first interface. FastAPI owns the product domain and coordinates PostgreSQL,
MinIO, Redis, workers, law ingestion, audits, reports, and notifications.

