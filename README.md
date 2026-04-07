# Transcribe_Subs

> Generate `.srt` subtitles for videos that have **no subtitles**, using local AI speech-to-text and LLM-powered cleanup.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux-blue)](https://github.com/dexusno/Transcribe-Subs)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/)

Whisper runs **locally on your NVIDIA GPU** — no audio leaves your machine. Only the transcribed text is sent to an LLM API for punctuation and cleanup, at a cost of roughly **$0.06 per movie**.

---

## Table of Contents

- [Features](#features)
- [How It Works](#how-it-works)
- [Cost](#cost)
- [Installation](#installation)
  - [Windows](#windows-install)
  - [Linux (Debian/Ubuntu)](#linux-install)
- [Configuration](#configuration)
- [Usage](#usage)
- [Advanced Configuration](#advanced-configuration)
- [Under the Hood](#under-the-hood)
- [Troubleshooting](#troubleshooting)
- [Disclaimer](#disclaimer)
- [License](#license)

---

## Features

- **Fully automatic** — point at a folder, get subtitles for every video without subs
- **Local speech-to-text** — audio never leaves your machine (Whisper on your GPU)
- **AI-polished output** — LLM fixes punctuation, misheard words, and filler
- **Cheap** — ~$0.06 per movie via DeepSeek API
- **Smart filtering** — skips videos that already have subtitles (embedded or sidecar)
- **Cross-platform** — Windows and Linux (Debian/Ubuntu) supported
- **Netflix-style formatting** — 42 chars/line, 2-line max, proper timing and reading speed

---

## How It Works

```
Video file
  │
  ▼
[1] Whisper (local GPU) ─── speech to text with word-level timestamps
  │
  ▼
[2] LLM Punctuation ─── add periods, commas, capitals to raw transcript
  │
  ▼
[3] Re-segmentation ─── rebuild entries at sentence boundaries (code)
  │
  ▼
[4] LLM Cleanup ─── fix misheard words, remove filler
  │
  ▼
[5] Post-processing ─── timing rules, merge, line wrap, validate (code)
  │
  ▼
Polished .srt file
```

**What it does:**
- Scans a folder recursively for video files (MKV, MP4, AVI, MOV, WebM)
- Skips any video that already has subtitles (embedded or sidecar `.srt`)
- Transcribes speech using [faster-whisper](https://github.com/SYSTRAN/faster-whisper) with the `large-v3` model
- Fixes punctuation and misheard words via LLM (DeepSeek Chat or any OpenAI-compatible API)
- Formats output following professional subtitle standards (42 chars/line, 2-line max, readable pacing)

---

## Cost

| Step | Cost |
|------|------|
| Whisper transcription | Free (runs on your GPU) |
| LLM processing — 45-min episode | ~$0.03 |
| LLM processing — 2-hr movie | ~$0.06 |
| LLM processing — 10-episode season | ~$0.30 |

> [!NOTE]
> These estimates are based on **DeepSeek Chat pricing as of April 2026** (the default and recommended provider). Pricing may change over time. Costs will also vary if you use a different provider or model — OpenAI GPT-4o, Anthropic Claude, and other premium models can cost 10-50x more. DeepSeek Chat offers the best cost/quality balance for this task. Always check your provider's current pricing before processing large libraries.

> The `--skip-llm` flag exists for testing but is **not recommended** — raw Whisper output lacks proper punctuation and sentence boundaries, making it difficult to read as subtitles. The LLM passes are essential for usable output.

---

## Installation

Choose your platform:

### Requirements (both platforms)

| Component | Required | Notes |
|---|---|---|
| **NVIDIA GPU** | Recommended | CPU works but is 10-20x slower |
| **Python 3.11+** | Yes | Installed by the script if missing |
| **Git** | Yes | Installed by the script if missing |
| **FFmpeg** | Yes | Installed by the script if missing |
| **DeepSeek API key** | Yes | Free to create, pay-as-you-go |

> Both installers automatically detect and install missing dependencies.

---

### Windows Install

#### Prerequisites
- Windows 10 or 11
- PowerShell 5.1+ (built into Windows)
- [Anaconda or Miniconda](https://www.anaconda.com/download) (the installer can install Miniconda for you)
- NVIDIA drivers (the installer can install them for you)

#### Step 1: Run the installer

Open PowerShell, navigate to where you want to install, and run the one-liner:

```powershell
cd D:\
irm https://raw.githubusercontent.com/dexusno/Transcribe-Subs/main/install.ps1 | iex
```

This creates a `Transcribe_Subs` folder in your current directory (e.g. `D:\Transcribe_Subs`).

The installer will:
1. Check for NVIDIA GPU, drivers, and CUDA
2. Check for conda, git, ffmpeg — offer to install via `winget` if missing
3. Clone the repository
4. Create an isolated `transcribe_subs` conda environment with Python 3.11
5. Install all Python dependencies
6. Verify CUDA works end-to-end — automatically fix missing runtime libraries
7. Pre-download the Whisper `large-v3` model (~3 GB, one-time download)
8. Create config files from templates

> If the installer installs NVIDIA drivers, you will need to **restart your computer** and run the installer again.

#### Step 2: Get your DeepSeek API key

See [Configuration](#configuration) below.

#### Step 3: Run it

```powershell
cd D:\Transcribe_Subs
.\transcribe_subs.ps1 "D:\Movies\Some Movie"
```

The wrapper automatically activates the conda environment.

---

### Linux Install

#### Prerequisites
- Debian 13, Ubuntu 22.04+, or similar
- `sudo` access (for installing system packages)
- NVIDIA drivers: `sudo apt install nvidia-driver` (and reboot afterwards)

#### Step 1: Run the installer

Open a terminal, navigate to where you want to install, and run the one-liner:

```bash
cd /opt
curl -fsSL https://raw.githubusercontent.com/dexusno/Transcribe-Subs/main/linux/install.sh | bash
```

This creates a `Transcribe_Subs` folder in your current directory (e.g. `/opt/Transcribe_Subs`).

The installer will:
1. Check for NVIDIA GPU and drivers
2. `apt-get install` system dependencies (python3, python3-venv, git, ffmpeg)
3. Clone the repository
4. Create a Python `venv` at `.venv/` inside the project
5. Install all Python dependencies
6. Verify CUDA works end-to-end — automatically fix missing runtime libraries
7. Pre-download the Whisper `large-v3` model (~3 GB, one-time download)
8. Create config files from templates

> If you need to install NVIDIA drivers first: `sudo apt install nvidia-driver && sudo reboot`

#### Step 2: Get your DeepSeek API key

See [Configuration](#configuration) below.

#### Step 3: Run it

```bash
cd /opt/Transcribe_Subs
./linux/transcribe_subs.sh "/media/movies/Some Movie"
```

The wrapper runs the venv Python directly — no manual activation needed.

---

## Configuration

### Get a DeepSeek API key

The LLM is a required part of the pipeline — it adds punctuation to the raw transcript and fixes speech recognition errors. Without it, the output lacks sentence boundaries and is not usable as subtitles.

**DeepSeek** is recommended — the pipeline is developed and tested with DeepSeek Chat. It offers the best balance of quality, speed, and cost (~$0.03 per episode):

1. Go to [platform.deepseek.com/api_keys](https://platform.deepseek.com/api_keys)
2. Create an account (or sign in)
3. Add some credit ($2-5 is enough for hundreds of episodes)
4. Generate a new API key
5. Copy the key (it starts with `sk-`)

> **Other providers** (OpenAI, Groq, OpenRouter) are also supported — see [LLM Profiles](#llm-profiles). Any OpenAI-compatible API will work, though results may vary as the prompts are optimised for DeepSeek.

### Add your key to .env

Open the `.env` file in the project directory and replace `your-key-here`:

```env
DEEPSEEK_API_KEY=sk-abc123your-actual-key-here
```

Save the file. That's all the configuration needed to start using the tool.

> **Security:** The `.env` file is in `.gitignore` and will never be committed to git. Your API key stays local.

---

## Usage

### Windows

```powershell
# Basic — generate subtitles for all videos without subs
.\transcribe_subs.ps1 "D:\Movies\Inception (2010)"

# Preview — see what would be processed
.\transcribe_subs.ps1 "D:\Movies" -DryRun

# Force language — skip auto-detection
.\transcribe_subs.ps1 "D:\TvSeries\Breaking Bad" -Language en

# Smaller model — faster but less accurate (good for testing)
.\transcribe_subs.ps1 "D:\Movies" -WhisperModel medium

# Limit files
.\transcribe_subs.ps1 "D:\Movies" -Limit 5

# Force re-transcribe (overwrite existing .srt)
.\transcribe_subs.ps1 "D:\Movies" -Force

# Use a different LLM
.\transcribe_subs.ps1 "D:\Movies" -Profile openai

# Network share (UNC paths supported)
.\transcribe_subs.ps1 "\\NAS\Media\Movies"
```

### Linux

```bash
# Basic — generate subtitles for all videos without subs
./linux/transcribe_subs.sh "/media/movies/Inception (2010)"

# Preview — see what would be processed
./linux/transcribe_subs.sh "/media/movies" --dry-run

# Force language — skip auto-detection
./linux/transcribe_subs.sh "/media/tv/Breaking Bad" --language en

# Smaller model — faster but less accurate
./linux/transcribe_subs.sh "/media/movies" --whisper-model medium

# Limit files
./linux/transcribe_subs.sh "/media/movies" --limit 5

# Force re-transcribe (overwrite existing .srt)
./linux/transcribe_subs.sh "/media/movies" --force

# Use a different LLM
./linux/transcribe_subs.sh "/media/movies" --profile openai

# Network share
./linux/transcribe_subs.sh "/mnt/nas/movies"
```

### CLI Options

| Windows flag | Linux flag | Description | Default |
|---|---|---|---|
| `folder` | `folder` | Path to scan for video files | Required |
| `-Profile` | `--profile` | LLM profile from llm_config.json | `deepseek` |
| `-BatchSize` | `--batch-size` | Subtitle entries per LLM API call | 150 |
| `-Parallel` | `--parallel` | Concurrent file processing threads | 4 |
| `-WhisperModel` | `--whisper-model` | Override Whisper model size | `large-v3` |
| `-Language` | `--language` | Force language code (e.g. `en`, `es`) | auto-detect |
| `-Limit` | `--limit` | Max number of files to process | unlimited |
| `-Force` | `--force` | Re-transcribe even if `.srt` exists | off |
| `-DryRun` | `--dry-run` | Show what would be processed | off |
| `-SkipLLM` | `--skip-llm` | Output raw Whisper without LLM cleanup | off |
| `-LogFile` | `--log-file` | Also write log output to this file | none |

### Output

For each video without subtitles, the tool creates a `.srt` file next to it:

```
Movies/Inception (2010)/
    Inception.mkv
    Inception.srt          ← generated
```

The `.srt` file:
- Uses UTF-8 encoding with BOM for maximum player compatibility
- Follows SRT format specification
- Works in VLC, MPC-HC, Plex, Jellyfin, Kodi, and all standard media players

---

## Advanced Configuration

All settings live in `llm_config.json` in the project directory.

### Whisper Settings

```json
"whisper": {
    "model": "large-v3",
    "device": "cuda",
    "compute_type": "int8",
    "language": null,
    "beam_size": 10,
    "best_of": 5,
    "patience": 2.0,
    "vad_filter": false,
    "word_timestamps": true,
    "condition_on_previous_text": false
}
```

| Setting | Description | Options |
|---|---|---|
| `model` | Whisper model size | `tiny`, `base`, `small`, `medium`, `large-v2`, `large-v3` |
| `device` | Run on GPU or CPU | `cuda`, `cpu` |
| `compute_type` | Precision (lower = faster, less VRAM) | `int8`, `float16`, `float32` |
| `language` | Force language or auto-detect | `null` (auto), `"en"`, `"es"`, `"fr"`, etc. |
| `beam_size` | Beam search width (higher = more accurate) | `1`-`15` |
| `best_of` | Candidates per segment (picks the best) | `1`-`5` |
| `patience` | Beam search patience | `1.0`-`3.0` |
| `vad_filter` | Voice Activity Detection filter | `false` (default), `true` |
| `word_timestamps` | Word-level timing for precise segmentation | `true` (required) |
| `condition_on_previous_text` | Feed previous text as context to next chunk | `false` (default) |

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

> [!WARNING]
> These defaults are tuned to work with the current pipeline logic. Changing values like `max_lines` or `max_chars_per_line` may require changes to the code — the line wrapping, entry merging, and sentence splitting logic are built around these specific values. Only modify these if you understand the impact on the processing pipeline.

| Rule | Default | What it does | How it's enforced |
|---|---|---|---|
| `max_chars_per_line` | 42 | Preferred max characters per line | Soft limit — lines may slightly exceed to avoid dropping words |
| `max_lines` | 2 | Maximum lines per subtitle entry | Hard limit |
| `target_cps` | 17 | Target reading speed (chars/sec) | Used for character budget calculations |
| `max_cps` | 20 | Maximum reading speed | Logged as warning, not hard-enforced |
| `min_duration_ms` | 1000 | Shortest a subtitle can display (ms) | Extended to meet minimum when possible |
| `max_duration_ms` | 7000 | Longest a subtitle can display (ms) | Long entries split in pre-processing |
| `min_gap_ms` | 83 | Minimum gap between subtitles (ms) | Hard-enforced (83ms = 2 frames at 24fps) |

### LLM Profiles

The `profiles` section in `llm_config.json` defines LLM backends. You can use any OpenAI-compatible API.

**Default profile (DeepSeek Chat):**
```json
"deepseek": {
    "api_url": "https://api.deepseek.com/v1/chat/completions",
    "model": "deepseek-chat",
    "api_key_env": "DEEPSEEK_API_KEY",
    "batch_size": 150,
    "parallel": 4,
    "timeout": 120
}
```

**Included profiles:** `deepseek` (default, recommended), `openai`, `groq`, `openrouter`, `local` (Ollama/LM Studio)

**To use a different cloud provider**, add its API key to `.env` and pass the profile flag:

```powershell
# Windows
.\transcribe_subs.ps1 "D:\Movies" -Profile openai
```

```bash
# Linux
./linux/transcribe_subs.sh "/media/movies" --profile openai
```

> [!CAUTION]
> **Local LLMs** (Ollama, LM Studio) require very capable hardware and large models (14B+ parameters) to produce acceptable results. Small local models (7B and below) will struggle with the punctuation and error correction tasks and may produce poor quality subtitles. The cloud APIs are strongly recommended unless you have high-end hardware (48GB+ VRAM) and can run models like Qwen 2.5 72B or Llama 3 70B.

---

## Under the Hood

<details>
<summary><strong>Click to expand — explains the engineering decisions behind the pipeline</strong></summary>

### Why 5 Passes?

Early versions used a simpler pipeline (Whisper → single LLM pass → done) but produced poor results: sentence bleeding, dropped words, garbled punctuation. Each problem required a focused solution, leading to the current 5-pass architecture where each step does one thing well.

### Pass 1: Whisper Transcription

**Model:** `large-v3` with INT8 quantization — best accuracy while using only ~3-4 GB VRAM.

**Word-level timestamps:** Instead of trusting Whisper's segment boundaries (which often dump 6-25 seconds of multi-speaker dialogue into a single block), we extract per-word timestamps and rebuild subtitle entries ourselves.

**`condition_on_previous_text=false`:** The default (`true`) feeds previous output as context to the next chunk, which gives better punctuation consistency but causes catastrophic cascading errors — if Whisper mishears a place name once, it repeats the error for the entire file. We tested both extensively. With `false`, each 30-second chunk is independent.

**VAD disabled:** Silero VAD was filtering out 30 minutes of a 57-minute episode — removing quiet dialogue along with silence. Whisper's own silence handling plus our hallucination detection in post-processing is sufficient.

**Quality settings** (`beam_size=10`, `best_of=5`, `patience=2.0`): We prioritise accuracy over speed. This is a one-shot-per-file tool for content with no subtitles at all, so spending extra time is acceptable.

### Pass 2: LLM Punctuation

**The problem:** With `condition_on_previous_text=false`, Whisper produces ~27% of entries with no punctuation at all — entire passages of lowercase text with no periods, commas, or capitals.

**Why not a simple approach?** We tried several:
- Having the LLM punctuate per-entry with `[N]` indexing — it added false periods at entry boundaries because it couldn't see that sentences continued in the next entry
- Sending continuous text and remapping by word count — fragile, any word change breaks alignment
- NLP libraries (spaCy, PySBD) — adds dependencies and doesn't work well on unpunctuated text

**The solution:** Overlapping windows with `[N]` indexing. Each batch includes 20 entries of context overlap from the previous batch. The LLM sees flowing dialogue across entries and can detect where sentences truly end. Only the non-overlapping portion of each response is used, giving us perfect `[N]` mapping back to entries.

**Strict prompt:** The LLM is instructed with emphatic language ("You MUST read ALL lines THOROUGHLY", "Do NOT place a period just because a line ends", "Read AHEAD to find where each sentence actually ends"). Testing showed this produces significantly better results than polite instructions.

**ALL CAPS normalisation:** Whisper sometimes outputs entire sections in ALL CAPS (dramatic scenes, shouting). Before the LLM sees the text, we convert any all-uppercase entries to lowercase — the LLM adds proper capitalisation back.

### Pass 3: Sentence Re-Segmentation

After punctuation, the code rebuilds entries from scratch at sentence boundaries. This is pure code — no LLM involved.

1. Flatten all entries into a word stream with timestamps
2. Group words into sentences using punctuation (`.` `!` `?`)
3. Sentences that fit within limits (84 chars, 7 seconds) become one entry
4. Long sentences are split at clause boundaries with tiered preference:
   - **Tier 1:** comma + conjunction ("..., but") — best
   - **Tier 2:** after any comma
   - **Tier 3:** before a conjunction without comma
   - **Tier 4:** nearest to midpoint — last resort

An abbreviation list (Mr., Mrs., Dr., D.I., etc.) prevents false sentence splits.

**The key insight:** Build sentences first, then fit them into entries. The previous approach (build entries, hope sentences align) caused constant bleeding.

### Pass 4: LLM Cleanup

Now that entries contain clean, properly punctuated sentences, the LLM can focus purely on fixing speech recognition errors:

- **Misheard words:** "lorry ticket" → "lottery ticket", "tandem gloid" → "tandem glider"
- **Filler words:** um, uh, er, like, you know, I mean
- **Stuttering:** "it's it's important" → "it's important"
- **False starts:** "I was— I went there" → "I went there"

The prompt explicitly says: "Do NOT remove, shorten, or rephrase anything else. Keep every word." Earlier versions asked the LLM to also condense text to fit character budgets, but LLMs cannot count characters and were dropping words. The condensation was moved to code.

### Pass 5: Post-Processing

All formatting is done by code, not the LLM:

**Hallucination detection:**
- Speaking speed: 3+ words in under 0.5 seconds is physically impossible — removed
- Speed limit: over 12 words/second is beyond any human speech — removed
- Metadata patterns: credit lines, website names, music symbols, copyright lines
- Consecutive duplicate entries

**Entry merging:** Consecutive short entries (each under 42 chars, under 2.5 seconds, gap < 0.5s) are merged into 2-line entries. This reduces subtitle flickering during rapid dialogue.

**Line wrapping:** Entries over 42 characters are split into 2 lines using a scoring system:
- Balance score (lines should be roughly equal length)
- Inverted pyramid bonus (bottom line ≥ top line)
- Natural break points (conjunctions, prepositions, after punctuation)
- Overflow penalty (soft limit — a 45-char line is penalised but never truncated, because dropping words is worse than a slightly long line)

**Timing enforcement:** Min 1 second display, max 7 seconds, min 83ms gap between entries, CPS checking.

### Whisper Cache (.whisper files)

Raw Whisper output is saved as a `.whisper` file next to the video. If the LLM pass fails or the user aborts, subsequent runs skip Whisper entirely and go straight to LLM processing. The `.whisper` extension was chosen because it's not a real subtitle format — no media player or scanner will detect it.

### Why Each LLM Pass Does Only One Thing

Through testing, we discovered that LLMs forget complex multi-step instructions after processing the first few entries in a batch. A prompt that says "fix punctuation AND condense to fit budget AND correct misheard words" works on entries 1-5 and degrades from there. Two passes with simple, focused instructions ("add punctuation" then "fix errors") produce dramatically better results across entire batches.

</details>

---

## Troubleshooting

**"No API key found"**
Make sure `.env` exists in the project directory and has your key. See [Configuration](#configuration).

**"Failed to extract audio"**
FFmpeg is not installed or not in PATH.
- Windows: `winget install ffmpeg` then restart your terminal
- Linux: `sudo apt install ffmpeg`

**Whisper is very slow**
You may be running on CPU. Check the log output for `device=cuda`. Re-run the installer (`install.ps1` / `install.sh`) to diagnose GPU/CUDA issues.

**Subtitles have wrong language**
Whisper auto-detects language. Force it with `-Language en` (or `--language en` on Linux). Supports all Whisper language codes: `en`, `es`, `fr`, `de`, `ja`, `zh`, etc.

**Place names or character names are wrong**
This is a Whisper limitation. The speech-to-text engine sometimes mishears proper nouns that are uncommon. The LLM cleanup catches many of these using context but can't get them all.

**Linux: `nvidia-smi: command not found`**
Install drivers: `sudo apt install nvidia-driver` and reboot. Then re-run `install.sh`.

**Linux: venv activation fails**
The wrapper doesn't activate the venv — it runs `.venv/bin/python` directly. If you want to activate manually: `source .venv/bin/activate`.

---

## Disclaimer

This software is provided as-is, without warranty of any kind. By using Transcribe_Subs, you acknowledge the following:

- **Transcription accuracy** — subtitles are generated by AI speech recognition (Whisper) and cleaned up by third-party LLM APIs. Output quality depends on audio quality, accents, background noise, and the speech recognition model. Always spot-check subtitles before relying on them for critical use.
- **API costs** — cloud LLM providers charge per token. While costs are low (~$0.03 per episode), processing a very large library will accumulate charges. Use `--dry-run` to preview what will be processed before committing.
- **Third-party services** — this tool sends transcribed text (not audio) to external APIs (DeepSeek, OpenAI, etc.) for processing. Do not use it on content you are not authorised to share with these services.
- **File creation** — this tool creates `.srt` and `.whisper` files alongside your video files. It does not modify or delete your original media files.
- **Legal responsibility** — you are solely responsible for ensuring your use of this tool complies with applicable laws, including copyright and content licensing. The authors of this project are not responsible for how it is used.

---

## License

MIT
