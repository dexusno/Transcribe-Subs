"""
transcribe_subs.py — Scan a media folder, find videos with NO subtitles,
generate .srt subtitle files using local Whisper (faster-whisper) for
speech-to-text, then polish them via LLM (DeepSeek Reasoner or compatible).

Whisper settings, subtitle rules, and LLM provider are configured in
llm_config.json next to this script.

Usage:
    python transcribe_subs.py "D:\\Movies\\Some Movie"
    python transcribe_subs.py --skip-llm "D:\\Movies\\Some Movie"
    python transcribe_subs.py --profile deepseek --dry-run "/mnt/media/Tv/Show"
"""

import argparse
import json
import logging
import math
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable, Dict, Generator, List, Optional, Tuple

import requests
from dotenv import load_dotenv

try:
    from faster_whisper import WhisperModel
except ImportError:
    print(
        "ERROR: faster-whisper is not installed.\n"
        "  pip install faster-whisper\n"
        "See: https://github.com/SYSTRAN/faster-whisper",
        file=sys.stderr,
    )
    sys.exit(1)

# Force UTF-8 output on Windows
if sys.platform == "win32" and hasattr(sys.stdout, "buffer"):
    sys.stdout.reconfigure(encoding="utf-8")

load_dotenv(Path(__file__).parent / ".env")

# ── Constants ────────────────────────────────────────────────────────────────

VIDEO_EXTENSIONS = {".mkv", ".mp4", ".mov", ".webm", ".ogm", ".avi"}

# Text subtitle codecs ffmpeg can detect
TEXT_SUB_CODECS = {"subrip", "ass", "ssa", "mov_text", "webvtt", "text"}
# Bitmap subtitle codecs
BITMAP_SUB_CODECS = {"hdmv_pgs_subtitle", "dvd_subtitle", "xsub"}

# Whisper hallucination patterns — ONLY things that can never be real dialogue.
# Whisper's training data included subtitle metadata, so it sometimes generates
# credit lines, website names, and formatting artifacts during silence.
# We do NOT filter words like "subscribe" or "thank you for watching" because
# characters in a show could actually say those words.
HALLUCINATION_PATTERNS = [
    re.compile(
        r"^\s*("
        r"subtitles by\s.*|captions by\s.*|translated by\s.*|"  # credit lines
        r"subtitles made by\s.*|captioned by\s.*|"              # credit lines
        r"amara\.org|opensubtitles|subscene|"                   # website names
        r"\u266a[\s\u266a]*|"                                   # music note symbols
        r"\.{4,}|_{4,}|-{4,}|"                                  # formatting artifacts
        r"\u00a9.*|"                                             # © copyright lines
        r"transcript\s+\w+.*"                                   # "transcript Emily Beynon"
        r")[.!?,;]*\s*$",
        re.IGNORECASE,
    ),
]

# Maximum words per second a human can physically speak.
# Normal speech: 3-4 w/s. Fast TV dialogue: 6-8 w/s.
# Auctioneers: ~12 w/s. Above 14 is physically impossible.
MAX_WORDS_PER_SECOND = 12.0

# Sidecar subtitle extensions to look for
SIDECAR_EXTENSIONS = {".srt", ".ass", ".ssa", ".sub", ".vtt"}

log = logging.getLogger("transcribe_subs")

# ── Config loading ───────────────────────────────────────────────────────────

CONFIG_FILE = Path(__file__).parent / "llm_config.json"


def load_config() -> dict:
    """Load llm_config.json from next to this script."""
    if not CONFIG_FILE.exists():
        log.error("Config file not found: %s", CONFIG_FILE)
        sys.exit(1)
    with open(CONFIG_FILE, encoding="utf-8") as f:
        return json.load(f)


def resolve_profile(config: dict, profile_name: str | None) -> dict:
    """Resolve a named profile to {api_url, model, api_key, ...}."""
    name = profile_name or config.get("default_profile", "deepseek")
    profiles = config.get("profiles", {})
    if name not in profiles:
        log.error("Unknown profile '%s'. Available: %s", name, ", ".join(profiles))
        sys.exit(1)
    p = profiles[name]
    # Resolve api_key: from env var or literal
    if "api_key_env" in p:
        api_key = os.getenv(p["api_key_env"], "")
    else:
        api_key = p.get("api_key", "")
    return {
        "name": name,
        "api_url": p["api_url"],
        "model": p["model"],
        "api_key": api_key,
        "timeout": p.get("timeout", 300),
        "batch_size": p.get("batch_size", 500),
        "parallel": p.get("parallel", 4),
    }


_WHISPER_DEFAULTS = {
    "model": "large-v3",
    "device": "cuda",
    "compute_type": "int8",
    "language": None,
    "beam_size": 5,
    "vad_filter": True,
}

_SUBTITLE_RULE_DEFAULTS = {
    "max_chars_per_line": 42,
    "max_lines": 2,
    "target_cps": 17,
    "max_cps": 20,
    "min_duration_ms": 1000,
    "max_duration_ms": 7000,
    "min_gap_ms": 83,
    "preferred_split_words": [
        "but", "and", "or", "so", "because", "when", "while",
        "that", "which", "who", "where", "if", "then",
        "in", "on", "at", "to", "for", "with", "from", "of",
    ],
}


def get_whisper_config(config: dict) -> dict:
    """Return whisper config merged over defaults."""
    out = dict(_WHISPER_DEFAULTS)
    out.update(config.get("whisper", {}))
    return out


def get_subtitle_rules(config: dict) -> dict:
    """Return subtitle rules merged over defaults."""
    out = dict(_SUBTITLE_RULE_DEFAULTS)
    out.update(config.get("subtitle_rules", {}))
    return out


# ══════════════════════════════════════════════════════════════════════════════
# SRT Utilities
# ══════════════════════════════════════════════════════════════════════════════

def _nfc(s: str) -> str:
    """Normalize string to NFC form."""
    return unicodedata.normalize("NFC", s or "")


def _strip_bom(line: str) -> str:
    return line.lstrip("\ufeff") if line else line


_TAG_RE = re.compile(r"<[^>]+>")


def _protect_tags(text: str) -> Tuple[str, Dict[str, str]]:
    """Replace HTML-ish tags with placeholders to protect from LLM."""
    tags: Dict[str, str] = {}

    def _replace(m):
        key = f"__TAG{len(tags)}__"
        tags[key] = m.group(0)
        return key

    protected_text = _TAG_RE.sub(_replace, text)
    return protected_text, tags


def _restore_tags(text: str, tags: Dict[str, str]) -> str:
    """Restore placeholders back to original tags."""
    for key, val in tags.items():
        text = text.replace(key, val)
    return text


_TIME_RE = re.compile(r"\d{2}:\d{2}:\d{2},\d{3}\s*-->\s*\d{2}:\d{2}:\d{2},\d{3}")


def _is_time_line(line: str) -> bool:
    return bool(_TIME_RE.search(line or ""))


def _seconds_to_srt_time(seconds: float) -> str:
    """Convert float seconds to SRT timestamp: HH:MM:SS,mmm"""
    if seconds < 0:
        seconds = 0.0
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int(round((seconds - int(seconds)) * 1000))
    if ms >= 1000:
        ms = 999
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _srt_time_to_seconds(ts: str) -> float:
    """Parse SRT timestamp HH:MM:SS,mmm to float seconds."""
    ts = ts.strip()
    parts = ts.replace(",", ":").split(":")
    if len(parts) != 4:
        return 0.0
    h, m, s, ms = int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3])
    return h * 3600 + m * 60 + s + ms / 1000.0


