## Setup (requires Docker)

All commands must be run from the `backend/` directory:

```bash
cd backend
```

### Build

```bash
docker build -t itr-platform:latest .
```

### Tag & Push

```bash
docker tag itr-platform:latest omicron9009/itr-platform:latest
docker push omicron9009/itr-platform:latest
```

### Run (from build)

Linux/Mac:
```bash
docker run -d \
  --name itr \
  --restart unless-stopped \
  -p 8000:8000 \
  -p 9001:9001 \
  --env-file .env \
  -v itr-pgdata:/var/lib/postgresql/data \
  -v itr-minio:/data/minio \
  itr-platform:latest
```

Windows PowerShell:
```powershell
docker run -d `
  --name itr `
  --restart unless-stopped `
  -p 8000:8000 `
  -p 9001:9001 `
  --env-file .env `
  -v itr-pgdata:/var/lib/postgresql/data `
  -v itr-minio:/data/minio `
  itr-platform:latest
```

### Run (from Docker Hub)

```bash
docker run -d --name itr-platform -p 8000:8000 -p 9001:9001 --env-file .env -v itr-pgdata:/var/lib/postgresql/data -v itr-minio:/data/minio omicron9009/itr-platform:latest
```
