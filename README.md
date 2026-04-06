# Transcribe_Subs

Generate `.srt` subtitle files for videos that have **no subtitles**, using local AI speech-to-text and LLM-powered cleanup.

Whisper runs **locally on your NVIDIA GPU** — no audio leaves your machine. Only the transcribed text is sent to an LLM API for punctuation and cleanup, at a cost of roughly **$0.06 per movie**.

---

## How It Works

```
Video file
  |
  v
Pass 1: Whisper (local GPU) ── speech to text with word-level timestamps
  |
  v
Pass 2: LLM Punctuation ── add periods, commas, capitals to raw transcript
  |
  v
Pass 3: Re-segmentation ── rebuild entries at sentence boundaries
  |
  v
Pass 4: LLM Cleanup ── fix misheard words, remove filler
  |
  v
Pass 5: Post-processing ── timing rules, merge, line wrap, validate
  |
  v
Polished .srt file
```

**What it does:**
- Scans a folder recursively for video files (MKV, MP4, AVI, MOV, WebM)
- Skips any video that already has subtitles (embedded or sidecar `.srt`)
- Transcribes speech using [faster-whisper](https://github.com/SYSTRAN/faster-whisper) with the `large-v3` model
- Fixes punctuation and misheard words via LLM (DeepSeek Chat or any OpenAI-compatible API)
- Formats output following Netflix subtitle standards (42 chars/line, 17 CPS, 2-line max)

**What it costs:**

| Step | Cost |
|------|------|
| Whisper transcription | Free (local GPU) |
| LLM processing — 45-min episode | ~$0.03 |
| LLM processing — 2-hr movie | ~$0.06 |
| LLM processing — 10-episode season (45 min each) | ~$0.30 |

> The `--skip-llm` flag exists for testing but is **not recommended** — raw Whisper output lacks proper punctuation and sentence boundaries, making it difficult to read as subtitles. The LLM passes are essential for usable output.

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
| **DeepSeek API key** | LLM processes the transcript into readable subtitles | [platform.deepseek.com/api_keys](https://platform.deepseek.com/api_keys) |

> The installer checks for system dependencies and offers to install missing ones automatically via `winget`.

---

## Step-by-Step Setup

### Step 1: Run the Installer

Open PowerShell, `cd` to where you want to install, and run:

```powershell
cd D:\
irm https://raw.githubusercontent.com/dexusno/Transcribe-Subs/main/install.ps1 | iex
```

This creates a `Transcribe_Subs` folder in your current directory (e.g. `D:\Transcribe_Subs`).

The installer will:
1. Check for NVIDIA GPU, drivers, and CUDA — offer to install/update if needed
2. Check for conda, git, ffmpeg — offer to install via winget if missing
3. Clone the repository into a `Transcribe_Subs` folder in your current directory
4. Create an isolated `transcribe_subs` conda environment with Python 3.11
5. Install all Python dependencies (faster-whisper, requests, python-dotenv)
6. Verify CUDA works end-to-end — automatically fix missing runtime libraries
7. Pre-download the Whisper `large-v3` model (~3 GB, one-time download)
8. Create config files from templates (`.env`, `llm_config.json`)

> If the installer installs NVIDIA drivers, you will need to **restart your computer** and run the installer again.

### Step 2: Get a DeepSeek API Key

The LLM is a required part of the pipeline — it adds punctuation to the raw transcript and fixes speech recognition errors. Without it, the output lacks sentence boundaries and is not usable as subtitles.

1. Go to [platform.deepseek.com/api_keys](https://platform.deepseek.com/api_keys)
2. Create an account (or sign in)
3. Add some credit ($2-5 is enough for hundreds of episodes)
4. Generate a new API key
5. Copy the key (it starts with `sk-`)

### Step 3: Configure Your API Key

Open the `.env` file in the project directory and replace `your-key-here` with your actual API key:

```env
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
| `-Profile` | LLM profile name from llm_config.json | `deepseek` |
| `-BatchSize` | Subtitle entries per LLM API call | 150 |
| `-Parallel` | Concurrent file processing threads | 4 |
| `-WhisperModel` | Override Whisper model size | `large-v3` |
| `-Language` | Force language code (e.g. `en`, `es`, `fr`) | auto-detect |
| `-Limit` | Max number of files to process | unlimited |
| `-Force` | Re-transcribe even if `.srt` already exists | off |
| `-DryRun` | Show what would be processed, do nothing | off |
| `-SkipLLM` | Output raw Whisper `.whisper` without LLM cleanup | off |
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
| `beam_size` | Beam search width (higher = more accurate, slower) | `1`-`15` (default `10`) |
| `best_of` | Candidates per segment (picks the best) | `1`-`5` (default `5`) |
| `patience` | Beam search patience (higher = more thorough) | `1.0`-`3.0` (default `2.0`) |
| `vad_filter` | Voice Activity Detection filter | `false` (default), `true` |
| `word_timestamps` | Word-level timing for precise segmentation | `true` (default) |
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

**Default (DeepSeek Chat):**
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

**Included profiles:** `deepseek`, `openai`, `groq`, `openrouter`, `local` (Ollama/LM Studio)

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

## Under the Hood

This section explains the engineering decisions behind the pipeline for anyone interested in how it works or wanting to contribute.

### Why 5 Passes?

Early versions used a simpler pipeline (Whisper -> single LLM pass -> done) but produced poor results: sentence bleeding, dropped words, garbled punctuation. Each problem required a focused solution, leading to the current 5-pass architecture where each step does one thing well.

### Pass 1: Whisper Transcription

**Model:** `large-v3` with INT8 quantization — best accuracy while using only ~3-4 GB VRAM.

**Word-level timestamps:** Instead of trusting Whisper's segment boundaries (which often dump 6-25 seconds of multi-speaker dialogue into a single block), we extract per-word timestamps and rebuild subtitle entries ourselves. This gives us precise control over entry boundaries.

**`condition_on_previous_text=false`:** The default (`true`) feeds previous output as context to the next chunk, which gives better punctuation consistency but causes catastrophic cascading errors — if Whisper mishears a place name once, it repeats the error for the entire file. We tested both extensively. With `false`, each 30-second chunk is independent. We use `initial_prompt` to prime punctuation style instead.

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

### Pass 3: Sentence Re-Segmentation

After punctuation, the code rebuilds entries from scratch at sentence boundaries. This is pure code — no LLM involved.

1. Flatten all entries into a word stream with timestamps
2. Group words into sentences using punctuation (`.` `!` `?`)
3. Sentences that fit within limits (84 chars, 7 seconds) become one entry
4. Long sentences are split at clause boundaries with tiered preference:
   - Tier 1: comma + conjunction ("..., but") — best
   - Tier 2: after any comma
   - Tier 3: before a conjunction without comma
   - Tier 4: nearest to midpoint — last resort

An abbreviation list (Mr., Mrs., Dr., D.I., etc.) prevents false sentence splits.

**The key insight:** Build sentences first, then fit them into entries. The previous approach (build entries, hope sentences align) caused constant bleeding.

### Pass 4: LLM Cleanup

Now that entries contain clean, properly punctuated sentences, the LLM can focus purely on fixing speech recognition errors:

- **Misheard words:** "lorry ticket" -> "lottery ticket", "tandem gloid" -> "tandem glider" (uses surrounding context to determine the correct word)
- **Filler words:** um, uh, er, like, you know, I mean
- **Stuttering:** "it's it's important" -> "it's important"
- **False starts:** "I was- I went there" -> "I went there"

The prompt explicitly says: "Do NOT remove, shorten, or rephrase anything else. Keep every word." Earlier versions asked the LLM to also condense text to fit character budgets, but LLMs cannot count characters and were dropping words. The condensation was moved to code.

### Pass 5: Post-Processing

All formatting is done by code, not the LLM:

**Hallucination detection:**
- Speaking speed: 3+ words in under 0.5 seconds is physically impossible — removed
- Speed limit: over 12 words/second is beyond any human speech — removed
- Text patterns: "subscribe", "thank you for watching", "copyright" etc. — removed
- Consecutive duplicate entries — removed

**Entry merging:** Consecutive short entries (each under 42 chars, under 2.5 seconds, gap < 0.5s) are merged into 2-line entries. This reduces subtitle flickering during rapid dialogue.

**Line wrapping:** Entries over 42 characters are split into 2 lines using a scoring system:
- Balance score (lines should be roughly equal length)
- Inverted pyramid bonus (bottom line >= top line)
- Natural break points (conjunctions, prepositions, after punctuation)
- Overflow penalty (soft limit — a 45-char line is penalised but never truncated, because dropping words is worse than a slightly long line)

**Timing enforcement:** Min 1 second display, max 7 seconds, min 83ms gap between entries, CPS checking.

### Whisper Cache (.whisper files)

Raw Whisper output is saved as a `.whisper` file next to the video. If the LLM pass fails or the user aborts, subsequent runs skip Whisper entirely and go straight to LLM processing. The `.whisper` extension was chosen because it's not a real subtitle format — no media player or scanner will detect it.

### Why Each LLM Pass Does Only One Thing

Through testing, we discovered that LLMs forget complex multi-step instructions after processing the first few entries in a batch. A prompt that says "fix punctuation AND condense to fit budget AND correct misheard words" works on entries 1-5 and degrades from there. Two passes with simple, focused instructions ("add punctuation" then "fix errors") produce dramatically better results across entire batches.

---

## Troubleshooting

**"No API key found"** — Make sure `.env` exists and has your key. Use `--skip-llm` to test without an API key.

**"Failed to extract audio"** — FFmpeg is not installed or not in PATH. Run `winget install ffmpeg` and restart your terminal.

**Whisper is very slow** — You may be running on CPU. Check the log output for `device=cuda`. Re-run `install.ps1` to diagnose GPU/CUDA issues.

**Subtitles have wrong language** — Whisper auto-detects language. Force it with `-Language en` (or `es`, `fr`, `de`, etc.).

**Place names or character names are wrong** — This is a Whisper limitation. The speech-to-text engine sometimes mishears proper nouns that are uncommon. The LLM cleanup catches many of these using context but can't get them all.

---

## License

MIT
