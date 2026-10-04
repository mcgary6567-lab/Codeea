# syntax=docker/dockerfile:1
# Online Quran College - production image. Shared by docker compose, Railway and Fly.io.
# Tailwind CSS is compiled and committed (app/static/css/tailwind.css); no Node at build time.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# curl: HEALTHCHECK.  gosu: drop from root to oqc after fixing volume ownership.
# fonts-noto-core / fonts-dejavu-core: app/services/academic.py probes
#   /usr/share/fonts/truetype/noto/NotoNaskhArabic-Regular.ttf and .../dejavu/DejaVuSans.ttf
#   for Arabic/Urdu text on result cards and certificates.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl gosu fonts-noto-core fonts-dejavu-core \
 && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 1000 --shell /bin/bash oqc

WORKDIR /app

COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY --chown=oqc:oqc . .

# Every directory the app writes to at runtime (see app/services/*.py, app/web/*.py). The storage
# volume is mounted over /app/storage; data/ is only used when DATABASE_URL is SQLite.
RUN chmod +x deploy/entrypoint.sh deploy/smoke.sh deploy/backup.sh \
 && for d in agent_screenshots attachments backups certificates exports invoices payslips receipts recordings reports result_cards uploads; do \
      mkdir -p "storage/$d"; done \
 && mkdir -p data \
 && chown -R oqc:oqc storage data

ARG GIT_COMMIT=""
ENV GIT_COMMIT=$GIT_COMMIT \
    PORT=8000 \
    WEB_CONCURRENCY=1 \
    HOST=0.0.0.0 \
    APP_ENV=production

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
  CMD curl -fsS "http://127.0.0.1:${PORT}/health" || exit 1

# The entrypoint starts as root only to chown the mounted volume, then re-executes itself as oqc.
# Set `user: "1000:1000"` in compose if you want no root at all (named volumes keep image ownership).
ENTRYPOINT ["/app/deploy/entrypoint.sh"]
