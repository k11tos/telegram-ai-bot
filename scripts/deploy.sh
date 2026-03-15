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

DEPLOY_RUNTIME_ENV_FILE="${DEPLOY_RUNTIME_ENV_FILE:-.deploy-runtime.env}"

if [[ "$APP_DIR" == "~" ]]; then
  APP_DIR="$HOME"
elif [[ "$APP_DIR" == ~/* ]]; then
  APP_DIR="${APP_DIR/#\~/$HOME}"
fi

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

previous_commit="$(git rev-parse HEAD)"
log "Previous commit (before deploy): $previous_commit"

log "Fetching latest refs from origin/$BRANCH"
git fetch --prune origin "$BRANCH"

target_ref="origin/$BRANCH"
if ! git rev-parse --verify --quiet "$target_ref" >/dev/null; then
  echo "Error: branch '$BRANCH' was not found on origin." >&2
  exit 1
fi

log "Checking out branch '$BRANCH' at $target_ref"
if ! git checkout -B "$BRANCH" "$target_ref"; then
  echo "Error: failed to checkout '$target_ref' to local branch '$BRANCH'." >&2
  exit 1
fi


log "Persisting runtime version metadata"
app_version_value="${APP_VERSION:-}"
commit_sha_value="${COMMIT_SHA:-}"
env_file_path="$APP_DIR/$DEPLOY_RUNTIME_ENV_FILE"

if [[ -n "$app_version_value" || -n "$commit_sha_value" ]]; then
  if [[ -z "$app_version_value" ]]; then
    app_version_value="${BRANCH}-$(git rev-parse --short=12 HEAD)"
  fi
  if [[ -z "$commit_sha_value" ]]; then
    commit_sha_value="$(git rev-parse HEAD)"
  fi

  printf 'APP_VERSION=%s\nCOMMIT_SHA=%s\n' "$app_version_value" "$commit_sha_value" > "$env_file_path"

  drop_in_dir="/etc/systemd/system/${SERVICE_NAME}.service.d"
  drop_in_file="$drop_in_dir/override.conf"
  sudo mkdir -p "$drop_in_dir"
  sudo tee "$drop_in_file" >/dev/null <<EOF
[Service]
EnvironmentFile=$env_file_path
EOF

  sudo systemctl daemon-reload
  log "Runtime metadata written to $env_file_path"
else
  log "APP_VERSION and COMMIT_SHA were not provided; skipping runtime metadata update"
fi

log "Restarting service: $SERVICE_NAME"
sudo systemctl restart "$SERVICE_NAME"

if [[ -n "$STARTUP_WAIT_SECONDS" ]]; then
  log "Waiting ${STARTUP_WAIT_SECONDS}s for startup"
  sleep "$STARTUP_WAIT_SECONDS"
fi

log "Running post-restart smoke check (systemctl is-active)"
if ! sudo systemctl is-active --quiet "$SERVICE_NAME"; then
  echo "Error: service '$SERVICE_NAME' is not active after restart." >&2
  log "Recent journal logs for $SERVICE_NAME:"
  sudo journalctl -u "$SERVICE_NAME" -n 60 --no-pager
  exit 1
fi

new_commit="$(git rev-parse HEAD)"
log "New commit (deployed): $new_commit"
log "Deployment complete. Service '$SERVICE_NAME' is active."
