FROM python:3.12-slim

# ═══════════════════════════════════════════════════════════════
# All-in-one: PostgreSQL 16 + MinIO + FastAPI in one container
# Managed by supervisord
# ═══════════════════════════════════════════════════════════════

ENV DEBIAN_FRONTEND=noninteractive

# Install PostgreSQL + supervisor + wget
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev \
    postgresql postgresql-client \
    supervisor wget curl \
    && rm -rf /var/lib/apt/lists/*

# Install MinIO binary
RUN wget -q https://dl.min.io/server/minio/release/linux-amd64/minio -O /usr/local/bin/minio \
    && chmod +x /usr/local/bin/minio

# Python deps
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application code
COPY . .

# ─── PostgreSQL setup ────────────────────────────────────────
ENV POSTGRES_USER=postgres
ENV POSTGRES_PASSWORD=postgres
ENV POSTGRES_DB=itr_platform
ENV PGDATA=/var/lib/postgresql/16/main

# Initialize PostgreSQL cluster
RUN mkdir -p /var/run/postgresql && chown postgres:postgres /var/run/postgresql \
    && su - postgres -c "/usr/lib/postgresql/*/bin/initdb -D /var/lib/postgresql/data" \
    || true

# ─── MinIO setup ─────────────────────────────────────────────
ENV MINIO_ROOT_USER=minioadmin
ENV MINIO_ROOT_PASSWORD=minioadmin
RUN mkdir -p /data/minio

# ─── Supervisor config ───────────────────────────────────────
COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf

# ─── Startup script ──────────────────────────────────────────
COPY start.sh /start.sh
RUN chmod +x /start.sh

# ─── Volumes for persistent data ─────────────────────────────
VOLUME ["/var/lib/postgresql/data", "/data/minio"]

EXPOSE 8000 5432 9000 9001

CMD ["/start.sh"]