def _build_raw_srt(segments) -> str:
    """Build an SRT string from faster-whisper segments.

    Uses word-level timestamps to create subtitle-sized entries
    (max ~5 seconds, split at natural pauses between words).
    Falls back to segment-level if word timestamps aren't available.
    """
    # Collect all words with timestamps from all segments
    all_words = []
    for seg in segments:
        if seg.words:
            for w in seg.words:
                all_words.append({
                    "word": w.word,
                    "start": w.start,
                    "end": w.end,
                    "probability": getattr(w, "probability", 1.0),
                })
        else:
            # Fallback: no word timestamps, use segment as-is
            text = (seg.text or "").strip()
            if text:
                all_words.append({
                    "word": text,
                    "start": seg.start,
                    "end": seg.end,
                    "probability": 1.0,
                })

    if not all_words:
        return ""

    # Build subtitle entries from words, splitting at natural points.
    #
    # Priority (highest to lowest):
    # 1. Always split at long pauses (>= 0.7s) — likely speaker change
    # 2. Prefer splitting at sentence endings (. ! ?) with any pause
    # 3. Split at comma/semicolon boundaries when near max duration
    # 4. Hard split at max duration/chars as last resort
    # 5. Never create entries shorter than MIN_CHARS unless forced by pause

    MAX_DURATION = 5.0    # Max seconds per subtitle entry
    MAX_CHARS = 84        # Max characters per entry (42 x 2 lines)
    MIN_CHARS = 10        # Don't create tiny fragments below this
    PAUSE_SPLIT = 0.7     # Seconds of silence = forced split (speaker change)
    SENTENCE_PAUSE = 0.15 # Shorter pause is enough after sentence-ending punctuation

    entries = []
    current_words = []
    current_start = all_words[0]["start"]

    for i, word in enumerate(all_words):
        current_words.append(word)
        current_text = "".join(w["word"] for w in current_words).strip()
        current_duration = word["end"] - current_start
        is_last = (i == len(all_words) - 1)

        # Gap to next word (0 if last)
        gap = all_words[i + 1]["start"] - word["end"] if not is_last else 0

        # Determine if we should split here
        should_split = False

        # 1. Long pause — almost certainly a speaker change or scene break
        if gap >= PAUSE_SPLIT:
            should_split = True

        # 2. Sentence-ending punctuation with even a small pause
        if gap >= SENTENCE_PAUSE and current_text and current_text[-1] in ".!?":
            should_split = True

        # 3. Near max duration — split at comma/clause boundary
        if current_duration >= MAX_DURATION * 0.8 and current_text:
            if current_text[-1] in ".,;:—–-" and gap >= 0.1:
                should_split = True

        # 4. Hard limits — must split regardless
        if current_duration >= MAX_DURATION:
            should_split = True
        if len(current_text) >= MAX_CHARS:
            should_split = True

        # 5. Last word
        if is_last:
            should_split = True

        # Anti-fragment: don't split if it would create a tiny entry,
        # UNLESS forced by a long pause (speaker change)
        if should_split and len(current_text) < MIN_CHARS and gap < PAUSE_SPLIT and not is_last:
            should_split = False

        if should_split and current_text:
            entries.append({
                "start": current_start,
                "end": word["end"],
                "text": current_text,
            })
            current_words = []
            if not is_last:
                current_start = all_words[i + 1]["start"]

    # Build SRT string
    blocks = []
    for idx, entry in enumerate(entries, 1):
        start = _seconds_to_srt_time(entry["start"])
        end = _seconds_to_srt_time(entry["end"])
        blocks.append(f"{idx}\n{start} --> {end}\n{entry['text']}")

    return "\n\n".join(blocks) + "\n" if blocks else ""


def _parse_srt_entries(srt_text: str) -> List[dict]:
    """Parse SRT text into a list of structured entries.

    Each entry: {index, start_sec, end_sec, start_ts, end_ts, text}
    """
    if not srt_text:
        return []

    normalized = srt_text.replace("\r\n", "\n").replace("\r", "\n")
    chunks = re.split(r"\n{2,}", normalized)
    entries = []

    for chunk in chunks:
        chunk = chunk.strip()
        if not chunk:
            continue
        lines = chunk.split("\n")

        # Find the timecode line
        time_idx = None
        for i, line in enumerate(lines):
            if _is_time_line(line):
                time_idx = i
                break
        if time_idx is None:
            continue

        # Parse timestamps
        tm = _TIME_RE.search(lines[time_idx])
        if not tm:
            continue
        ts_str = tm.group(0)
        parts = ts_str.split("-->")
        if len(parts) != 2:
            continue
        start_ts = parts[0].strip()
        end_ts = parts[1].strip()

        # Text is everything after the timecode line
        text_lines = [l for l in lines[time_idx + 1:] if l.strip()]
        text = "\n".join(text_lines)
        if not text:
            continue

        entries.append({
            "start_sec": _srt_time_to_seconds(start_ts),
            "end_sec": _srt_time_to_seconds(end_ts),
            "start_ts": start_ts,
            "end_ts": end_ts,
            "text": text,
        })

    # Re-index
    for i, e in enumerate(entries, 1):
        e["index"] = i

    return entries


def _entries_to_srt(entries: List[dict]) -> str:
    """Convert structured entries back to an SRT string with UTF-8 BOM."""
    blocks = []
    for i, e in enumerate(entries, 1):
        start = e.get("start_ts") or _seconds_to_srt_time(e["start_sec"])
        end = e.get("end_ts") or _seconds_to_srt_time(e["end_sec"])
        blocks.append(f"{i}\n{start} --> {end}\n{e['text']}")
    srt = "\n\n".join(blocks) + "\n" if blocks else ""
    return "\ufeff" + srt


# ══════════════════════════════════════════════════════════════════════════════
# Pre-Processing Pass
# ══════════════════════════════════════════════════════════════════════════════

def _calculate_char_budget(duration_sec: float, target_cps: int) -> int:
    """Character budget for a subtitle entry based on display duration."""
    return max(10, int(duration_sec * target_cps))


def _merge_short_entries(entries: List[dict], min_duration_ms: int) -> List[dict]:
    """Merge micro-entries (too short to read) with their neighbors."""
    if not entries:
        return entries

    min_dur = min_duration_ms / 1000.0
    max_merge_gap = 0.5  # Only merge if gap to neighbor < 500ms
    merged = []
    skip = set()

    for i, e in enumerate(entries):
        if i in skip:
            continue

        duration = e["end_sec"] - e["start_sec"]
        text_len = len(e["text"].replace("\n", " ").strip())

        # Only merge very short entries with very little text
        if duration < min_dur and text_len < 15:
            # Try merge with next entry
            if i + 1 < len(entries):
                nxt = entries[i + 1]
                gap = nxt["start_sec"] - e["end_sec"]
                if gap < max_merge_gap:
                    nxt_text = e["text"].strip() + " " + nxt["text"].strip()
                    merged.append({
                        "start_sec": e["start_sec"],
                        "end_sec": nxt["end_sec"],
                        "start_ts": _seconds_to_srt_time(e["start_sec"]),
                        "end_ts": _seconds_to_srt_time(nxt["end_sec"]),
                        "text": nxt_text,
                    })
                    skip.add(i + 1)
                    continue

            # Try merge with previous entry
            if merged:
                prev = merged[-1]
                gap = e["start_sec"] - prev["end_sec"]
                if gap < max_merge_gap:
                    prev["text"] = prev["text"].strip() + " " + e["text"].strip()
                    prev["end_sec"] = e["end_sec"]
                    prev["end_ts"] = _seconds_to_srt_time(e["end_sec"])
                    continue

        merged.append(dict(e))

    # Re-index
    for i, e in enumerate(merged, 1):
        e["index"] = i
    return merged


