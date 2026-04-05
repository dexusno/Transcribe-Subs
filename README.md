# Transcribe_Subs

Generate `.srt` subtitle files for videos that have **no subtitles**, using local AI speech-to-text (Whisper on your GPU) and optional LLM-powered cleanup (DeepSeek Reasoner).

> **Status:** Beta — works, but needs real-world testing across different content types.

## What it does

1. **Scans** a folder recursively for video files (MKV, MP4, etc.)
2. **Skips** any video that already has subtitles (embedded or sidecar)
3. **Transcribes** speech to text using [faster-whisper](https://github.com/SYSTRAN/faster-whisper) locally on your NVIDIA GPU
4. **Cleans up** the raw transcription via LLM (fixes grammar, removes filler words, condenses to fit reading speed limits)
5. **Formats** the output as a properly timed `.srt` file following Netflix subtitle standards

All Whisper processing runs **locally on your GPU** — no audio is sent to the cloud. Only the text goes to the LLM API for cleanup (~$0.03 per movie).

## Requirements

- **Windows** (PowerShell 5.1+)
- **NVIDIA GPU** with CUDA support (tested on RTX 4090, works on any modern NVIDIA card)
- **Anaconda or Miniconda** — [Download](https://www.anaconda.com/download)
- **Git** — [Download](https://git-scm.com/download/win)
- **ffmpeg** in PATH — `winget install ffmpeg` or [Download](https://ffmpeg.org/download.html)
- **DeepSeek API key** (optional, for LLM cleanup) — [Get one here](https://platform.deepseek.com/api_keys)

## Install

One-liner — open PowerShell and run:

```powershell
irm https://raw.githubusercontent.com/dexusno/Transcribe-Subs/main/install.ps1 | iex
```

This will:
- Clone the repo to `D:\Transcribe_Subs`
- Create a `transcribe_subs` conda environment with Python 3.11
- Install all dependencies (faster-whisper, requests, python-dotenv)
- Pre-download the Whisper `large-v3` model (~3 GB)

After install, edit `.env` with your DeepSeek API key:
```
DEEPSEEK_API_KEY=sk-your-key-here
```

## Usage

```powershell
# Activate the environment
conda activate transcribe_subs

# Generate subtitles for all videos in a folder
cd D:\Transcribe_Subs
python transcribe_subs.py "D:\Movies\Some Movie"

# Or use the wrapper (activates conda automatically)
.\transcribe_subs.ps1 "D:\Movies\Some Movie"

# Whisper-only mode (no LLM, no API key needed, free)
.\transcribe_subs.ps1 "D:\Movies\Some Movie" -SkipLLM

# Preview what would be processed
.\transcribe_subs.ps1 "D:\Movies" -DryRun

# Force a specific language instead of auto-detect
.\transcribe_subs.ps1 "D:\Movies" -Language en

# Use a smaller/faster Whisper model
.\transcribe_subs.ps1 "D:\Movies" -WhisperModel medium

# Limit to 5 files, force re-transcribe
.\transcribe_subs.ps1 "D:\Movies" -Limit 5 -Force
```

## How it works

```
Video file
  → ffmpeg extracts audio (16kHz mono WAV)
  �� faster-whisper transcribes speech (local GPU, ~2-3 min per movie)
  → Pre-process: merge micro-entries, split mega-entries, calculate char budgets
  → DeepSeek Reasoner: fix grammar, remove filler, condense to fit budgets
  → Post-process: Netflix timing rules, line wrapping, hallucination removal
  → Polished .srt file
```

### Cost

| Step | Cost |
|------|------|
| Whisper transcription | Free (runs locally) |
| LLM cleanup (per 2hr movie) | ~$0.03 |
| LLM cleanup (per 10-ep season) | ~$0.15 |

Use `--skip-llm` for completely free operation (raw Whisper output, no cleanup).

## Configuration

Edit `llm_config.json` to change:
- **Whisper settings** — model size, device, language, VAD filter
- **Subtitle rules** — max chars per line, reading speed (CPS), timing limits
- **LLM profiles** — DeepSeek, OpenAI, Groq, local Ollama, etc.

## License

MIT
