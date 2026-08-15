#!/usr/bin/env bash

set -uo pipefail

SOURCE_ROOT="${SOURCE_ROOT:-}"
OUTPUT_ROOT="${OUTPUT_ROOT:-}"
FFMPEG="${FFMPEG:-ffmpeg}"
FFPROBE="${FFPROBE:-ffprobe}"
EXPECTED_VIDEOS="${EXPECTED_VIDEOS:-3000}"
VIDEO_BITRATE="${VIDEO_BITRATE:-10M}"
MAX_BITRATE="${MAX_BITRATE:-12M}"
BUFFER_SIZE="${BUFFER_SIZE:-20M}"

if [[ -z "$SOURCE_ROOT" || -z "$OUTPUT_ROOT" ]]; then
    printf 'Set SOURCE_ROOT and OUTPUT_ROOT before running this script.\n' >&2
    exit 1
fi

mkdir -p "$OUTPUT_ROOT"

LOCK_FILE="$OUTPUT_ROOT/.transcode.lock"
PID_FILE="$OUTPUT_ROOT/transcode.pid"
LOG_FILE="$OUTPUT_ROOT/transcode.log"
PROGRESS_FILE="$OUTPUT_ROOT/transcode_progress.tsv"
FAILURE_FILE="$OUTPUT_ROOT/transcode_failures.tsv"

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    printf 'Another conversion is already running. Lock: %s\n' "$LOCK_FILE" >&2
    exit 1
fi

printf '%s\n' "$$" >"$PID_FILE"
trap 'rm -f "$PID_FILE"' EXIT

log() {
    printf '%s %s\n' "$(date '+%F %T')" "$*" | tee -a "$LOG_FILE"
}

valid_output() {
    local file=$1
    local codec audio_streams

    [[ -s "$file" ]] || return 1
    codec=$(
        "$FFPROBE" -v error -select_streams v:0 \
            -show_entries stream=codec_name -of default=nw=1:nk=1 "$file" 2>/dev/null
    )
    audio_streams=$(
        "$FFPROBE" -v error -select_streams a \
            -show_entries stream=index -of csv=p=0 "$file" 2>/dev/null | wc -l
    )
    [[ "$codec" == 'hevc' && "$audio_streams" -eq 0 ]]
}

if [[ ! -d "$SOURCE_ROOT" ]]; then
    log "ERROR source directory does not exist: $SOURCE_ROOT"
    exit 1
fi
if ! command -v "$FFMPEG" >/dev/null 2>&1 || ! command -v "$FFPROBE" >/dev/null 2>&1; then
    log 'ERROR FFmpeg/FFprobe is unavailable.'
    exit 1
fi
if ! "$FFMPEG" -hide_banner -encoders 2>/dev/null | grep 'hevc_nvenc' >/dev/null; then
    log 'ERROR this FFmpeg build has no hevc_nvenc encoder.'
    exit 1
fi

# Copy CSV/JSON sidecars and the directory structure, but never copy MP4s.
rsync -a --exclude='*.mp4' --exclude='*.MP4' "$SOURCE_ROOT/" "$OUTPUT_ROOT/"

mapfile -d '' videos < <(
    find "$SOURCE_ROOT" -type f -iname '*.mp4' -print0 | sort -zV
)
total=${#videos[@]}
if [[ "$total" -ne "$EXPECTED_VIDEOS" ]]; then
    log "ERROR expected $EXPECTED_VIDEOS MP4 files, found $total; refusing an incomplete batch."
    exit 1
fi

if [[ ! -e "$PROGRESS_FILE" ]]; then
    printf 'timestamp\tindex\tstatus\tinput_bytes\toutput_bytes\tratio_percent\telapsed_seconds\trelative_path\n' >"$PROGRESS_FILE"
fi
if [[ ! -e "$FAILURE_FILE" ]]; then
    printf 'timestamp\tindex\trelative_path\n' >"$FAILURE_FILE"
fi

log "START total=$total codec=hevc_nvenc bitrate=$VIDEO_BITRATE audio=removed"

success=0
skipped=0
failed=0
batch_start=$(date +%s)

for i in "${!videos[@]}"; do
    src=${videos[$i]}
    rel=${src#"$SOURCE_ROOT"/}
    out="$OUTPUT_ROOT/$rel"
    tmp="$out.part.mp4"
    index=$((i + 1))

    if valid_output "$out"; then
        skipped=$((skipped + 1))
        if (( index % 100 == 0 || index == total )); then
            log "PROGRESS $index/$total converted=$success skipped=$skipped failed=$failed"
        fi
        continue
    fi

    mkdir -p "$(dirname "$out")"
    rm -f "$tmp"
    input_bytes=$(stat -c %s "$src")
    item_start=$(date +%s)

    log "CONVERT $index/$total $rel"
    if "$FFMPEG" -hide_banner -nostdin -y -loglevel warning \
        -i "$src" \
        -map 0:v:0 -an \
        -c:v hevc_nvenc \
        -preset p6 -tune hq -profile:v main \
        -rc vbr -multipass fullres \
        -b:v "$VIDEO_BITRATE" -maxrate "$MAX_BITRATE" -bufsize "$BUFFER_SIZE" \
        -rc-lookahead 32 -spatial-aq 1 -temporal-aq 1 -aq-strength 8 -bf 4 \
        -tag:v hvc1 -movflags +faststart \
        "$tmp" >>"$LOG_FILE" 2>&1 \
        && valid_output "$tmp"; then
        mv -f "$tmp" "$out"
        output_bytes=$(stat -c %s "$out")
        elapsed=$(( $(date +%s) - item_start ))
        ratio=$(awk -v source="$input_bytes" -v output="$output_bytes" \
            'BEGIN { printf "%.2f", 100 * output / source }')
        printf '%s\t%d\tconverted\t%d\t%d\t%s\t%d\t%s\n' \
            "$(date '+%F %T')" "$index" "$input_bytes" "$output_bytes" \
            "$ratio" "$elapsed" "$rel" >>"$PROGRESS_FILE"
        success=$((success + 1))
    else
        rm -f "$tmp"
        failed=$((failed + 1))
        printf '%s\t%d\t%s\n' "$(date '+%F %T')" "$index" "$rel" >>"$FAILURE_FILE"
        log "FAILED $index/$total $rel"
    fi
done

batch_elapsed=$(( $(date +%s) - batch_start ))
log "DONE total=$total converted=$success skipped=$skipped failed=$failed elapsed_seconds=$batch_elapsed"

if (( failed > 0 )); then
    exit 2
fi