def _split_long_entries(entries: List[dict], max_duration_ms: int) -> List[dict]:
    """Split entries that are too long into multiple sub-entries."""
    if not entries:
        return entries

    max_dur = max_duration_ms / 1000.0
    result = []

    for e in entries:
        duration = e["end_sec"] - e["start_sec"]
        if duration <= max_dur:
            result.append(dict(e))
            continue

        # Split into N sub-entries
        n = math.ceil(duration / max_dur)
        words = e["text"].replace("\n", " ").split()
        if len(words) <= 1:
            result.append(dict(e))
            continue

        # Distribute words proportionally
        words_per_part = max(1, len(words) // n)
        start = e["start_sec"]
        total_words = len(words)
        word_idx = 0

        for part_i in range(n):
            if word_idx >= total_words:
                break

            if part_i == n - 1:
                # Last part gets remaining words
                part_words = words[word_idx:]
            else:
                part_words = words[word_idx: word_idx + words_per_part]

            # Distribute time proportionally by word count
            part_ratio = len(part_words) / total_words
            part_duration = duration * part_ratio
            end = start + part_duration

            result.append({
                "start_sec": start,
                "end_sec": end,
                "start_ts": _seconds_to_srt_time(start),
                "end_ts": _seconds_to_srt_time(end),
                "text": " ".join(part_words),
            })

            start = end
            word_idx += len(part_words)

    # Re-index
    for i, e in enumerate(result, 1):
        e["index"] = i
    return result


def _preprocess(entries: List[dict], rules: dict) -> List[dict]:
    """Orchestrate pre-processing: merge short, split long."""
    entries = _merge_short_entries(entries, rules["min_duration_ms"])
    entries = _split_long_entries(entries, rules["max_duration_ms"])
    return entries


# Abbreviations that end with a period but are NOT sentence endings
_ABBREVIATIONS = {
    "mr.", "mrs.", "ms.", "dr.", "st.", "sr.", "jr.",
    "a.m.", "p.m.", "etc.", "vs.", "vol.", "dept.",
    "sgt.", "cpl.", "pvt.", "lt.", "col.", "gen.",
    "prof.", "rev.", "d.i.", "d.s.", "c.s.o.", "p.c.",
}


def _resegment_by_sentences(entries: List[dict], rules: dict) -> List[dict]:
    """Re-segment entries at sentence boundaries.

    After the LLM punctuation pass, entries have proper punctuation.
    This function rebuilds entries so each contains complete sentences,
    splitting at . ! ? boundaries. Long sentences are split at clause
    boundaries (commas + conjunctions).

    This eliminates "sentence bleeding" where a sentence starts in one
    entry and finishes in the next.
    """
    max_chars = rules["max_chars_per_line"] * rules["max_lines"]  # 84
    max_dur = rules["max_duration_ms"] / 1000.0  # 7.0

    # Step 1: Flatten all entries into a stream of (word, start_sec, end_sec)
    # We need to estimate per-word timing from the entry timestamps.
    word_stream = []
    for e in entries:
        text = e["text"].replace("\n", " ").strip()
        words = text.split()
        if not words:
            continue

        e_start = e["start_sec"]
        e_end = e["end_sec"]
        e_duration = max(0.01, e_end - e_start)

        # Distribute time across words proportionally by character count
        total_chars = max(1, sum(len(w) for w in words))
        t = e_start
        for w in words:
            w_duration = e_duration * (len(w) / total_chars)
            word_stream.append({
                "word": w,
                "start": t,
                "end": t + w_duration,
            })
            t += w_duration

    if not word_stream:
        return entries

    # Step 2: Build sentences from the word stream using punctuation
    sentences = []
    current_words = []
    current_start = word_stream[0]["start"]

    for i, w in enumerate(word_stream):
        current_words.append(w)
        word_text = w["word"]

        # Check if this word ends a sentence
        is_sentence_end = False
        if word_text.endswith((".", "!", "?")):
            # Check it's not an abbreviation
            lower = word_text.lower()
            if lower not in _ABBREVIATIONS:
                is_sentence_end = True

        # Also treat end of stream as sentence end
        if i == len(word_stream) - 1:
            is_sentence_end = True

        if is_sentence_end and current_words:
            sentence_text = " ".join(cw["word"] for cw in current_words)
            sentences.append({
                "text": sentence_text,
                "start_sec": current_start,
                "end_sec": w["end"],
                "words": list(current_words),
            })
            current_words = []
            if i + 1 < len(word_stream):
                current_start = word_stream[i + 1]["start"]

    # Step 3: Build subtitle entries from sentences
    result = []

    for sent in sentences:
        text = sent["text"]
        duration = sent["end_sec"] - sent["start_sec"]

        # Sentence fits in one entry? Done.
        if len(text) <= max_chars and duration <= max_dur:
            result.append({
                "start_sec": sent["start_sec"],
                "end_sec": sent["end_sec"],
                "text": text,
            })
            continue

        # Sentence too long — split at clause boundaries
        words = sent["words"]
        _split_sentence_into_entries(words, result, max_chars, max_dur)

    # Re-index and add timestamps
    for i, e in enumerate(result, 1):
        e["index"] = i
        e["start_ts"] = _seconds_to_srt_time(e["start_sec"])
        e["end_ts"] = _seconds_to_srt_time(e["end_sec"])

    return result


# Conjunctions and relative pronouns — good places to split clauses
_CLAUSE_WORDS = {
    "but", "and", "or", "so", "because", "since", "when", "while",
    "although", "though", "if", "then", "that", "which", "who",
    "where", "whereas", "unless", "until", "after", "before",
}


def _split_sentence_into_entries(
    words: List[dict],
    result: List[dict],
    max_chars: int,
    max_dur: float,
):
    """Split a long sentence into multiple entries at clause boundaries.

    Split preference (highest to lowest):
    1. Before a conjunction/relative pronoun after a comma
    2. After a comma
    3. Before a conjunction without comma
    4. Nearest to midpoint (last resort)
    """
    if not words:
        return

    full_text = " ".join(w["word"] for w in words)

    # If it fits now (after earlier splitting), just add it
    duration = words[-1]["end"] - words[0]["start"]
    if len(full_text) <= max_chars and duration <= max_dur:
        result.append({
            "start_sec": words[0]["start"],
            "end_sec": words[-1]["end"],
            "text": full_text,
        })
        return

    # Find best split point
    best_idx = None
    best_tier = 99

    midpoint = len(full_text) / 2
    char_pos = 0

    for idx in range(1, len(words)):
        word_lower = words[idx]["word"].lower().rstrip(".,;:!?")
        prev_word = words[idx - 1]["word"]

        # Calculate character position of this split
        char_pos = len(" ".join(w["word"] for w in words[:idx]))
        left_len = char_pos
        right_len = len(full_text) - char_pos - 1

        # Both halves must be non-trivial
        if left_len < 10 or right_len < 10:
            continue

        # Tier 1: Comma + conjunction (best)
        if prev_word.endswith(",") and word_lower in _CLAUSE_WORDS:
            tier = 1
        # Tier 2: After comma
        elif prev_word.endswith(","):
            tier = 2
        # Tier 3: Before conjunction (no comma)
        elif word_lower in _CLAUSE_WORDS:
            tier = 3
        else:
            continue  # Skip non-boundary positions for tiers 1-3

        # Among same tier, prefer closer to midpoint
        if tier < best_tier or (tier == best_tier and
                abs(char_pos - midpoint) < abs(
                    len(" ".join(w["word"] for w in words[:best_idx])) - midpoint
                )):
            best_tier = tier
            best_idx = idx

    # Tier 4 fallback: nearest space to midpoint
    if best_idx is None:
        char_pos = 0
        best_dist = 999
        for idx in range(1, len(words)):
            char_pos = len(" ".join(w["word"] for w in words[:idx]))
            dist = abs(char_pos - midpoint)
            if dist < best_dist:
                best_dist = dist
                best_idx = idx

    if best_idx is None:
        best_idx = len(words) // 2

    # Safety: if we can't split further (single word or split at 0),
    # just add the entry as-is even if it's oversized. This prevents
    # infinite recursion on unsplittable content.
    if best_idx <= 0 or best_idx >= len(words):
        result.append({
            "start_sec": words[0]["start"],
            "end_sec": words[-1]["end"],
            "text": full_text,
        })
        return

    # Recurse on each half
    _split_sentence_into_entries(words[:best_idx], result, max_chars, max_dur)
    _split_sentence_into_entries(words[best_idx:], result, max_chars, max_dur)


# ══════════════════════════════════════════════════════════════════════════════
# LLM Passes
# ══════════════════════════════════════════════════════════════════════════════

# Regex to parse [N] markers from LLM response
_RESPONSE_RE = re.compile(r"\[(\d+)\]\s*(.*?)(?=\n\[\d+\]|\Z)", re.DOTALL)


def _llm_process_texts(
    texts: List[str],
    system_prompt: str,
    batch_size: int,
    pass_name: str,
    *,
    api_url: str,
    model: str,
    api_key: str,
    api_timeout: int = 120,
) -> List[str]:
    """Generic LLM text processor using [N] indexing pattern.

    Sends texts in batches with [N] numbering, parses [N] responses.
    Falls back to original text on failure. Used for both punctuation
    and cleanup passes.
    """
    if not texts:
        return texts

    total = len(texts)
    total_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    headers = {"Content-Type": "application/json"}
    if api_key and api_key.lower() != "none":
        headers["Authorization"] = f"Bearer {api_key}"

    result_texts: List[str] = []

    for i in range(0, total, max(1, batch_size)):
        batch = texts[i: i + batch_size]

        numbered = [f"[{j}] {text}" for j, text in enumerate(batch)]
        user_msg = "\n".join(numbered)

        body: dict = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_msg},
            ],
            "temperature": 0.3,
        }

        max_retries = 2
        batch_success = False
        batch_num = (i // max(1, batch_size)) + 1
        total_batches = math.ceil(total / max(1, batch_size))

        for attempt in range(1, max_retries + 1):
            try:
                # Only log per-batch progress when there are multiple batches.
                # Single-batch calls are already logged by the caller.
                if total_batches > 1:
                    log.info("  %s: batch %d/%d (%d entries) ...",
                             pass_name, batch_num, total_batches, len(batch))
                t_batch = time.time()

                resp = requests.post(
                    api_url, headers=headers, json=body, timeout=api_timeout,
                )
                resp.raise_for_status()
                data = resp.json()

                if total_batches > 1:
                    log.info("  %s: batch %d/%d completed in %.1fs",
                             pass_name, batch_num, total_batches, time.time() - t_batch)

                usage = data.get("usage", {})
                for k in total_usage:
                    total_usage[k] += usage.get(k, 0)

                content = data["choices"][0]["message"].get("content", "").strip()

                results: Dict[int, str] = {}
                for match in _RESPONSE_RE.finditer(content):
                    idx = int(match.group(1))
                    text = match.group(2).strip()
                    results[idx] = text

                for j in range(len(batch)):
                    result_texts.append(results.get(j, batch[j]))

                batch_success = True
                break

            except Exception as exc:
                if attempt < max_retries:
                    log.warning("  %s: batch %d attempt %d failed: %s — retrying ...",
                                pass_name, batch_num, attempt, exc)
                    time.sleep(2)
                else:
                    log.warning("  %s: batch %d failed after %d attempts: %s — using original",
                                pass_name, batch_num, max_retries, exc)

        if not batch_success:
            result_texts.extend(batch)

    log.debug("  %s usage — prompt: %d, completion: %d, total: %d",
              pass_name, total_usage["prompt_tokens"],
              total_usage["completion_tokens"], total_usage["total_tokens"])

    return result_texts


def _llm_punctuation_pass(
    entries: List[dict],
    batch_size: int,
    *,
    api_url: str,
    model: str,
    api_key: str,
    api_timeout: int = 120,
    file_tag: str = "",
) -> List[dict]:
    """LLM Pass 1: Add proper punctuation and capitalisation.

    Uses overlapping windows of [N]-indexed entries so the LLM sees
    flowing context across entries. Overlap entries provide context
    but only the non-overlapping portion of each response is used.
    Uses larger batches than the cleanup pass for more context.
    """
    system_prompt = (
        "/no_think\n"
        "You are a professional transcript editor.\n"
        "You MUST add correct punctuation and capitalisation to these lines.\n"
        "\n"
        "CRITICAL INSTRUCTIONS:\n"
        "- You MUST read ALL the lines THOROUGHLY before making any changes.\n"
        "- These lines are CONTINUOUS DIALOGUE — text flows from one line to\n"
        "  the next. A sentence that starts in [3] may end in [4] or [5].\n"
        "- You MUST read AHEAD to find where each sentence actually ends\n"
        "  before placing a period.\n"
        "- Do NOT place a period just because a line ends. The sentence may\n"
        "  continue on the next line.\n"
        "- ONLY place periods, question marks, or exclamation marks where\n"
        "  a sentence TRULY ends.\n"
        "- Add commas where there are natural pauses within a sentence.\n"
        "- Capitalise the first word of each new sentence.\n"
        "- Capitalise proper nouns (names of people, places).\n"
        "\n"
        "You MUST NOT change, remove, add, or rephrase ANY words.\n"
        "ONLY add punctuation and fix capitalisation.\n"
        "Every single word MUST remain exactly as it is.\n"
        "\n"
        "Input: [N] text\n"
        "Output: [N] text with punctuation added\n"
        "Return ONLY the numbered lines. No explanations."
    )

    # Use larger batches for punctuation — more context helps.
    # Overlap 20 entries between batches for cross-boundary context.
    # Keep punctuation batches within 8K output token limit.
    # ~20 tokens per entry output, 8000/20 = 400 max, but leave headroom.
    PUNCT_BATCH = min(max(batch_size, 200), 300)
    OVERLAP = 20

    texts = [e["text"].replace("\n", " ").strip() for e in entries]
    total = len(texts)

    # Result array — will be filled in chunks, overlap portions discarded
    result_texts = list(texts)  # Start with originals as fallback

    chunk_start = 0
    chunk_num = 0

    while chunk_start < total:
        chunk_num += 1
        chunk_end = min(chunk_start + PUNCT_BATCH, total)

        # Add overlap from previous context (but don't use those results)
        context_start = max(0, chunk_start - OVERLAP)
        batch_texts = texts[context_start: chunk_end]

        # The offset tells us where the "real" entries start in this batch
        real_offset = chunk_start - context_start

        total_chunks = math.ceil(total / PUNCT_BATCH)
        _t = f"  [{file_tag}] " if file_tag else "  "
        log.info("%sPunctuation: batch %d/%d (%d entries, +%d context) ...",
                 _t, chunk_num, total_chunks, chunk_end - chunk_start, real_offset)

        t_punct = time.time()
        pname = f"[{file_tag}] Punctuation" if file_tag else "Punctuation"
        fixed = _llm_process_texts(
            batch_texts, system_prompt, len(batch_texts), pname,
            api_url=api_url, model=model, api_key=api_key, api_timeout=api_timeout,
        )

        log.info("%sPunctuation: batch %d/%d completed in %.1fs",
                 _t, chunk_num, total_chunks, time.time() - t_punct)

        # Only use results from the non-overlap portion
        for i in range(real_offset, len(fixed)):
            global_idx = context_start + i
            if global_idx < total:
                result_texts[global_idx] = fixed[i]

        chunk_start = chunk_end

    # Build result entries with punctuated text
    result = []
    for entry, new_text in zip(entries, result_texts):
        new_entry = dict(entry)
        new_entry["text"] = _nfc(new_text)
        result.append(new_entry)
    return result


def _llm_cleanup_pass(
    entries: List[dict],
    batch_size: int,
    *,
    api_url: str,
    model: str,
    api_key: str,
    api_timeout: int = 120,
    file_tag: str = "",
) -> List[dict]:
    """LLM Pass 2: Fix misheard words, remove filler.

    Runs AFTER sentence re-segmentation, so each entry is a clean
    sentence or clause. The LLM only needs to fix word errors.
    """
    system_prompt = (
        "/no_think\n"
        "Fix speech recognition errors in these subtitle lines.\n"
        "\n"
        "Input: [N] text\n"
        "Output: [N] corrected text\n"
        "\n"
        "Fix these issues:\n"
        "- Misheard words: use context to figure out the correct word\n"
        "- Filler words: remove um, uh, er, like, you know, I mean, basically\n"
        "- Stuttering: \"it's it's important\" → \"it's important\"\n"
        "- False starts: \"I was— I went there\" → \"I went there\"\n"
        "\n"
        "IMPORTANT: Do NOT remove, shorten, or rephrase anything else.\n"
        "Keep every word that is not a filler, stutter, or error.\n"
        "If you are not sure a word is wrong, leave it as it is.\n"
        "Do not change punctuation or capitalisation (already correct).\n"
        "Preserve __TAG0__, __TAG1__ placeholders exactly.\n"
        "Return ONLY numbered entries. No explanations."
    )

    # Protect tags
    texts = []
    tag_maps = []
    for e in entries:
        text, tags = _protect_tags(e["text"].replace("\n", " ").strip())
        texts.append(text)
        tag_maps.append(tags)

    cname = f"[{file_tag}] Cleanup" if file_tag else "Cleanup"
    fixed = _llm_process_texts(
        texts, system_prompt, batch_size, cname,
        api_url=api_url, model=model, api_key=api_key, api_timeout=api_timeout,
    )

    result = []
    for entry, new_text, tags in zip(entries, fixed, tag_maps):
        restored = _restore_tags(new_text, tags)
        new_entry = dict(entry)
        new_entry["text"] = _nfc(restored)
        result.append(new_entry)
    return result


# ══════════════════════════════════════════════════════════════════════════════
# Post-Processing Pass
# ══════════════════════════════════════════════════════════════════════════════

def _wrap_lines(text: str, rules: dict) -> str:
    """Wrap subtitle text into max 2 lines per Netflix standards.

    Line wrapping is done entirely by code — the LLM returns single-line text.
    """
    max_cpl = rules["max_chars_per_line"]
    max_lines = rules["max_lines"]
    preferred = set(rules.get("preferred_split_words", []))

    # Collapse to single line
    text = " ".join(text.replace("\n", " ").split()).strip()

    if not text:
        return text

    # Single line fits
    if len(text) <= max_cpl:
        return text

    max_total = max_cpl * max_lines  # 84 for 42×2

    # NEVER truncate/drop words. A slightly long subtitle is readable;
    # a subtitle with missing words is broken. If text exceeds max_total,
    # just split it into 2 lines as best we can — the CPS check will
    # flag it but the viewer can still read it.

    if max_lines < 2 or len(text) <= max_cpl:
        return text

    # Split into 2 lines — find best split point
    best_pos = None
    best_score = -1
    midpoint = len(text) / 2

    # Score each possible split position
    words_with_pos = []
    pos = 0
    for word in text.split():
        words_with_pos.append((pos, word))
        pos += len(word) + 1  # +1 for space

    for wp_idx in range(1, len(words_with_pos)):
        split_pos = words_with_pos[wp_idx][0]  # Position of the word start
        word = words_with_pos[wp_idx][1].lower().rstrip(".,;:!?")

        line1_len = split_pos - 1  # Exclude trailing space
        line2_len = len(text) - split_pos

        if line1_len <= 0 or line2_len <= 0:
            continue

        # Score: prefer balance + natural break points
        # Balance score: closer to midpoint is better (0-100)
        balance = 100 - abs(split_pos - midpoint) * 2

        # Bottom line should be >= top line (inverted pyramid bonus)
        pyramid_bonus = 20 if line2_len >= line1_len else 0

        # Preferred split word bonus
        word_bonus = 30 if word in preferred else 0

        # After punctuation bonus
        char_before = text[split_pos - 2] if split_pos >= 2 else ""
        punct_bonus = 25 if char_before in ".,;:!?—–-" else 0

        # Penalty for lines exceeding max_cpl (soft limit, not hard)
        overflow_penalty = 0
        if line1_len > max_cpl:
            overflow_penalty -= (line1_len - max_cpl) * 3
        if line2_len > max_cpl:
            overflow_penalty -= (line2_len - max_cpl) * 3

        score = balance + pyramid_bonus + word_bonus + punct_bonus + overflow_penalty

        if score > best_score:
            best_score = score
            best_pos = split_pos

    if best_pos is not None:
        line1 = text[:best_pos].rstrip()
        line2 = text[best_pos:].lstrip()
        return f"{line1}\n{line2}"

    # Fallback: split at nearest space to midpoint
    mid = len(text) // 2
    left = text.rfind(" ", 0, mid + 10)
    right = text.find(" ", max(0, mid - 10))
    if right != -1 and (left == -1 or (mid - left) > (right - mid)):
        split = right
    elif left != -1:
        split = left
    else:
        return text  # No space found, return as-is

    return f"{text[:split].rstrip()}\n{text[split + 1:].lstrip()}"


def _enforce_timing(entries: List[dict], rules: dict) -> List[dict]:
    """Enforce timing rules: extend to comfortable duration, cap, enforce gap.

    Subtitles that disappear just as the viewer starts reading are jarring.
    This function extends each entry to a comfortable reading duration,
    using any available dead air before the next subtitle, while:
      - Never overlapping the next entry (respects min_gap)
      - Never exceeding max_duration (doesn't linger forever)
      - Never shrinking entries that are already long enough
    """
    min_dur = rules["min_duration_ms"] / 1000.0
    max_dur = rules["max_duration_ms"] / 1000.0
    min_gap = rules["min_gap_ms"] / 1000.0
    max_cps = rules["max_cps"]
    target_cps = rules["target_cps"]

    # Comfortable reading speed: more relaxed than target CPS.
    # At target_cps=17, comfortable_cps ≈ 11 (65% of target).
    # This gives viewers plenty of time to read without rushing.
    # Only extends into available gap — never overlaps next entry.
    comfortable_cps = target_cps * 0.65

    for i, e in enumerate(entries):
        text_len = len(e["text"].replace("\n", ""))

        # Target duration for comfortable reading:
        #  - At least 1.5 seconds (avoids flashing)
        #  - Or text_length / comfortable_cps (whichever is longer)
        #  - Capped at max_duration
        comfortable_dur = max(1.5, text_len / comfortable_cps)
        comfortable_dur = min(comfortable_dur, max_dur)
        target_end = e["start_sec"] + comfortable_dur

        # How far we CAN extend (respecting next entry + min_gap)
        if i + 1 < len(entries):
            max_allowed_end = entries[i + 1]["start_sec"] - min_gap
        else:
            max_allowed_end = e["start_sec"] + max_dur

        # Extend toward comfortable duration, but don't overshoot allowed end
        new_end = min(target_end, max_allowed_end)

        # Only extend — never shrink via this rule
        if new_end > e["end_sec"]:
            e["end_sec"] = new_end

        # Cap at max_duration
        if e["end_sec"] - e["start_sec"] > max_dur:
            e["end_sec"] = e["start_sec"] + max_dur

        # Enforce min_gap to next entry (never overlap)
        if i + 1 < len(entries):
            gap = entries[i + 1]["start_sec"] - e["end_sec"]
            if gap < min_gap:
                e["end_sec"] = entries[i + 1]["start_sec"] - min_gap
                if e["end_sec"] <= e["start_sec"]:
                    e["end_sec"] = e["start_sec"] + 0.1  # Safety floor

        # Enforce absolute minimum duration (rare case when comfortable < min_dur)
        if e["end_sec"] - e["start_sec"] < min_dur:
            max_end = entries[i + 1]["start_sec"] - min_gap if i + 1 < len(entries) else e["start_sec"] + min_dur
            e["end_sec"] = min(e["start_sec"] + min_dur, max_end)

        e["end_ts"] = _seconds_to_srt_time(e["end_sec"])

        # CPS warning (text was already condensed by LLM; just log)
        duration = max(0.1, e["end_sec"] - e["start_sec"])
        cps = text_len / duration
        if cps > max_cps:
            log.debug("  CPS warning: entry %d has %.1f CPS (max %d): %s",
                      e.get("index", i), cps, max_cps,
                      e["text"][:40].replace("\n", " "))

    return entries


def _remove_hallucinations(entries: List[dict]) -> List[dict]:
    """Remove entries that are likely Whisper hallucinations.

    Uses three detection methods:
    1. Text patterns — known non-dialogue text (subtitles by, ©, etc.)
    2. Speaking speed — words that can't physically be spoken in the duration
    3. Isolation — short generic phrases surrounded by long silence
    """
    cleaned = []
    prev_text = None

    for i, e in enumerate(entries):
        text = e["text"].replace("\n", " ").strip()
        duration = max(0.01, e["end_sec"] - e["start_sec"])
        word_count = len(text.split())
        is_hallucination = False

        # 1. Text pattern check (metadata, formatting artifacts)
        for pattern in HALLUCINATION_PATTERNS:
            if pattern.match(text):
                is_hallucination = True
                log.debug("  Hallucination (pattern): %s", text[:50])
                break

        # 2. Speaking speed check — physically impossible to speak that fast
        if not is_hallucination and word_count >= 3:
            words_per_sec = word_count / duration
            if words_per_sec > MAX_WORDS_PER_SECOND:
                is_hallucination = True
                log.debug("  Hallucination (speed %.1f w/s): %s",
                          words_per_sec, text[:50])

        # 3. Duration check — multiple words in under 0.5 seconds is not real speech
        if not is_hallucination and word_count >= 3 and duration < 0.5:
            is_hallucination = True
            log.debug("  Hallucination (%.3fs too short for %d words): %s",
                      duration, word_count, text[:50])

        if is_hallucination:
            continue

        # Remove consecutive exact duplicates
        if text == prev_text:
            log.debug("  Duplicate removed: %s", text[:50])
            continue

        prev_text = text
        cleaned.append(e)

    return cleaned


def _validate_srt(entries: List[dict]) -> List[dict]:
    """Final validation: re-index, remove empties, fix overlaps."""
    valid = []

    for e in entries:
        text = e["text"].strip()
        if not text:
            continue
        e["text"] = text

        # Fix overlap with previous entry
        if valid:
            prev = valid[-1]
            if e["start_sec"] < prev["end_sec"]:
                prev["end_sec"] = e["start_sec"] - 0.001
                if prev["end_sec"] <= prev["start_sec"]:
                    prev["end_sec"] = prev["start_sec"] + 0.1
                prev["end_ts"] = _seconds_to_srt_time(prev["end_sec"])

        valid.append(e)

    # Re-index
    for i, e in enumerate(valid, 1):
        e["index"] = i
        e["start_ts"] = _seconds_to_srt_time(e["start_sec"])
        e["end_ts"] = _seconds_to_srt_time(e["end_sec"])

    return valid


def _merge_rapid_entries(entries: List[dict], rules: dict) -> List[dict]:
    """Merge consecutive short entries into 2-line entries.

    Instead of flashing two short subtitles in rapid succession:
        Entry 1 (1.2s): "Have you searched him?"
        Entry 2 (1.3s): "83p and a lottery ticket."

    Merge into one entry with more reading time:
        Entry 1 (2.5s): "Have you searched him?
                          83p and a lottery ticket."

    Only merges when:
    - Both entries are short enough to fit as 2 lines (each ≤ 42 chars)
    - The gap between them is small (< 0.5s)
    - Combined duration stays within max_duration
    - Each entry on its own would display for less than 2.5 seconds
    """
    max_cpl = rules["max_chars_per_line"]
    max_dur = rules["max_duration_ms"] / 1000.0
    min_gap = rules["min_gap_ms"] / 1000.0
    SHORT_THRESHOLD = 2.5  # Only merge entries shorter than this

    merged = []
    skip_next = False

    for i, e in enumerate(entries):
        if skip_next:
            skip_next = False
            continue

        # Check if this entry and the next can be merged
        if i + 1 < len(entries):
            nxt = entries[i + 1]
            e_text = e["text"].replace("\n", " ").strip()
            nxt_text = nxt["text"].replace("\n", " ").strip()
            e_dur = e["end_sec"] - e["start_sec"]
            nxt_dur = nxt["end_sec"] - nxt["start_sec"]
            gap = nxt["start_sec"] - e["end_sec"]
            combined_dur = nxt["end_sec"] - e["start_sec"]

            can_merge = (
                len(e_text) <= max_cpl          # First line fits in one line
                and len(nxt_text) <= max_cpl    # Second line fits in one line
                and gap < 0.5                   # Entries are close together
                and gap >= 0                    # Not overlapping
                and combined_dur <= max_dur     # Combined doesn't exceed max
                and e_dur < SHORT_THRESHOLD     # First entry is short
                and nxt_dur < SHORT_THRESHOLD   # Second entry is short
            )

            if can_merge:
                merged.append({
                    "start_sec": e["start_sec"],
                    "end_sec": nxt["end_sec"],
                    "start_ts": _seconds_to_srt_time(e["start_sec"]),
                    "end_ts": _seconds_to_srt_time(nxt["end_sec"]),
                    "text": e_text + "\n" + nxt_text,
                })
                skip_next = True
                continue

        merged.append(dict(e))

    # Re-index
    for i, e in enumerate(merged, 1):
        e["index"] = i
    return merged


def _postprocess(entries: List[dict], rules: dict) -> List[dict]:
    """Orchestrate post-processing: hallucinations, timing, wrapping, validation."""
    entries = _remove_hallucinations(entries)
    entries = _enforce_timing(entries, rules)

    # Merge rapid consecutive short entries into 2-line entries
    entries = _merge_rapid_entries(entries, rules)

    # Wrap lines for entries that are still single-line and too long
    for e in entries:
        if "\n" not in e["text"]:  # Don't re-wrap already merged 2-line entries
            e["text"] = _wrap_lines(e["text"], rules)

    entries = _validate_srt(entries)
    return entries


# ══════════════════════════════════════════════════════════════════════════════
# Whisper Engine
# ══════════════════════════════════════════════════════════════════════════════

def _load_whisper_model(whisper_config: dict) -> WhisperModel:
    """Load faster-whisper model. Called once at startup.

    Tries to load normally (checks HuggingFace for updates).
    If that fails due to no internet, retries in offline mode
    using the cached model.
    """
    model_name = whisper_config["model"]
    device = whisper_config["device"]
    compute_type = whisper_config["compute_type"]

    log.info("Loading Whisper model '%s' (device=%s, compute=%s) ...",
             model_name, device, compute_type)
    t0 = time.time()

    try:
        model = WhisperModel(model_name, device=device, compute_type=compute_type)
    except Exception as exc:
        # If it failed (likely no internet), try offline mode with cached model
        log.warning("Model load failed (%s) — retrying with cached model ...", exc)
        os.environ["HF_HUB_OFFLINE"] = "1"
        try:
            model = WhisperModel(model_name, device=device, compute_type=compute_type)
        finally:
            os.environ.pop("HF_HUB_OFFLINE", None)

    log.info("Whisper model loaded in %.1f seconds", time.time() - t0)
    return model


def _extract_audio(video_path: Path, output_wav: Path) -> bool:
    """Extract audio from video to 16kHz mono WAV via ffmpeg."""
    try:
        result = subprocess.run(
            [
                "ffmpeg", "-y", "-nostdin",
                "-i", str(video_path),
                "-vn",
                "-acodec", "pcm_s16le",
                "-ar", "16000",
                "-ac", "1",
                str(output_wav),
            ],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=600,
        )
        return result.returncode == 0 and output_wav.exists() and output_wav.stat().st_size > 0
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        log.error("  Audio extraction failed: %s", exc)
        return False


def _get_media_duration(path: Path) -> Optional[float]:
    """Get duration of a media file in seconds via ffprobe."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=15,
        )
        if result.returncode == 0 and result.stdout.strip():
            return float(result.stdout.strip())
    except (subprocess.TimeoutExpired, FileNotFoundError, ValueError):
        pass
    return None


_whisper_lock = threading.Lock()


def _transcribe_video(
    video_path: Path,
    whisper_model: WhisperModel,
    whisper_config: dict,
    language_override: str | None = None,
    file_tag: str = "",
) -> Tuple[str, str]:
    """Transcribe a video file to raw SRT.

    Returns (raw_srt_text, detected_language_code).
    Whisper inference is serialized via _whisper_lock for thread safety.
    """
    # Extract audio to temp WAV
    tmp_dir = tempfile.gettempdir()
    wav_path = Path(tmp_dir) / f"transcribe_subs_{os.getpid()}_{video_path.stem}.wav"

    try:
        # Get video duration for progress percentage
        total_duration = _get_media_duration(video_path)
        _t = f"  [{file_tag}] " if file_tag else "  "
        if total_duration:
            dur_display = _seconds_to_srt_time(total_duration).rsplit(",", 1)[0]
            log.info("%sVideo duration: %s", _t, dur_display)

        log.info("%sExtracting audio ...", _t)
        if not _extract_audio(video_path, wav_path):
            raise RuntimeError(f"Failed to extract audio from {video_path}")

        lang = language_override or whisper_config.get("language")
        beam_size = whisper_config.get("beam_size", 10)
        best_of = whisper_config.get("best_of", 5)
        patience = whisper_config.get("patience", 2.0)
        vad_filter = whisper_config.get("vad_filter", False)
        word_timestamps = whisper_config.get("word_timestamps", True)
        condition_on_previous = whisper_config.get("condition_on_previous_text", False)

        # Initial prompt primes Whisper to produce properly punctuated,
        # capitalised output. Without this, condition_on_previous_text=False
        # often produces lowercase unpunctuated text in the first chunks.
        initial_prompt = whisper_config.get(
            "initial_prompt",
            "Hello, how are you? I'm doing well, thank you. "
            "This is a conversation with proper punctuation and capitalisation."
        )

        log.info("%sTranscribing with Whisper (lang=%s, beam=%d, best_of=%d) ...",
                 _t, lang or "auto", beam_size, best_of)
        t0 = time.time()

        with _whisper_lock:
            segments, info = whisper_model.transcribe(
                str(wav_path),
                beam_size=beam_size,
                best_of=best_of,
                patience=patience,
                vad_filter=vad_filter,
                word_timestamps=word_timestamps,
                condition_on_previous_text=condition_on_previous,
                initial_prompt=initial_prompt,
                language=lang,
            )
            # Iterate segments as they're generated, logging progress.
            # Whisper streams segments — total is unknown until done,
            # but we know video duration so we can show percentage.
            segment_list = []
            last_log = 0
            for seg in segments:
                segment_list.append(seg)
                if len(segment_list) - last_log >= 100:
                    pos = _seconds_to_srt_time(seg.end).rsplit(",", 1)[0]
                    elapsed_so_far = time.time() - t0
                    if total_duration and total_duration > 0:
                        pct = min(99, int(seg.end / total_duration * 100))
                        log.info("%sWhisper: %d segments, at %s / %s (%d%%)",
                                 _t, len(segment_list), pos, dur_display, pct)
                    else:
                        log.info("%sWhisper: %d segments, at %s (%.0fs elapsed)",
                                 _t, len(segment_list), pos, elapsed_so_far)
                    last_log = len(segment_list)

        elapsed = time.time() - t0
        detected_lang = info.language or "unknown"
        log.info("%sWhisper done: %d segments, language=%s, %.1f seconds",
                 _t,
                 len(segment_list), detected_lang, elapsed)

        raw_srt = _build_raw_srt(segment_list)
        return raw_srt, detected_lang

    finally:
        # Clean up temp WAV
        try:
            if wav_path.exists():
                wav_path.unlink()
        except OSError:
            pass


# ══════════════════════════════════════════════════════════════════════════════
# Subtitle Detection (skip videos that already have subs)
# ══════════════════════════════════════════════════════════════════════════════

def run_ffprobe(path: Path) -> list[dict]:
    """Return subtitle stream metadata from a media file."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "stream=index,codec_type,codec_name:stream_tags=language,title",
                "-of", "json",
                str(path),
            ],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=30,
        )
        if result.returncode != 0:
            return []
        data = json.loads(result.stdout)
        streams = data.get("streams", [])
        return [s for s in streams if s.get("codec_type") == "subtitle"]
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
        return []


