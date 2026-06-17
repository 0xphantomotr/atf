FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
      build-essential \
      libffi-dev \
      libpango-1.0-0 \
      libpangoft2-1.0-0 \
      libgdk-pixbuf-2.0-0 \
      libcairo2 \
      shared-mime-info \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml /app/pyproject.toml
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -e .

COPY app /app/app
COPY alembic /app/alembic
COPY alembic.ini /app/alembic.ini

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

