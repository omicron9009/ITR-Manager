FROM python:3.12-slim

# Install only the bare essentials for Python/Postgres drivers
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
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

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]