def _has_any_subtitles(video_path: Path, cache: Optional["DirCache"] = None) -> bool:
    """Check if a video has ANY subtitles (embedded or sidecar).

    Returns True if the video already has subtitles — meaning we should skip it.
    """
    # Check embedded subtitle tracks
    sub_streams = run_ffprobe(video_path)
    if sub_streams:
        return True

    # Check for sidecar subtitle files
    stem = video_path.stem
    parent = video_path.parent

    for ext in SIDECAR_EXTENSIONS:
        # Check: Movie.srt, Movie.en.srt, Movie.eng.srt, etc.
        if cache:
            if cache.exists(parent / f"{stem}{ext}"):
                return True
            for child in cache.children(parent):
                if child.stem.startswith(stem) and child.suffix == ext:
                    return True
        else:
            if (parent / f"{stem}{ext}").exists():
                return True
            for f in parent.glob(f"{stem}.*{ext}"):
                return True

    return False


def _find_existing_output(video_path: Path, cache: Optional["DirCache"] = None) -> Optional[Path]:
    """Check if our output .srt already exists for this video."""
    output = video_path.with_suffix(".srt")
    if cache:
        return output if cache.exists(output) else None
    return output if output.exists() else None


# ══════════════════════════════════════════════════════════════════════════════
# Directory Cache (for network share performance)
# ══════════════════════════════════════════════════════════════════════════════

