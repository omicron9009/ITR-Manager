# Dev Docker Setup

This folder contains a local-development Docker Compose stack derived from `deploy/new_dockercompose.yml` with these changes:

- `cloudflared` is removed.
- data is stored in Docker named volumes instead of `F:\PlatformData\...`.
- core services are exposed on `127.0.0.1` so you can use them directly without public DNS.
- Traefik is kept only as a local convenience router on `localhost:8080`.

## Files

- `docker-compose.yml`: dev stack
- `.env`: ready-to-run local defaults
- `.env.example`: template/reset copy
- `prometheus.yml`, `loki-config.yml`, `promtail-config.yml`: observability config
- `grafana/`: Grafana provisioning and dashboards

## Start

From this folder:

```powershell
docker compose up -d
```

If you want to reset the env file:

```powershell
Copy-Item .env.example .env -Force
```

## Services And Ports

| Service | Container | Host Port | What to open |
| --- | --- | --- | --- |
| Frontend | `itr-frontend-ui` | `3000` | `http://localhost:3000` |
| Backend API | `itr-backend-api` | `8000` | `http://localhost:8000` |
| Traefik app router | `itr-traefik` | `8080` | `http://localhost:8080` |
| Traefik dashboard | `itr-traefik` | `8088` | `http://localhost:8088` |
| MinIO API | `itr-minio` | `9000` | `http://localhost:9000` |
| MinIO Console | `itr-minio` | `9001` | `http://localhost:9001` |
| OpenWA API | `itr-openwa-api` | `2785` | `http://localhost:2785/api/health` |
| PostgreSQL | `itr-postgres` | `5433` | connect with a DB client |
| Redis | `itr-redis` | `6379` | connect with a Redis client |
| Prometheus | `itr-prometheus` | `9090` | `http://localhost:9090` |
| Loki | `itr-loki` | `3100` | internal API, optional to open directly |
| Grafana | `itr-grafana` | `3050` | `http://localhost:3050` |

## Route Summary

- `http://localhost:3000`: direct frontend access
- `http://localhost:8000`: direct backend access
- `http://localhost:8080`: frontend through Traefik
- `http://localhost:8080/api/...`: backend through Traefik
- `http://localhost:8080/docs`: backend docs through Traefik

## Notes

- `NEXT_PUBLIC_API_URL` defaults to `http://localhost:8000`.
- `MINIO_PUBLIC_ENDPOINT` defaults to `localhost:9000`, so browser presigned URLs stay local.
- If port `3000`, `5433`, `6379`, `8000`, `8080`, `8088`, `9000`, `9001`, `9090`, `3050`, or `3100` is already in use, change it in `docker-compose.yml` before running.
