# Changelog

## [0.5.0-beta] - 2026-04-05

Initial beta release.

### Added
- **Whisper transcription** via faster-whisper with local GPU acceleration (CUDA)
  - Supports models: tiny, base, small, medium, large-v2, large-v3
  - INT8 quantization for minimal VRAM usage (~3-4 GB for large-v3)
  - Automatic language detection (99 languages) or manual override
  - Silero VAD filtering to reduce hallucinations on silent segments
- **LLM-powered subtitle cleanup** via DeepSeek Reasoner (or any OpenAI-compatible API)
  - Grammar, punctuation, and capitalization fixes
  - Filler word removal (um, uh, like, you know, etc.)
  - False start and stutter cleanup
  - Character budget-aware condensation for readability
  - System prompt sent with every request for consistency
- **Netflix-standard subtitle formatting**
  - 42 characters per line maximum, 2 lines per entry
  - 17 CPS target reading speed, 20 CPS hard limit
  - 1-7 second display duration enforcement
  - 83ms minimum gap between entries
  - Intelligent line wrapping at natural break points (conjunctions, prepositions, punctuation)
  - Inverted pyramid line balancing (bottom line >= top line)
- **4-pass processing pipeline**
  - Pass 1: Whisper transcription with word-level timestamps
  - Pass 2: Pre-processing (merge micro-entries, split mega-entries, calculate budgets)
  - Pass 3: LLM cleanup with character budgets per entry
  - Pass 4: Post-processing (timing rules, line wrapping, hallucination removal, validation)
- **Folder scanning** with recursive video discovery
  - Skips videos that already have subtitles (embedded or sidecar)
  - DirCache for network share performance
  - Supports MKV, MP4, AVI, MOV, WebM, OGM
- **Multi-profile LLM support**
  - Pre-configured: DeepSeek Reasoner, DeepSeek Chat, OpenAI, Groq, OpenRouter, local (Ollama)
  - Any OpenAI-compatible API works
- **One-liner installer** (`irm | iex`)
  - Checks GPU, drivers, CUDA, conda, git, ffmpeg
  - Offers to install missing dependencies via winget
  - Discovers and fixes CUDA version mismatches automatically
  - Creates isolated conda environment
  - Pre-downloads Whisper model
- **PowerShell wrapper** with automatic conda activation
  - Named parameters (-DryRun, -SkipLLM, -Language, -Force, etc.)
  - UNC path support for network shares
- **Fallback safety**
  - LLM failure: saves raw Whisper .srt (still usable)
  - GPU not available: falls back to CPU mode
  - Missing entries in LLM response: keeps original text
- **Hallucination detection** for common Whisper artifacts
  - "Subscribe", "Thank you for watching", repeated phrases, music symbols
  - Consecutive duplicate removal
- **UTF-8 BOM** on all output .srt files for maximum player compatibility
- **Report logging** to `logs/` directory after each run

### Known Limitations
- Windows only (PowerShell installer; Python script may work on Linux with manual setup)
- No embedded subtitle muxing into MKV (outputs sidecar .srt only)