class DirCache:
    """Cache of all file paths under a root — avoids repeated stat() calls."""

    def __init__(self, root: Path):
        log.debug("Building directory cache for %s ...", root)
        t0 = time.time()
        self._files: set[Path] = set()
        try:
            for p in root.rglob("*"):
                if p.is_file():
                    self._files.add(p)
        except OSError as exc:
            log.warning("DirCache scan error: %s", exc)
        log.debug("DirCache: %d files in %.1f seconds", len(self._files), time.time() - t0)

    def exists(self, path: Path) -> bool:
        return path in self._files

    def add(self, path: Path):
        self._files.add(path)

    def remove(self, path: Path):
        self._files.discard(path)

    def children(self, parent: Path) -> List[Path]:
        """Return all cached files directly in a directory."""
        return [p for p in self._files if p.parent == parent]

    def video_files(self) -> List[Path]:
        """Return all video files in the cache, sorted."""
        return sorted(p for p in self._files if p.suffix.lower() in VIDEO_EXTENSIONS)


# ══════════════════════════════════════════════════════════════════════════════
# Job Generation & Pipeline
# ══════════════════════════════════════════════════════════════════════════════

TranscribeJob = dict  # Keys: media, rel, output, description


def _generate_jobs(
    folder: Path,
    dry_run: bool,
    force: bool,
    stats: dict,
    cache: DirCache,
) -> Generator[TranscribeJob, None, None]:
    """Scan folder for videos without subtitles, yield transcription jobs."""
    video_files = cache.video_files()
    log.info("Found %d video files to check", len(video_files))

    for video_path in video_files:
        rel = video_path.relative_to(folder)

        # 1. Output already exists?
        if not force:
            existing = _find_existing_output(video_path, cache)
            if existing:
                log.debug("  SKIP (output exists): %s", rel)
                stats["already_done"] += 1
                continue

        # 2. Video already has subtitles?
        #    Skip this check if --force (user wants to re-transcribe regardless)
        if not force and _has_any_subtitles(video_path, cache):
            log.debug("  SKIP (has subtitles): %s", rel)
            stats["has_subs"] += 1
            continue

        # 3. Yield job
        output = video_path.with_suffix(".srt")
        description = str(rel)

        stats["to_process"] += 1
        log.info("  QUEUE: %s", rel)

        if not dry_run:
            yield {
                "media": video_path,
                "rel": rel,
                "output": output,
                "description": description,
            }


