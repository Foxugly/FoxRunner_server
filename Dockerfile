FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements.lock ./
RUN pip install --prefix=/install -r requirements.lock


FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/usr/local/bin:$PATH

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system app \
    && useradd --system --gid app --home /app --shell /usr/sbin/nologin app

WORKDIR /app

COPY --from=builder /install /usr/local
COPY --chown=app:app . .

USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/api/v1/health || exit 1

# Default CMD runs the legacy FastAPI app during the dual-stack window.
# Phase 13 swaps to:
#   CMD ["sh", "-c", "python server_django/manage.py migrate && gunicorn --chdir server_django foxrunner.wsgi:application --bind 0.0.0.0:8000 --workers 2"]
# To preview Django in a container today, override CMD:
#   docker run -e DATABASE_URL=... -e DJANGO_SECRET_KEY=... <image> sh -c "python server_django/manage.py migrate && python server_django/manage.py runserver 0.0.0.0:8000"
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
