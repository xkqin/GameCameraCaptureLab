#!/usr/bin/env bash

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE="${SOURCE:-}"
DESTINATION="${DESTINATION:-}"
ENDPOINT_HOST="${ENDPOINT_HOST:-}"
EXPECTED_FILES="${EXPECTED_FILES:-12001}"
LOG_FILE="${LOG_FILE:-${SCRIPT_DIR}/rclone_upload_scene_1_3_3000.log}"
STATUS_FILE="${STATUS_FILE:-${SCRIPT_DIR}/rclone_upload_scene_1_3_3000.status}"
LOCK_FILE="${LOCK_FILE:-${SCRIPT_DIR}/.rclone_upload_scene_1_3_3000.lock}"

if [[ -z "$SOURCE" || -z "$DESTINATION" ]]; then
    printf 'Set SOURCE and DESTINATION before running this script.\n' >&2
    exit 1
fi

# The Ceph endpoint is on the internal network; bypass the desktop HTTP proxy.
if [[ -n "$ENDPOINT_HOST" ]]; then
    export NO_PROXY="${NO_PROXY:+$NO_PROXY,}$ENDPOINT_HOST"
    export no_proxy="${no_proxy:+$no_proxy,}$ENDPOINT_HOST"
fi

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    printf 'Another upload is already running.\n' >&2
    exit 1
fi

log() {
    printf '%s %s\n' "$(date '+%F %T')" "$*" | tee -a "$LOG_FILE"
}

set_status() {
    printf '%s\n' "$1" >"$STATUS_FILE"
}

if [[ ! -d "$SOURCE" ]]; then
    log "ERROR source directory not found: $SOURCE"
    set_status 'failed: source directory not found'
    exit 1
fi

local_files=$(find "$SOURCE" -type f | wc -l)
if [[ "$local_files" -ne "$EXPECTED_FILES" ]]; then
    log "ERROR expected $EXPECTED_FILES dataset files, found $local_files"
    set_status "failed: expected $EXPECTED_FILES files, found $local_files"
    exit 1
fi

set_status 'uploading'
log "START source=$SOURCE destination=$DESTINATION files=$local_files bwlimit=60M"

if ! rclone copy "$SOURCE" "$DESTINATION" \
    --fast-list \
    --transfers 4 \
    --checkers 12 \
    --buffer-size 32M \
    --s3-upload-concurrency 4 \
    --s3-chunk-size 16M \
    --bwlimit 60M \
    --contimeout 15s \
    --timeout 5m \
    --retries 8 \
    --low-level-retries 20 \
    --stats 30s \
    --stats-one-line-date \
    --log-file "$LOG_FILE" \
    --log-level INFO; then
    log 'ERROR upload failed; rerunning this script will resume it.'
    set_status 'failed: upload; safe to resume'
    exit 2
fi

set_status 'verifying'
log 'VERIFY checking remote files'

if ! rclone check "$SOURCE" "$DESTINATION" \
    --one-way \
    --size-only \
    --checkers 32 \
    --contimeout 15s \
    --timeout 5m \
    --retries 5 \
    --low-level-retries 10 \
    --log-file "$LOG_FILE" \
    --log-level INFO; then
    log 'ERROR verification failed; see the upload log.'
    set_status 'failed: verification'
    exit 3
fi

remote_json=$(rclone size "$DESTINATION" --json \
    --contimeout 15s --timeout 5m --retries 5 --low-level-retries 10)
log "DONE verified remote=$remote_json"
set_status "complete: $remote_json"