_stats_lock = threading.Lock()


def _transcribe_one(
    job: TranscribeJob,
    whisper_model: WhisperModel,
    whisper_config: dict,
    language_override: str | None,
    rules: dict,
    batch_size: int,
    profile: dict,
    skip_llm: bool,
    stats: dict,
) -> None:
    """Worker: transcribe a single video file through the full pipeline."""
    video_path: Path = job["media"]
    output_path: Path = job["output"]
    rel = job["rel"]
    t0 = time.time()

    # Tag for log lines so interleaved output is identifiable.
    tag = video_path.stem

    try:
        file_num = job.get("file_num", "?")
        file_total = job.get("file_total", "?")
        log.info("[TRANSCRIBE %s/%s] %s", file_num, file_total, rel)

        # Path for raw Whisper output (used as cache between runs)
        raw_srt_path = video_path.with_suffix(".whisper")

        # ── Pass 1: Whisper transcription (or reuse cached raw) ──────────
        if raw_srt_path.exists() and raw_srt_path.stat().st_size > 0:
            log.info("  [%s] Reusing cached Whisper output", tag)
            raw_srt = raw_srt_path.read_text(encoding="utf-8").lstrip("\ufeff")
            detected_lang = "cached"
        else:
            log.info("  [%s] Pass 1: Whisper transcription", tag)
            raw_srt, detected_lang = _transcribe_video(
                video_path, whisper_model, whisper_config, language_override, tag
            )

        if not raw_srt.strip():
            log.warning("  [%s] EMPTY — no speech detected", tag)
            with _stats_lock:
                stats["empty"] += 1
            return

        # Save raw Whisper output as .whisper (always — it's the honest label)
        if not raw_srt_path.exists():
            raw_srt_path.write_text("\ufeff" + raw_srt, encoding="utf-8")

        # If --skip-llm, we're done — the .whisper IS the output
        if skip_llm:
            elapsed = time.time() - t0
            log.info("  [%s] OK-RAW (%.1fs, lang=%s)", tag, elapsed, detected_lang)
            with _stats_lock:
                stats["transcribed"] += 1
            return

        # ── Pass 2: LLM punctuation ─────────────────────────────────────
        entries = _parse_srt_entries(raw_srt)

        # Normalise ALL CAPS entries to lowercase before LLM sees them.
        for e in entries:
            text = e["text"]
            if text == text.upper() and len(text) > 3:
                e["text"] = text.lower()

        log.info("  [%s] Pass 2: LLM punctuation (%d entries)", tag, len(entries))

        try:
            entries = _llm_punctuation_pass(
                entries,
                batch_size=batch_size,
                api_url=profile["api_url"],
                model=profile["model"],
                api_key=profile["api_key"],
                api_timeout=profile["timeout"],
                file_tag=tag,
            )
        except Exception as exc:
            log.warning("  [%s] Punctuation failed: %s — using raw text", tag, exc)

        # ── Pass 3: Sentence re-segmentation ────────────────────────────
        log.info("  [%s] Pass 3: Re-segmenting at sentence boundaries", tag)
        entries = _resegment_by_sentences(entries, rules)
        log.info("  [%s] After re-segmentation: %d entries", tag, len(entries))

        # ── Pass 4: LLM cleanup ─────────────────────────────────────────
        log.info("  [%s] Pass 4: LLM cleanup (misheard words, filler)", tag)

        try:
            entries = _llm_cleanup_pass(
                entries,
                batch_size=batch_size,
                api_url=profile["api_url"],
                model=profile["model"],
                api_key=profile["api_key"],
                api_timeout=profile["timeout"],
                file_tag=tag,
            )
        except Exception as exc:
            log.warning("  [%s] Cleanup failed: %s — using punctuated text", tag, exc)

        # ── Pass 5: Post-process ─────────────────────────────────────────
        log.info("  [%s] Pass 5: Post-processing", tag)
        entries = _postprocess(entries, rules)

        # ── Write output ─────────────────────────────────────────────────
        srt_text = _entries_to_srt(entries)
        output_path.write_text(srt_text, encoding="utf-8")

        # Keep .whisper cache file for now — useful for comparing
        # raw Whisper output vs LLM-cleaned output during testing.
        # TODO: delete .whisper after beta testing is complete

        elapsed = time.time() - t0
        log.info("  [%s] OK (%d entries, %.1fs, lang=%s)",
                 tag, len(entries), elapsed, detected_lang)

        with _stats_lock:
            stats["transcribed"] += 1

    except Exception as exc:
        log.error("  [ERROR] %s: %s", rel, exc)
        with _stats_lock:
            stats["errors"] += 1
        # Raw .srt is already saved during Pass 1, so on re-run
        # Whisper will be skipped and only LLM cleanup retried.


