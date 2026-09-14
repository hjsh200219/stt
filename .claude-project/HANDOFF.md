---
created: 2026-09-14
project: stt
summary: Apple Voice Memos local transcription added in v0.3.0.
---

## Session Digest

Added Apple Voice Memos/exported audio import alongside Clova Note. Audio is
transcribed locally using whisper.cpp and ffmpeg; source files and Apple DBs are not modified.
Feature commit: `47f8dd2`.

## Progress

- Complete: discovery, doctor, local transcription, Markdown export, hash deduplication,
  changed-audio updates, output locking and manually edited note protection.
- Verified: 17 unittest cases, Python compilation, Apple skill validation and diff checks.
- Synthetic Korean M4A was transcribed with whisper.cpp 1.9.4 and the multilingual small model;
  a repeated import skipped it. Some recognition errors remain possible.
- Not verified: actual user Voice Memos library import; the inspected library had no audio.

## Next Steps

1. Once Voice Memos has downloaded recordings, run `doctor` and `list`.
2. Alternatively import an exported recording with `--file` and a private `--out` directory.

## Blockers

Actual library verification needs a local recording, not another code change.

## Watch Out

- No cloud transcription or automatic model download. Setup is documented in README.
- File names and modification times are not authoritative Apple titles/recording dates.
- No speaker diarization. Keep transcripts, source recordings and credentials outside this public repository.
- `--force` does not override manually edited notes. Retain output state and lock files.
- No configured lint/type/package build tasks; compilation, tests and skill validation were used.

## Files Touched

- `scripts/sources/apple/import.py`, `skills/apple/SKILL.md`, `tests/test_apple.py`
- `README.md`, `.gitignore`, `.claude-plugin/{plugin,marketplace}.json`
