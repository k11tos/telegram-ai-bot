#!/usr/bin/env bash
set -euo pipefail

log() {
  echo "[deploy] $*"
}

require_command() {
  local cmd="$1"
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "Error: required command '$cmd' is not installed or not in PATH." >&2
    exit 1
  fi
}

require_env() {
  local name="$1"
  if [[ -z "${!name:-}" ]]; then
    echo "Error: required environment variable '$name' is not set." >&2
    exit 1
  fi
}

BRANCH="${BRANCH:-main}"
STARTUP_WAIT_SECONDS="${STARTUP_WAIT_SECONDS:-}"

log "Validating required commands"
require_command git
require_command systemctl

log "Validating required environment variables"
require_env APP_DIR
require_env SERVICE_NAME

log "Changing directory to APP_DIR: $APP_DIR"
if ! cd "$APP_DIR"; then
  echo "Error: failed to change directory to APP_DIR '$APP_DIR'." >&2
  exit 1
fi

log "Verifying git repository"
if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "Error: APP_DIR '$APP_DIR' is not a git repository." >&2
  exit 1
fi

current_commit="$(git rev-parse HEAD)"
log "Current commit: $current_commit"

log "Fetching latest refs from origin"
git fetch origin

log "Checking out branch: $BRANCH"
if ! git checkout "$BRANCH"; then
  echo "Error: failed to checkout branch '$BRANCH'." >&2
  exit 1
fi

log "Pulling latest changes for branch: $BRANCH"
git pull --ff-only origin "$BRANCH"

log "Restarting service: $SERVICE_NAME"
sudo systemctl restart "$SERVICE_NAME"

if [[ -n "$STARTUP_WAIT_SECONDS" ]]; then
  log "Waiting ${STARTUP_WAIT_SECONDS}s for startup"
  sleep "$STARTUP_WAIT_SECONDS"
fi

new_commit="$(git rev-parse HEAD)"
log "Deployment complete. Current commit: $new_commit"
