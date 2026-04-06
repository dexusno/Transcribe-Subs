#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# transcribe_subs.sh — Generate subtitles for videos using Whisper + LLM.
#
# Bash wrapper for transcribe_subs.py. Scans a folder recursively for video
# files without subtitles and generates .srt files.
#
# Usage:
#   ./transcribe_subs.sh "/media/movies/Some Movie"
#   ./transcribe_subs.sh "/media/tv/Show" --dry-run
#   ./transcribe_subs.sh "/media/movies" --skip-llm
#   ./transcribe_subs.sh "/media/movies" --language en --profile deepseek
#   ./transcribe_subs.sh "/media/movies" --force --limit 5
# ──────────────────────────────────────────────────────────────────────────────
set -euo pipefail

# ── Configuration ────────────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
PYTHON_EXE="$PROJECT_DIR/.venv/bin/python"
PYTHON_SCRIPT="$PROJECT_DIR/transcribe_subs.py"

# ── Validation ───────────────────────────────────────────────────────────────

usage() {
    echo ""
    echo "  [ERROR] $1"
    echo ""
    echo "  Usage:"
    echo "    ./transcribe_subs.sh \"/media/movies/Some Movie\""
    echo "    ./transcribe_subs.sh \"/media/tv/Show\" --dry-run"
    echo "    ./transcribe_subs.sh \"/media/movies\" --skip-llm"
    echo "    ./transcribe_subs.sh \"/media/movies\" --language en"
    echo "    ./transcribe_subs.sh \"/media/movies\" --whisper-model medium"
    echo "    ./transcribe_subs.sh \"/media/movies\" --profile openai --parallel 4"
    echo "    ./transcribe_subs.sh \"/media/movies\" --force --limit 5"
    echo "    ./transcribe_subs.sh \"/media/movies\" --log-file /tmp/transcribe.log"
    echo ""
    exit 1
}

# Extract folder (first non-flag argument) and collect remaining args
FOLDER=""
PY_ARGS=()
SKIP_NEXT=false

for arg in "$@"; do
    if $SKIP_NEXT; then
        PY_ARGS+=("$arg")
        SKIP_NEXT=false
        continue
    fi

    case "$arg" in
        --profile|--batch-size|--parallel|--whisper-model|--language|--limit|--log-file)
            PY_ARGS+=("$arg")
            SKIP_NEXT=true
            ;;
        --force|--dry-run|--skip-llm)
            PY_ARGS+=("$arg")
            ;;
        *)
            if [[ -z "$FOLDER" ]]; then
                FOLDER="$arg"
            else
                PY_ARGS+=("$arg")
            fi
            ;;
    esac
done

if [[ -z "$FOLDER" ]]; then
    usage "No folder specified."
fi

if [[ ! -d "$FOLDER" ]]; then
    usage "Folder not found: $FOLDER"
fi

FOLDER="$(realpath "$FOLDER")"

# Find Python — prefer venv, fallback to system
if [[ ! -x "$PYTHON_EXE" ]]; then
    if command -v python3 &>/dev/null; then
        PYTHON_EXE="python3"
        echo "  [!] venv not found, using system Python: $PYTHON_EXE"
    else
        usage "Python not found at: $PYTHON_EXE\n         Run install.sh first."
    fi
fi

if [[ ! -f "$PYTHON_SCRIPT" ]]; then
    usage "transcribe_subs.py not found at: $PYTHON_SCRIPT"
fi

# Warn if .env missing (unless --skip-llm or --dry-run)
ENV_FILE="$PROJECT_DIR/.env"
IS_SKIP_LLM=false
IS_DRY_RUN=false
for arg in "$@"; do
    [[ "$arg" == "--skip-llm" ]] && IS_SKIP_LLM=true
    [[ "$arg" == "--dry-run" ]] && IS_DRY_RUN=true
done

if [[ ! -f "$ENV_FILE" ]] && ! $IS_SKIP_LLM && ! $IS_DRY_RUN; then
    echo "  [!] .env not found — LLM cleanup requires an API key"
    echo "      Use --skip-llm for Whisper-only mode, or create .env"
    echo ""
fi

# ── Display ──────────────────────────────────────────────────────────────────

echo ""
echo "  ---------------------------------------------------"
echo "  Python:    $PYTHON_EXE"
echo "  Script:    $PYTHON_SCRIPT"
echo "  Folder:    $FOLDER"
if [[ ${#PY_ARGS[@]} -gt 0 ]]; then
    echo "  Args:      ${PY_ARGS[*]}"
fi
echo "  ---------------------------------------------------"
echo ""

# ── Run ──────────────────────────────────────────────────────────────────────

exec "$PYTHON_EXE" "$PYTHON_SCRIPT" "${PY_ARGS[@]}" "$FOLDER"
