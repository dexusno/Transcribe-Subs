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

# Common Whisper hallucination patterns (case-insensitive)
HALLUCINATION_PATTERNS = [
    re.compile(
        r"^\s*("
        r"subscribe|like and subscribe|thanks for watching|"
        r"thank you for watching|please subscribe|"
        r"subtitles by|captions by|translated by|"
        r"subtitles made by|captioned by|"
        r"amara\.org|opensubtitles|subscene|"
        r"music|music playing|\u266a[\s\u266a]*|"
        r"\.{4,}|_{4,}|-{4,}"
        r")\s*$",
        re.IGNORECASE,
    ),
]

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
    name = profile_name or config.get("default_profile", "deepseek-reasoner")
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
    """Build an SRT string from faster-whisper segments."""
    blocks = []
    idx = 1
    for seg in segments:
        start = _seconds_to_srt_time(seg.start)
        end = _seconds_to_srt_time(seg.end)
        text = (seg.text or "").strip()
        if not text:
            continue
        blocks.append(f"{idx}\n{start} --> {end}\n{text}")
        idx += 1
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
    """Orchestrate pre-processing: merge short, split long, attach budgets."""
    entries = _merge_short_entries(entries, rules["min_duration_ms"])
    entries = _split_long_entries(entries, rules["max_duration_ms"])

    # Attach character budget to each entry
    for e in entries:
        duration = e["end_sec"] - e["start_sec"]
        e["budget"] = _calculate_char_budget(duration, rules["target_cps"])

    return entries


# ══════════════════════════════════════════════════════════════════════════════
# LLM Cleanup Pass
# ══════════════════════════════════════════════════════════════════════════════

def _build_cleanup_system_prompt() -> str:
    """Build the system prompt for subtitle cleanup via LLM."""
    return (
        "You are a professional subtitle editor. You will receive subtitle entries\n"
        "from speech recognition that need cleanup.\n"
        "\n"
        "Each entry is formatted as: [N|budget] text\n"
        "Where N is the entry number and budget is the maximum character count.\n"
        "\n"
        "Rules:\n"
        "1. Return each entry as: [N] corrected text\n"
        "2. Fix spelling, grammar, punctuation, and capitalization\n"
        "3. If the text length EXCEEDS the budget: condense and rephrase\n"
        "   to fit within the budget while preserving the full meaning\n"
        "4. If the text fits within the budget: only correct errors,\n"
        "   do not rephrase or shorten unnecessarily\n"
        "5. Remove filler words: um, uh, er, hmm, like (as filler), you know,\n"
        "   I mean, sort of, kind of, well (as filler), right (as filler),\n"
        "   basically, actually (as filler), literally (as filler)\n"
        "6. Remove false starts and self-corrections:\n"
        '   "I was\u2014 I went to the store" \u2192 "I went to the store"\n'
        "7. Remove stuttering and repetition:\n"
        '   "It\'s it\'s really important" \u2192 "It\'s really important"\n'
        "8. Preserve speaker tone, intent, and character voice\n"
        "9. Preserve any __TAG0__, __TAG1__ etc. placeholders exactly\n"
        "10. Do not add explanations. Return ONLY the numbered entries."
    )


def _build_cleanup_system_prompt_no_think() -> str:
    """Build the system prompt with /no_think prefix for non-reasoning models."""
    return "/no_think\n" + _build_cleanup_system_prompt()


# Regex to parse [N] markers from LLM response
_RESPONSE_RE = re.compile(r"\[(\d+)\]\s*(.*?)(?=\n\[\d+\]|\Z)", re.DOTALL)


