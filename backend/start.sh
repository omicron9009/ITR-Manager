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

# ─── Start PostgreSQL temporarily to create DB ───────────────
echo "Starting PostgreSQL for initialization..."
su - postgres -c "$PG_BIN/pg_ctl -D $PGDATA -l /tmp/pg_startup.log start -w"

# Set password for postgres user
su - postgres -c "psql -c \"ALTER USER postgres PASSWORD 'postgres';\""

# Create application database if not exists
su - postgres -c "psql -tc \"SELECT 1 FROM pg_database WHERE datname = 'itr_platform'\" | grep -q 1 || psql -c 'CREATE DATABASE itr_platform'"

echo "Database 'itr_platform' ready."

# Stop PostgreSQL (supervisor will start it properly)
su - postgres -c "$PG_BIN/pg_ctl -D $PGDATA stop -w"

# ─── Ensure MinIO data dir ───────────────────────────────────
mkdir -p /data/minio

# ─── Start all services via supervisord ──────────────────────
echo "Starting all services..."
exec /usr/bin/supervisord -c /etc/supervisor/conf.d/supervisord.conf
