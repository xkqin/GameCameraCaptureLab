#!/usr/bin/env bash

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE="${SOURCE:-}"
DESTINATION="${DESTINATION:-}"
ENDPOINT_HOST="${ENDPOINT_HOST:-}"
EXPECTED_FILES="${EXPECTED_FILES:-16001}"
LOG_FILE="${LOG_FILE:-${SCRIPT_DIR}/rclone_upload_ezcam_4000.log}"
STATUS_FILE="${STATUS_FILE:-${SCRIPT_DIR}/rclone_upload_ezcam_4000.status}"
LOCK_FILE="${LOCK_FILE:-${SCRIPT_DIR}/.rclone_upload_ezcam_4000.lock}"

if [[ -z "$SOURCE" || -z "$DESTINATION" ]]; then
    printf 'Set SOURCE and DESTINATION before running this script.\n' >&2
    exit 1
fi

EXCLUDES=(
    --exclude '/.transcode.lock'
    --exclude '/transcode.log'
    --exclude '/transcode_progress.tsv'
    --exclude '/transcode_failures.tsv'
    --exclude '/batch_console.log'
    --exclude '/transcode.pid'
)

# The Ceph endpoint is on the internal network. The desktop HTTP proxy returns
# 502 for it, so this job must connect to the endpoint directly.
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

local_files=$(find "$SOURCE" -type f \
    ! -name '.transcode.lock' \
    ! -name 'transcode.log' \
    ! -name 'transcode_progress.tsv' \
    ! -name 'transcode_failures.tsv' \
    ! -name 'batch_console.log' \
    ! -name 'transcode.pid' | wc -l)

if [[ "$local_files" -ne "$EXPECTED_FILES" ]]; then
    log "ERROR expected $EXPECTED_FILES dataset files, found $local_files"
    set_status "failed: expected $EXPECTED_FILES files, found $local_files"
    exit 1
fi

set_status 'uploading'
log "START source=$SOURCE destination=$DESTINATION files=$local_files"

if ! rclone copy "$SOURCE" "$DESTINATION" \
    "${EXCLUDES[@]}" \
    --fast-list \
    --transfers 8 \
    --checkers 16 \
    --buffer-size 32M \
    --s3-upload-concurrency 4 \
    --s3-chunk-size 16M \
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
log 'VERIFY checking remote file sizes'

if ! rclone check "$SOURCE" "$DESTINATION" \
    "${EXCLUDES[@]}" \
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
