#!/usr/bin/env bash
# Deploy a Cloud Run (proyecto safeapp-b32af).
# Requiere: gcloud autenticado con rol editor/owner, billing OPEN en el proyecto.
# Uso:
#   PROVIDER=replay  ./scripts/deploy_gcloud.sh            # demo de samples (sin Gemini)
#   ./scripts/deploy_gcloud.sh                             # en vivo (exige las variables de abajo)
#
# Variables:
#   PROJECT      (default safeapp-b32af)
#   REGION       (default us-central1)
#   SERVICE      (default nerdearla-live)
#   PROVIDER     (default gemini; usar replay para demo)
#   NERDEARLA_STREAM_URL   URL HLS .m3u8 del stream (solo modo gemini)
#   SOURCE_LANG_DEFAULT    (default en)
#   TARGET_LANGS           (default "es,en")
#   GOOGLE_CLOUD_LOCATION  (default global; transcribe-live-preview en us-central1 como alternativa)
#   MIN_INSTANCES / MAX_INSTANCES
#   IMAGE_TAG    si se define, no se contruye local (para builds en CI/Cloud Build)

set -euo pipefail

PROJECT="${PROJECT:-safeapp-b32af}"
REGION="${REGION:-us-central1}"
SERVICE="${SERVICE:-nerdearla-live}"
PROVIDER="${PROVIDER:-gemini}"
SOURCE_LANG_DEFAULT="${SOURCE_LANG_DEFAULT:-en}"
TARGET_LANGS="${TARGET_LANGS:-es,en}"
GOOGLE_CLOUD_LOCATION="${GOOGLE_CLOUD_LOCATION:-global}"
MIN_INSTANCES="${MIN_INSTANCES:-0}"
MAX_INSTANCES="${MAX_INSTANCES:-1}"

DOCKER_IMAGE="${REGION}-docker.pkg.dev/${PROJECT}/${SERVICE}/${SERVICE}"

echo "==> Proyecto: ${PROJECT} (región ${REGION})"
gcloud config set project "${PROJECT}" >/dev/null

echo "==> Verificando billing del proyecto..."
if [[ "$(gcloud billing projects describe "${PROJECT}" --format='value(billingEnabled)')" != "True" ]]; then
    echo "ERROR: el proyecto NO tiene billing habilitado."
    echo "  1) Consola: Billing > Accounts > create billing account y vincularla."
    echo "  2) o: gcloud billing projects link '${PROJECT}' --billing-account=<NUEVA_CUENTA>"
    exit 1
fi

echo "==> Habilitando APIs..."
gcloud services enable run.googleapis.com artifactregistry.googleapis.com --quiet

if [[ -z "${IMAGE_TAG:-}" ]]; then
    echo "==> Construyendo imagen (${DOCKER_IMAGE}:latest)..."
    docker build -t "${DOCKER_IMAGE}:latest" .
else
    echo "==> Usando imagen pre-construida: ${IMAGE_TAG}"
    DOCKER_IMAGE="${IMAGE_TAG}"
fi

echo "==> Autenticando docker contra Artifact Registry..."
gcloud auth configure-docker "${REGION}-docker.pkg.dev" --quiet

if [[ -z "${IMAGE_TAG:-}" ]]; then
    echo "==> Subiendo imagen..."
    docker push "${DOCKER_IMAGE}:latest"
else
    echo "==> (imagen provista por IMAGE_TAG, se saltea el push)"
fi

ENV_VARS="PROVIDER=${PROVIDER},SOURCE_LANG_DEFAULT=${SOURCE_LANG_DEFAULT},TARGET_LANGS=${TARGET_LANGS}"
CMD=(
    gcloud run deploy "${SERVICE}"
    --image "${DOCKER_IMAGE}:latest"
    --platform managed
    --region "${REGION}"
    --allow-unauthenticated
    --min-instances "${MIN_INSTANCES}"
    --max-instances "${MAX_INSTANCES}"
    --cpu 2
    --memory 1Gi
    --timeout 300
    --concurrency 10
    --set-env-vars "${ENV_VARS}"
)

if [[ "${PROVIDER}" == "gemini" ]]; then
    if [[ -z "${NERDEARLA_STREAM_URL:-}" ]]; then
        echo "ERROR: PROVIDER=gemini exige NERDEARLA_STREAM_URL (HLS .m3u8 de Castr)."
        exit 1
    fi
    CMD+=(--set-env-vars \
        "NERDEARLA_STREAM_URL=${NERDEARLA_STREAM_URL},GOOGLE_CLOUD_PROJECT=${PROJECT},GOOGLE_CLOUD_LOCATION=${GOOGLE_CLOUD_LOCATION},GOOGLE_GENAI_USE_VERTEXAI=true")
else
    CMD+=(--set-env-vars "GOOGLE_GENAI_USE_VERTEXAI=false")
fi

echo "==> ${CMD[*]}"
"${CMD[@]}"

URL=$(gcloud run services describe "${SERVICE}" --region "${REGION}" --format='value(status.url)')
echo ""
echo "==> Deploy listo: ${URL}"
echo "    Health:    ${URL}/healthz"
echo "    Sessions:  ${URL}/api/sessions"
echo "    Overlay:   ${URL}/overlay/<session_id>?lang=es  (OBS Browser Source)"