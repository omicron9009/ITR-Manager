#!/bin/bash
set -e

# ─── Detect PostgreSQL version ───────────────────────────────
export PG_VERSION=$(ls /usr/lib/postgresql/ | head -1)
PG_BIN="/usr/lib/postgresql/$PG_VERSION/bin"
export PGDATA="/var/lib/postgresql/data"

echo "=== ITR Filing Platform — All-in-One Container ==="
echo "PostgreSQL version: $PG_VERSION"

# ─── Initialize PostgreSQL if needed ─────────────────────────
if [ ! -f "$PGDATA/PG_VERSION" ]; then
    echo "Initializing PostgreSQL data directory..."
    mkdir -p "$PGDATA"
    chown -R postgres:postgres "$PGDATA"
    su - postgres -c "$PG_BIN/initdb -D $PGDATA"

    # Allow local connections without password for setup
    echo "host all all 127.0.0.1/32 md5" >> "$PGDATA/pg_hba.conf"
    echo "local all all trust" >> "$PGDATA/pg_hba.conf"
fi

# ─── Remove stale PID file from previous container run ───────
if [ -f "$PGDATA/postmaster.pid" ]; then
    echo "Removing stale postmaster.pid..."
    rm -f "$PGDATA/postmaster.pid"
fi

# ─── Fix ownership/permissions (required for bind mounts) ────
echo "Fixing PostgreSQL data directory permissions..."
mkdir -p "$PGDATA"
chown -R postgres:postgres "$PGDATA"
chmod 700 "$PGDATA"

# ─── Start PostgreSQL temporarily to create DB ───────────────
echo "Starting PostgreSQL for initialization..."
if ! su - postgres -c "$PG_BIN/pg_ctl -D $PGDATA -l /tmp/pg_startup.log start -w"; then
    echo "=== PostgreSQL startup FAILED. Log output: ==="
    cat /tmp/pg_startup.log 2>/dev/null || true
    exit 1
fi

# Set password for postgres user
su - postgres -c "psql -c \"ALTER USER postgres PASSWORD 'postgres';\""

# Create application database if not exists
su - postgres -c "psql -tc \"SELECT 1 FROM pg_database WHERE datname = 'itr_platform'\" | grep -q 1 || psql -c 'CREATE DATABASE itr_platform'"

echo "Database 'itr_platform' ready."

# Stop PostgreSQL (supervisor will start it properly)
su - postgres -c "$PG_BIN/pg_ctl -D $PGDATA stop -w"

# ─── Ensure MinIO data dir ───────────────────────────────────
mkdir -p /data/minio

# ─── Set defaults for MinIO env vars (supervisor needs them) ─
export MINIO_ACCESS_KEY="${MINIO_ACCESS_KEY:-minioadmin}"
export MINIO_SECRET_KEY="${MINIO_SECRET_KEY:-minioadmin}"
export MINIO_KMS_SECRET_KEY="${MINIO_KMS_SECRET_KEY:-}"

# ─── Start all services via supervisord (background) ─────────
echo "Starting all services..."
/usr/bin/supervisord -c /etc/supervisor/conf.d/supervisord.conf &
SUPERVISOR_PID=$!

# ─── Wait for MinIO to be ready, then configure encryption ───
if [ -n "$MINIO_KMS_SECRET_KEY" ]; then
    echo "Waiting for MinIO to be ready..."
    for i in $(seq 1 30); do
        if mc alias set local http://localhost:9000 "$MINIO_ACCESS_KEY" "$MINIO_SECRET_KEY" >/dev/null 2>&1; then
            break
        fi
        sleep 1
    done

    # Ensure bucket exists
    BUCKET="${MINIO_BUCKET_NAME:-itr-documents}"
    mc mb --ignore-existing "local/$BUCKET" 2>/dev/null || true

    # Enable SSE-S3 auto-encryption on the bucket
    if mc encrypt set sse-s3 "local/$BUCKET" 2>/dev/null; then
        echo "Bucket '$BUCKET' — SSE-S3 encryption enabled."
    else
        echo "Warning: Could not set bucket encryption (MinIO KMS may not be configured)."
    fi
else
    echo "MINIO_KMS_SECRET_KEY not set — bucket encryption disabled."
fi

# ─── Wait for supervisor (foreground) ────────────────────────
wait $SUPERVISOR_PID
