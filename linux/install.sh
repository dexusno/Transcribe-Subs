#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# install.sh — Install and configure Transcribe_Subs on Debian/Ubuntu
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/dexusno/Transcribe-Subs/main/linux/install.sh | bash
#   ./install.sh
#   ./install.sh --python /usr/bin/python3.12
# ──────────────────────────────────────────────────────────────────────────────
set -euo pipefail

REPO_URL="https://github.com/dexusno/Transcribe-Subs.git"
PYTHON_EXE=""
INSTALL_DIR=""

# Parse arguments
while [[ $# -gt 0 ]]; do
    case "$1" in
        --python)  PYTHON_EXE="$2"; shift 2 ;;
        --dir)     INSTALL_DIR="$2"; shift 2 ;;
        *)         echo "Unknown option: $1"; exit 1 ;;
    esac
done

echo ""
echo "  ================================================================"
echo "    Transcribe_Subs — Installer (Linux)"
echo "    AI subtitle generation: Whisper + DeepSeek"
echo "  ================================================================"
echo ""

# ── Step 1: System packages ─────────────────────────────────────────────────

echo "  [1] Installing system dependencies..."

sudo apt-get update -qq

sudo apt-get install -y -qq \
    python3 \
    python3-pip \
    python3-venv \
    ffmpeg \
    git \
    > /dev/null 2>&1

echo "  [OK] System packages installed (python3, pip, venv, ffmpeg, git)"

# ── Step 2: GPU detection ───────────────────────────────────────────────────

echo ""
echo "  [2] Checking GPU..."

GPU_DETECTED=false
GPU_NAME=""
DRIVER_VERSION=""

if command -v nvidia-smi &>/dev/null; then
    GPU_INFO=$(nvidia-smi --query-gpu=name,driver_version --format=csv,noheader 2>/dev/null || true)
    if [[ -n "$GPU_INFO" ]]; then
        GPU_NAME=$(echo "$GPU_INFO" | cut -d',' -f1 | xargs)
        DRIVER_VERSION=$(echo "$GPU_INFO" | cut -d',' -f2 | xargs)
        GPU_DETECTED=true
        echo "  [OK] NVIDIA GPU: $GPU_NAME (driver $DRIVER_VERSION)"
    fi
fi

if ! $GPU_DETECTED; then
    # Try lspci as fallback
    if command -v lspci &>/dev/null; then
        NVIDIA_PCI=$(lspci | grep -i nvidia 2>/dev/null || true)
        if [[ -n "$NVIDIA_PCI" ]]; then
            GPU_NAME=$(echo "$NVIDIA_PCI" | head -1)
            GPU_DETECTED=true
            echo "  [!] NVIDIA GPU found but drivers not working: $GPU_NAME"
            echo "      Install drivers: sudo apt-get install nvidia-driver"
            echo "      Then reboot and run this installer again."
        fi
    fi
fi

if ! $GPU_DETECTED; then
    echo "  [!] No NVIDIA GPU detected — Whisper will run on CPU (much slower)"
    echo "      GPU:  ~2-3 minutes per movie"
    echo "      CPU:  ~30-60 minutes per movie"
fi

# ── Step 3: Clone or find repository ────────────────────────────────────────

echo ""
echo "  [3] Setting up project directory..."

SCRIPT_DIR=""
NEEDS_CLONE=true

# If run from a file, check if we're already in the repo
if [[ -n "${BASH_SOURCE[0]:-}" ]] && [[ -f "${BASH_SOURCE[0]}" ]]; then
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    PARENT_DIR="$(dirname "$SCRIPT_DIR")"
    if [[ -f "$PARENT_DIR/transcribe_subs.py" ]]; then
        NEEDS_CLONE=false
        SCRIPT_DIR="$PARENT_DIR"
    fi
fi

if $NEEDS_CLONE; then
    if [[ -z "$INSTALL_DIR" ]]; then
        INSTALL_DIR="$(pwd)/Transcribe_Subs"
    fi

    if [[ -f "$INSTALL_DIR/transcribe_subs.py" ]]; then
        echo "  [OK] Repository already exists at: $INSTALL_DIR"
    else
        echo "  Cloning repository to: $INSTALL_DIR"
        git clone --quiet "$REPO_URL" "$INSTALL_DIR"
        echo "  [OK] Repository cloned"
    fi
    SCRIPT_DIR="$INSTALL_DIR"
fi

echo "  Project: $SCRIPT_DIR"

# ── Step 4: Find Python 3.11+ ──────────────────────────────────────────────

