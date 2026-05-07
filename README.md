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

### windows Powershell command 
```bash
docker run -it `
  --name itr-test `
  -p 8000:8000 `
  -p 9001:9001 `
  -p 5432:5432 `
  -p 9000:9000 `
  -v "C:\itr-platform\postgres-data:/var/lib/postgresql/data" `
  -v "C:\itr-platform\minio-data:/data/minio" `
  -e APP_NAME="ITR Filing Platform" `
  -e APP_VERSION="1.0.0" `
  -e DEBUG="true" `
  -e API_V1_PREFIX="/api/v1" `
  -e POSTGRES_HOST="localhost" `
  -e POSTGRES_PORT=5432 `
  -e POSTGRES_DB="itr_platform" `
  -e POSTGRES_USER="postgres" `
  -e POSTGRES_PASSWORD="postgres" `
  -e POSTGRES_POOL_SIZE=20 `
  -e POSTGRES_MAX_OVERFLOW=10 `
  -e MINIO_ENDPOINT="localhost:9000" `
  -e MINIO_ACCESS_KEY="minioadmin" `
  -e MINIO_SECRET_KEY="minioadmin" `
  -e MINIO_BUCKET_NAME="itr-documents" `
  -e MINIO_USE_SSL="false" `
  -e JWT_SECRET_KEY="dev-secret-key-change-in-production" `
  -e JWT_ALGORITHM="HS256" `
  -e JWT_ACCESS_TOKEN_EXPIRE_MINUTES=60 `
  -e ADMIN_EMAIL="admin@itr-platform.com" `
  -e ADMIN_PASSWORD="admin123" `
  omicron9009/itr-platform
```


### windows command prompt 
```bash
docker run -it ^
--name itr-platform ^
-p 8000:8000 ^
-p 9001:9001 ^
-e APP_NAME="ITR Filing Platform" ^
-e APP_VERSION="1.0.0" ^
-e DEBUG=true ^
-e API_V1_PREFIX="/api/v1" ^
-e DATABASE_HOST="127.0.0.1" ^
-e DATABASE_PORT=5432 ^
-e DATABASE_NAME="itr_platform" ^
-e DATABASE_USER="postgres" ^
-e DATABASE_PASSWORD="postgres" ^
-e DATABASE_POOL_SIZE=20 ^
-e DATABASE_MAX_OVERFLOW=10 ^
-e MINIO_ENDPOINT="127.0.0.1:9000" ^
-e MINIO_ACCESS_KEY="minioadmin" ^
-e MINIO_SECRET_KEY="minioadmin" ^
-e MINIO_BUCKET_NAME="itr-documents" ^
-e MINIO_USE_SSL=false ^
-e JWT_SECRET_KEY="dev-secret-key-change-in-production" ^
-e JWT_ALGORITHM="HS256" ^
-e JWT_ACCESS_TOKEN_EXPIRE_MINUTES=60 ^
-e ADMIN_EMAIL="admin@itr-platform.com" ^
-e ADMIN_PASSWORD="admin123" ^
-e ADMIN_FULL_NAME="Platform Admin" ^
-v itr-pgdata:/var/lib/postgresql/data ^
-v itr-minio:/data/minio ^
omicron9009/itr-platform:latest
```


### linux command 

```bash 
docker run -it \
--name itr-platform \
-p 8000:8000 \
-p 9001:9001 \
-e APP_NAME="ITR Filing Platform" \
-e APP_VERSION="1.0.0" \
-e DEBUG=true \
-e API_V1_PREFIX="/api/v1" \
-e DATABASE_HOST="127.0.0.1" \
-e DATABASE_PORT=5432 \
-e DATABASE_NAME="itr_platform" \
-e DATABASE_USER="postgres" \
-e DATABASE_PASSWORD="postgres" \
-e DATABASE_POOL_SIZE=20 \
-e DATABASE_MAX_OVERFLOW=10 \
-e MINIO_ENDPOINT="127.0.0.1:9000" \
-e MINIO_ACCESS_KEY="minioadmin" \
-e MINIO_SECRET_KEY="minioadmin" \
-e MINIO_BUCKET_NAME="itr-documents" \
-e MINIO_USE_SSL=false \
-e JWT_SECRET_KEY="dev-secret-key-change-in-production" \
-e JWT_ALGORITHM="HS256" \
-e JWT_ACCESS_TOKEN_EXPIRE_MINUTES=60 \
-e ADMIN_EMAIL="admin@itr-platform.com" \
-e ADMIN_PASSWORD="admin123" \
-e ADMIN_FULL_NAME="Platform Admin" \
-v itr-pgdata:/var/lib/postgresql/data \
-v itr-minio:/data/minio \
omicron9009/itr-platform:latest

```
