---
name: apple-local-transcription
description: Apple adapter configuration and update semantics
type: project
created: 2026-09-14
---

Apple uses local whisper.cpp through `scripts/sources/apple/import.py`. Configuration
comes from `APPLE_STT_INPUT`, `APPLE_STT_OUT`, `APPLE_STT_MODEL` or CLI options;
the adapter does not automatically load `.env`.

**Why:** Recording privacy and reproducible local operation without an account or remote STT service.

**How to apply:** Preserve source read-only access, hash deduplication, output locking,
and manual-note protection even with `--force`. Retain output state. Do not describe
file names/mtime as actual Apple titles/recording dates. Tests run with
`python3 -m unittest discover -s tests -v` without private recordings.
