# HexCast — central control plane

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
| GET  | `/updates/latest` | desktop auto-update manifest `{version,url,notes}` |

Tokens are HMAC-signed with `SECRET_KEY` (server-only), so the desktop client
can't forge one — it verifies online via `/auth/verify`.

## Run locally (SQLite)
```bash
cd central
pip install -r requirements.txt
uvicorn main:app --port 8790
# → SQLite file central.db, dev secret in .central-secret
```

## Deploy to GCP (Cloud Run + Firestore) — the current setup
Backend is Firestore (`HEXCAST_BACKEND=firestore`): serverless, scales to
zero, no idle cost. The DB already exists (asia-south1, Native mode). Deploy:
```bash
cd central && ./deploy.sh
```
`deploy.sh` grants the runtime service account `roles/datastore.user`, ensures a
stable session secret (`.central-secret`, gitignored), then builds + deploys
`--allow-unauthenticated` (the service must be public — the desktop app calls it
from users' machines with no GCP credentials). It prints the URL and hits
`/health`. Point the desktop app at that URL via `HEXCAST_AUTH_URL`.

Bump the desktop version on a new release without redeploying code:
```bash
gcloud run services update hexcast-central --region asia-south1 \
  --update-env-vars UPDATE_VERSION=0.2.0,UPDATE_URL=https://…/HexCast.dmg
```

`SECRET_KEY` is delivered from **Secret Manager** (`hexcast-secret`), not a
plaintext env var — `deploy.sh` creates the secret, grants the runtime SA
`secretmanager.secretAccessor`, and deploys with
`--set-secrets SECRET_KEY=hexcast-secret:latest`.

### Alternative: Cloud SQL Postgres
Set `DATABASE_URL=postgresql://…` (and `--add-cloudsql-instances`) instead of
`HEXCAST_BACKEND=firestore`. `store.py` supports both.
