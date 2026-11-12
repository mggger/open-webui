#!/usr/bin/env bash
set -euo pipefail

# Build metadata
BUILD_HASH="${BUILD_HASH:-$(git rev-parse --short HEAD 2>/dev/null || date +%s)}"
IMAGE_TAG="${IMAGE_TAG:-archeround/open-webui:latest}"
CONTAINER_NAME="${CONTAINER_NAME:-open-webui-archeround}"

BUILD_OPTIONS=(--pull)
if [[ "${NO_CACHE:-false}" == "true" ]]; then
  BUILD_OPTIONS+=(--no-cache)
fi

# Build the image
docker build \
  "${BUILD_OPTIONS[@]}" \
  --build-arg BUILD_HASH="$BUILD_HASH" \
  --build-arg USE_CUDA=false \
  --build-arg USE_OLLAMA=false \
  --build-arg USE_PERMISSION_HARDENING=false \
  -t "$IMAGE_TAG" .

# Do not replace the running container with an incomplete backend image.
docker run --rm --entrypoint python3 "$IMAGE_TAG" -c \
  "import uvicorn, livekit, mcp; print('Backend runtime dependencies verified')"

# Repair users whose id is an empty string (see scripts/fix_empty_user_id.py).
#   FIX_EMPTY_USER_ID=check (default) report only, never blocks the deploy
#   FIX_EMPTY_USER_ID=apply  back up the SQLite DB, then rewrite the ids
#   FIX_EMPTY_USER_ID=skip   do nothing
FIX_EMPTY_USER_ID="${FIX_EMPTY_USER_ID:-check}"
DATA_VOLUME="${DATA_VOLUME:-open-webui}"
DB_URL="${DATABASE_URL:-sqlite:////app/backend/data/webui.db}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FIX_SCRIPT="$SCRIPT_DIR/scripts/fix_empty_user_id.py"

run_fix_empty_user_id() {
  docker run --rm --network host \
    -v "$DATA_VOLUME":/app/backend/data \
    -v "$FIX_SCRIPT":/tmp/fix_empty_user_id.py:ro \
    --entrypoint python3 "$IMAGE_TAG" /tmp/fix_empty_user_id.py --db "$DB_URL" "$@"
}

if [[ "$FIX_EMPTY_USER_ID" != "skip" && ! -f "$FIX_SCRIPT" ]]; then
  echo "WARNING: $FIX_SCRIPT not found, skipping empty user id check"
  FIX_EMPTY_USER_ID=skip
fi

case "$FIX_EMPTY_USER_ID" in
  skip) ;;
  check)
    echo "Checking for users with an empty id (dry run)..."
    run_fix_empty_user_id || echo "WARNING: empty user id check reported a problem (see above)"
    ;;
  apply)
    # Dry run first, while the old container is still serving: abort before any downtime
    # if the data cannot be repaired automatically.
    echo "Checking for users with an empty id (dry run)..."
    run_fix_empty_user_id
    ;;
  *)
    echo "FIX_EMPTY_USER_ID must be check, apply or skip (got: $FIX_EMPTY_USER_ID)" >&2
    exit 1
    ;;
esac

# Stop and remove any existing container to enable zero-conf updates
if docker ps -a --format '{{.Names}}' | grep -Eq "^${CONTAINER_NAME}$"; then
  docker rm -f "$CONTAINER_NAME"
fi

if [[ "$FIX_EMPTY_USER_ID" == "apply" ]]; then
  # The old container is stopped now, so nothing else writes to the database.
  if [[ "$DB_URL" == sqlite:* ]]; then
    BACKUP_SUFFIX="bak-$(date +%Y%m%d-%H%M%S)"
    echo "Backing up webui.db to webui.db.$BACKUP_SUFFIX in volume $DATA_VOLUME..."
    docker run --rm -v "$DATA_VOLUME":/app/backend/data --entrypoint sh "$IMAGE_TAG" -c \
      "cd /app/backend/data && for f in webui.db webui.db-wal webui.db-shm; do [ -f \"\$f\" ] && cp -p \"\$f\" \"\$f.$BACKUP_SUFFIX\"; done; true"
  else
    echo "Non-SQLite database: make sure you have your own backup before applying."
  fi
  echo "Repairing users with an empty id..."
  # The repair runs in one transaction; on failure nothing is written, so still start the new container.
  run_fix_empty_user_id --apply || echo "WARNING: empty user id repair failed; database left unchanged"
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
  -e BIG_EVENTS_LLM_ENDPOINT="${BIG_EVENTS_LLM_ENDPOINT:-http://10.168.140.9:8000/v1}" \
  -e BIG_EVENTS_LLM_MODEL="${BIG_EVENTS_LLM_MODEL:-openai/gpt-oss-120b}" \
  -e BIG_EVENTS_LLM_API_KEY="${BIG_EVENTS_LLM_API_KEY:-}" \
  -e BIG_EVENTS_LLM_TEMPERATURE="${BIG_EVENTS_LLM_TEMPERATURE:-0.3}" \
  -e BIG_EVENTS_LLM_MAX_TOKENS="${BIG_EVENTS_LLM_MAX_TOKENS:-4096}" \
  -e BIG_EVENTS_LLM_MAX_ATTEMPTS="${BIG_EVENTS_LLM_MAX_ATTEMPTS:-2}" \
  -e BIG_EVENTS_LLM_TIMEOUT_SECONDS="${BIG_EVENTS_LLM_TIMEOUT_SECONDS:-180}" \
  -e VECTOR_DB="${VECTOR_DB:-qdrant}" \
  -e QDRANT_URI="${QDRANT_URI:-http://192.168.140.10:6333}" \
  -e QDRANT_API_KEY="${QDRANT_API_KEY:-Y3NMMGKMhmAtwFbLG4OJPGRtNV7pHtwFbLG4OJPGRtNV7pH8sOlVNtveGcejEl}" \
  -e AIOHTTP_CLIENT_SESSION_TOOL_SERVER_SSL="${AIOHTTP_CLIENT_SESSION_TOOL_SERVER_SSL:-false}" \
  -v "$DATA_VOLUME":/app/backend/data \
  "$IMAGE_TAG"

echo "Deployment complete. Container: $CONTAINER_NAME, Image: $IMAGE_TAG"