echo ""
echo "  [4] Finding Python 3.11+..."

if [[ -z "$PYTHON_EXE" ]]; then
    for candidate in python3.13 python3.12 python3.11 python3; do
        if command -v "$candidate" &>/dev/null; then
            ver=$("$candidate" --version 2>&1 | grep -oP '\d+\.\d+')
            major=$(echo "$ver" | cut -d. -f1)
            minor=$(echo "$ver" | cut -d. -f2)
            if [[ "$major" -ge 3 ]] && [[ "$minor" -ge 11 ]]; then
                PYTHON_EXE="$candidate"
                break
            fi
        fi
    done
fi

if [[ -z "$PYTHON_EXE" ]]; then
    echo "  [ERROR] Python 3.11+ not found."
    echo "          Install with: sudo apt-get install python3.11 python3.11-venv"
    echo "          Or specify:   ./install.sh --python /path/to/python3"
    exit 1
fi

PY_VERSION=$("$PYTHON_EXE" --version 2>&1)
echo "  [OK] Python: $PYTHON_EXE ($PY_VERSION)"

# ── Step 5: Create virtual environment ──────────────────────────────────────

echo ""
echo "  [5] Setting up virtual environment..."

VENV_DIR="$SCRIPT_DIR/.venv"

if [[ -d "$VENV_DIR" ]]; then
    echo "  [OK] Virtual environment already exists"
else
    echo "  Creating virtual environment..."
    "$PYTHON_EXE" -m venv "$VENV_DIR"
    echo "  [OK] Virtual environment created at: $VENV_DIR"
fi

VENV_PYTHON="$VENV_DIR/bin/python"
VENV_PIP="$VENV_DIR/bin/pip"

# ── Step 6: Install Python dependencies ─────────────────────────────────────

echo ""
echo "  [6] Installing Python dependencies..."

"$VENV_PIP" install --quiet --upgrade pip
"$VENV_PIP" install --quiet -r "$SCRIPT_DIR/requirements.txt"
echo "  [OK] faster-whisper, requests, python-dotenv installed"

# ── Step 7: Verify CUDA (if GPU detected) ──────────────────────────────────

