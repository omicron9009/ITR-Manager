# 1. Configuration
$backendPath = "D:\Dev\ITR-Manager"
$containerName = "itr-platform"
$imageName = "omicron9009/itr-platform:api"

# 2. Navigate to folder
if (Test-Path $backendPath) {
    Set-Location $backendPath
    Write-Host "✅ Set location to $backendPath" -ForegroundColor Cyan
} else {
    Write-Host "❌ Error: Path not found" -ForegroundColor Red
    exit
}

# 3. Cleanup existing container
if (docker ps -a -q --filter "name=$containerName") {
    Write-Host "🔄 Removing old $containerName..." -ForegroundColor Yellow
    docker rm -f $containerName | Out-Null
}

# 4. Run with Baked ENV
Write-Host "🚀 Launching with baked environment variables..." -ForegroundColor Green

docker run -it `
  --name $containerName `
  -p 8000:8000 `
  -p 9001:9001 `
  -e APP_NAME="ITR Filing Platform" `
  -e APP_VERSION="1.0.0" `
  -e DEBUG="true" `
  -e API_V1_PREFIX="/api/v1" `
  -e DATABASE_HOST="127.0.0.1" `
  -e DATABASE_PORT=5432 `
  -e DATABASE_NAME="itr_platform" `
  -e DATABASE_USER="postgres" `
  -e DATABASE_PASSWORD="postgres" `
  -e MINIO_ENDPOINT="127.0.0.1:9000" `
  -e MINIO_ACCESS_KEY="minioadmin" `
  -e MINIO_SECRET_KEY="minioadmin" `
  -e MINIO_BUCKET_NAME="itr-documents" `
  -e MINIO_USE_SSL="false" `
  -e JWT_SECRET_KEY="dev-secret-key-change-in-production" `
  -e JWT_ALGORITHM="HS256" `
  -e JWT_ACCESS_TOKEN_EXPIRE_MINUTES=60 `
  -e ADMIN_EMAIL="admin@itr-platform.com" `
  -e ADMIN_PASSWORD="admin123" `
  -e CORS_ORIGINS='["http://localhost:3000"]' `
  -v itr-pgdata:/var/lib/postgresql/data `
  -v itr-minio:/data/minio `
  $imageName