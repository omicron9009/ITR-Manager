FROM python:3.12-slim

# ─── System dependencies (PostgreSQL, MinIO, Supervisor) ─────
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    curl \
    gnupg \
    lsb-release \
    supervisor \
    && echo "deb http://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" \
       > /etc/apt/sources.list.d/pgdg.list \
    && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc | gpg --dearmor -o /etc/apt/trusted.gpg.d/pgdg.gpg \
    && apt-get update \
    && apt-get install -y --no-install-recommends postgresql-17 \
    && rm -rf /var/lib/apt/lists/*

# ─── Install MinIO binary ────────────────────────────────────
RUN curl -fsSL https://dl.min.io/server/minio/release/linux-amd64/minio -o /usr/local/bin/minio \
    && chmod +x /usr/local/bin/minio

WORKDIR /app

# ─── Python dependencies ─────────────────────────────────────
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ─── Application code ────────────────────────────────────────
COPY . .

# ─── Runtime directories ─────────────────────────────────────
RUN mkdir -p /var/run/postgresql /data/minio /var/log \
    && chown postgres:postgres /var/run/postgresql

# ─── Supervisor config ───────────────────────────────────────
COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf

# ─── Startup script ──────────────────────────────────────────
COPY start.sh /start.sh
RUN chmod +x /start.sh

# ─── Volumes for persistent data ─────────────────────────────
VOLUME ["/var/lib/postgresql/data", "/data/minio"]

EXPOSE 8000 5432 9000 9001

CMD ["/start.sh"]