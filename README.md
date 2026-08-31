# FastAPI Microservices

Local microservice stack with:
- Auth service: register, login, JWT verify, current user, logout
- Skills service
- API gateway with separate nginx route files per service
- Postgres per service
- MinIO S3-compatible object storage
- Shared storage/file utilities

## Services
- Gateway: `http://localhost`
- Auth docs: `http://localhost/auth/docs`
- Skills docs: `http://localhost/skills/docs`
- MinIO console via gateway: `http://localhost/minio/`
- MinIO console direct: `http://localhost:9001`
- Public media base: `http://localhost/media`

Default MinIO credentials for local development are `minioadmin` / `minioadmin123`.

## Run Full Stack
```bash
docker compose up -d
```

`compose.yml` includes `compose.db.yml` and `compose.service.yml`, so the default command starts storage, gateway, databases, and app services.

## Run By Purpose
Databases only:
```bash
docker compose -f compose.db.yml up -d
```

## Auth Sample Flow
Register:
```bash
curl -X POST http://localhost/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@example.com","password":"StrongPass123"}'
```

Login:
```bash
curl -X POST http://localhost/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@example.com","password":"StrongPass123"}'
```

Current user:
```bash
curl http://localhost/auth/me \
  -H "Authorization: Bearer <ACCESS_TOKEN>"
```

Delete an empty bucket:
```bash
curl -X DELETE "http://localhost/auth/storage/buckets/user-profile" \
  -H "Authorization: Bearer <ACCESS_TOKEN>"
```

Force-delete a bucket and all objects inside it:
```bash
curl -X DELETE "http://localhost/auth/storage/buckets/user-profile?force=true" \
  -H "Authorization: Bearer <ACCESS_TOKEN>"
```

By default, each service derives its media bucket as `{SERVICE_SHORT_NAME}-media`.
Examples: `auth-media`, `skills-media`.

## Gateway Layout
- Root config: `gateway/nginx/nginx.conf`
- Shared proxy headers/timeouts: `gateway/nginx/snippets/proxy.conf`
- Service routes: `gateway/nginx/conf.d/*.conf`

## Production Notes
- Replace local secrets with a secret manager.
- Put TLS/HSTS in front of nginx.
- Replace `generate_schemas`/`create_all` with migrations before production data exists.
- Restrict MinIO bucket policy for private files; the local stack exposes media for development convenience.
- Add request IDs, tracing, rate limits, and integration tests in CI.
