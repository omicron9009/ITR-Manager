run this using :
for build
docker build -t itr-platform:latest .

for tag
docker tag itr-platform:latest omicron9009/itr-platform:latest

for push
docker push omicron9009/itr-platform:latest

### Running the image

- Linux :

```sh
docker run -d \
 --name itr \
 --restart unless-stopped \
 -p 8000:8000 \
 -p 9001:9001 \
 -v itr-pgdata:/var/lib/postgresql/data \
 -v itr-minio:/data/minio \
 itr-platform:latest

docker run -it --name itr-platform -p 8000:8000 -p 9001:9001 --env-file .env -v itr-pgdata:/var/lib/postgresql/data -v itr-minio:/data/minio omicron9009/itr-platform:api

```

- Windows :

```pwsh
docker run -d --name itr --restart unless-stopped -p 8000:8000 -p 9001:9001 -v C:\Dev\ITR-Manager\pgdata:/var/lib/postgresql/data -v C:\Dev\ITR-Manager\minio:/data/minio itr-platform:latest
```
