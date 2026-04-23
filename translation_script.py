#!/usr/bin/env python3
"""Cross-platform video -> Greek and English subtitles generator for NAS + cron.

Behavior:
- Scan INPUT_DIR for video files
- Generate Greek and English subtitles
- Save SRT files to OUTPUT_DIR
- Save logs to LOG_DIR
- Move processed video to TRANSLATED_DIR after successful completion

Designed for cron execution every 2 minutes.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path


# INPUT_DIR = Path("/volume1/Shared data/video_translation/input")
# OUTPUT_DIR = Path("/volume1/Shared data/video_translation/output")
# TRANSLATED_DIR = Path("/volume1/Shared data/video_translation/translated_videos")
# LOG_DIR = Path("/volume1/Shared data/video_translation/logs")

INPUT_DIR = Path(r"E:\Herbs are my world\Translation_script_subtitles\input")
OUTPUT_DIR = Path(r"E:\Herbs are my world\Translation_script_subtitles\output")
TRANSLATED_DIR = Path(r"E:\Herbs are my world\Translation_script_subtitles\translated_videos")
LOG_DIR = Path(r"E:\Herbs are my world\Translation_script_subtitles\logs")

VIDEO_EXTENSIONS = {
    ".mp4",
    ".mkv",
    ".avi",
    ".mov",
    ".m4v",
    ".wmv",
    ".flv",
    ".webm",
    ".mpeg",
    ".mpg",
}


def find_ffmpeg(script_dir: Path) -> str:
    project_root = script_dir.parent
    candidates = [
        project_root / "ffmpeg.exe",
        project_root / "ffmpeg",
        script_dir / "ffmpeg.exe",
        script_dir / "ffmpeg",
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)

    ffmpeg_in_path = shutil.which("ffmpeg")
    if ffmpeg_in_path:
        return ffmpeg_in_path

    raise FileNotFoundError(
        "ffmpeg was not found. Place ffmpeg in project root or install it in PATH."
    )


def run_ffmpeg_extract_audio(
    ffmpeg_bin: str, video_path: Path, wav_path: Path, log_lines: list[str]
) -> None:
    cmd = [
    ffmpeg_bin,
    "-y",
    "-i",
    str(video_path),
    "-vn",
    "-map",
    "0:a:0?",
    "-ac",
    "1",
    "-ar",
    "16000",
    "-c:a",
    "pcm_s16le",
    "-af",
    "loudnorm",
    str(wav_path),
]

    proc = subprocess.run(cmd, capture_output=True, text=True)
    log_lines.append("[FFMPEG CMD] " + " ".join(cmd))
    if proc.stdout:
        log_lines.append("[FFMPEG STDOUT]\n" + proc.stdout)
    if proc.stderr:
        log_lines.append("[FFMPEG STDERR]\n" + proc.stderr)

    if proc.returncode != 0 or not wav_path.exists() or wav_path.stat().st_size == 0:
        if "does not contain any stream" in (proc.stderr or ""):
            raise RuntimeError("No audio stream found in this video.")
        raise RuntimeError("ffmpeg failed to extract audio. See log file for details.")


def format_srt_time(seconds: float) -> str:
    ms_total = int(round(seconds * 1000))
    hours = ms_total // 3_600_000
    ms_total %= 3_600_000
    minutes = ms_total // 60_000
    ms_total %= 60_000
    secs = ms_total // 1000
    ms = ms_total % 1000
    return f"{hours:02}:{minutes:02}:{secs:02},{ms:03}"


MAX_CHARS_PER_LINE = 42   # max characters per subtitle line
MAX_LINES_PER_CUE = 1    # max lines shown at once on screen


def split_subtitle_text(text: str) -> list[str]:
    """Split a long subtitle text into screen-friendly lines of MAX_CHARS_PER_LINE chars,
    breaking only at word boundaries, and grouping into MAX_LINES_PER_CUE-line cues."""
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        if not current:
            current = word
        elif len(current) + 1 + len(word) <= MAX_CHARS_PER_LINE:
            current += " " + word
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    # Group lines into cues of MAX_LINES_PER_CUE
    cues: list[str] = []
    for i in range(0, len(lines), MAX_LINES_PER_CUE):
        cues.append("\n".join(lines[i : i + MAX_LINES_PER_CUE]))
    return cues if cues else [text]


def write_srt(path: Path, segments: list[dict]) -> None:
    with path.open("w", encoding="utf-8-sig") as f:
        subtitle_index = 1
        for seg in segments:
            start = float(seg["start"])
            end = float(seg["end"])
            text = (seg.get("text") or "").strip()
            if not text:
                continue
            cues = split_subtitle_text(text)
            # Distribute the segment duration evenly across cues
            duration = end - start
            cue_duration = duration / len(cues)
            for i, cue in enumerate(cues):
                cue_start = start + i * cue_duration
                cue_end = cue_start + cue_duration
                f.write(
                    f"{subtitle_index}\n"
                    f"{format_srt_time(cue_start)} --> {format_srt_time(cue_end)}\n"
                    f"{cue}\n\n"
                )
                subtitle_index += 1


def get_wav_duration_seconds(wav_path: Path) -> float:
    with wave.open(str(wav_path), "rb") as wf:
        frames = wf.getnframes()
        rate = wf.getframerate()
    return float(frames) / float(rate)


def run_ffmpeg_cut_audio(
    ffmpeg_bin: str,
    source_wav: Path,
    output_wav: Path,
    start_sec: float,
    duration_sec: float,
    log_lines: list[str],
) -> None:
    cmd = [
        ffmpeg_bin,
        "-y",
        "-ss",
        f"{start_sec:.3f}",
        "-t",
        f"{duration_sec:.3f}",
        "-i",
        str(source_wav),
        "-ac",
        "1",
        "-ar",
        "16000",
        str(output_wav),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if (
        proc.returncode != 0
        or not output_wav.exists()
        or output_wav.stat().st_size == 0
    ):
        if proc.stderr:
            log_lines.append("[FFMPEG CHUNK STDERR]\n" + proc.stderr)
        raise RuntimeError("ffmpeg failed during chunk split.")


def generate_ranges(
    total_duration: float, chunk_seconds: float
) -> list[tuple[float, float]]:
    ranges: list[tuple[float, float]] = []
    start = 0.0
    while start < total_duration:
        duration = min(chunk_seconds, total_duration - start)
        ranges.append((start, duration))
        start += chunk_seconds
    return ranges


def resolve_mode(mode: str, wav_duration_seconds: float) -> str:
    if mode != "auto":
        return mode
    if wav_duration_seconds >= 30.0 * 60.0:
        return "fast"
    return "best"


def benchmark_cpu(iterations: int = 2_500_000) -> float:
    acc = 0
    start = time.perf_counter()
    for i in range(iterations):
        acc = (acc * 33 + i) % 1_000_003
    elapsed = max(time.perf_counter() - start, 1e-6)
    _ = acc
    return iterations / elapsed


def get_hardware_profile() -> dict[str, object]:
    cores = os.cpu_count() or 4
    cpu_score = benchmark_cpu()
    gpu_available = False
    gpu_name = "none"
    gpu_vram_gb = 0.0

    try:
        import torch

        gpu_available = torch.cuda.is_available()
        if gpu_available:
            props = torch.cuda.get_device_properties(0)
            gpu_name = props.name
            gpu_vram_gb = props.total_memory / (1024**3)
    except Exception:
        pass

    return {
        "platform": platform.platform(),
        "cores": cores,
        "cpu_score": cpu_score,
        "gpu_available": gpu_available,
        "gpu_name": gpu_name,
        "gpu_vram_gb": gpu_vram_gb,
    }


def resolve_mode_with_hardware(
    requested_mode: str,
    wav_duration_seconds: float,
    hw: dict[str, object],
) -> str:
    if requested_mode in {"fast", "best"}:
        return requested_mode

    if requested_mode == "auto":
        return resolve_mode("auto", wav_duration_seconds)

    cores = int(hw.get("cores", 4))
    cpu_score = float(hw.get("cpu_score", 0.0))
    gpu_available = bool(hw.get("gpu_available", False))
    gpu_vram_gb = float(hw.get("gpu_vram_gb", 0.0))

    if gpu_available and gpu_vram_gb >= 6.0:
        return "best"
    if wav_duration_seconds >= 45.0 * 60.0:
        return "fast"
    if cores <= 4:
        return "fast"
    if cpu_score < 5_500_000:
        return "fast"
    return "best"


def clean_segments(
    segments: list[dict], lang_label: str, log_lines: list[str]
) -> list[dict]:
    cleaned: list[dict] = []

    watermark_patterns = [
        re.compile(r"authorwave", re.IGNORECASE),
        re.compile(r"^\s*υπότιτλοι\s*$", re.IGNORECASE),
    ]

    previous_text: str | None = None
    repeated_count = 0

    for seg in segments:
        text = (seg.get("text") or "").strip()
        if not text:
            continue

        lower_text = text.casefold()
        no_speech_prob = float(seg.get("no_speech_prob", 0.0) or 0.0)
        duration = float(seg.get("end", 0.0) or 0.0) - float(
            seg.get("start", 0.0) or 0.0
        )

        if any(p.search(text) for p in watermark_patterns):
            log_lines.append(
                f"[FILTER:{lang_label}] dropped watermark-like segment: {text}"
            )
            continue

        if duration >= 20.0 and len(lower_text) <= 32:
            log_lines.append(
                f"[FILTER:{lang_label}] dropped long short-phrase hallucination: text={text!r}, duration={duration:.2f}s"
            )
            continue

        if previous_text == lower_text:
            repeated_count += 1
        else:
            previous_text = lower_text
            repeated_count = 0

        if repeated_count >= 1 and duration >= 10.0 and len(lower_text) <= 40:
            log_lines.append(
                f"[FILTER:{lang_label}] dropped repeated long segment: text={text!r}, repeat_index={repeated_count + 1}, duration={duration:.2f}s"
            )
            continue

        if no_speech_prob >= 0.85 and duration >= 8.0 and len(lower_text) <= 40:
            log_lines.append(
                f"[FILTER:{lang_label}] dropped likely silence hallucination: text={text!r}, no_speech_prob={no_speech_prob:.3f}, duration={duration:.2f}s"
            )
            continue

        cleaned.append(seg)

    return cleaned


# ---------------------------------------------------------------------------
# Cypriot dialect → Standard Greek corrections
# Add more entries here as you discover misheard or dialect words.
# Format: "dialect/misheard word": "standard Greek word"
# ---------------------------------------------------------------------------
CYPRIOT_CORRECTIONS: dict[str, str] = {
    # Nouns / botanical terms
    "φιτό": "φυτό",
    "φιτού": "φυτού",
    "φιτά": "φυτά",
    "φίλομα": "φύλλωμα",
    "φιλώματος": "φυλλώματος",
    "γρέλια": "βλαστάρια",
    "γρέλι": "βλαστάρι",
    "αγρελιά": "αγρελιά",   # keep — it's the actual plant name
    "σαγρελιάς": "αγρελιάς",
    "ρύζαν": "ρίζα",
    "ρύζα": "ρίζα",
    "ρίζαν": "ρίζα",
    # Food / cooking
    "βγά": "αυγά",
    "βγάλα": "αυγά",
    "τσιγαρή": "τηγανητά",
    "τραντά": "τηγανητά",
    "καλητροφή": "καλή τροφή",
    # Medical
    "ορικόνοξη": "ουρικό οξύ",
    "ρικόνοξι": "ουρικό οξύ",
    "ουρικόνοξο": "ουρικό οξύ",
    # Verb forms / dialect
    "μετρώ": "τρώω",          # "δεν μετρώ" → "δεν το τρώω"
    "χαριστώ": "ευχαριστώ",
    # Adjectives
    "περσίνον": "περσινό",
    "περσίνο": "περσινό",
    # Common Cypriot endings
    "έναν": "έναν",            # acceptable, keep
    "δεύτερον": "δεύτερο",
    "άσπρον": "άσπρο",
    "πάντοτε": "πάντοτε",     # acceptable, keep
}


def correct_greek_segments(
    segments: list[dict], log_lines: list[str]
) -> list[dict]:
    """Fix Cypriot dialect words and common speech-recognition errors using a local dictionary."""
    corrected_segments = []
    fixes_total = 0

    # Build a single regex that matches any key (whole-word, case-insensitive)
    pattern = re.compile(
        r"\b(" + "|".join(re.escape(k) for k in CYPRIOT_CORRECTIONS) + r")\b",
        re.IGNORECASE,
    )

    def replace_match(m: re.Match) -> str:
        original = m.group(0)
        replacement = CYPRIOT_CORRECTIONS.get(original.lower(), CYPRIOT_CORRECTIONS.get(original, original))
        # Preserve capitalisation if the original started with uppercase
        if original[0].isupper():
            replacement = replacement[0].upper() + replacement[1:]
        return replacement

    for seg in segments:
        text = (seg.get("text") or "").strip()
        new_text, n = pattern.subn(replace_match, text)
        fixes_total += n
        updated = dict(seg)
        updated["text"] = new_text
        corrected_segments.append(updated)

    log_lines.append(f"[CORRECT] Applied {fixes_total} dialect/recognition fix(es) across {len(segments)} segment(s).")
    return corrected_segments


# def transcribe_and_translate(
#     ffmpeg_bin: str,
#     wav_path: Path,
#     mode: str,
#     model_name: str,
#     split_threshold_min: float,
#     chunk_min: float,
#     log_lines: list[str],
# ) -> tuple[list[dict], list[dict]]:
#     try:
#         import torch
#         import whisper
#     except ImportError as exc:
#         raise RuntimeError(
#             "Missing dependencies. Install with: pip install openai-whisper torch"
#         ) from exc

#     device = "cuda" if torch.cuda.is_available() else "cpu"
#     fp16 = device == "cuda"

#     total_duration = get_wav_duration_seconds(wav_path)
#     effective_mode = (
#         mode if mode in {"fast", "best"} else resolve_mode(mode, total_duration)
#     )
#     log_lines.append(
#         f"[WHISPER] device={device}, fp16={fp16}, mode={mode}, effective_mode={effective_mode}, requested_model={model_name}"
#     )

#     decode_args = {
#         "temperature": 0.0,
#         "condition_on_previous_text": False,
#         "no_speech_threshold": 0.6,
#         "logprob_threshold": -1.0,
#         "compression_ratio_threshold": 2.4,
#     }
#     if effective_mode == "fast":
#         decode_args["beam_size"] = 1
#         decode_args["best_of"] = 1

#     model_candidates = [model_name]
#     for candidate in ["large-v3", "medium", "small"]:
#         if candidate not in model_candidates:
#             model_candidates.append(candidate)

#     last_error: Exception | None = None
#     for candidate in model_candidates:
#         try:
#             log_lines.append(f"[WHISPER] Loading model: {candidate}")
#             model = whisper.load_model(candidate, device=device)

#             total_duration = get_wav_duration_seconds(wav_path)
#             split_threshold_sec = split_threshold_min * 60.0
#             chunk_sec = max(60.0, chunk_min * 60.0)

#             if total_duration >= split_threshold_sec:
#                 ranges = generate_ranges(total_duration, chunk_sec)
#                 log_lines.append(
#                     f"[CHUNK] enabled total={total_duration:.2f}s, chunk={chunk_sec:.2f}s, parts={len(ranges)}"
#                 )
#             else:
#                 ranges = [(0.0, total_duration)]
#                 log_lines.append(
#                     f"[CHUNK] disabled total={total_duration:.2f}s < threshold={split_threshold_sec:.2f}s"
#                 )

#             def transcribe_task(task_name: str) -> list[dict]:
#                 collected: list[dict] = []
#                 with tempfile.TemporaryDirectory(prefix="subtitle_chunks_") as tmp_dir:
#                     tmp_path = Path(tmp_dir)
#                     for idx, (start_sec, dur_sec) in enumerate(ranges, start=1):
#                         chunk_wav = tmp_path / f"chunk_{idx:04}.wav"
#                         if len(ranges) == 1:
#                             chunk_wav = wav_path
#                         else:
#                             run_ffmpeg_cut_audio(
#                                 ffmpeg_bin=ffmpeg_bin,
#                                 source_wav=wav_path,
#                                 output_wav=chunk_wav,
#                                 start_sec=start_sec,
#                                 duration_sec=dur_sec,
#                                 log_lines=log_lines,
#                             )

#                         log_lines.append(
#                             f"[WHISPER] task={task_name} chunk={idx}/{len(ranges)} start={start_sec:.2f}s dur={dur_sec:.2f}s"
#                         )
#                         result = model.transcribe(
#                             str(chunk_wav),
#                             language="el",
#                             task=task_name,
#                             fp16=fp16,
#                             verbose=False,
#                             **decode_args,
#                         )

#                         for seg in result.get("segments", []):
#                             adjusted = dict(seg)
#                             adjusted["start"] = (
#                                 float(seg.get("start", 0.0) or 0.0) + start_sec
#                             )
#                             adjusted["end"] = (
#                                 float(seg.get("end", 0.0) or 0.0) + start_sec
#                             )
#                             collected.append(adjusted)

#                 return collected

#             log_lines.append("[WHISPER] Transcribing Greek subtitles...")
#             greek_segments_raw = transcribe_task("transcribe")

#             log_lines.append("[WHISPER] Translating to English subtitles...")
#             english_segments_raw = transcribe_task("translate")

#             greek_segments = clean_segments(greek_segments_raw, "EL", log_lines)
#             greek_segments = correct_greek_segments(greek_segments, log_lines)
#             english_segments = clean_segments(english_segments_raw, "EN", log_lines)
#             return greek_segments, english_segments
#         except Exception as exc:
#             last_error = exc
#             log_lines.append(f"[WHISPER] Model {candidate} failed: {exc}")

#     assert last_error is not None
#     raise RuntimeError(f"Whisper failed for all model candidates: {last_error}")

def detect_audio_language(wav_path: Path, log_lines: list[str]) -> str:
    """Detect spoken language using Whisper."""
    try:
        import torch
        import whisper
    except ImportError as exc:
        raise RuntimeError(
            "Missing dependencies. Install with: pip install openai-whisper torch"
        ) from exc

    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = whisper.load_model("small", device=device)

    audio = whisper.load_audio(str(wav_path))
    audio = whisper.pad_or_trim(audio)
    mel = whisper.log_mel_spectrogram(audio).to(model.device)

    _, probs = model.detect_language(mel)
    detected_lang = max(probs, key=probs.get)

    top_probs = sorted(probs.items(), key=lambda x: x[1], reverse=True)[:5]
    log_lines.append(
        "[LANG DETECT] top candidates: "
        + ", ".join(f"{lang}={prob:.3f}" for lang, prob in top_probs)
    )
    log_lines.append(f"[LANG DETECT] detected_language={detected_lang}")

    return detected_lang

def translate_segments(
    segments: list[dict],
    source_lang: str,
    target_lang: str,
    log_lines: list[str],
) -> list[dict]:
    """Translate subtitle segments using deep-translator."""
    try:
        from deep_translator import GoogleTranslator
    except ImportError:
        log_lines.append(
            "[WARN] deep-translator not installed. "
            "Install with: pip install deep-translator"
        )
        return []

    translator = GoogleTranslator(source=source_lang, target=target_lang)
    translated_segments: list[dict] = []
    failed_count = 0

    for i, seg in enumerate(segments):
        text = (seg.get("text") or "").strip()
        if not text:
            continue

        try:
            translated = translator.translate(text)
            new_seg = dict(seg)
            new_seg["text"] = (translated or text).strip()
            translated_segments.append(new_seg)
        except Exception as exc:
            failed_count += 1
            log_lines.append(
                f"[TRANSLATE {source_lang}->{target_lang}] segment {i} failed: {exc}"
            )
            fallback_seg = dict(seg)
            fallback_seg["text"] = text
            translated_segments.append(fallback_seg)

        if (i + 1) % 10 == 0:
            time.sleep(0.5)

    log_lines.append(
        f"[TRANSLATE {source_lang}->{target_lang}] translated={len(translated_segments)} failed={failed_count}"
    )
    return translated_segments

def transcribe_and_translate(
    ffmpeg_bin: str,
    wav_path: Path,
    mode: str,
    model_name: str,
    split_threshold_min: float,
    chunk_min: float,
    log_lines: list[str],
) -> tuple[list[dict], list[dict]]:
    try:
        import torch
        import whisper
    except ImportError as exc:
        raise RuntimeError(
            "Missing dependencies. Install with: pip install openai-whisper torch"
        ) from exc

    device = "cuda" if torch.cuda.is_available() else "cpu"
    fp16 = device == "cuda"

    total_duration = get_wav_duration_seconds(wav_path)
    effective_mode = (
        mode if mode in {"fast", "best"} else resolve_mode(mode, total_duration)
    )
    log_lines.append(
        f"[WHISPER] device={device}, fp16={fp16}, mode={mode}, effective_mode={effective_mode}, requested_model={model_name}"
    )

    decode_args = {
        "temperature": 0.0,
        "condition_on_previous_text": False,
        "no_speech_threshold": 0.6,
        "logprob_threshold": -1.0,
        "compression_ratio_threshold": 2.4,
    }
    if effective_mode == "fast":
        decode_args["beam_size"] = 5
        decode_args["best_of"] = 5

    model_candidates = [model_name]
    for candidate in ["large-v3", "medium", "small"]:
        if candidate not in model_candidates:
            model_candidates.append(candidate)

    last_error: Exception | None = None
    for candidate in model_candidates:
        try:
            log_lines.append(f"[WHISPER] Loading model: {candidate}")
            model = whisper.load_model(candidate, device=device)

            total_duration = get_wav_duration_seconds(wav_path)
            split_threshold_sec = split_threshold_min * 60.0
            chunk_sec = max(60.0, chunk_min * 60.0)

            if total_duration >= split_threshold_sec:
                ranges = generate_ranges(total_duration, chunk_sec)
                log_lines.append(
                    f"[CHUNK] enabled total={total_duration:.2f}s, chunk={chunk_sec:.2f}s, parts={len(ranges)}"
                )
            else:
                ranges = [(0.0, total_duration)]
                log_lines.append(
                    f"[CHUNK] disabled total={total_duration:.2f}s < threshold={split_threshold_sec:.2f}s"
                )

            detected_language = detect_audio_language(wav_path, log_lines)

            def transcribe_task(language: str) -> list[dict]:
                collected: list[dict] = []
                with tempfile.TemporaryDirectory(prefix="subtitle_chunks_") as tmp_dir:
                    tmp_path = Path(tmp_dir)

                    for idx, (start_sec, dur_sec) in enumerate(ranges, start=1):
                        chunk_wav = tmp_path / f"chunk_{idx:04}.wav"
                        if len(ranges) == 1:
                            chunk_wav = wav_path
                        else:
                            run_ffmpeg_cut_audio(
                                ffmpeg_bin=ffmpeg_bin,
                                source_wav=wav_path,
                                output_wav=chunk_wav,
                                start_sec=start_sec,
                                duration_sec=dur_sec,
                                log_lines=log_lines,
                            )

                        log_lines.append(
                            f"[WHISPER] transcribe chunk={idx}/{len(ranges)} start={start_sec:.2f}s dur={dur_sec:.2f}s language={language}"
                        )

                        result = model.transcribe(
                            str(chunk_wav),
                            language=language,
                            task="transcribe",
                            fp16=fp16,
                            verbose=False,
                            **decode_args,
                        )

                        for seg in result.get("segments", []):
                            adjusted = dict(seg)
                            adjusted["start"] = float(seg.get("start", 0.0) or 0.0) + start_sec
                            adjusted["end"] = float(seg.get("end", 0.0) or 0.0) + start_sec
                            collected.append(adjusted)

                return collected

            # --------------------------------------------------
            # Greek audio -> Greek transcription + English translation
            # --------------------------------------------------
            if detected_language == "el":
                log_lines.append("[FLOW] Greek audio detected.")

                greek_segments_raw = transcribe_task("el")
                greek_segments = clean_segments(greek_segments_raw, "EL", log_lines)
                greek_segments = correct_greek_segments(greek_segments, log_lines)

                english_segments = translate_segments(
                    greek_segments,
                    source_lang="el",
                    target_lang="en",
                    log_lines=log_lines,
                )

                if not english_segments:
                    log_lines.append("[FLOW] Greek->English translation failed.")
                    english_segments = []

                return greek_segments, english_segments

            # --------------------------------------------------
            # English audio -> English transcription + Greek translation
            # --------------------------------------------------
            if detected_language == "en":
                log_lines.append("[FLOW] English audio detected.")

                english_segments_raw = transcribe_task("en")
                english_segments = clean_segments(english_segments_raw, "EN", log_lines)

                greek_segments = translate_segments(
                    english_segments,
                    source_lang="en",
                    target_lang="el",
                    log_lines=log_lines,
                )
                greek_segments = correct_greek_segments(greek_segments, log_lines)

                if not greek_segments:
                    log_lines.append("[FLOW] English->Greek translation failed.")
                    greek_segments = []

                return greek_segments, english_segments

            # --------------------------------------------------
            # Other language -> transcribe only, no translation
            # --------------------------------------------------
            log_lines.append(f"[FLOW] Other language detected: {detected_language}")

            source_segments = transcribe_task(detected_language)
            source_segments = clean_segments(
                source_segments, detected_language.upper(), log_lines
            )

            return source_segments, []

        except Exception as exc:
            last_error = exc
            log_lines.append(f"[WHISPER] Model {candidate} failed: {exc}")

    assert last_error is not None
    raise RuntimeError(f"Whisper failed for all model candidates: {last_error}")

# Translate function using deep-translator's Google Translate (free, no API key) as a fallback if Whisper's translate task is not used or fails.

def translate_greek_to_english(
    greek_segments: list[dict], log_lines: list[str]
) -> list[dict]:
    """Translate Greek segments to English using Google Translate (free, no API key)."""
    try:
        from deep_translator import GoogleTranslator
    except ImportError:
        log_lines.append(
            "[WARN] deep-translator not installed. "
            "Install with: pip install deep-translator. "
            "Falling back to Whisper translate task."
        )
        return []

    translator = GoogleTranslator(source="el", target="en")
    english_segments: list[dict] = []
    failed_count = 0

    for i, seg in enumerate(greek_segments):
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        try:
            translated = translator.translate(text)
            english_seg = dict(seg)
            english_seg["text"] = translated or text
            english_segments.append(english_seg)
        except Exception as e:
            failed_count += 1
            log_lines.append(
                f"[TRANSLATE] Segment {i} failed: {e}. Keeping Greek text."
            )
            english_seg = dict(seg)
            english_seg["text"] = text
            english_segments.append(english_seg)

        # Small delay every 10 segments to avoid rate limiting
        if (i + 1) % 10 == 0:
            time.sleep(0.5)

    log_lines.append(
        f"[TRANSLATE] Translated {len(english_segments)} segments "
        f"({failed_count} failed) from Greek to English using Google Translate."
    )
    return english_segments

def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate Greek and English SRT subtitles from videos in input folder."
    )
    parser.add_argument(
        "--mode",
        choices=["auto", "auto-hw", "fast", "best"],
        default="auto-hw",
        help="Processing mode: auto-hw (default), auto, fast, or best.",
    )
    parser.add_argument("--model", default=None, help="Whisper model override.")
    parser.add_argument(
        "--split-threshold-min",
        type=float,
        default=20.0,
        help="Auto-split when audio duration exceeds this many minutes.",
    )
    parser.add_argument(
        "--chunk-min",
        type=float,
        default=15.0,
        help="Chunk size in minutes when auto-splitting.",
    )
    parser.add_argument(
        "--keep-temp",
        action="store_true",
        help="Keep temporary WAV audio file for debugging.",
    )
    return parser.parse_args(argv)


def ensure_directories() -> None:
    INPUT_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    TRANSLATED_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)


def is_video_file(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS


def is_file_stable(path: Path, wait_seconds: int = 3) -> bool:
    try:
        size1 = path.stat().st_size
        time.sleep(wait_seconds)
        size2 = path.stat().st_size
        return size1 > 0 and size1 == size2
    except FileNotFoundError:
        return False


def unique_destination_path(dest: Path) -> Path:
    if not dest.exists():
        return dest

    stem = dest.stem
    suffix = dest.suffix
    counter = 1
    while True:
        candidate = dest.with_name(f"{stem}_{counter}{suffix}")
        if not candidate.exists():
            return candidate
        counter += 1


def process_single_video(
    video_path: Path,
    ffmpeg_bin: str,
    args: argparse.Namespace,
    hw: dict[str, object],
) -> bool:
    timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = LOG_DIR / f"run_log_{video_path.stem}_{timestamp}.txt"
    log_lines: list[str] = []

    wav_path = Path(tempfile.gettempdir()) / f"{video_path.stem}_{timestamp}_temp.wav"

    try:
        if not video_path.exists():
            raise FileNotFoundError(f"Video not found: {video_path}")

        if not is_file_stable(video_path):
            log_lines.append(f"[SKIP] file is still changing: {video_path}")
            return False

        base = video_path.stem
        el_srt = OUTPUT_DIR / f"{base}_subtitles_el.srt"
        en_srt = OUTPUT_DIR / f"{base}_subtitles_en.srt"

        log_lines.append(f"[INFO] video={video_path}")
        log_lines.append(f"[INFO] ffmpeg={ffmpeg_bin}")
        log_lines.append(f"[INFO] temp_wav={wav_path}")

        if el_srt.exists() and en_srt.exists():
            log_lines.append("[INFO] SRT files already exist, skipping transcription.")
        else:
            run_ffmpeg_extract_audio(ffmpeg_bin, video_path, wav_path, log_lines)

            audio_duration = get_wav_duration_seconds(wav_path)
            effective_mode = resolve_mode_with_hardware(args.mode, audio_duration, hw)
            selected_model = args.model or (
                "small" if effective_mode == "fast" else "medium"
            )
            log_lines.append(
                f"[INFO] audio_duration={audio_duration:.2f}s, mode={args.mode}, effective_mode={effective_mode}, selected_model={selected_model}, cores={hw['cores']}, cpu_score={float(hw['cpu_score']):.0f}, gpu_available={hw['gpu_available']}, gpu_name={hw['gpu_name']}, gpu_vram_gb={float(hw['gpu_vram_gb']):.2f}, split_threshold_min={args.split_threshold_min}, chunk_min={args.chunk_min}"
            )

            greek_segments, english_segments = transcribe_and_translate(
                ffmpeg_bin=ffmpeg_bin,
                wav_path=wav_path,
                mode=effective_mode,
                model_name=selected_model,
                split_threshold_min=args.split_threshold_min,
                chunk_min=args.chunk_min,
                log_lines=log_lines,
            )

            # write_srt(el_srt, greek_segments)
            # write_srt(en_srt, english_segments)

            # log_lines.append(f"[OK] greek_srt={el_srt}")
            # log_lines.append(f"[OK] english_srt={en_srt}")

            if greek_segments:
                write_srt(el_srt, greek_segments)
                log_lines.append(f"[OK] greek_srt={el_srt}")
            else:
                log_lines.append("[INFO] No Greek subtitles generated.")

            if english_segments:
                write_srt(en_srt, english_segments)
                log_lines.append(f"[OK] english_srt={en_srt}")
            else:
                log_lines.append("[INFO] No English subtitles generated.")

        moved_video_path = unique_destination_path(TRANSLATED_DIR / video_path.name)
        shutil.move(str(video_path), str(moved_video_path))
        log_lines.append(f"[OK] moved_video={moved_video_path}")

        print(f"Processed video     : {video_path.name}")
        print(f"Greek subtitles     : {el_srt}")
        print(f"English subtitles   : {en_srt}")
        print(f"Moved original video: {moved_video_path}")
        return True

    except Exception as exc:
        print(f"ERROR processing {video_path.name}: {exc}")
        log_lines.append(f"[ERROR] {exc}")
        return False

    finally:
        if not args.keep_temp and wav_path.exists():
            try:
                wav_path.unlink(missing_ok=True)
                log_lines.append("[INFO] temp wav cleaned")
            except Exception:
                pass

        try:
            log_path.write_text("\n\n".join(log_lines), encoding="utf-8")
            print(f"Log file: {log_path}")
        except Exception:
            pass


def scan_input_videos() -> list[Path]:
    return sorted([p for p in INPUT_DIR.iterdir() if is_video_file(p)])


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    script_dir = Path(__file__).resolve().parent

    ensure_directories()
    ffmpeg_bin = find_ffmpeg(script_dir)

    print(f"Input folder      : {INPUT_DIR}")
    print(f"Output folder     : {OUTPUT_DIR}")
    print(f"Translated folder : {TRANSLATED_DIR}")
    print(f"Log folder        : {LOG_DIR}")

    videos = scan_input_videos()
    if not videos:
        print("No videos found.")
        return 0

    hw = get_hardware_profile()

    for video_path in videos:
        process_single_video(video_path, ffmpeg_bin, args, hw)

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))