def scan_and_transcribe(
    folder: Path,
    whisper_model: WhisperModel,
    whisper_config: dict,
    language_override: str | None,
    rules: dict,
    batch_size: int,
    profile: dict,
    parallel: int,
    skip_llm: bool,
    dry_run: bool,
    force: bool,
    limit: int = 0,
) -> dict:
    """Main pipeline: scan folder, generate jobs, transcribe in parallel."""
    stats = {
        "already_done": 0,
        "has_subs": 0,
        "to_process": 0,
        "transcribed": 0,
        "empty": 0,
        "errors": 0,
    }

    # Build directory cache
    cache = DirCache(folder)

    # Generate jobs (streaming)
    job_gen = _generate_jobs(folder, dry_run, force, stats, cache)

    # Collect all jobs first so we know the total (scan is fast)
    all_jobs = list(job_gen)

    if dry_run:
        return stats

    if not all_jobs:
        log.info("No files to process")
        return stats

    # Apply limit
    if limit > 0:
        all_jobs = all_jobs[:limit]

    # Number the jobs for progress display
    total_jobs = len(all_jobs)
    for i, job in enumerate(all_jobs, 1):
        job["file_num"] = i
        job["file_total"] = total_jobs

    log.info("Processing %d file(s) ...", total_jobs)
    log.info("-" * 70)

    # Process with thread pool
    # Note: Whisper inference is serialized via _whisper_lock, but audio
    # extraction, LLM calls, and file I/O can overlap between threads.
    workers = max(1, parallel)
    futures = {}
    completed = 0

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for job in all_jobs:
            future = pool.submit(
                _transcribe_one,
                job, whisper_model, whisper_config, language_override,
                rules, batch_size, profile, skip_llm, stats,
            )
            futures[future] = job

        # Harvest completed
        for future in as_completed(futures):
            completed += 1
            try:
                future.result()
            except Exception as exc:
                job = futures[future]
                log.error("[ERROR] %s: %s", job["rel"], exc)
                with _stats_lock:
                    stats["errors"] += 1

    return stats