if $GPU_DETECTED && [[ -n "$DRIVER_VERSION" ]]; then
    echo ""
    echo "  [7] Verifying CUDA..."

    CUDA_CHECK=$("$VENV_PYTHON" -c "
import sys
try:
    import ctranslate2
    supported = ctranslate2.get_supported_compute_types('cuda')
    if supported:
        print(f'CUDA_OK:{\",\".join(sorted(supported))}')
    else:
        print('CUDA_FAIL:no compute types')
except Exception as e:
    print(f'CUDA_FAIL:{e}')
" 2>/dev/null || echo "CUDA_FAIL:import error")

    if [[ "$CUDA_CHECK" == CUDA_OK* ]]; then
        TYPES=$(echo "$CUDA_CHECK" | cut -d: -f2)
        echo "  [OK] CUDA verified — compute types: $TYPES"
    else
        echo "  [!] CUDA not available — attempting to install runtime libraries..."

        # Discover required CUDA version from ctranslate2 deps
        CU_MAJOR=$("$VENV_PYTHON" -c "
import importlib.metadata, re
deps = importlib.metadata.requires('ctranslate2') or []
for dep in deps:
    m = re.search(r'nvidia-\w+-cu(\d+)', dep)
    if m:
        print(m.group(1))
        break
" 2>/dev/null || echo "")

        if [[ -n "$CU_MAJOR" ]]; then
            echo "  Installing nvidia-cublas-cu$CU_MAJOR nvidia-cudnn-cu$CU_MAJOR..."
            "$VENV_PIP" install --quiet "nvidia-cublas-cu$CU_MAJOR" "nvidia-cudnn-cu$CU_MAJOR" 2>/dev/null || true

            # Re-check
            RECHECK=$("$VENV_PYTHON" -c "
import ctranslate2
types = ctranslate2.get_supported_compute_types('cuda')
print('CUDA_OK' if types else 'CUDA_FAIL')
" 2>/dev/null || echo "CUDA_FAIL")

            if [[ "$RECHECK" == "CUDA_OK" ]]; then
                echo "  [OK] CUDA working after installing runtime libraries"
            else
                echo "  [!] CUDA still not available — Whisper will use CPU"
                echo "      You may need to install NVIDIA CUDA toolkit:"
                echo "      sudo apt-get install nvidia-cuda-toolkit"
            fi
        else
            echo "  [!] Could not determine required CUDA version — using CPU"
        fi
    fi

    # Update config for CPU if CUDA failed
    FINAL_CHECK=$("$VENV_PYTHON" -c "
import ctranslate2
types = ctranslate2.get_supported_compute_types('cuda')
print('OK' if types else 'FAIL')
" 2>/dev/null || echo "FAIL")

    if [[ "$FINAL_CHECK" == "FAIL" ]]; then
        CONFIG_FILE="$SCRIPT_DIR/llm_config.json"
        if [[ -f "$CONFIG_FILE" ]]; then
            sed -i 's/"device":\s*"cuda"/"device": "cpu"/' "$CONFIG_FILE"
            sed -i 's/"compute_type":\s*"int8"/"compute_type": "float32"/' "$CONFIG_FILE"
            echo "  [OK] Config updated for CPU mode"
        fi
    fi
else
    echo ""
    echo "  [7] Skipping CUDA check (no GPU driver detected)"
fi

# ── Step 8: Pre-download Whisper model ──────────────────────────────────────

echo ""
echo "  [8] Pre-downloading Whisper model (large-v3, ~3 GB)..."
echo "      This may take a few minutes on first install."
echo ""

"$VENV_PYTHON" -c "
try:
    from huggingface_hub import snapshot_download
    path = snapshot_download('Systran/faster-whisper-large-v3')
    print(f'  [OK] Model cached at: {path}')
except ImportError:
    from faster_whisper.utils import download_model
    path = download_model('large-v3')
    print(f'  [OK] Model cached at: {path}')
except Exception as e:
    print(f'  [!] Model download failed: {e}')
    print('      It will download on first run instead.')
"

# ── Step 9: Set up config files ─────────────────────────────────────────────

echo ""
echo "  [9] Setting up configuration files..."

# .env
ENV_FILE="$SCRIPT_DIR/.env"
ENV_EXAMPLE="$SCRIPT_DIR/.env.example"

if [[ -f "$ENV_FILE" ]]; then
    echo "  [OK] .env already exists"
elif [[ -f "$ENV_EXAMPLE" ]]; then
    cp "$ENV_EXAMPLE" "$ENV_FILE"
    echo "  [OK] Created .env from .env.example"
    echo "  [!] Edit $ENV_FILE and add your DeepSeek API key"
else
    echo "  [!] .env.example not found — create .env manually"
fi

# llm_config.json
LLM_CONFIG="$SCRIPT_DIR/llm_config.json"
LLM_EXAMPLE="$SCRIPT_DIR/llm_config.example.json"

if [[ -f "$LLM_CONFIG" ]]; then
    echo "  [OK] llm_config.json already exists"
elif [[ -f "$LLM_EXAMPLE" ]]; then
    cp "$LLM_EXAMPLE" "$LLM_CONFIG"
    echo "  [OK] Created llm_config.json from example"
else
    echo "  [!] llm_config.example.json not found"
fi

# ── Step 10: Make wrapper scripts executable ────────────────────────────────

LINUX_DIR="$SCRIPT_DIR/linux"
if [[ -d "$LINUX_DIR" ]]; then
    chmod +x "$LINUX_DIR"/*.sh 2>/dev/null || true
    echo "  [OK] Wrapper scripts made executable"
fi

# ── Done ────────────────────────────────────────────────────────────────────

DEVICE="CPU"
if $GPU_DETECTED && [[ "$FINAL_CHECK" == "OK" ]] 2>/dev/null; then
    DEVICE="$GPU_NAME (CUDA)"
fi

echo ""
echo "  ================================================================"
echo "    Installation complete!"
echo "  ================================================================"
echo ""
echo "  Project:   $SCRIPT_DIR"
echo "  Python:    $VENV_PYTHON"
echo "  Device:    $DEVICE"
echo ""
echo "  Quick start:"
echo "    cd $SCRIPT_DIR"
echo "    ./linux/transcribe_subs.sh \"/media/movies/Some Movie\""
echo ""
echo "    # Whisper-only (no API key needed):"
echo "    ./linux/transcribe_subs.sh \"/media/movies/Some Movie\" --skip-llm"
echo ""
echo "    # Preview without processing:"
echo "    ./linux/transcribe_subs.sh \"/media/movies\" --dry-run"
echo ""

if [[ -f "$ENV_FILE" ]] && grep -q "your-key-here" "$ENV_FILE"; then
    echo "  NEXT STEP: Add your DeepSeek API key to .env"
    echo "             Get your key: https://platform.deepseek.com/api_keys"
    echo ""
fi
