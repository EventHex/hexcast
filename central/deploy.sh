#!/usr/bin/env bash
# One-shot deploy of the HexCast central control plane to Cloud Run (Firestore).
# Prereqs (already done): gcloud auth login; Firestore DB created in asia-south1.
#   cd central && ./deploy.sh
#
# This runs two privileged actions your CLI blocks in auto mode, which is why
# it lives in a script you run yourself:
#   1. grants the Cloud Run runtime service account Firestore access
#   2. deploys the service --allow-unauthenticated (it MUST be public: the
#      desktop app calls it from users' machines with no GCP credentials)
set -euo pipefail
cd "$(dirname "$0")"

PROJECT="${PROJECT:-speech-to-text-app-448611}"
REGION="${REGION:-asia-south1}"
SERVICE="${SERVICE:-hexcast-central}"

PNUM="$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')"
SA="${PNUM}-compute@developer.gserviceaccount.com"

echo "==> 1/3  Firestore access for runtime SA ($SA)"
gcloud projects add-iam-policy-binding "$PROJECT" \
  --member="serviceAccount:${SA}" \
  --role="roles/datastore.user" --condition=None >/dev/null
echo "    granted roles/datastore.user"

echo "==> 2/3  session secret in Secret Manager (stable across deploys)"
if [ ! -f .central-secret ]; then
  openssl rand -hex 32 > .central-secret && chmod 600 .central-secret
fi
if ! gcloud secrets describe hexcast-secret --project "$PROJECT" >/dev/null 2>&1; then
  gcloud secrets create hexcast-secret --data-file=.central-secret \
    --replication-policy=automatic --project "$PROJECT" >/dev/null
  echo "    created secret hexcast-secret"
else
  echo "    secret hexcast-secret exists"
fi
gcloud secrets add-iam-policy-binding hexcast-secret --project "$PROJECT" \
  --member="serviceAccount:${SA}" \
  --role="roles/secretmanager.secretAccessor" >/dev/null
echo "    runtime SA can read it"

echo "==> 3/3  build + deploy to Cloud Run ($REGION)"
gcloud run deploy "$SERVICE" \
  --source . \
  --region "$REGION" \
  --allow-unauthenticated \
  --memory 512Mi \
  --set-env-vars "HEXCAST_BACKEND=firestore,ALLOW_ORIGINS=*,UPDATE_VERSION=0.1.0,MAC_URL=${MAC_URL:-https://storage.googleapis.com/hexcast-dl-657487551020/HexCast.dmg}" \
  --set-secrets "SECRET_KEY=hexcast-secret:latest" \
  --project "$PROJECT"

URL="$(gcloud run services describe "$SERVICE" --region "$REGION" --format='value(status.url)')"
echo ""
echo "Deployed:  $URL"
echo "Health:"
curl -s "$URL/health"; echo ""
echo ""
echo "Point the desktop app at it (rebuild the DMG with this set):"
echo "  export HEXCAST_AUTH_URL=$URL"
