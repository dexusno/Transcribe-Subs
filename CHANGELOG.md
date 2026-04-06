# Changelog

## [1.0.0] - 2026-04-06

First stable release. Complete rewrite of the subtitle generation pipeline based on extensive real-world testing.

### Architecture: 5-Pass Pipeline
- **Pass 1: Whisper transcription** — local GPU, word-level timestamps
- **Pass 2: LLM punctuation** — adds proper punctuation and capitalisation to raw transcript
- **Pass 3: Sentence re-segmentation** — code rebuilds entries at sentence boundaries
- **Pass 4: LLM cleanup** — fixes misheard words, removes filler
- **Pass 5: Post-processing** — timing rules, entry merging, line wrapping, validation

### Whisper Transcription
- faster-whisper with `large-v3` model, INT8 quantization (~3-4 GB VRAM)
- Word-level timestamps for precise subtitle timing
- Quality settings: `beam_size=10`, `best_of=5`, `patience=2.0`
- `condition_on_previous_text=false` with `initial_prompt` for consistent punctuation without cascading errors
- VAD disabled by default (was removing too much audio — 30 min of a 57 min episode)
- 99 languages with auto-detection or manual override via `-Language`

### 2-Pass LLM Processing
- **Punctuation pass**: Sends text as continuous dialogue with overlapping windows (20 entries overlap between batches) so the LLM sees flowing context. Strong, strict prompt prevents false sentence endings at entry boundaries.
- **Cleanup pass**: Per-entry `[N]` indexed format. Fixes misheard words using context (e.g. "lorry ticket" to "lottery ticket"), removes filler words, fixes stuttering and false starts. Explicit instruction to never remove or rephrase correct words.
- Each pass does ONE simple task — no complex multi-step instructions that get forgotten.
- Works with any OpenAI-compatible API (DeepSeek, OpenAI, Groq, OpenRouter, local Ollama)

### Sentence-Aware Re-Segmentation
- After LLM punctuation, code rebuilds entries at sentence boundaries (`.` `!` `?`)
- Eliminates "sentence bleeding" where sentences split mid-phrase across entries
- Long sentences split at clause boundaries with tiered preference:
  - Tier 1: comma + conjunction ("..., but")
  - Tier 2: after comma
  - Tier 3: before conjunction
  - Tier 4: nearest midpoint (last resort)
- Abbreviation detection prevents false splits (Mr., Mrs., Dr., D.I., etc.)
- Never truncates or drops words — slightly long lines preferred over missing text

### Smart Entry Merging
- Consecutive short entries merged into 2-line subtitles for comfortable reading
- Reduces subtitle flickering during rapid dialogue exchanges
- Only merges when both entries fit on one line each (≤42 chars), gap <0.5s, combined duration ≤7s

### Hallucination Detection
- Speaking speed check: 3+ words in under 0.5 seconds is physically impossible
- Speed limit: over 12 words/second flagged as hallucination
- Text patterns: "subscribe", "thank you for watching", "©", "transcript" etc.
- Consecutive duplicate removal

### Post-Processing (Netflix Standards)
- 42 characters per line maximum, 2 lines per entry
- 17 CPS target reading speed, 20 CPS hard limit
- 1-7 second display duration enforcement
- 83ms minimum gap between entries
- Intelligent line wrapping at natural break points with scoring:
  - Conjunctions and prepositions preferred
  - After punctuation preferred
  - Inverted pyramid (bottom line ≥ top line)
  - Overflow penalty (soft limit, never truncates)

### Whisper Cache (.whisper files)
- Raw Whisper output saved as `.whisper` file next to the video
- If LLM fails or user aborts, Whisper doesn't need to re-run
- Subsequent runs skip straight to LLM processing
- Cleaned up after successful completion (configurable)

### Installer
- One-liner: `irm https://raw.githubusercontent.com/dexusno/Transcribe-Subs/main/install.ps1 | iex`
- Installs to current directory
- Discovers GPU, drivers, CUDA via runtime testing (no hardcoded version numbers)
- Auto-fixes: installs NVIDIA drivers, CUDA runtime libraries, conda, git, ffmpeg
- Discovers required CUDA version from CTranslate2 pip metadata
- Creates isolated conda environment with Python 3.11
- Pre-downloads Whisper model with progress bars
- Falls back to CPU mode if GPU unavailable

### Quality Results (tested on BBC drama)
- ~92% dialogue accuracy compared to official subtitles
- 86% of entries have clean sentence boundaries
- Zero dropped words (previous versions lost "hasn't", "nothing", "impression")
- Proper nouns remain the main challenge (Whisper limitation)

### Known Limitations
- Windows only (PowerShell installer; Python script may work on Linux with manual setup)
- Proper nouns specific to a show may be misheard by Whisper (character names, place names)
- Occasional hallucinated short phrases during silence that pass the speed/duration filters

---

## [0.5.0-beta] - 2026-04-05

Initial beta release. See git history for details.