# ══════════════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════════════

def main():
    config = load_config()

    parser = argparse.ArgumentParser(
        description="Generate subtitles for videos using Whisper + LLM cleanup.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            '  python transcribe_subs.py "D:\\Movies\\Some Movie"\n'
            '  python transcribe_subs.py --skip-llm "D:\\Movies\\Some Movie"\n'
            '  python transcribe_subs.py --dry-run "/mnt/media/Tv/Show"\n'
            '  python transcribe_subs.py --profile deepseek --language en "D:\\Movies"'
        ),
    )
    parser.add_argument("folder", type=str, help="Path to scan for video files")
    parser.add_argument("--profile", type=str, default=None,
                        help="LLM profile name (default: from config)")
    parser.add_argument("--batch-size", type=int, default=None,
                        help="Subtitle entries per LLM call (default: from profile)")
    parser.add_argument("--parallel", type=int, default=None,
                        help="Concurrent file processing (default: from profile)")
    parser.add_argument("--whisper-model", type=str, default=None,
                        help="Override Whisper model (e.g. large-v3, medium, small)")
    parser.add_argument("--language", type=str, default=None,
                        help="Force language code (e.g. en, es, fr) — skip auto-detect")
    parser.add_argument("--limit", type=int, default=0,
                        help="Max files to process (0 = unlimited)")
    parser.add_argument("--force", action="store_true",
                        help="Re-transcribe even if .srt already exists")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be processed, no actual work")
    parser.add_argument("--skip-llm", action="store_true",
                        help="Output raw Whisper .srt without LLM cleanup")
    parser.add_argument("--log-file", type=str, default=None,
                        help="Also log to this file")

    args = parser.parse_args()

    # ── Logging setup ────────────────────────────────────────────────────
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if args.log_file:
        log_path = Path(args.log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(str(log_path), encoding="utf-8"))

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(message)s",
        datefmt="%H:%M:%S",
        handlers=handlers,
    )

    # ── Resolve paths and config ─────────────────────────────────────────
    folder = Path(args.folder)
    if not folder.is_dir():
        log.error("Not a directory: %s", folder)
        sys.exit(1)

    profile = resolve_profile(config, args.profile)
    whisper_config = get_whisper_config(config)
    rules = get_subtitle_rules(config)

    # CLI overrides
    batch_size = args.batch_size or profile["batch_size"]
    parallel = args.parallel or profile["parallel"]
    if args.whisper_model:
        whisper_config["model"] = args.whisper_model

    # ── Validate API key (unless --skip-llm or --dry-run) ────────────────
    if not args.skip_llm and not args.dry_run:
        if not profile["api_key"] or profile["api_key"].lower() == "none":
            if profile["name"] != "local":
                log.error("No API key found for profile '%s'. "
                          "Set %s in your .env file, or use --skip-llm.",
                          profile["name"],
                          config["profiles"][profile["name"]].get("api_key_env", "???"))
                sys.exit(1)

    # ── Banner ───────────────────────────────────────────────────────────
    log.info("=" * 70)
    log.info("transcribe_subs — Subtitle Generation Pipeline")
    log.info("=" * 70)
    log.info("  Folder:        %s", folder)
    log.info("  Whisper model: %s (device=%s, compute=%s)",
             whisper_config["model"], whisper_config["device"],
             whisper_config["compute_type"])
    log.info("  Language:      %s", args.language or "auto-detect")
    log.info("  LLM profile:   %s (%s)", profile["name"], profile["model"])
    log.info("  Batch size:    %d", batch_size)
    log.info("  Parallel:      %d", parallel)
    log.info("  Skip LLM:      %s", args.skip_llm)
    log.info("  Force:         %s", args.force)
    log.info("  Dry run:       %s", args.dry_run)
    log.info("-" * 70)

    # ── Load Whisper model (unless dry run) ──────────────────────────────
    whisper_model = None
    if not args.dry_run:
        whisper_model = _load_whisper_model(whisper_config)

    # ── Run pipeline ─────────────────────────────────────────────────────
    t_start = time.time()
    stats = scan_and_transcribe(
        folder=folder,
        whisper_model=whisper_model,
        whisper_config=whisper_config,
        language_override=args.language,
        rules=rules,
        batch_size=batch_size,
        profile=profile,
        parallel=parallel,
        skip_llm=args.skip_llm,
        dry_run=args.dry_run,
        force=args.force,
        limit=args.limit,
    )
    elapsed = time.time() - t_start

    # ── Summary ──────────────────────────────────────────────────────────
    log.info("=" * 70)
    log.info("SUMMARY")
    log.info("=" * 70)
    log.info("  Already had .srt:    %d", stats["already_done"])
    log.info("  Already had subs:    %d", stats["has_subs"])
    log.info("  Queued for work:     %d", stats["to_process"])
    log.info("  Transcribed:         %d", stats["transcribed"])
    log.info("  Empty (no speech):   %d", stats["empty"])
    log.info("  Errors:              %d", stats["errors"])
    log.info("  Total time:          %.1f seconds", elapsed)
    log.info("=" * 70)

    # ── Write report log ─────────────────────────────────────────────────
    log_dir = Path(__file__).parent / "logs"
    log_dir.mkdir(exist_ok=True)
    report_name = f"report_{time.strftime('%Y%m%d_%H%M%S')}.txt"
    report_path = log_dir / report_name
    try:
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(f"transcribe_subs report — {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"Folder: {folder}\n")
            f.write(f"Profile: {profile['name']} ({profile['model']})\n")
            f.write(f"Whisper: {whisper_config['model']}\n\n")
            for k, v in stats.items():
                f.write(f"  {k}: {v}\n")
            f.write(f"\nTotal time: {elapsed:.1f} seconds\n")
        log.info("Report saved: %s", report_path)
    except OSError:
        pass

    sys.exit(1 if stats["errors"] > 0 else 0)


if __name__ == "__main__":
    main()