def _llm_cleanup_batched(
    entries: List[dict],
    batch_size: int = 500,
    progress_cb: Optional[Callable[[int, int], None]] = None,
    *,
    api_url: str,
    model: str,
    api_key: str,
    api_timeout: int = 300,
) -> List[dict]:
    """Clean up subtitle entries via an OpenAI-compatible LLM API.

    Sends batches of entries with character budgets. Returns entries with
    updated text fields.
    """
    if not entries:
        return entries

    # Choose system prompt based on model (reasoning models don't need /no_think)
    is_reasoner = "reasoner" in model.lower()
    if is_reasoner:
        system_prompt = _build_cleanup_system_prompt()
    else:
        system_prompt = _build_cleanup_system_prompt_no_think()

    # Protect tags
    protected_pairs: List[Tuple[dict, Dict[str, str]]] = []
    prepped_texts: List[str] = []
    for e in entries:
        text, tags = _protect_tags(e["text"].replace("\n", " ").strip())
        protected_pairs.append((e, tags))
        prepped_texts.append(text)

    total = len(entries)
    done = 0
    if progress_cb:
        progress_cb(total, done)

    total_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    headers = {"Content-Type": "application/json"}
    if api_key and api_key.lower() != "none":
        headers["Authorization"] = f"Bearer {api_key}"

    cleaned_texts: List[str] = []

    for i in range(0, total, max(1, batch_size)):
        batch_entries = entries[i: i + batch_size]
        batch_texts = prepped_texts[i: i + batch_size]

        # Build numbered user message with budgets: [N|budget] text
        numbered = []
        for j, (e, text) in enumerate(zip(batch_entries, batch_texts)):
            budget = e.get("budget", 84)
            numbered.append(f"[{j}|{budget}] {text}")
        user_msg = "\n".join(numbered)

        # Build request body
        body: dict = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_msg},
            ],
        }
        # Only set temperature for non-reasoning models
        if not is_reasoner:
            body["temperature"] = 0.3

        try:
            resp = requests.post(
                api_url,
                headers=headers,
                json=body,
                timeout=api_timeout,
            )
            resp.raise_for_status()
            data = resp.json()

            # Accumulate usage
            usage = data.get("usage", {})
            for k in total_usage:
                total_usage[k] += usage.get(k, 0)

            # Log reasoning content at debug level if present
            msg = data["choices"][0]["message"]
            reasoning = msg.get("reasoning_content")
            if reasoning:
                log.debug("  Reasoning tokens used for batch %d-%d",
                          i, i + len(batch_entries))

            translated_text = msg.get("content", "").strip()

            # Parse [N] markers from response
            results: Dict[int, str] = {}
            for match in _RESPONSE_RE.finditer(translated_text):
                idx = int(match.group(1))
                text = match.group(2).strip()
                results[idx] = text

            # Map results back, fallback to original on missing
            for j in range(len(batch_texts)):
                cleaned_texts.append(results.get(j, batch_texts[j]))

        except Exception as exc:
            log.warning("  LLM batch %d-%d failed: %s — using raw text",
                        i, i + len(batch_entries), exc)
            # Fallback: keep original text for this batch
            cleaned_texts.extend(batch_texts)

        done = min(total, done + len(batch_entries))
        if progress_cb:
            progress_cb(total, done)

    # Restore tags, normalize, and update entries
    result_entries = []
    for (entry, tags), cleaned in zip(protected_pairs, cleaned_texts):
        restored = _restore_tags(cleaned, tags)
        new_entry = dict(entry)
        new_entry["text"] = _nfc(restored)
        result_entries.append(new_entry)

    log.debug("  LLM usage — prompt: %d, completion: %d, total: %d",
              total_usage["prompt_tokens"], total_usage["completion_tokens"],
              total_usage["total_tokens"])

    return result_entries


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

    # Truncate to max total if still too long
    if len(text) > max_total:
        # Cut at last word boundary within max_total
        truncated = text[:max_total]
        last_space = truncated.rfind(" ")
        if last_space > max_total // 2:
            text = truncated[:last_space].rstrip()
        else:
            text = truncated.rstrip()

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

        # Both lines must fit
        if line1_len > max_cpl or line2_len > max_cpl:
            continue
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

        score = balance + pyramid_bonus + word_bonus + punct_bonus

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
    """Enforce timing rules: min/max duration, min gap, CPS check."""
    min_dur = rules["min_duration_ms"] / 1000.0
    max_dur = rules["max_duration_ms"] / 1000.0
    min_gap = rules["min_gap_ms"] / 1000.0
    max_cps = rules["max_cps"]

    for i, e in enumerate(entries):
        duration = e["end_sec"] - e["start_sec"]

        # Min duration: extend end if too short
        if duration < min_dur:
            max_end = entries[i + 1]["start_sec"] - min_gap if i + 1 < len(entries) else e["start_sec"] + min_dur
            e["end_sec"] = min(e["start_sec"] + min_dur, max_end)
            e["end_ts"] = _seconds_to_srt_time(e["end_sec"])

        # Max duration: cap
        duration = e["end_sec"] - e["start_sec"]
        if duration > max_dur:
            e["end_sec"] = e["start_sec"] + max_dur
            e["end_ts"] = _seconds_to_srt_time(e["end_sec"])

        # Min gap to next entry
        if i + 1 < len(entries):
            gap = entries[i + 1]["start_sec"] - e["end_sec"]
            if gap < min_gap:
                e["end_sec"] = entries[i + 1]["start_sec"] - min_gap
                if e["end_sec"] <= e["start_sec"]:
                    e["end_sec"] = e["start_sec"] + 0.1  # Safety floor
                e["end_ts"] = _seconds_to_srt_time(e["end_sec"])

        # CPS warning (text was already condensed by LLM; just log)
        duration = max(0.1, e["end_sec"] - e["start_sec"])
        text_len = len(e["text"].replace("\n", ""))
        cps = text_len / duration
        if cps > max_cps:
            log.debug("  CPS warning: entry %d has %.1f CPS (max %d): %s",
                      e.get("index", i), cps, max_cps,
                      e["text"][:40].replace("\n", " "))

    return entries


