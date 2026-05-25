# ITR Filing Platform — Deployment Guide

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                        HOST MACHINE (LAN)                            │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  ┌──────────────┐         Port 80                                   │
│  │  Cloudflared │◄─── Cloudflare Tunnel ───► Internet               │
│  └──────┬───────┘                                                   │
│         │                                                           │
│         ▼                                                           │
│  ┌──────────────┐    Host-based routing                             │
│  │   Traefik    │────────────────────────────────────┐              │
│  │   (Port 80)  │    :8080 = Traefik Dashboard       │              │
│  └──┬───┬───┬───┘                                    │              │
│     │   │   │                                        │              │
│     │   │   │  api.localhost          app.localhost   │  grafana.    │
│     ▼   │   ▼                                        ▼  localhost   │
│  ┌─────┐│┌──────────┐                         ┌─────────┐          │
│  │Back-│││ Frontend  │                         │ Grafana │          │
│  │end  │││ (Next.js) │                         │ (:3050) │          │
│  │:8000│││ :3000     │                         └────┬────┘          │
│  └──┬──┘│└──────────┘                              │               │
│     │   │                                          │               │
│     │   │  ┌──────────────────────────────────┐    │               │
│     │   │  │        internal_network           │    │               │
│     ▼   ▼  │                                   │    ▼               │
│  ┌──────────┐  ┌────────────┐           ┌────────────┐             │
│  │PostgreSQL│  │   MinIO    │           │ Prometheus │             │
│  │  :5432   │  │ :9000/:9001│           │   :9090    │             │
│  └──────────┘  └────────────┘           └────────────┘             │
│                  ▲ LAN Exposed                                      │
│                  │ (presigned URLs + console UI)                    │
└─────────────────────────────────────────────────────────────────────┘
```

## Services

| Service | Image | Port(s) | Access Method |
|---------|-------|---------|---------------|
| **PostgreSQL** | `postgres:16-alpine` | 5432 (internal only) | Docker DNS: `postgres` |
| **MinIO** | `minio/minio:latest` | 9000 (API), 9001 (Console) | LAN: `<IP>:9000`, `<IP>:9001` |
| **Traefik** | `traefik:v2.11` | 80, 8080 | `http://localhost`, dashboard at `:8080` |
| **Backend API** | `omicron9009/itr-backend:latest` | 8000 (internal) | `http://api.localhost` via Traefik |
| **Frontend** | `omicron9009/itr-frontend:latest` | 3000 (internal) | `http://app.localhost` via Traefik |
| **Cloudflared** | `cloudflare/cloudflared:latest` | — | Tunnel to Cloudflare edge |
| **Prometheus** | `prom/prometheus:v2.51.0` | 9090 (internal) | Internal scraping only |
| **Grafana** | `grafana/grafana:10.4.0` | 3050 (host), 3000 (internal) | `http://grafana.localhost` or `:3050` |

## Networks

| Network | Purpose | Services |
|---------|---------|----------|
| `ingress_network` (itr-frontend-bridge) | Traffic from Traefik to app containers | traefik, backend, frontend, minio, grafana, cloudflared, prometheus |
| `internal_network` (itr-internal-bridge) | Secure backend ↔ data layer comms | postgres, minio, backend, traefik, prometheus, grafana |

## Routing (Traefik)

Traefik listens on port 80 and routes by `Host` header:

| Host Header | Destination |
|-------------|-------------|
| `api.localhost` | backend-api:8000 |
| `app.localhost` | frontend-app:3000 |
| `grafana.localhost` | grafana:3000 |

> **Note:** `.localhost` domains always resolve to `127.0.0.1` per RFC 6761. For LAN access, use the machine's IP directly or configure Cloudflare Tunnel with real domains.

## Cloudflare Tunnel

The `cloudflared` service creates a secure outbound tunnel to Cloudflare's edge network, eliminating the need for port forwarding or public IPs.

### Setup Steps:

1. Go to [Cloudflare Zero Trust Dashboard](https://one.dash.cloudflare.com)
2. Navigate to **Networks → Tunnels → Create a tunnel**
3. Name your tunnel (e.g., `itr-platform`)
4. Copy the tunnel token
5. Paste it into `.env` as `CLOUDFLARE_TUNNEL_TOKEN`
6. In the tunnel config on Cloudflare dashboard, add public hostnames:

| Public Hostname | Service | URL |
|-----------------|---------|-----|
| `app.yourdomain.com` | HTTP | `http://itr-traefik:80` |
| `api.yourdomain.com` | HTTP | `http://itr-traefik:80` |
| `minio.yourdomain.com` | HTTP | `http://itr-minio:9001` |

> Cloudflared connects to Traefik internally, so Host-based routing still works. Set the `Host` header in Cloudflare tunnel config to match Traefik's router rules.

## Environment Variables (`.env`)

### Required — Must Change Before Production

| Variable | Description | Example |
|----------|-------------|---------|
| `POSTGRES_PASSWORD` | Database password | Use a strong random string |
| `MINIO_SECRET_KEY` | MinIO root password | Use a strong random string |
| `MINIO_PUBLIC_ENDPOINT` | LAN-accessible MinIO address for presigned URLs | `192.168.1.100:9000` |
| `JWT_SECRET_KEY` | Token signing key | 64-char random string |
| `ADMIN_PASSWORD` | Initial admin account password | Strong password |
| `CLOUDFLARE_TUNNEL_TOKEN` | From Cloudflare dashboard | `eyJh...` |

### Internal Networking — Do NOT Change

| Variable | Value | Why |
|----------|-------|-----|
| `POSTGRES_HOST` | `postgres` | Docker service DNS name |
| `MINIO_ENDPOINT` | `minio:9000` | Docker service DNS name |

### Application Defaults

| Variable | Default | Description |
|----------|---------|-------------|
| `APP_NAME` | ITR Filing Platform | Display name |
| `DEBUG` | true | Set `false` in production |
| `API_V1_PREFIX` | /api/v1 | API route prefix |
| `POSTGRES_DB` | itr_platform | Database name (auto-created on first boot) |
| `POSTGRES_POOL_SIZE` | 20 | SQLAlchemy connection pool |
| `MINIO_BUCKET_NAME` | itr-documents | Object storage bucket (auto-created) |
| `JWT_ALGORITHM` | HS256 | JWT signing algorithm |
| `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` | 60 | Token TTL |
| `CORS_ORIGINS` | `["http://app.localhost"]` | Allowed CORS origins (JSON array) |
| `NEXT_PUBLIC_API_URL` | `http://api.localhost` | Frontend's backend URL |
| `GRAFANA_ADMIN_PASSWORD` | admin | Grafana login password |
| `TZ` | Asia/Kolkata | Container timezone |

## MinIO LAN Access

MinIO ports are exposed directly on the host:

- **API (presigned URLs):** `http://<YOUR_LAN_IP>:9000`
- **Console (Web UI):** `http://<YOUR_LAN_IP>:9001`

The `MINIO_PUBLIC_ENDPOINT` variable must be set to `<YOUR_LAN_IP>:9000` so the backend generates presigned URLs that browsers on the LAN can reach.

To find your LAN IP:
```bash
# Linux
hostname -I | awk '{print $1}'

# Windows
ipconfig | findstr "IPv4"
```

## Observability Stack

### Prometheus
- Scrapes metrics every 15s from:
  - Itself (`localhost:9090`)
  - Traefik (`itr-traefik:8080`) — request counts, latencies, entrypoint stats
  - Backend API (`itr-backend-api:8000/metrics`) — HTTP request metrics via `prometheus-fastapi-instrumentator`
- Data retained for 15 days

### Grafana
- Auto-provisioned with Prometheus as default datasource (via `grafana/provisioning/`)
- Access: `http://grafana.localhost` or `http://<IP>:3050`
- Login: `admin` / value of `GRAFANA_ADMIN_PASSWORD`

## Deployment

### Prerequisites
- Docker Engine 24+ with Compose v2
- Machine with minimum 8GB RAM (sum of container limits ≈ 8GB)

### Quick Start

```bash
cd deploy

# 1. Edit .env — set your LAN IP and Cloudflare token
nano .env

# 2. Launch
docker compose up -d

# 3. Verify
docker compose ps
```

### Verify Connectivity

```bash
# Backend health
curl http://api.localhost/

# Frontend
curl http://app.localhost/

# MinIO Console (LAN)
curl http://<YOUR_LAN_IP>:9001

# Prometheus targets
curl http://localhost:9090/api/v1/targets

# Grafana
curl http://grafana.localhost/api/health
```

### Logs

```bash
# All services
docker compose logs -f

# Specific service
docker compose logs -f backend-api

# Last 100 lines
docker compose logs --tail=100 backend-api
```

### Update Images

```bash
docker compose pull
docker compose up -d
```

### Full Reset (destroys data)

```bash
docker compose down -v
```

## File Structure

```
deploy/
├── .env                          # All environment variables
├── docker-compose.yml            # Service definitions
├── prometheus.yml                # Prometheus scrape targets
├── grafana/
│   └── provisioning/
│       └── datasources/
│           └── prometheus.yml    # Auto-configures Prometheus in Grafana
└── README.md                     # This file
```

## Troubleshooting

| Problem | Cause | Fix |
|---------|-------|-----|
| Backend can't connect to Postgres | Wrong `POSTGRES_HOST` | Must be `postgres` (service name) |
| Presigned URLs 404 in browser | `MINIO_PUBLIC_ENDPOINT` wrong | Set to LAN IP:9000 |
| CORS errors in browser | Missing origin in `CORS_ORIGINS` | Add frontend URL to the JSON array |
| Cloudflared not connecting | Invalid tunnel token | Regenerate from Cloudflare dashboard |
| Prometheus can't scrape Traefik | Network isolation | Both must share `internal_network` (fixed in this config) |
| Frontend shows blank/errors | `NEXT_PUBLIC_API_URL` wrong | Must include `http://` protocol |
