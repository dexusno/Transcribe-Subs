# Changelog

## [1.1.0] - 2026-04-06

### Added
- **Linux support** (Debian/Ubuntu) with bash wrappers and venv-based installer
  - `linux/install.sh` — one-liner installer with full dependency detection
  - `linux/transcribe_subs.sh` — bash wrapper matching PowerShell functionality
  - Uses Python `venv` instead of conda (lighter, no extra install)
  - Uses `apt-get` instead of `winget` for system packages
  - Same GPU/CUDA detection, auto-fix, and model download as Windows
- **README reorganised** — separate Windows and Linux install sections for clarity

### Fixed
- **ALL CAPS normalisation** — Whisper sometimes outputs entire sections in ALL CAPS (dramatic scenes, shouting). Now converted to lowercase before the LLM punctuation pass, which adds proper capitalisation back. Fixed 14% of entries being stuck in uppercase on test content.
- **Hallucination filter safety** — removed text patterns that could match real dialogue ("subscribe", "thank you for watching", "music"). Only kept patterns that can never be spoken: credit lines, website names, music note symbols, copyright lines.
- **Punctuation batch size cap** — limited to 300 entries max to stay within DeepSeek Chat's 8K output token limit. Prevents silent failures where the API returns the input unchanged.

### Changed
- Subtitle rules documentation now shows defaults in the table (not just the JSON example)
- Added warning about modifying subtitle rule defaults — some values are baked into pipeline logic
- LLM provider section clarified — DeepSeek is recommended/tested, others are supported alternatives
- Local LLM warning — requires 14B+ models and high-end hardware (48GB+ VRAM) for acceptable quality

---

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
- Metadata patterns: credit lines, website names, music symbols, copyright lines
- Consecutive duplicate removal

### Post-Processing
- 42 characters per line (soft limit — preserves words over strict limits)
- 2 lines per entry maximum
- 17 CPS target reading speed, 20 CPS logged as warning
- 1-7 second display duration enforcement
- 83ms minimum gap between entries
- Intelligent line wrapping at natural break points with scoring

### Whisper Cache (.whisper files)
- Raw Whisper output saved as `.whisper` file next to the video
- If LLM fails or user aborts, Whisper doesn't need to re-run
- Subsequent runs skip straight to LLM processing

### Installer
- One-liner: `irm https://raw.githubusercontent.com/dexusno/Transcribe-Subs/main/install.ps1 | iex`
- Installs to current directory
- Discovers GPU, drivers, CUDA via runtime testing (no hardcoded version numbers)
- Auto-fixes: installs NVIDIA drivers, CUDA runtime libraries, conda, git, ffmpeg
- Discovers required CUDA version from CTranslate2 pip metadata
- Creates isolated conda environment with Python 3.11
- Pre-downloads Whisper model with progress bars
- Falls back to CPU mode if GPU unavailable

### Cost (based on real DeepSeek usage data)
- 45-minute episode: ~$0.03 (2-pass LLM processing)
- 2-hour movie: ~$0.06
- 10-episode season: ~$0.30

### Quality Results (tested on BBC drama)
- ~92% dialogue accuracy compared to official subtitles
- 86% of entries have clean sentence boundaries
- Zero dropped words (previous versions lost "hasn't", "nothing", "impression")
- Proper nouns remain the main challenge (Whisper limitation)

### Known Limitations
- Proper nouns specific to a show may be misheard by Whisper (character names, place names)
- Occasional hallucinated short phrases during silence that pass the speed/duration filters

---

## [0.5.0-beta] - 2026-04-05

Initial beta release. See git history for details.