def _remove_hallucinations(entries: List[dict]) -> List[dict]:
    """Remove entries that match known Whisper hallucination patterns."""
    cleaned = []
    prev_text = None

    for e in entries:
        text = e["text"].replace("\n", " ").strip()

        # Check hallucination patterns
        is_hallucination = False
        for pattern in HALLUCINATION_PATTERNS:
            if pattern.match(text):
                is_hallucination = True
                log.debug("  Hallucination removed: %s", text[:50])
                break

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


def _postprocess(entries: List[dict], rules: dict) -> List[dict]:
    """Orchestrate post-processing: hallucinations, timing, wrapping, validation."""
    entries = _remove_hallucinations(entries)
    entries = _enforce_timing(entries, rules)

    # Wrap lines (code handles this, not LLM)
    for e in entries:
        e["text"] = _wrap_lines(e["text"], rules)

    entries = _validate_srt(entries)
    return entries


# ══════════════════════════════════════════════════════════════════════════════
# Whisper Engine
# ══════════════════════════════════════════════════════════════════════════════

def _load_whisper_model(whisper_config: dict) -> WhisperModel:
    """Load faster-whisper model. Called once at startup."""
    model_name = whisper_config["model"]
    device = whisper_config["device"]
    compute_type = whisper_config["compute_type"]

    log.info("Loading Whisper model '%s' (device=%s, compute=%s) ...",
             model_name, device, compute_type)
    t0 = time.time()
    model = WhisperModel(model_name, device=device, compute_type=compute_type)
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


_whisper_lock = threading.Lock()


