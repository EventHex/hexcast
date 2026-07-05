# Remaster — central control plane

A tiny accounts + usage API. It never touches video or provider keys: the
desktop app processes everything locally with the user's own keys, and only
calls this service to sign in, verify a session, and report usage for metering.

## Endpoints
| Method | Path | Purpose |
|---|---|---|
| GET  | `/health` | liveness + which DB/Google are active |
| POST | `/auth/signup` | `{email,password,name}` → `{token, user}` |
| POST | `/auth/login` | `{email,password}` → `{token, user}` |
| GET  | `/auth/verify` | `Bearer <token>` → `{user}` (desktop validates its session) |
| GET  | `/auth/me` | `Bearer` → `{user, usage}` |
| POST | `/usage/report` | `Bearer` + `{kind}` → records an event, returns usage |
| GET  | `/auth/google/login` · `/auth/google/callback` | optional Google sign-in (loopback) |

Tokens are HMAC-signed with `SECRET_KEY` (server-only), so the desktop client
can't forge one — it verifies online via `/auth/verify`.

## Run locally (SQLite)
```bash
cd central
pip install -r requirements.txt
uvicorn main:app --port 8790
# → SQLite file central.db, dev secret in .central-secret
```

## Deploy to GCP (Cloud Run + Cloud SQL Postgres)
```bash
# 1. Cloud SQL Postgres instance + database "remaster", note the connection name.
# 2. Build + deploy:
gcloud run deploy remaster-central \
  --source . --region <REGION> --allow-unauthenticated \
  --add-cloudsql-instances <PROJECT:REGION:INSTANCE> \
  --set-env-vars SECRET_KEY=<hex32>,DATABASE_URL=postgresql://USER:PASS@/remaster?host=/cloudsql/<PROJECT:REGION:INSTANCE>
```
Cloud Run scales to zero — you pay only when someone signs in / reports usage.
Point the desktop app at the resulting URL via `REMASTER_AUTH_URL`.
