#!/usr/bin/env bash
set -euo pipefail

# Build metadata
BUILD_HASH="${BUILD_HASH:-$(git rev-parse --short HEAD 2>/dev/null || date +%s)}"
IMAGE_TAG="${IMAGE_TAG:-archeround/open-webui:latest}"
CONTAINER_NAME="${CONTAINER_NAME:-open-webui-archeround}"

# Build the image
docker build \
  --build-arg BUILD_HASH="$BUILD_HASH" \
  --build-arg USE_CUDA=false \
  --build-arg USE_OLLAMA=false \
  --build-arg USE_PERMISSION_HARDENING=false \
  -t "$IMAGE_TAG" .

# Stop and remove any existing container to enable zero-conf updates
if docker ps -a --format '{{.Names}}' | grep -Eq "^${CONTAINER_NAME}$"; then
  docker rm -f "$CONTAINER_NAME"
fi

# Run the new container
docker run -d \
  --name "$CONTAINER_NAME" \
  --restart always \
  --network host \
  -e OLLAMA_BASE_URL="${OLLAMA_BASE_URL:-http://10.168.140.9:11434}" \
  -e WEBUI_NAME="${WEBUI_NAME:-AI Chat Platform}" \
  -e DEFAULT_LOCALE="${DEFAULT_LOCALE:-en-US}" \
  -e ENABLE_SIGNUP="${ENABLE_SIGNUP:-true}" \
  -e WEBUI_URL="${WEBUI_URL:-https://nsyd-ai-vm02.archeround.com}" \
  -e ENABLE_OAUTH_SIGNUP="${ENABLE_OAUTH_SIGNUP:-false}" \
  -e ENABLE_LOGIN_FORM="${ENABLE_LOGIN_FORM:-true}" \
  -e VECTOR_DB="${VECTOR_DB:-qdrant}" \
  -e QDRANT_URI="${QDRANT_URI:-http://192.168.140.10:6333}" \
  -e QDRANT_API_KEY="${QDRANT_API_KEY:-Y3NMMGKMhmAtwFbLG4OJPGRtNV7pHtwFbLG4OJPGRtNV7pH8sOlVNtveGcejEl}" \
  -e AIOHTTP_CLIENT_SESSION_TOOL_SERVER_SSL="${AIOHTTP_CLIENT_SESSION_TOOL_SERVER_SSL:-false}" \
  -v "${DATA_VOLUME:-open-webui}":/app/backend/data \
  "$IMAGE_TAG"

echo "Deployment complete. Container: $CONTAINER_NAME, Image: $IMAGE_TAG"