def _transcribe_video(
    video_path: Path,
    whisper_model: WhisperModel,
    whisper_config: dict,
    language_override: str | None = None,
) -> Tuple[str, str]:
    """Transcribe a video file to raw SRT.

    Returns (raw_srt_text, detected_language_code).
    Whisper inference is serialized via _whisper_lock for thread safety.
    """
    # Extract audio to temp WAV
    tmp_dir = tempfile.gettempdir()
    wav_path = Path(tmp_dir) / f"transcribe_subs_{os.getpid()}_{video_path.stem}.wav"

    try:
        log.info("  Extracting audio ...")
        if not _extract_audio(video_path, wav_path):
            raise RuntimeError(f"Failed to extract audio from {video_path}")

        lang = language_override or whisper_config.get("language")
        beam_size = whisper_config.get("beam_size", 5)
        vad_filter = whisper_config.get("vad_filter", True)

        log.info("  Transcribing with Whisper (lang=%s, vad=%s) ...",
                 lang or "auto", vad_filter)
        t0 = time.time()

        with _whisper_lock:
            segments, info = whisper_model.transcribe(
                str(wav_path),
                beam_size=beam_size,
                vad_filter=vad_filter,
                language=lang,
            )
            # Iterate segments (generator) to collect them
            segment_list = list(segments)

        elapsed = time.time() - t0
        detected_lang = info.language or "unknown"
        log.info("  Whisper done: %d segments, language=%s, %.1f seconds",
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
            # Use cache for network share performance
            if cache.exists(parent / f"{stem}{ext}"):
                return True
            # Also check common patterns like Movie.XX.srt
            for child in cache.children(parent):
                if child.stem.startswith(stem) and child.suffix == ext:
                    return True
        else:
            # Direct filesystem check
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
        if _has_any_subtitles(video_path, cache):
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

    try:
        log.info("[TRANSCRIBE] %s", rel)

        # ── Pass 1: Whisper ──────────────────────────────────────────────
        raw_srt, detected_lang = _transcribe_video(
            video_path, whisper_model, whisper_config, language_override
        )

        if not raw_srt.strip():
            log.warning("  [EMPTY] No speech detected: %s", rel)
            with _stats_lock:
                stats["empty"] += 1
            return

        # If --skip-llm, write raw and return
        if skip_llm:
            output_path.write_text("\ufeff" + raw_srt, encoding="utf-8")
            elapsed = time.time() - t0
            log.info("  [OK-RAW] %s (%.1fs, lang=%s)", rel, elapsed, detected_lang)
            with _stats_lock:
                stats["transcribed"] += 1
            return

        # ── Pass 2: Pre-process ──────────────────────────────────────────
        entries = _parse_srt_entries(raw_srt)
        log.info("  Pre-processing %d entries ...", len(entries))
        entries = _preprocess(entries, rules)
        log.info("  After pre-processing: %d entries", len(entries))

        # ── Pass 3: LLM cleanup ─────────────────────────────────────────
        def _progress(total, done):
            if done > 0 and done < total:
                log.info("  LLM cleanup: %d/%d entries ...", done, total)

        try:
            entries = _llm_cleanup_batched(
                entries,
                batch_size=batch_size,
                progress_cb=_progress,
                api_url=profile["api_url"],
                model=profile["model"],
                api_key=profile["api_key"],
                api_timeout=profile["timeout"],
            )
        except Exception as exc:
            log.warning("  LLM cleanup failed: %s — saving raw Whisper output", exc)
            # Re-parse raw (pre-processed entries may be partially updated)
            entries = _parse_srt_entries(raw_srt)

        # ── Pass 4: Post-process ─────────────────────────────────────────
        entries = _postprocess(entries, rules)

        # ── Write output ─────────────────────────────────────────────────
        srt_text = _entries_to_srt(entries)
        output_path.write_text(srt_text, encoding="utf-8")

        elapsed = time.time() - t0
        log.info("  [OK] %s (%d entries, %.1fs, lang=%s)",
                 rel, len(entries), elapsed, detected_lang)

        with _stats_lock:
            stats["transcribed"] += 1

    except Exception as exc:
        log.error("  [ERROR] %s: %s", rel, exc)
        with _stats_lock:
            stats["errors"] += 1
        # Try to save raw Whisper output as fallback
        try:
            if "raw_srt" in dir() and raw_srt and raw_srt.strip():
                fallback = video_path.with_suffix(".raw.srt")
                fallback.write_text("\ufeff" + raw_srt, encoding="utf-8")
                log.info("  Fallback raw .srt saved: %s", fallback.name)
        except Exception:
            pass


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

    if dry_run:
        # Exhaust generator to collect stats
        for _ in job_gen:
            pass
        return stats

    # Process with thread pool
    # Note: Whisper inference is serialized via _whisper_lock, but audio
    # extraction, LLM calls, and file I/O can overlap between threads.
    workers = max(1, parallel)
    futures = {}
    completed = 0

    with ThreadPoolExecutor(max_workers=workers) as pool:
        submitted = 0
        for job in job_gen:
            if 0 < limit <= submitted:
                break

            future = pool.submit(
                _transcribe_one,
                job, whisper_model, whisper_config, language_override,
                rules, batch_size, profile, skip_llm, stats,
            )
            futures[future] = job
            submitted += 1

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
