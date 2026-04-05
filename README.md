# Transcribe_Subs

Generate `.srt` subtitle files for videos that have **no subtitles**, using local AI speech-to-text and LLM-powered cleanup.

Whisper runs **locally on your NVIDIA GPU** — no audio leaves your machine. Only the transcribed text is sent to an LLM API for grammar and formatting cleanup, at a cost of roughly **$0.03 per movie**.

> **Status:** Beta — functional, undergoing real-world testing.

---

## How It Works

```
Video file
  |
  v
FFmpeg ── extract audio (16kHz mono)
  |
  v
Whisper (faster-whisper, local GPU) ── speech to text, ~2-3 min per movie
  |
  v
Pre-processing ── merge micro-entries, split long entries, calculate character budgets
  |
  v
DeepSeek Reasoner (API) ── fix grammar, remove filler words, condense over-budget lines
  |
  v
Post-processing ── Netflix subtitle timing rules, line wrapping, hallucination removal
  |
  v
Polished .srt file
```

**What it does:**
- Scans a folder recursively for video files (MKV, MP4, AVI, MOV, WebM)
- Skips any video that already has subtitles (embedded or sidecar `.srt`)
- Transcribes speech using [faster-whisper](https://github.com/SYSTRAN/faster-whisper) with the `large-v3` model
- Cleans up the raw transcription via LLM — fixes grammar, removes "um/uh/like", condenses long lines to fit reading speed limits
- Formats output as a properly timed `.srt` following Netflix subtitle standards (42 chars/line, 17 CPS, 2-line max)

**What it costs:**

| Step | Cost |
|------|------|
| Whisper transcription | Free (local GPU) |
| LLM cleanup — single movie (2 hrs) | ~$0.03 |
| LLM cleanup — 10-episode season (45 min each) | ~$0.15 |

Use `--skip-llm` for completely free operation with raw Whisper output.

---

## Prerequisites

Before running the installer, make sure you have:

| Requirement | Why | Install |
|---|---|---|
| **Windows 10/11** | PowerShell 5.1+ needed for installer | - |
| **NVIDIA GPU** | Whisper runs on GPU via CUDA (CPU works but is 10-20x slower) | - |
| **NVIDIA Drivers** | Required for GPU access | [nvidia.com/Download](https://www.nvidia.com/Download/index.aspx) |
| **Anaconda or Miniconda** | Manages the isolated Python environment | [anaconda.com/download](https://www.anaconda.com/download) |
| **Git** | Clones the repository | [git-scm.com](https://git-scm.com/download/win) or `winget install Git.Git` |
| **FFmpeg** | Extracts audio from video files | `winget install ffmpeg` or [ffmpeg.org](https://ffmpeg.org/download.html) |

> The installer checks for all of these and offers to install missing ones automatically via `winget`.

---

## Step-by-Step Setup

### Step 1: Run the Installer

Open PowerShell and run this one-liner:

```powershell
irm https://raw.githubusercontent.com/dexusno/Transcribe-Subs/main/install.ps1 | iex
```

The installer will:
1. Check for NVIDIA GPU, drivers, and CUDA — offer to install/update if needed
2. Check for conda, git, ffmpeg — offer to install via winget if missing
3. Clone the repository to `D:\Transcribe_Subs`
4. Create an isolated `transcribe_subs` conda environment with Python 3.11
5. Install all Python dependencies (faster-whisper, requests, python-dotenv)
6. Verify CUDA works end-to-end — automatically fix missing runtime libraries
7. Pre-download the Whisper `large-v3` model (~3 GB, one-time download)
8. Create your `.env` file from the template

> If the installer installs NVIDIA drivers, you will need to **restart your computer** and run the installer again.

### Step 2: Get a DeepSeek API Key

The LLM cleanup step uses DeepSeek Reasoner, which costs roughly 3 cents per movie.

1. Go to [platform.deepseek.com/api_keys](https://platform.deepseek.com/api_keys)
2. Create an account (or sign in)
3. Generate a new API key
4. Copy the key (it starts with `sk-`)

> You can skip this step and use `--skip-llm` mode, which outputs raw Whisper subtitles without LLM cleanup. No API key needed, completely free.

### Step 3: Configure Your API Key

Open the `.env` file in the project directory:

```
D:\Transcribe_Subs\.env
```

Replace `your-key-here` with your actual API key:

```env
# Before:
DEEPSEEK_API_KEY=your-key-here

# After:
DEEPSEEK_API_KEY=sk-abc123your-actual-key-here
```

Save the file. That's all the configuration needed.

> **Security:** The `.env` file is in `.gitignore` and will never be committed to git. Your API key stays local.

### Step 4: Run It

Navigate to the project directory and use the wrapper script:

```powershell
cd D:\Transcribe_Subs

# Generate subtitles for all videos in a folder
.\transcribe_subs.ps1 "D:\Movies\Some Movie"
```

The wrapper script automatically activates the conda environment.

**Or activate the environment manually and use Python directly:**

```powershell
conda activate transcribe_subs
python transcribe_subs.py "D:\Movies\Some Movie"
```

---

## Usage Examples

```powershell
# Basic — generate subtitles for all videos without subs in a folder
.\transcribe_subs.ps1 "D:\Movies\Inception (2010)"

# Whisper-only — free, no API key, no LLM cleanup
.\transcribe_subs.ps1 "D:\Movies\Inception (2010)" -SkipLLM

# Preview — see what would be processed, no actual work
.\transcribe_subs.ps1 "D:\Movies" -DryRun

# Force language — skip auto-detection, assume English
.\transcribe_subs.ps1 "D:\TvSeries\Breaking Bad" -Language en

# Smaller model — faster but less accurate (good for testing)
.\transcribe_subs.ps1 "D:\Movies" -WhisperModel medium

# Limit files — process only the first 5 videos found
.\transcribe_subs.ps1 "D:\Movies" -Limit 5

# Force re-transcribe — overwrite existing .srt files
.\transcribe_subs.ps1 "D:\Movies" -Force

# Use a different LLM — e.g. OpenAI instead of DeepSeek
.\transcribe_subs.ps1 "D:\Movies" -Profile openai

# Parallel processing with custom batch size
.\transcribe_subs.ps1 "D:\Movies" -Parallel 4 -BatchSize 300

# Log to file for review
.\transcribe_subs.ps1 "D:\Movies" -LogFile "C:\logs\transcribe.log"

# Network share (UNC paths supported)
.\transcribe_subs.ps1 "\\NAS\Media\Movies"
```

### All CLI Options

| Option | Description | Default |
|---|---|---|
| `folder` (positional) | Path to scan for video files | Required |
| `-Profile` | LLM profile name from llm_config.json | `deepseek-reasoner` |
| `-BatchSize` | Subtitle entries per LLM API call | 500 |
| `-Parallel` | Concurrent file processing threads | 4 |
| `-WhisperModel` | Override Whisper model size | `large-v3` |
| `-Language` | Force language code (e.g. `en`, `es`, `fr`) | auto-detect |
| `-Limit` | Max number of files to process | unlimited |
| `-Force` | Re-transcribe even if `.srt` already exists | off |
| `-DryRun` | Show what would be processed, do nothing | off |
| `-SkipLLM` | Output raw Whisper `.srt` without LLM cleanup | off |
| `-LogFile` | Also write log output to this file | none |

---

## Configuration

All settings live in `llm_config.json` in the project directory.

### Whisper Settings

```json
"whisper": {
    "model": "large-v3",
    "device": "cuda",
    "compute_type": "int8",
    "language": null,
    "beam_size": 5,
    "vad_filter": true
}
```

| Setting | Description | Options |
|---|---|---|
| `model` | Whisper model size | `tiny`, `base`, `small`, `medium`, `large-v2`, `large-v3` |
| `device` | Run on GPU or CPU | `cuda`, `cpu` |
| `compute_type` | Precision (lower = faster, less VRAM) | `int8`, `float16`, `float32` |
| `language` | Force language or auto-detect | `null` (auto), `"en"`, `"es"`, `"fr"`, etc. |
| `beam_size` | Beam search width (higher = more accurate, slower) | `1`-`10` (default `5`) |
| `vad_filter` | Voice Activity Detection — reduces hallucinations on silence | `true`, `false` |

### Subtitle Rules

```json
"subtitle_rules": {
    "max_chars_per_line": 42,
    "max_lines": 2,
    "target_cps": 17,
    "max_cps": 20,
    "min_duration_ms": 1000,
    "max_duration_ms": 7000,
    "min_gap_ms": 83
}
```

These follow Netflix's subtitle standards. You probably don't need to change them.

| Rule | What it does | Netflix standard |
|---|---|---|
| `max_chars_per_line` | Maximum characters per subtitle line | 42 |
| `max_lines` | Maximum lines per subtitle entry | 2 |
| `target_cps` | Target reading speed (characters per second) | 17 |
| `max_cps` | Absolute max reading speed | 20 |
| `min_duration_ms` | Shortest a subtitle can display | 1000ms |
| `max_duration_ms` | Longest a subtitle can display | 7000ms |
| `min_gap_ms` | Minimum gap between consecutive subtitles | 83ms (2 frames at 24fps) |

### LLM Profiles

The `profiles` section defines LLM backends. You can use any OpenAI-compatible API.

**Default (DeepSeek Reasoner):**
```json
"deepseek-reasoner": {
    "api_url": "https://api.deepseek.com/v1/chat/completions",
    "model": "deepseek-reasoner",
    "api_key_env": "DEEPSEEK_API_KEY",
    "batch_size": 500,
    "parallel": 4,
    "timeout": 300
}
```

**Included profiles:** `deepseek-reasoner`, `deepseek` (chat), `openai`, `groq`, `openrouter`, `local` (Ollama/LM Studio)

**To use a different profile**, add its API key to `.env` and pass `-Profile`:
```powershell
.\transcribe_subs.ps1 "D:\Movies" -Profile openai
```

**To use a local LLM** (free, no API key), start Ollama or LM Studio, then:
```powershell
.\transcribe_subs.ps1 "D:\Movies" -Profile local
```

---

## Output

For each video without subtitles, the tool creates a `.srt` file next to it:

```
D:\Movies\Inception (2010)\
    Inception.mkv
    Inception.srt          <-- generated
```

The `.srt` file:
- Uses UTF-8 encoding with BOM for maximum player compatibility
- Follows SRT format specification (sequential numbering, `HH:MM:SS,mmm` timestamps)
- Works in VLC, MPC-HC, Plex, Jellyfin, Kodi, and all standard media players

---

## Troubleshooting

**"No API key found"** — Make sure `.env` exists and has your key. Use `--skip-llm` to test without an API key.

**"Failed to extract audio"** — FFmpeg is not installed or not in PATH. Run `winget install ffmpeg` and restart your terminal.

**Whisper is very slow** — You may be running on CPU. Check the log output for `device=cuda`. Re-run `install.ps1` to diagnose GPU/CUDA issues.

**Subtitles have wrong language** — Whisper auto-detects language. Force it with `-Language en` (or `es`, `fr`, `de`, etc.).

**Too many hallucinated lines** — Whisper sometimes generates phantom text during silence. The LLM cleanup pass removes most of these. Make sure you're NOT using `--skip-llm`.

---

## License

MIT
